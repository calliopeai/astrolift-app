"""Tests for ArtifactRegistryDriver (#40)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from gcp._errors import NotFoundError
from gcp.registry_artifact import (
    ArtifactRegistryConfig,
    ArtifactRegistryDriver,
)


class _NotFound(Exception):
    pass


@dataclass
class FakeRepository:
    name: str


@dataclass
class FakeTag:
    name: str
    version: str = ""


@dataclass
class FakeOperation:
    def result(self) -> None:
        return None


@dataclass
class FakeARClient:
    repos: dict[str, FakeRepository] = field(default_factory=dict)
    tags: dict[str, list[FakeTag]] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)

    def get_repository(self, *, name: str) -> FakeRepository:
        if name not in self.repos:
            raise _NotFound(name)
        return self.repos[name]

    def create_repository(
        self, *, parent: str, repository: Any, repository_id: str,
    ) -> FakeOperation:
        full = f"{parent}/repositories/{repository_id}"
        self.repos[full] = FakeRepository(name=full)
        self.create_calls.append({
            "parent": parent, "repository_id": repository_id,
            "repository": repository,
        })
        return FakeOperation()

    def list_tags(self, *, parent: str) -> list[FakeTag]:
        return self.tags.get(parent, [])


@pytest.fixture(autouse=True)
def patch_ar_module(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_module = types.ModuleType("google.cloud.artifactregistry_v1")

    class _RepoStub:
        class Format:
            DOCKER = "DOCKER"

        class DockerRepositoryConfig:
            def __init__(self, *, immutable_tags: bool = False) -> None:
                self.immutable_tags = immutable_tags

        def __init__(self, *, format_: str = "DOCKER") -> None:
            self.format_ = format_
            self.docker_config: Any = None
            self.kms_key_name: str | None = None

    fake_module.Repository = _RepoStub
    fake_module.ArtifactRegistryClient = lambda: None

    google_module = sys.modules.get("google", types.ModuleType("google"))
    cloud_module = sys.modules.get(
        "google.cloud", types.ModuleType("google.cloud"),
    )
    sys.modules.setdefault("google", google_module)
    sys.modules.setdefault("google.cloud", cloud_module)
    sys.modules["google.cloud.artifactregistry_v1"] = fake_module


@pytest.fixture
def fake_client() -> FakeARClient:
    _NotFound.__name__ = "NotFound"
    return FakeARClient()


@pytest.fixture
def driver(fake_client: FakeARClient) -> ArtifactRegistryDriver:
    return ArtifactRegistryDriver(
        config=ArtifactRegistryConfig(
            project_id="acme",
            location="us-central1",
            repository_id="astrolift-images",
            client=fake_client,
        ),
    )


def test_ensure_repo_creates_ar_repo_when_missing(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    repo = driver.ensure_repo("api")
    assert repo.name == "api"
    assert "us-central1-docker.pkg.dev/acme/astrolift-images/api" in repo.uri
    assert len(fake_client.create_calls) == 1
    assert fake_client.create_calls[0]["repository_id"] == "astrolift-images"


def test_ensure_repo_idempotent(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    driver.ensure_repo("api")
    driver.ensure_repo("api")  # second call hits the existing AR repo
    assert len(fake_client.create_calls) == 1


def test_ensure_repo_different_apps_share_ar_repo(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    a = driver.ensure_repo("api")
    b = driver.ensure_repo("worker")
    assert a.uri.endswith("/api")
    assert b.uri.endswith("/worker")
    # Only the underlying AR repo got created once
    assert len(fake_client.create_calls) == 1


def test_get_pull_secret_marker_for_gke(
    driver: ArtifactRegistryDriver,
) -> None:
    secret = driver.get_pull_secret(
        cluster="prod", namespace="acme-api",
    )
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["namespace"] == "acme-api"
    assert (
        secret["metadata"]["annotations"]["astrolift.io/note"]
        .startswith("GKE clusters use Workload Identity")
    )
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    assert docker == {"auths": {}}


def test_push_returns_full_uri(
    driver: ArtifactRegistryDriver,
) -> None:
    uri = driver.push("local:dev", "api", "abc123")
    assert uri == (
        "us-central1-docker.pkg.dev/acme/astrolift-images/api:abc123"
    )


def test_push_rejects_blank_local_image(
    driver: ArtifactRegistryDriver,
) -> None:
    with pytest.raises(ValueError, match="local_image"):
        driver.push("", "api", "abc123")


def test_list_tags_translates_response(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    package_path = (
        "projects/acme/locations/us-central1"
        "/repositories/astrolift-images/packages/api"
    )
    fake_client.tags[package_path] = [
        FakeTag(
            name=f"{package_path}/tags/v1",
            version=(
                "projects/acme/locations/us-central1"
                "/repositories/astrolift-images/packages/api/versions/sha256:abc"
            ),
        ),
    ]
    tags = driver.list_tags("api")
    assert len(tags) == 1
    assert tags[0].name == "v1"
    assert tags[0].digest == "sha256:abc"


def test_list_tags_missing_package_raises_not_found(
    driver: ArtifactRegistryDriver,
) -> None:
    # FakeARClient.list_tags returns [] for unknown parents — but
    # we want to assert NotFound surfaces for genuinely-missing paths.
    # Patch the method to raise.
    def raise_nf(*args: Any, **kw: Any) -> Any:
        raise _NotFound("nope")

    driver._client.list_tags = raise_nf  # type: ignore[assignment]
    with pytest.raises(NotFoundError):
        driver.list_tags("never")


def test_delete_repo_archive_is_noop(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    # archive=True doesn't touch any AR state
    driver.delete_repo("api", archive=True)
    assert fake_client.repos == {}


def test_delete_repo_force_not_implemented(
    driver: ArtifactRegistryDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.delete_repo("api", archive=False)
