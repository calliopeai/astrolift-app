"""GCP Secret Manager SecretsBackend (#39)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.secrets import SecretsBackend
from gcp._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class GCPSecretsConfig:
    project_id: str
    secret_id_prefix: str = "astrolift"
    """All managed secrets prefixed; lets operators filter via
    label-based IAM policies + budget."""

    kms_key_name: str | None = None
    client: Any | None = None


class GCPSecretsBackend(SecretsBackend):
    def __init__(self, *, config: GCPSecretsConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import secretmanager

            self._client = secretmanager.SecretManagerServiceClient()

    def ensure_initialized(self) -> dict | None:
        raise NotImplementedError(
            "GCPSecretsBackend does not require out-of-band initialisation"
        )

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.read")
    def get(self, path: str) -> dict[str, str] | None:
        secret_name = self._secret_name(path)
        version_path = f"projects/{self._config.project_id}" f"/secrets/{secret_name}/versions/latest"
        try:
            response = self._client.access_secret_version(
                name=version_path,
            )
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return None
            raise map_api_error(exc) from exc
        payload = response.payload.data.decode("utf-8")
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
        return {"value": payload}

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.write", redact_args=("kvs",))
    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        secret_name = self._secret_name(path)
        secret_path = f"projects/{self._config.project_id}/secrets/{secret_name}"
        # Create the parent secret if missing, then add a new
        # version with the payload.
        try:
            self._client.get_secret(name=secret_path)
        except Exception as exc:
            if type(exc).__name__ != "NotFound":
                raise map_api_error(exc) from exc
            self._create_secret(secret_name=secret_name)

        try:
            self._client.add_secret_version(
                parent=secret_path,
                payload={"data": json.dumps(kvs).encode("utf-8")},
            )
        except Exception as exc:
            raise map_api_error(exc) from exc

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.delete")
    def delete(self, path: str) -> None:
        secret_path = f"projects/{self._config.project_id}" f"/secrets/{self._secret_name(path)}"
        try:
            self._client.delete_secret(name=secret_path)
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(f"secret {path} not found") from exc
            raise map_api_error(exc) from exc

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.list")
    def list(self, prefix: str) -> list[str]:
        full_prefix = self._secret_name(prefix)
        try:
            iterator = self._client.list_secrets(
                parent=f"projects/{self._config.project_id}",
                filter=f'name:"{full_prefix}"',
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
        out: list[str] = []
        for secret in iterator:
            name = secret.name.rsplit("/", 1)[-1]
            stripped = (
                name[len(self._config.secret_id_prefix) + 1 :]
                if name.startswith(
                    self._config.secret_id_prefix + "-",
                )
                else name
            )
            out.append(stripped.replace("-", "/"))
        return sorted(out)

    def _secret_name(self, path: str) -> str:
        # GCP Secret IDs: ASCII letters, digits, hyphens,
        # underscores; max 255 chars; can't start with a number.
        clean = path.replace("/", "-").lstrip("/")
        clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in clean)
        return f"{self._config.secret_id_prefix}-{clean}"

    def _create_secret(self, *, secret_name: str) -> None:
        replication: dict[str, Any] = {"automatic": {}}
        if self._config.kms_key_name:
            replication = {
                "user_managed": {
                    "replicas": [
                        {
                            "location": self._config.project_id.split("-")[0],
                            "customer_managed_encryption": {
                                "kms_key_name": self._config.kms_key_name,
                            },
                        }
                    ],
                },
            }
        try:
            self._client.create_secret(
                parent=f"projects/{self._config.project_id}",
                secret_id=secret_name,
                secret={"replication": replication},
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
