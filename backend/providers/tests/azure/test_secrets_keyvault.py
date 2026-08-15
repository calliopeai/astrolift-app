"""Tests for KeyVaultSecretsBackend (#45)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from azure._errors import NotFoundError
from azure.secrets_keyvault import (
    KeyVaultConfig,
    KeyVaultReferenceError,
    KeyVaultSecretsBackend,
    key_vault_secret_ref,
)


class _NotFound(Exception):
    pass


@dataclass
class FakeSecret:
    name: str
    value: str


@dataclass
class FakeProps:
    name: str


@dataclass
class FakePoller:
    def result(self) -> None:
        return None


class FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get_secret(self, name: str) -> FakeSecret:
        if name not in self.secrets:
            raise _NotFound(name)
        return FakeSecret(name=name, value=self.secrets[name])

    def set_secret(
        self,
        *,
        name: str,
        value: str,
        tags: dict[str, str] | None = None,
    ) -> FakeSecret:
        self.secrets[name] = value
        return FakeSecret(name=name, value=value)

    def begin_delete_secret(self, name: str) -> FakePoller:
        if name not in self.secrets:
            raise _NotFound(name)
        del self.secrets[name]
        return FakePoller()

    def list_properties_of_secrets(self) -> list[FakeProps]:
        return [FakeProps(name=k) for k in self.secrets]


@pytest.fixture
def fake_client() -> FakeSecretClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    return FakeSecretClient()


@pytest.fixture
def backend(fake_client: FakeSecretClient) -> KeyVaultSecretsBackend:
    return KeyVaultSecretsBackend(
        config=KeyVaultConfig(
            vault_url="https://acmeprod.vault.azure.net",
            client=fake_client,
        ),
    )


def test_upsert_writes_json_payload(
    backend: KeyVaultSecretsBackend,
    fake_client: FakeSecretClient,
) -> None:
    backend.upsert("/database/url", {"url": "postgres://..."})
    assert "astrolift-database--url" in fake_client.secrets
    parsed = json.loads(fake_client.secrets["astrolift-database--url"])
    assert parsed == {"url": "postgres://..."}


def test_get_returns_kvs(
    backend: KeyVaultSecretsBackend,
) -> None:
    backend.upsert("/x", {"alpha": "1", "beta": "2"})
    result = backend.get("/x")
    assert result == {"alpha": "1", "beta": "2"}


def test_get_missing_returns_none(
    backend: KeyVaultSecretsBackend,
) -> None:
    assert backend.get("/never") is None


def test_get_non_json_wraps_as_value(
    backend: KeyVaultSecretsBackend,
    fake_client: FakeSecretClient,
) -> None:
    fake_client.secrets["astrolift-legacy"] = "plain-string"
    result = backend.get("legacy")
    assert result == {"value": "plain-string"}


def test_explicit_managed_reference_reads_exactly_once_and_selects_field(
    backend: KeyVaultSecretsBackend,
    fake_client: FakeSecretClient,
) -> None:
    fake_client.secrets["astrolift-pg-orders-master"] = json.dumps({"password": "secret", "user": "app"})
    ref = "azure-kv://acmeprod.vault.azure.net/secrets/astrolift-pg-orders-master#password"
    assert backend.get(ref) == {"password": "secret"}


def test_explicit_reference_rejects_foreign_vault_and_malformed_path(
    backend: KeyVaultSecretsBackend,
) -> None:
    with pytest.raises(KeyVaultReferenceError, match="different vault"):
        backend.get("azure-kv://foreign.vault.azure.net/secrets/astrolift-pg-orders-master")
    with pytest.raises(KeyVaultReferenceError, match="must be"):
        backend.get("azure-kv://acmeprod.vault.azure.net/keys/not-a-secret")


def test_delete_raises_not_found_for_missing(
    backend: KeyVaultSecretsBackend,
) -> None:
    with pytest.raises(NotFoundError):
        backend.delete("/never")


def test_delete_removes(
    backend: KeyVaultSecretsBackend,
    fake_client: FakeSecretClient,
) -> None:
    backend.upsert("/y", {"k": "v"})
    backend.delete("/y")
    assert "astrolift-y" not in fake_client.secrets


def test_secret_name_canonicalization() -> None:
    backend = KeyVaultSecretsBackend(
        config=KeyVaultConfig(
            vault_url="https://x.vault.azure.net",
            client=FakeSecretClient(),
        ),
    )
    # No underscores allowed → dashes
    assert backend._secret_name("/a/b/c") == "astrolift-a--b--c"
    # Bad chars get scrubbed
    assert backend._secret_name("a@b!c") == "astrolift-a-b-c"
    # Already-materialized managed-service rows carry physical names.
    assert backend._secret_name("astrolift-pg-orders-master") == "astrolift-pg-orders-master"
    # A logical path with a slash remains namespaced even if its first segment
    # resembles the physical prefix.
    assert backend._secret_name("/astrolift/orders") == "astrolift-astrolift--orders"


def test_custom_managed_prefix_is_an_explicit_legacy_allowlist(fake_client: FakeSecretClient) -> None:
    backend = KeyVaultSecretsBackend(
        config=KeyVaultConfig(
            vault_url="https://x.vault.azure.net",
            client=fake_client,
            managed_secret_name_prefixes=("customer-db",),
        ),
    )
    assert backend._secret_name("customer-db-orders-master") == "customer-db-orders-master"
    assert backend._secret_name("untrusted-orders-master") == "astrolift-untrusted-orders-master"


@pytest.mark.parametrize(
    "vault_url",
    [
        "http://x.vault.azure.net",
        "https://user@x.vault.azure.net",
        "https://x.vault.azure.net/secrets",
        "https://x.vault.azure.net?redirect=foreign",
    ],
)
def test_config_rejects_unsafe_vault_origins(vault_url: str) -> None:
    with pytest.raises(KeyVaultReferenceError, match="HTTPS origin"):
        KeyVaultConfig(vault_url=vault_url, client=FakeSecretClient())


def test_key_vault_secret_ref_is_unambiguous_and_validated() -> None:
    assert key_vault_secret_ref("https://kv.vault.azure.net/", "astrolift-pg-orders-master") == (
        "azure-kv://kv.vault.azure.net/secrets/astrolift-pg-orders-master"
    )
    with pytest.raises(KeyVaultReferenceError, match="secret name"):
        key_vault_secret_ref("https://kv.vault.azure.net", "bad/name")


def test_list_filters_by_prefix(
    backend: KeyVaultSecretsBackend,
    fake_client: FakeSecretClient,
) -> None:
    backend.upsert("/database/url", {"u": "v"})
    backend.upsert("/api/key", {"k": "v"})
    out = backend.list("")
    assert "database/url" in out
    assert "api/key" in out
