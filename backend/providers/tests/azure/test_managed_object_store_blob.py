"""Tests for BlobStorageDriver (#47 — object_store/blob)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from azure.managed.object_store_blob import (
    BlobStorageConfig,
    BlobStorageDriver,
)


class _NotFound(Exception):
    pass


class _Exists(Exception):
    pass


@dataclass
class FakeContainerProperties:
    name: str = ""


class FakeContainer:
    def __init__(self, name: str, *, parent: FakeBlobServiceClient) -> None:
        self.name = name
        self._parent = parent
        self.created = False
        self.metadata: dict[str, str] = {}

    def create_container(
        self,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if self.created:
            raise _Exists(self.name)
        self.created = True
        self.metadata = dict(metadata or {})
        self._parent.containers[self.name] = self

    def get_container_properties(self) -> FakeContainerProperties:
        if not self.created:
            raise _NotFound(self.name)
        return FakeContainerProperties(name=self.name)

    def delete_container(self) -> None:
        if not self.created:
            raise _NotFound(self.name)
        self.created = False
        self._parent.containers.pop(self.name, None)


class FakeBlobServiceClient:
    def __init__(self) -> None:
        self.containers: dict[str, FakeContainer] = {}

    def get_container_client(self, name: str) -> FakeContainer:
        if name in self.containers:
            return self.containers[name]
        return FakeContainer(name, parent=self)


@pytest.fixture
def fake_client() -> FakeBlobServiceClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    _Exists.__name__ = "ResourceExistsError"
    return FakeBlobServiceClient()


@pytest.fixture
def driver(fake_client: FakeBlobServiceClient) -> BlobStorageDriver:
    return BlobStorageDriver(
        config=BlobStorageConfig(
            storage_account="acmeprod",
            blob_service_client=fake_client,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


def test_provision_creates_container(
    driver: BlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert "object_store/" in result.handle
    assert len(fake_client.containers) == 1


def test_provision_metadata_recorded(
    driver: BlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    driver.provision(_spec())
    container = next(iter(fake_client.containers.values()))
    assert container.metadata["astrolift_io_organization"] == "acme"
    assert container.metadata["astrolift_io_app"] == "api"


def test_provision_idempotent_on_exists(driver: BlobStorageDriver) -> None:
    driver.provision(_spec())
    result = driver.provision(_spec())
    assert result.ok


def test_status_available(driver: BlobStorageDriver) -> None:
    res = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=res.handle))
    assert status.state == "available"


def test_status_deprovisioned(driver: BlobStorageDriver) -> None:
    status = driver.status(ServiceHandle(handle="object_store/missing"))
    assert status.state == "deprovisioned"


def test_deprovision_retains_by_default(
    driver: BlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=False,
    )
    assert len(fake_client.containers) == 1


def test_deprovision_deletes_when_flag(
    driver: BlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
    )
    assert fake_client.containers == {}


def test_binding_emits_required_envs(driver: BlobStorageDriver) -> None:
    binding = driver.binding(
        ServiceHandle(handle="object_store/foo-bucket"),
    )
    keys = set(binding.env_vars.keys())
    assert keys == {
        "AZURE_STORAGE_ACCOUNT",
        "AZURE_BLOB_CONTAINER",
        "AZURE_BLOB_ENDPOINT",
    }
    assert binding.env_vars["AZURE_BLOB_ENDPOINT"].literal == "https://acmeprod.blob.core.windows.net/foo-bucket"


def test_container_name_canonicalization(driver: BlobStorageDriver) -> None:
    name = driver._container_name_for(
        spec=_spec(
            organization_slug="ACME Corp",
            app_slug="My-API",
            environment_name="Prod",
            service_handle_hint="logs",
        ),
    )
    assert name == name.lower()
    assert "--" not in name
    assert len(name) <= 63
