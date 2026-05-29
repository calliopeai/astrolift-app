"""Tests for KeyVaultSecretsBackend (#45)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from azure._errors import NotFoundError
from azure.secrets_keyvault import KeyVaultConfig, KeyVaultSecretsBackend


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
        self, *, name: str, value: str, tags: dict[str, str] | None = None,
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
    backend: KeyVaultSecretsBackend, fake_client: FakeSecretClient,
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
    backend: KeyVaultSecretsBackend, fake_client: FakeSecretClient,
) -> None:
    fake_client.secrets["astrolift-legacy"] = "plain-string"
    result = backend.get("legacy")
    assert result == {"value": "plain-string"}


def test_delete_raises_not_found_for_missing(
    backend: KeyVaultSecretsBackend,
) -> None:
    with pytest.raises(NotFoundError):
        backend.delete("/never")


def test_delete_removes(
    backend: KeyVaultSecretsBackend, fake_client: FakeSecretClient,
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


def test_list_filters_by_prefix(
    backend: KeyVaultSecretsBackend, fake_client: FakeSecretClient,
) -> None:
    backend.upsert("/database/url", {"u": "v"})
    backend.upsert("/api/key", {"k": "v"})
    out = backend.list("")
    assert "database/url" in out
    assert "api/key" in out
