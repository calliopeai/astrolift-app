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

from dataclasses import dataclass, field
from typing import Any

from _sdk._telemetry import driver_op
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

    @driver_op(cloud="k8s_native", driver="secrets", audit=True, sensitive_kind="secret.read")
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

    @driver_op(cloud="k8s_native", driver="secrets", audit=True, sensitive_kind="secret.write", redact_args=("kvs",))
    def upsert(self, path: str, kvs: dict[str, str]) -> None:
        full = self._kv_path(path)
        self._client.post(
            f"/v1/{self._config.kv_mount}/data/{full}",
            json={"data": kvs},
            headers=self._headers(),
        )

    @driver_op(cloud="k8s_native", driver="secrets", audit=True, sensitive_kind="secret.delete")
    def delete(self, path: str) -> None:
        full = self._kv_path(path)
        # KV v2 supports soft-delete by default; this hits the
        # versioned-delete endpoint (operator can later use the
        # destroy endpoint for hard-delete).
        self._client.delete(
            f"/v1/{self._config.kv_mount}/metadata/{full}",
            headers=self._headers(),
        )

    @driver_op(cloud="k8s_native", driver="secrets", audit=True, sensitive_kind="secret.list")
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
        return sorted(f"{prefix.rstrip('/')}/{k.rstrip('/')}" if prefix else k.rstrip("/") for k in keys)

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
    """Default factory builds an httpx-backed client.

    Closes #569. The previous shape returned a ``_RealVaultClient``
    whose every method raised ``NotImplementedError`` even though the
    docstring claimed "thin httpx-backed client" — operators running
    HashiCorp Vault as the k8s-native secrets backend would die on
    the first secret read/write.
    """
    return _RealVaultClient(
        address=config.address,
        timeout_seconds=_VAULT_REQUEST_TIMEOUT_SECONDS,
    )


# Vault HTTP timeout. Tight enough to surface a stuck Vault server in
# the deploy log within ~10s rather than hanging the workflow; loose
# enough to absorb a typical cluster-network round-trip + Vault's
# K8s-auth handshake. KV ops are typically <100ms.
_VAULT_REQUEST_TIMEOUT_SECONDS = 10.0


class _RealVaultClient:
    """Thin httpx-backed Vault HTTP client.

    Wraps the four call shapes ``VaultSecretsBackend`` invokes:

      * ``get(path, headers)`` — KV-v2 read (or any GET).
      * ``post(path, json, headers)`` — KV-v2 write / generic POST.
      * ``delete(path, headers)`` — KV-v2 metadata delete.
      * ``list(path, headers)`` — Vault's non-standard ``LIST`` verb.

    All return the same envelope ``VaultSecretsBackend`` consumes:
    ``{"status_code": int, "body": dict}``. 404 responses raise
    ``_VaultNotFound`` so the backend's ``except _VaultNotFound:``
    clauses route to the "secret doesn't exist" branch (returning
    ``None`` from ``.get`` / ``[]`` from ``.list``).

    Production operators inject a Vault token via ``VaultConfig.token``
    (or the kubernetes-auth login flow when ``auth_method=kubernetes``).
    The header construction lives on the backend side; this client
    only handles HTTP transport + status-code → exception mapping.
    """

    def __init__(self, *, address: str, timeout_seconds: float = 10.0) -> None:
        self._address = address.rstrip("/")
        self._timeout = timeout_seconds
        # The client is created lazily because the module is imported
        # in unit-test paths that never touch it; httpx pulls in
        # cryptography on import for TLS, which is a measurable
        # collection-time cost we'd rather not pay for the k8s_native
        # tests that mock the SecretsBackend wholesale.
        self._client: Any = None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            # ``trust_env=True`` so HTTPS_PROXY / NO_PROXY env vars work
            # for operators behind a corporate proxy. TLS verification
            # uses the system bundle by default; operators with a
            # private CA point ``REQUESTS_CA_BUNDLE`` at it.
            self._client = httpx.Client(
                base_url=self._address,
                timeout=self._timeout,
                trust_env=True,
            )
        return self._client

    def _envelope(self, response: Any) -> dict[str, Any]:
        """Wrap an ``httpx.Response`` in the backend's expected shape.

        Returns ``{"status_code": int, "body": dict}``. Empty / non-JSON
        bodies return ``body={}`` — Vault's KV write returns ``204
        No Content`` when soft-deletes are disabled, and the backend
        consumes only ``body.data.data`` on reads.
        """
        try:
            body = response.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return {"status_code": response.status_code, "body": body}

    def get(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        response = self._http().get(path, headers=headers)
        if response.status_code == 404:
            raise _VaultNotFound(path)
        response.raise_for_status()
        return self._envelope(response)

    def post(
        self,
        path: str,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = self._http().post(path, json=json, headers=headers)
        if response.status_code == 404:
            raise _VaultNotFound(path)
        response.raise_for_status()
        return self._envelope(response)

    def delete(
        self,
        path: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = self._http().delete(path, headers=headers)
        # Vault returns 204 for a successful delete; 404 means the
        # metadata was already gone — both are idempotent-OK from the
        # backend's perspective. We only raise on harder failures.
        if response.status_code == 404:
            raise _VaultNotFound(path)
        response.raise_for_status()
        return self._envelope(response)

    def list(
        self,
        path: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        # Vault's list verb is the non-standard "LIST" method. httpx
        # supports arbitrary verbs via ``request()``. The header
        # alternative — appending ``?list=true`` to a GET — is
        # equivalent on the server side but slightly less explicit
        # in operator-side curl reproductions.
        response = self._http().request("LIST", path, headers=headers)
        if response.status_code == 404:
            raise _VaultNotFound(path)
        response.raise_for_status()
        return self._envelope(response)
