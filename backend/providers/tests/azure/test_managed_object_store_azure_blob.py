"""Tests for AzureBlobStorageDriver (#364 -- object_store/azure_blob).

Mirrors the AzureMySQLFlexibleDriver / GCS test surface: provision
(idempotent, tagged via container metadata), four-corner deprovision
matrix (delete_data x force_destroy, plus the immutability-policy lock
axis), status, binding shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.managed.object_store_blob import (
    KIND,
    AzureBlobConfig,
    AzureBlobStorageDriver,
    AzureBlobStorageError,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


class _Exists(Exception):
    pass


_Exists.__name__ = "ResourceExistsError"


@dataclass
class FakeImmutabilityPolicy:
    immutability_period_since_creation_in_days: int = 0
    policy_mode: str = "Unlocked"


@dataclass
class FakeContainerProperties:
    name: str = ""


@dataclass
class FakeContainer:
    name: str
    parent: FakeBlobServiceClient
    created: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    immutability_policy: FakeImmutabilityPolicy | None = None

    def create_container(
        self,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if self.created:
            raise _Exists(self.name)
        self.created = True
        self.metadata = dict(metadata or {})
        self.parent.containers[self.name] = self

    def get_container_properties(self) -> FakeContainerProperties:
        if not self.created:
            raise _NotFound(self.name)
        return FakeContainerProperties(name=self.name)

    def delete_container(self) -> None:
        if not self.created:
            raise _NotFound(self.name)
        self.created = False
        self.parent.containers.pop(self.name, None)

    def get_immutability_policy(self) -> FakeImmutabilityPolicy | None:
        if self.immutability_policy is None:
            return FakeImmutabilityPolicy()
        return self.immutability_policy

    def delete_immutability_policy(self) -> None:
        if self.immutability_policy is None:
            return
        if self.immutability_policy.policy_mode == "Locked":
            raise RuntimeError(
                "cannot delete locked immutability policy",
            )
        self.immutability_policy = None


@dataclass
class FakeBlobServiceClient:
    containers: dict[str, FakeContainer] = field(default_factory=dict)

    def get_container_client(self, name: str) -> FakeContainer:
        if name in self.containers:
            return self.containers[name]
        return FakeContainer(name=name, parent=self)


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def fake_client() -> FakeBlobServiceClient:
    return FakeBlobServiceClient()


@pytest.fixture
def driver(fake_client: FakeBlobServiceClient) -> AzureBlobStorageDriver:
    return AzureBlobStorageDriver(
        config=AzureBlobConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            storage_account="acmeprod",
            blob_service_client=fake_client,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="azure-prod",
        service_handle_hint="logs",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_container(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(fake_client.containers) == 1


def test_provision_records_metadata_with_astrolift_namespace(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    driver.provision(_spec())
    container = next(iter(fake_client.containers.values()))
    assert container.metadata["astrolift_io_managed_by"] == "platform"
    assert container.metadata["astrolift_io_app"] == "api"
    assert container.metadata["astrolift_io_environment"] == "prod"


def test_provision_idempotent_on_exists(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.ok and b.ok
    assert a.handle == b.handle
    assert len(fake_client.containers) == 1


# ---- update -----------------------------------------------------


def test_update_refuses_instead_of_reporting_a_no_op_success(
    driver: AzureBlobStorageDriver,
) -> None:
    """``access_tier`` / ``public_access`` are set at provision time only.

    Returning ``ok=True`` here made the update workflow record the requested
    tier as applied while the container kept the old one (#1376).
    """
    res = driver.provision(_spec())
    update = driver.update(UpdateSpec(handle=res.handle, config={"access_tier": "Cool"}))
    assert update.ok is False
    assert update.retryable is False
    assert update.errors == ["update_not_supported_in_place"]
    assert "reprovisionManagedService" in update.message
    assert driver.editable_fields() == []


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_retains_container(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=res.handle))
    assert result.ok
    assert "retained" in result.message
    assert len(fake_client.containers) == 1


def test_deprovision_retain_with_force_clears_unlocked_policy(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    container_name = res.handle.split("/", 1)[1]
    container = fake_client.containers[container_name]
    container.immutability_policy = FakeImmutabilityPolicy(
        immutability_period_since_creation_in_days=7,
        policy_mode="Unlocked",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    # The unlocked policy should have been cleared so a follow-up
    # delete_data=True doesn't refuse.
    assert container.immutability_policy is None
    # Container itself stays.
    assert container.name in fake_client.containers


def test_deprovision_delete_data_only_drops_container(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
    )
    assert result.ok
    assert "deleted with data" in result.message
    assert fake_client.containers == {}


def test_deprovision_delete_data_refuses_active_policy_without_force(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    container_name = res.handle.split("/", 1)[1]
    container = fake_client.containers[container_name]
    container.immutability_policy = FakeImmutabilityPolicy(
        immutability_period_since_creation_in_days=7,
        policy_mode="Unlocked",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
        force_destroy=False,
    )
    assert not result.ok
    assert "immutability/retention policy" in result.message
    assert "retention_policy_active" in result.errors
    # Container stays.
    assert container_name in fake_client.containers


def test_deprovision_delete_data_locked_policy_blocks_even_with_force(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    container_name = res.handle.split("/", 1)[1]
    container = fake_client.containers[container_name]
    container.immutability_policy = FakeImmutabilityPolicy(
        immutability_period_since_creation_in_days=7,
        policy_mode="Locked",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok
    assert "LOCKED" in result.message
    assert "retention_policy_locked" in result.errors


def test_deprovision_atomic_both_flags_drops_unlocked_policy_and_container(
    driver: AzureBlobStorageDriver,
    fake_client: FakeBlobServiceClient,
) -> None:
    res = driver.provision(_spec())
    container_name = res.handle.split("/", 1)[1]
    container = fake_client.containers[container_name]
    container.immutability_policy = FakeImmutabilityPolicy(
        immutability_period_since_creation_in_days=7,
        policy_mode="Unlocked",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy" in result.message
    assert fake_client.containers == {}


def test_deprovision_idempotent_when_already_gone(
    driver: AzureBlobStorageDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="object_store/never-existed"),
        delete_data=True,
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureBlobStorageDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="object_store/missing"))
    assert state.state == "deprovisioned"


def test_status_for_existing_returns_available(
    driver: AzureBlobStorageDriver,
) -> None:
    res = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=res.handle))
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(driver: AzureBlobStorageDriver) -> None:
    res = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=res.handle))
    env = binding.env_vars
    assert set(env.keys()) == {
        "AZURE_STORAGE_ACCOUNT",
        "AZURE_BLOB_CONTAINER",
        "AZURE_BLOB_ENDPOINT",
    }
    assert env["AZURE_STORAGE_ACCOUNT"].literal == "acmeprod"
    assert env["AZURE_BLOB_ENDPOINT"].literal.startswith(
        "https://acmeprod.blob.core.windows.net/",
    )


def test_binding_iam_grants_scoped_to_container_resource(
    driver: AzureBlobStorageDriver,
) -> None:
    res = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=res.handle))
    container_name = res.handle.split("/", 1)[1]
    grants = binding.iam_grants
    assert grants
    assert any(container_name in g.resource for g in grants)
    actions = {a for g in grants for a in g.actions}
    assert "Storage Blob Data Contributor" in actions


# ---- snapshot + restore -----------------------------------------


def test_snapshot_is_explicitly_unsupported(driver: AzureBlobStorageDriver) -> None:
    with pytest.raises(UnsupportedOperationError, match="per-blob versions"):
        driver.snapshot(ServiceHandle(handle="object_store/container"))


def test_restore_is_explicitly_unsupported(
    driver: AzureBlobStorageDriver,
) -> None:
    from _sdk.managed_service import SnapshotHandle

    snapshot = SnapshotHandle(
        "object_store/source",
        "not-an-atomic-snapshot",
        "2026-08-14T00:00:00Z",
    )
    with pytest.raises(UnsupportedOperationError, match="copy workflow"):
        driver.restore(snapshot, _spec(service_handle_hint="restored"))


# ---- naming + helpers -------------------------------------------


def test_container_name_canonicalization(
    driver: AzureBlobStorageDriver,
) -> None:
    name = driver._container_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="logs",
        ),
    )
    assert name == name.lower()
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    assert len(name) <= 63


def test_handle_round_trip(driver: AzureBlobStorageDriver) -> None:
    handle = driver._handle_for(container_name="my-container")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._container_name_from_handle(handle)  # type: ignore[attr-defined]
        == "my-container"
    )


def test_handle_rejects_malformed(driver: AzureBlobStorageDriver) -> None:
    with pytest.raises(AzureBlobStorageError):
        driver._container_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureBlobStorageError):
        driver._container_name_from_handle("object_store/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureBlobStorageDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    assert "access_tier" in schema["properties"]


def test_binding_schema_lists_all_env_vars(
    driver: AzureBlobStorageDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "AZURE_STORAGE_ACCOUNT",
        "AZURE_BLOB_CONTAINER",
        "AZURE_BLOB_ENDPOINT",
    ):
        assert key in schema.env_vars
