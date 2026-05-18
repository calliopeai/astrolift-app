"""Tests for ``_RealVaultClient`` — the live-Vault HTTP wrapper.

The driver-level tests in ``test_secrets_vault.py`` mock the whole
HTTP client via ``VaultConfig.http_client``. These tests target the
wrapper itself: the httpx wiring, KV-v2 path shapes, 404 → exception
mapping, and the non-standard ``LIST`` verb Vault uses.

Closes #569. The previous shape returned a wrapper whose every method
raised ``NotImplementedError`` even though the docstring claimed
"thin httpx-backed client" — production operators on the Vault
backend died on the first secret write.

We don't talk to a real Vault server — every request goes through
``httpx.MockTransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from k8s_native.secrets_vault import _RealVaultClient, _VaultNotFound

# ---- helpers ------------------------------------------------------


def _make_client(transport: httpx.MockTransport) -> _RealVaultClient:
    """Build a client and replace its lazy httpx.Client with one wired
    to the supplied MockTransport. We construct the helper, then pin
    the lazy client to skip the real cryptography import path."""
    client = _RealVaultClient(
        address="https://vault.example:8200",
        timeout_seconds=2.0,
    )
    client._client = httpx.Client(
        base_url="https://vault.example:8200",
        transport=transport,
        timeout=2.0,
    )
    return client


def _kv_data_path(key: str) -> str:
    return f"/v1/secret/data/astrolift/{key}"


def _kv_metadata_path(key: str) -> str:
    return f"/v1/secret/metadata/astrolift/{key}"


# ---- get (KV v2 read) --------------------------------------------


def test_get_returns_body_on_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == _kv_data_path("creds/app")
        assert request.headers["X-Vault-Token"] == "tok-1"
        return httpx.Response(
            200,
            json={
                "data": {
                    "data": {"username": "admin", "password": "secret"},
                },
            },
        )

    client = _make_client(httpx.MockTransport(handler))
    envelope = client.get(
        _kv_data_path("creds/app"),
        headers={"X-Vault-Token": "tok-1"},
    )
    assert envelope["status_code"] == 200
    assert envelope["body"]["data"]["data"]["username"] == "admin"


def test_get_raises_vault_not_found_on_404() -> None:
    """The backend's ``.get`` swallows ``_VaultNotFound`` into ``None``;
    that branch fires when Vault returns 404 (key missing, KV-v2)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": []})

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(_VaultNotFound):
        client.get(
            _kv_data_path("missing"),
            headers={"X-Vault-Token": "tok"},
        )


def test_get_raises_on_5xx() -> None:
    """A 500 from Vault is not a "secret missing" — surface it so the
    operator can see the failure in the deploy log."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="vault sealed")

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.get("/v1/secret/data/x", headers={})


def test_get_envelope_handles_empty_body() -> None:
    """A 200 with no body returns ``body={}`` rather than crashing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = _make_client(httpx.MockTransport(handler))
    envelope = client.get("/v1/sys/health", headers={})
    assert envelope == {"status_code": 200, "body": {}}


# ---- post (KV v2 write) ------------------------------------------


def test_post_writes_kv_data() -> None:
    received_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        received_payload.update(json.loads(request.content))
        return httpx.Response(200, json={"data": {"version": 1}})

    client = _make_client(httpx.MockTransport(handler))
    envelope = client.post(
        _kv_data_path("creds/app"),
        json={"data": {"username": "admin"}},
        headers={"X-Vault-Token": "tok-1"},
    )
    assert envelope["status_code"] == 200
    assert received_payload == {"data": {"username": "admin"}}


def test_post_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(_VaultNotFound):
        client.post(
            "/v1/secret/data/x",
            json={"data": {}},
            headers={},
        )


# ---- delete (KV v2 metadata delete) ------------------------------


def test_delete_returns_204_envelope() -> None:
    """Vault returns 204 on a successful metadata delete; envelope has
    ``status_code=204`` and an empty body."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == _kv_metadata_path("creds/app")
        return httpx.Response(204)

    client = _make_client(httpx.MockTransport(handler))
    envelope = client.delete(
        _kv_metadata_path("creds/app"),
        headers={"X-Vault-Token": "tok"},
    )
    assert envelope["status_code"] == 204
    assert envelope["body"] == {}


def test_delete_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(_VaultNotFound):
        client.delete("/v1/secret/metadata/gone", headers={})


# ---- list (Vault's non-standard LIST verb) -----------------------


def test_list_uses_list_method() -> None:
    """Vault's list endpoint uses the LIST verb (or GET with
    ``?list=true``). We use LIST so curl-reproductions match what the
    operator sees in the access log."""
    method_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method_seen.append(request.method)
        return httpx.Response(
            200,
            json={"data": {"keys": ["app-1/", "app-2/"]}},
        )

    client = _make_client(httpx.MockTransport(handler))
    envelope = client.list(
        _kv_metadata_path(""),
        headers={"X-Vault-Token": "tok"},
    )
    assert method_seen == ["LIST"]
    assert envelope["body"]["data"]["keys"] == ["app-1/", "app-2/"]


def test_list_raises_not_found_on_404() -> None:
    """An empty prefix in Vault returns 404; the backend swallows
    ``_VaultNotFound`` into ``[]``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(_VaultNotFound):
        client.list("/v1/secret/metadata/empty", headers={})


# ---- lazy httpx construction -------------------------------------


def test_lazy_httpx_client_built_on_first_call() -> None:
    """The httpx.Client is built lazily so importing the module doesn't
    pull in cryptography for code paths that mock the backend."""
    client = _RealVaultClient(address="https://vault.example:8200")
    assert client._client is None
    # Don't actually call ``_http()`` here — that builds a real
    # httpx.Client. The assertion is just that the lazy attr starts
    # ``None`` and the construction is deferred.


def test_address_trailing_slash_stripped() -> None:
    client = _RealVaultClient(address="https://vault.example:8200/")
    assert client._address == "https://vault.example:8200"
