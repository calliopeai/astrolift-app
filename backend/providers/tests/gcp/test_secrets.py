"""Tests for GCPSecretsBackend (#39)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from gcp._errors import NotFoundError
from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig, secret_id_for


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


@dataclass
class FakeSecret:
    name: str


@dataclass
class FakePayload:
    data: bytes


@dataclass
class FakeVersion:
    payload: FakePayload


@dataclass
class FakeSMClient:
    """In-memory Secret Manager stand-in. Implements just enough
    of google-cloud-secretmanager for the backend's operations."""

    secrets: dict[str, list[bytes]] = field(default_factory=dict)

    def get_secret(self, *, name: str) -> FakeSecret:
        secret_id = name.rsplit("/", 1)[-1]
        if secret_id not in self.secrets:
            raise _NotFound(name)
        return FakeSecret(name=name)

    def create_secret(
        self,
        *,
        parent: str,
        secret_id: str,
        secret: dict[str, Any],
    ) -> FakeSecret:
        if secret_id in self.secrets:
            raise _AlreadyExists(secret_id)
        self.secrets[secret_id] = []
        return FakeSecret(name=f"{parent}/secrets/{secret_id}")

    def add_secret_version(
        self,
        *,
        parent: str,
        payload: dict[str, Any],
    ) -> Any:
        secret_id = parent.rsplit("/", 1)[-1]
        self.secrets.setdefault(secret_id, []).append(payload["data"])
        return MagicMock()

    def access_secret_version(self, *, name: str) -> FakeVersion:
        # name shape: projects/p/secrets/x/versions/latest
        secret_id = name.split("/")[3]
        versions = self.secrets.get(secret_id)
        if not versions:
            raise _NotFound(name)
        return FakeVersion(payload=FakePayload(data=versions[-1]))

    def delete_secret(self, *, name: str) -> None:
        secret_id = name.rsplit("/", 1)[-1]
        if secret_id not in self.secrets:
            raise _NotFound(name)
        del self.secrets[secret_id]

    def list_secrets(
        self,
        *,
        parent: str,
        filter: str = "",
    ) -> list[FakeSecret]:
        return [FakeSecret(name=f"{parent}/secrets/{k}") for k in self.secrets]


@pytest.fixture
def fake_sm_client() -> FakeSMClient:
    # Patch the gcp._errors._NAME_MAP-keys onto our stub exception
    # classes by giving them the right class name.
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return FakeSMClient()


@pytest.fixture
def backend(fake_sm_client: FakeSMClient) -> GCPSecretsBackend:
    return GCPSecretsBackend(
        config=GCPSecretsConfig(
            project_id="acme-prod",
            client=fake_sm_client,
        ),
    )


def test_upsert_creates_secret_then_adds_version(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    backend.upsert("/database/url", {"url": "postgres://..."})
    assert "astrolift--database-url" in fake_sm_client.secrets
    versions = fake_sm_client.secrets["astrolift--database-url"]
    assert len(versions) == 1
    parsed = json.loads(versions[0])
    assert parsed == {"url": "postgres://..."}


def test_upsert_idempotent_on_existing_secret(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    backend.upsert("/api/key", {"k": "v1"})
    backend.upsert("/api/key", {"k": "v2"})
    versions = fake_sm_client.secrets["astrolift--api-key"]
    assert len(versions) == 2  # 2 versions, single secret


def test_get_returns_kvs(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    backend.upsert("/x", {"alpha": "1", "beta": "2"})
    result = backend.get("/x")
    assert result == {"alpha": "1", "beta": "2"}


def test_get_returns_none_on_missing(
    backend: GCPSecretsBackend,
) -> None:
    assert backend.get("/never-created") is None


def test_get_wraps_non_json_payload_as_value(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    fake_sm_client.secrets["astrolift--legacy"] = [b"plain-string"]
    result = backend.get("/legacy")
    assert result == {"value": "plain-string"}


def test_delete_raises_not_found_for_missing(
    backend: GCPSecretsBackend,
) -> None:
    with pytest.raises(NotFoundError):
        backend.delete("/never-created")


def test_delete_removes_secret(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    backend.upsert("/y", {"k": "v"})
    backend.delete("/y")
    assert "astrolift--y" not in fake_sm_client.secrets


def test_secret_name_canonicalization() -> None:
    backend = GCPSecretsBackend(
        config=GCPSecretsConfig(
            project_id="p",
            client=FakeSMClient(),
        ),
    )
    # Slashes → dashes
    assert backend._secret_name("/a/b/c") == "astrolift--a-b-c"
    # Bad characters replaced
    assert backend._secret_name("a@b!c") == "astrolift-a-b-c"


def test_managed_path_does_not_double_apply_default_prefix() -> None:
    assert secret_id_for("astrolift/cloudsql/app/master", prefix="astrolift") == "astrolift-cloudsql-app-master"


def test_operator_prefix_is_applied_to_managed_logical_path() -> None:
    assert secret_id_for("astrolift/cloudsql/app/master", prefix="smd") == "smd-astrolift-cloudsql-app-master"


def test_prefixed_legacy_secret_remains_readable_rotatable_and_deletable(
    backend: GCPSecretsBackend,
    fake_sm_client: FakeSMClient,
) -> None:
    path = "astrolift/legacy/key"
    legacy_id = "astrolift-astrolift-legacy-key"
    fake_sm_client.secrets[legacy_id] = [b'{"value":"old"}']

    assert backend.get(path) == {"value": "old"}
    backend.upsert(path, {"value": "new"})
    assert len(fake_sm_client.secrets[legacy_id]) == 2
    assert "astrolift-legacy-key" not in fake_sm_client.secrets

    backend.delete(path)
    assert legacy_id not in fake_sm_client.secrets
