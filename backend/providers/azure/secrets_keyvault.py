"""Azure Key Vault SecretsBackend (#45).

Key Vault stores each secret as a single string value — to fit the
{kvs} dict shape Astrolift uses elsewhere, the driver JSON-encodes
the dict before write and JSON-decodes on read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from _sdk._telemetry import driver_op
from _sdk.secrets import SecretsBackend
from azure._errors import NotFoundError, map_api_error

_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,127}$")
_LEGACY_MANAGED_SECRET_PREFIXES = (
    "astrolift-acs-email",
    "astrolift-aisearch",
    "astrolift-aoai",
    "astrolift-cosmos",
    "astrolift-event-grid",
    "astrolift-files",
    "astrolift-mysql",
    "astrolift-pg",
    "astrolift-redis",
    "astrolift-search",
)


class KeyVaultReferenceError(ValueError):
    """A Key Vault URL, logical path, or physical reference is unsafe."""


@dataclass(frozen=True)
class KeyVaultConfig:
    vault_url: str
    """e.g. https://acmeprod.vault.azure.net"""

    secret_name_prefix: str = "astrolift"
    """All managed secrets prefixed; lets operators filter via
    tag-based access policies + budget."""

    client: Any | None = None
    """SecretClient — injected for tests."""

    managed_secret_name_prefixes: tuple[str, ...] = ()
    """Legacy direct-write prefixes accepted as physical names.

    New managed-service bindings use explicit ``azure-kv://`` references.
    This allowlist keeps already-materialized binding rows readable without a
    broad fallback that could escape the platform namespace.
    """

    def __post_init__(self) -> None:
        _vault_netloc(self.vault_url)
        _validate_secret_prefix(self.secret_name_prefix)
        prefixes = tuple(
            sorted(
                {
                    *_LEGACY_MANAGED_SECRET_PREFIXES,
                    *(str(value) for value in self.managed_secret_name_prefixes),
                },
            ),
        )
        for prefix in prefixes:
            _validate_secret_prefix(prefix)
        object.__setattr__(self, "managed_secret_name_prefixes", prefixes)


class KeyVaultSecretsBackend(SecretsBackend):
    provider_id = "azure-key-vault"
    supports_value_reveal = True
    value_reveal_limitation = None

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
        secret_name, field = self._reference(path)
        try:
            secret = self._client.get_secret(secret_name)
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            raise map_api_error(exc) from exc
        result: dict[str, str]
        try:
            parsed = json.loads(secret.value)
            if isinstance(parsed, dict):
                result = {str(k): str(v) for k, v in parsed.items()}
            else:
                result = {"value": secret.value}
        except (json.JSONDecodeError, TypeError):
            result = {"value": secret.value}
        if not field:
            return result
        if field not in result:
            return None
        return {field: result[field]}

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
        except Exception as exc:
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
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"secret {path} not found") from exc
            raise map_api_error(exc) from exc

    @driver_op(cloud="azure", driver="secrets", audit=True, sensitive_kind="secret.list")
    def list(self, prefix: str) -> list[str]:
        try:
            iterator = self._client.list_properties_of_secrets()
        except Exception as exc:
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
        return self._reference(path)[0]

    def _reference(self, path: str) -> tuple[str, str]:
        if path.startswith("azure-kv://"):
            return self._explicit_reference(path)
        base_path, separator, field = path.partition("#")
        # Key Vault secret names: ASCII alphanumeric + dashes only.
        # No underscores. Slashes → double-dash for round-tripping.
        clean = base_path.replace("/", "--").lstrip("-")
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in clean)
        while "---" in clean:
            clean = clean.replace("---", "--")
        physical_prefixes = (self._config.secret_name_prefix, *self._config.managed_secret_name_prefixes)
        is_legacy_physical = "/" not in base_path and any(
            clean == prefix or clean.startswith(f"{prefix}-") for prefix in physical_prefixes
        )
        name = clean if is_legacy_physical else f"{self._config.secret_name_prefix}-{clean}"
        if not _SECRET_NAME_RE.fullmatch(name):
            raise KeyVaultReferenceError("Key Vault secret name must be 1-127 letters, numbers, or hyphens")
        return name, field if separator else ""

    def _explicit_reference(self, value: str) -> tuple[str, str]:
        parsed = urlparse(value)
        if parsed.scheme != "azure-kv" or parsed.query or parsed.username or parsed.password:
            raise KeyVaultReferenceError("invalid Azure Key Vault secret reference")
        expected = _vault_netloc(self._config.vault_url)
        if parsed.netloc.casefold() != expected.casefold():
            raise KeyVaultReferenceError("Azure Key Vault secret reference targets a different vault")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "secrets" or not _SECRET_NAME_RE.fullmatch(parts[1]):
            raise KeyVaultReferenceError(
                "Azure Key Vault secret reference must be azure-kv://<vault>/secrets/<name>[#key]",
            )
        return parts[1], parsed.fragment


def key_vault_secret_ref(vault_url: str, secret_name: str) -> str:
    """Return an unambiguous, same-vault physical secret reference."""

    if not _SECRET_NAME_RE.fullmatch(secret_name):
        raise KeyVaultReferenceError("Key Vault secret name must be 1-127 letters, numbers, or hyphens")
    return f"azure-kv://{_vault_netloc(vault_url)}/secrets/{secret_name}"


def _vault_netloc(vault_url: str) -> str:
    parsed = urlparse(vault_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise KeyVaultReferenceError("Key Vault URL must be an HTTPS origin without path, query, or credentials")
    return parsed.netloc


def _validate_secret_prefix(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", value):
        raise KeyVaultReferenceError("Key Vault secret prefix must be 1-100 letters, numbers, or hyphens")
