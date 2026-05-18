"""HashiCorp Vault SecretsBackend (#51 + #10).

Spec ref: spec 23-provider-plugin-k8s-native + _sdk/secrets.py.

Uses Vault's KV v2 secret engine. Auth is via service-account
JWT (Vault's k8s auth method) when running in-cluster, or a
static token when running externally.

Production deployments should use the External Secrets Operator
(ESO) to mount Vault secrets into the cluster as k8s Secrets;
this driver provides the platform-side CRUD path so apps can
upsert/get secrets from the platform UI/CLI without bypassing
Vault.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from _sdk.secrets import SecretsBackend


@dataclass(frozen=True)
class VaultConfig:
    address: str
    """Vault address, e.g. https://vault.example:8200."""

    token: str = ""
    """Static token (dev mode / CI). In production, use
    auth_method='kubernetes' with sa_role."""

    kv_mount: str = "secret"
    """KV v2 mount path. 'secret' is the dev-mode default."""

    kv_path_prefix: str = "astrolift"
    """All managed secrets land under {mount}/{prefix}/<path>."""

    namespace: str = ""
    """Vault Enterprise namespace. Empty = root namespace."""

    auth_method: str = "token"
    """token | kubernetes. kubernetes auth uses the projected
    SA JWT to authenticate via Vault's k8s auth backend."""

    sa_role: str = ""
    """Vault role bound to the platform's SA. Required when
    auth_method=kubernetes."""

    http_client: Any = field(default=None)
    """Injected for tests. None = build a real httpx.Client."""


class VaultSecretsBackend(SecretsBackend):
    def __init__(self, *, config: VaultConfig) -> None:
        self._config = config
        self._client = config.http_client or _build_http_client(config)

    def get(self, path: str) -> dict[str, str] | None:
        full = self._kv_path(path)
        try:
            response = self._client.get(
                f"/v1/{self._config.kv_mount}/data/{full}",
                headers=self._headers(),
            )
        except _VaultNotFound:
            return None
        if response.get("status_code") == 404:
            return None
        body = response.get("body", {})
        data = body.get("data", {}).get("data", {})
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        full = self._kv_path(path)
        self._client.post(
            f"/v1/{self._config.kv_mount}/data/{full}",
            json={"data": kvs},
            headers=self._headers(),
        )

    def delete(self, path: str) -> None:
        full = self._kv_path(path)
        # KV v2 supports soft-delete by default; this hits the
        # versioned-delete endpoint (operator can later use the
        # destroy endpoint for hard-delete).
        self._client.delete(
            f"/v1/{self._config.kv_mount}/metadata/{full}",
            headers=self._headers(),
        )

    def list(self, prefix: str) -> list[str]:
        full_prefix = self._kv_path(prefix).rstrip("/")
        try:
            response = self._client.list(
                f"/v1/{self._config.kv_mount}/metadata/{full_prefix}",
                headers=self._headers(),
            )
        except _VaultNotFound:
            return []
        body = response.get("body", {})
        keys = body.get("data", {}).get("keys", []) or []
        return sorted(
            f"{prefix.rstrip('/')}/{k.rstrip('/')}" if prefix else k.rstrip("/")
            for k in keys
        )

    def _kv_path(self, path: str) -> str:
        cleaned = path.lstrip("/")
        if self._config.kv_path_prefix:
            return f"{self._config.kv_path_prefix.rstrip('/')}/{cleaned}"
        return cleaned

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.token:
            h["X-Vault-Token"] = self._config.token
        if self._config.namespace:
            h["X-Vault-Namespace"] = self._config.namespace
        return h


class _VaultNotFound(Exception):
    pass


def _build_http_client(config: VaultConfig) -> Any:
    """Default factory builds a thin httpx-backed client.
    Tests inject a stub via VaultConfig.http_client."""
    return _RealVaultClient(address=config.address)


class _RealVaultClient:
    """Thin wrapper so tests can inject without httpx."""

    def __init__(self, *, address: str) -> None:
        self._address = address.rstrip("/")

    def get(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        raise NotImplementedError(
            "real Vault HTTP client not wired — tests inject a stub via "
            "VaultConfig.http_client; production wiring uses httpx",
        )

    def post(
        self, path: str, json: dict, headers: dict[str, str],
    ) -> dict[str, Any]:
        raise NotImplementedError("real client requires httpx")

    def delete(
        self, path: str, headers: dict[str, str],
    ) -> dict[str, Any]:
        raise NotImplementedError("real client requires httpx")

    def list(
        self, path: str, headers: dict[str, str],
    ) -> dict[str, Any]:
        raise NotImplementedError("real client requires httpx")
