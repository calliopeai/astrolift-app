"""GCP Secret Manager SecretsBackend (#39)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.secrets import SecretsBackend
from gcp._errors import NotFoundError, map_api_error


def secret_id_for(path: str, *, prefix: str = "astrolift") -> str:
    """Return the physical Secret Manager id for a logical secret path.

    Managed-service drivers return logical paths in ``ValueRef.secret_ref``;
    the deployment resolver must map those paths to exactly the same physical
    ids the drivers created.  Keep the historical leading-slash behaviour for
    existing operator secrets, but do not apply ``prefix`` twice when a
    logical managed-service path already begins with it.
    """

    clean = path.replace("/", "-").lstrip("/")
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in clean)
    clean_prefix = "".join(c if c.isalnum() or c in "-_" else "-" for c in prefix)
    if not clean_prefix:
        return clean
    if clean == clean_prefix or clean.startswith(f"{clean_prefix}-"):
        return clean
    return f"{clean_prefix}-{clean}"


@dataclass(frozen=True)
class GCPSecretsConfig:
    project_id: str
    secret_id_prefix: str = "astrolift"
    """All managed secrets prefixed; lets operators filter via
    label-based IAM policies + budget."""

    kms_key_name: str | None = None
    client: Any | None = None


class GCPSecretsBackend(SecretsBackend):
    provider_id = "gcp-secret-manager"
    supports_value_reveal = True
    value_reveal_limitation = None

    def __init__(self, *, config: GCPSecretsConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import secretmanager

            self._client = secretmanager.SecretManagerServiceClient()

    @driver_op(cloud="gcp", driver="secrets")
    def ensure_initialized(self) -> dict | None:
        raise NotImplementedError("GCPSecretsBackend does not require out-of-band initialisation")

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.read")
    def get(self, path: str) -> dict[str, str] | None:
        response = None
        for secret_name in self._candidate_secret_names(path):
            version_path = f"projects/{self._config.project_id}/secrets/{secret_name}/versions/latest"
            try:
                response = self._client.access_secret_version(name=version_path)
                break
            except Exception as exc:
                if type(exc).__name__ != "NotFound":
                    raise map_api_error(exc) from exc
        if response is None:
            return None
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
        secret_path = ""
        # Create the parent secret if missing, then add a new
        # version with the payload. Prefer the canonical id, but continue
        # updating a pre-fix legacy id if that is where the path already
        # lives; this avoids forking a user's secret during rotation.
        for candidate in self._candidate_secret_names(path):
            candidate_path = f"projects/{self._config.project_id}/secrets/{candidate}"
            try:
                self._client.get_secret(name=candidate_path)
                secret_path = candidate_path
                break
            except Exception as exc:
                if type(exc).__name__ != "NotFound":
                    raise map_api_error(exc) from exc
        if not secret_path:
            self._create_secret(secret_name=secret_name)
            secret_path = f"projects/{self._config.project_id}/secrets/{secret_name}"

        try:
            self._client.add_secret_version(
                parent=secret_path,
                payload={"data": json.dumps(kvs).encode("utf-8")},
            )
        except Exception as exc:
            raise map_api_error(exc) from exc

    @driver_op(cloud="gcp", driver="secrets", audit=True, sensitive_kind="secret.delete")
    def delete(self, path: str) -> None:
        for secret_name in self._candidate_secret_names(path):
            secret_path = f"projects/{self._config.project_id}/secrets/{secret_name}"
            try:
                self._client.delete_secret(name=secret_path)
                return
            except Exception as exc:
                if type(exc).__name__ != "NotFound":
                    raise map_api_error(exc) from exc
        raise NotFoundError(f"secret {path} not found")

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
        return secret_id_for(path, prefix=self._config.secret_id_prefix)

    def _candidate_secret_names(self, path: str) -> tuple[str, ...]:
        canonical = self._secret_name(path)
        # Before managed-service refs were made canonical, a logical path
        # already beginning with ``secret_id_prefix`` received that prefix a
        # second time. Read/update/delete that legacy id as a fallback.
        clean = path.replace("/", "-").lstrip("/")
        clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in clean)
        legacy = f"{self._config.secret_id_prefix}-{clean}"
        return (canonical,) if legacy == canonical else (canonical, legacy)

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
