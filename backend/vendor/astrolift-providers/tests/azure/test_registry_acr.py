"""Tests for ACRDriver (#46)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from azure._errors import NotFoundError
from azure.registry_acr import ACRConfig, ACRDriver


class _NotFound(Exception):
    pass


@dataclass
class FakeRegistry:
    name: str


@dataclass
class FakeRegistries:
    registries: dict[str, FakeRegistry] = field(default_factory=dict)

    def get(self, *, resource_group_name: str, registry_name: str) -> FakeRegistry:
        if registry_name not in self.registries:
            raise _NotFound(registry_name)
        return self.registries[registry_name]


@dataclass
class FakeACRClient:
    registries_obj: FakeRegistries = field(default_factory=FakeRegistries)
    tags_data: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def registries(self) -> FakeRegistries:
        return self.registries_obj

    def list_tags(
        self, *, resource_group_name: str, registry_name: str, repository: str,
    ) -> list[Any]:
        if repository not in self.tags_data:
            raise _NotFound(repository)
        return self.tags_data[repository]


@pytest.fixture
def fake_client() -> FakeACRClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    client = FakeACRClient()
    client.registries_obj.registries["acmeprod"] = FakeRegistry(
        name="acmeprod",
    )
    return client


@pytest.fixture
def driver(fake_client: FakeACRClient) -> ACRDriver:
    return ACRDriver(
        config=ACRConfig(
            subscription_id="00000000-0000-0000-0000-000000000000",
            resource_group="rg-prod",
            registry_name="acmeprod",
            client=fake_client,
        ),
    )


def test_login_server_format(driver: ACRDriver) -> None:
    assert driver.login_server == "acmeprod.azurecr.io"


def test_ensure_repo_returns_uri(driver: ACRDriver) -> None:
    repo = driver.ensure_repo("api")
    assert repo.name == "api"
    assert repo.uri == "acmeprod.azurecr.io/api"


def test_ensure_repo_missing_registry(
    fake_client: FakeACRClient,
) -> None:
    fake_client.registries_obj.registries.pop("acmeprod")
    driver = ACRDriver(
        config=ACRConfig(
            subscription_id="0",
            resource_group="rg",
            registry_name="acmeprod",
            client=fake_client,
        ),
    )
    with pytest.raises(NotFoundError):
        driver.ensure_repo("api")


def test_pull_secret_marker_for_aks(driver: ACRDriver) -> None:
    secret = driver.get_pull_secret(cluster="prod", namespace="acme-api")
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["namespace"] == "acme-api"
    assert (
        secret["metadata"]["annotations"]["astrolift.io/note"]
        .startswith("AKS clusters use --attach-acr")
    )
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    assert docker == {"auths": {}}


def test_push_returns_full_uri(driver: ACRDriver) -> None:
    uri = driver.push("local:dev", "api", "abc123")
    assert uri == "acmeprod.azurecr.io/api:abc123"


def test_push_rejects_blank_local_image(driver: ACRDriver) -> None:
    with pytest.raises(ValueError, match="local_image"):
        driver.push("", "api", "abc123")


def test_list_tags_translates_response(
    driver: ACRDriver, fake_client: FakeACRClient,
) -> None:
    @dataclass
    class _T:
        name: str
        digest: str = "sha256:abc"
        last_updated: str = "2026-01-01"

    fake_client.tags_data["api"] = [_T(name="v1")]
    tags = driver.list_tags("api")
    assert len(tags) == 1
    assert tags[0].name == "v1"
    assert tags[0].digest == "sha256:abc"


def test_list_tags_missing_repo_raises(driver: ACRDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.list_tags("never")


def test_delete_repo_archive_noop(driver: ACRDriver) -> None:
    driver.delete_repo("api", archive=True)


def test_delete_repo_force_unsupported(driver: ACRDriver) -> None:
    with pytest.raises(NotImplementedError):
        driver.delete_repo("api", archive=False)
