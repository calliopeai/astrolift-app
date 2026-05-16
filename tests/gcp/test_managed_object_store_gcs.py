"""Tests for GCSDriver (#41 — object_store/gcs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from gcp.managed.object_store_gcs import GCSConfig, GCSDriver


class _NotFound(Exception):
    pass


class _Conflict(Exception):
    pass


@dataclass
class FakeIAMConfig:
    uniform_bucket_level_access_enabled: bool = False


@dataclass
class FakeBucket:
    name: str
    location: str = ""
    storage_class: str = ""
    iam_configuration: FakeIAMConfig = field(default_factory=FakeIAMConfig)
    versioning_enabled: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    blobs: list[Any] = field(default_factory=list)
    deleted: bool = False
    patches: int = 0
    retention_policy: Any = None

    def patch(self) -> None:
        self.patches += 1

    def list_blobs(self, *, versions: bool = False) -> list[Any]:
        return list(self.blobs)

    def delete(self) -> None:
        self.deleted = True


@dataclass
class FakeBlob:
    name: str
    deleted: bool = False
    event_based_hold: bool = False
    temporary_hold: bool = False
    patches: int = 0

    def delete(self) -> None:
        if self.event_based_hold or self.temporary_hold:
            raise RuntimeError(
                f"object {self.name} is on hold; cannot delete",
            )
        self.deleted = True

    def patch(self) -> None:
        self.patches += 1


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        if name in self.buckets:
            return self.buckets[name]
        b = FakeBucket(name=name)
        # don't yet register — driver calls create_bucket separately
        return b

    def create_bucket(self, bucket: FakeBucket, *, location: str) -> FakeBucket:
        if bucket.name in self.buckets:
            raise _Conflict(bucket.name)
        bucket.location = location
        self.buckets[bucket.name] = bucket
        return bucket

    def get_bucket(self, name: str) -> FakeBucket:
        if name not in self.buckets:
            raise _NotFound(name)
        return self.buckets[name]


@pytest.fixture
def fake_storage() -> FakeStorageClient:
    _NotFound.__name__ = "NotFound"
    _Conflict.__name__ = "Conflict"
    return FakeStorageClient()


@pytest.fixture
def driver(fake_storage: FakeStorageClient) -> GCSDriver:
    return GCSDriver(
        config=GCSConfig(project_id="acme", client=fake_storage),
    )


def _spec(
    *,
    organization_slug: str = "acme",
    app_slug: str = "api",
    environment_name: str = "prod",
    service_handle_hint: str = "",
) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug=organization_slug,
        app_id="app-1",
        app_slug=app_slug,
        environment_id="env-1",
        environment_name=environment_name,
        tenant_cluster_id="cluster-1",
        service_handle_hint=service_handle_hint,
        size="small",
    )


def test_provision_creates_bucket_with_versioning(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert "object_store/" in result.handle
    bucket = list(fake_storage.buckets.values())[0]
    assert bucket.versioning_enabled is True
    assert bucket.iam_configuration.uniform_bucket_level_access_enabled


def test_provision_applies_labels(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    assert bucket.labels["astrolift-io-organization"] == "acme"
    assert bucket.labels["astrolift-io-app"] == "api"
    assert bucket.labels["astrolift-io-environment"] == "prod"


def test_provision_idempotent_on_conflict(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    driver.provision(_spec())
    # second time, the same bucket name will hit Conflict and be
    # treated as success
    result = driver.provision(_spec())
    assert result.ok


def test_status_available(driver: GCSDriver) -> None:
    result = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=result.handle))
    assert status.state == "available"


def test_status_deprovisioned(driver: GCSDriver) -> None:
    status = driver.status(ServiceHandle(handle="object_store/missing"))
    assert status.state == "deprovisioned"


def test_deprovision_retains_by_default(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle), delete_data=False,
    )
    assert deprov.ok
    # bucket still there
    assert len(fake_storage.buckets) == 1


def test_deprovision_deletes_when_flag_set(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    bucket.blobs.extend([FakeBlob(name="x"), FakeBlob(name="y")])

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle), delete_data=True,
    )
    assert deprov.ok
    assert all(b.deleted for b in bucket.blobs)
    assert bucket.deleted is True


def test_binding_emits_required_envs(driver: GCSDriver) -> None:
    binding = driver.binding(
        ServiceHandle(handle="object_store/foo-bucket"),
    )
    keys = set(binding.env_vars.keys())
    assert keys == {"GCS_BUCKET_NAME", "GCS_BUCKET_URI", "GCP_PROJECT_ID"}
    # gs:// URI prefix
    assert binding.env_vars["GCS_BUCKET_URI"].literal == (
        "gs://foo-bucket"
    )


def test_bucket_name_canonicalization(driver: GCSDriver) -> None:
    name = driver._bucket_name_for(
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


# ---- four-corner deprovision matrix --------------------------------


def test_deprovision_retain_with_force_destroy_is_safe_path(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    """delete_data=False + force_destroy=True is a no-op at the
    bucket level — the bucket is retained either way."""
    result = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=False, force_destroy=True,
    )
    assert deprov.ok
    assert len(fake_storage.buckets) == 1
    bucket = list(fake_storage.buckets.values())[0]
    assert bucket.deleted is False


def test_deprovision_atomic_both_flags_deletes_with_message(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=True,
    )
    assert deprov.ok
    assert "force_destroy" in deprov.message


def test_deprovision_refuses_when_retention_policy_active(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    """Active retention policy + force_destroy=False errors out
    cleanly."""
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    bucket.retention_policy = {"retentionPeriod": 86400}

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=False,
    )
    assert not deprov.ok
    assert "retention" in deprov.message.lower()
    assert bucket.deleted is False


def test_deprovision_force_destroy_clears_unlocked_retention(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    bucket.retention_policy = {
        "retentionPeriod": 86400, "isLocked": False,
    }

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=True,
    )
    assert deprov.ok
    assert bucket.retention_policy is None
    assert bucket.deleted is True


def test_deprovision_refuses_locked_retention_even_with_force(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    """A locked retention policy CANNOT be cleared via the API —
    GCS contract. The driver surfaces this distinctly."""
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    bucket.retention_policy = {
        "retentionPeriod": 86400, "isLocked": True,
    }

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=True,
    )
    assert not deprov.ok
    assert "locked" in deprov.message.lower()
    assert bucket.deleted is False


def test_deprovision_force_destroy_releases_object_holds(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    held = FakeBlob(name="held", event_based_hold=True)
    soft_held = FakeBlob(name="soft", temporary_hold=True)
    bucket.blobs.extend([held, soft_held])

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=True,
    )
    assert deprov.ok
    assert held.deleted and soft_held.deleted
    assert held.event_based_hold is False
    assert soft_held.temporary_hold is False
    assert bucket.deleted is True


def test_deprovision_held_objects_block_default_delete(
    driver: GCSDriver, fake_storage: FakeStorageClient,
) -> None:
    """delete_data=True without force_destroy fails when an object
    has a hold — the FakeBlob's delete raises and the driver
    surfaces it."""
    result = driver.provision(_spec())
    bucket = list(fake_storage.buckets.values())[0]
    bucket.blobs.append(FakeBlob(name="x", event_based_hold=True))

    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True, force_destroy=False,
    )
    assert not deprov.ok
    assert bucket.deleted is False


def test_deprovision_idempotent_already_gone(
    driver: GCSDriver,
) -> None:
    deprov = driver.deprovision(
        DeprovisionSpec(handle="object_store/never-existed"),
        delete_data=True,
    )
    assert deprov.ok
    assert "already gone" in deprov.message
