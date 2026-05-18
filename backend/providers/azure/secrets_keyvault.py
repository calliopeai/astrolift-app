"""Azure Key Vault SecretsBackend (#45).

Key Vault stores each secret as a single string value — to fit the
{kvs} dict shape Astrolift uses elsewhere, the driver JSON-encodes
the dict before write and JSON-decodes on read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.secrets import SecretsBackend

from azure._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class KeyVaultConfig:
    vault_url: str
    """e.g. https://acmeprod.vault.azure.net"""

    secret_name_prefix: str = "astrolift"
    """All managed secrets prefixed; lets operators filter via
    tag-based access policies + budget."""

    client: Any | None = None
    """SecretClient — injected for tests."""


class KeyVaultSecretsBackend(SecretsBackend):
    def __init__(self, *, config: KeyVaultConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._client = SecretClient(
                vault_url=config.vault_url,
                credential=DefaultAzureCredential(),
            )

    @driver_op(cloud="azure", driver="secrets", audit=True, sensitive_kind="secret.read")
    def get(self, path: str) -> dict[str, str] | None:
        secret_name = self._secret_name(path)
        try:
            secret = self._client.get_secret(secret_name)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            raise map_api_error(exc) from exc
        try:
            parsed = json.loads(secret.value)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            pass
        return {"value": secret.value}

    @driver_op(cloud="azure", driver="secrets", audit=True, sensitive_kind="secret.write", redact_args=("kvs",))
    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        secret_name = self._secret_name(path)
        try:
            self._client.set_secret(
                name=secret_name,
                value=json.dumps(kvs),
                tags={
                    "astrolift_io_managed_by": "platform",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc

    @driver_op(cloud="azure", driver="secrets", audit=True, sensitive_kind="secret.delete")
    def delete(self, path: str) -> None:
        try:
            poller = self._client.begin_delete_secret(
                self._secret_name(path),
            )
            # Tests inject sync poller; production polls real op.
            if hasattr(poller, "result"):
                poller.result()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"secret {path} not found") from exc
            raise map_api_error(exc) from exc

    @driver_op(cloud="azure", driver="secrets", audit=True, sensitive_kind="secret.list")
    def list(self, prefix: str) -> list[str]:
        try:
            iterator = self._client.list_properties_of_secrets()
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        prefixed = self._secret_name(prefix)
        out: list[str] = []
        for prop in iterator:
            name = getattr(prop, "name", "") or ""
            if not name.startswith(prefixed):
                continue
            stripped = name[len(self._config.secret_name_prefix) + 1 :]
            out.append(stripped.replace("--", "/"))
        return sorted(out)

    def _secret_name(self, path: str) -> str:
        # Key Vault secret names: ASCII alphanumeric + dashes only.
        # No underscores. Slashes → double-dash for round-tripping.
        clean = path.replace("/", "--").lstrip("-")
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in clean)
        while "---" in clean:
            clean = clean.replace("---", "--")
        return f"{self._config.secret_name_prefix}-{clean}"
