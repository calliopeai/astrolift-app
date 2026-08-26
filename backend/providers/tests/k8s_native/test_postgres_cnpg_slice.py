"""CNPG per-preview slices (#1578 feature 3, first driver).

The test that matters most is `test_the_parent_cluster_is_applied_whole`.
The shorter implementation patches the parent `Cluster` with only
`spec.managed.roles`, and under this repo's `server_side_apply` -- one
field manager, `force_conflicts=True` -- that makes the platform relinquish
every field it owns and is not carrying, pruning `spec.instances` and
`spec.storage` off a live production database. I wrote that version first.

Second most important: the slice must not hand the preview the parent's
credentials. Overriding only `POSTGRES_DB` passes every guard in
`SliceResult` and still lets a workload with a hardcoded connection string
reach production data, which is the failure #1578 was filed about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import ServiceHandle, SliceSpec
from k8s_native.managed._handle import pack as pack_handle
from k8s_native.managed.postgres_cnpg import CNPGConfig, CNPGPostgresDriver

PARENT_SPEC = {
    "instances": 3,
    "storage": {"size": "100Gi", "storageClass": "gp3"},
    "resources": {"requests": {"cpu": "1", "memory": "4Gi"}},
    "bootstrap": {"initdb": {"database": "app", "owner": "app"}},
}


@dataclass
class FakeApplyResult:
    errors: list = field(default_factory=list)

    def summary(self):
        return [str(e) for e in self.errors]


@dataclass
class FakeClusterDriver:
    parent: dict | None = None
    applied: list = field(default_factory=list)
    deleted: list = field(default_factory=list)

    def get_manifest(self, cluster, namespace, kind, name):
        return self.parent

    def apply_manifests(self, cluster, namespace, manifests):
        self.applied.extend(manifests)
        return FakeApplyResult()

    def delete_manifests(self, cluster, namespace, manifests):
        self.deleted.extend(manifests)
        return FakeApplyResult()


def _parent_object(roles: list[dict[str, Any]] | None = None) -> dict:
    spec = dict(PARENT_SPEC)
    if roles is not None:
        spec["managed"] = {"roles": roles}
    return {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {"name": "pg-main", "namespace": "acme-app"},
        "spec": spec,
    }


def _handle() -> ServiceHandle:
    return ServiceHandle(
        handle=pack_handle(
            kind="postgres",
            cluster_id="cl-1",
            namespace="acme-app",
            name="pg-main",
        )
    )


def _driver(parent: dict | None = None):
    fake = FakeClusterDriver(parent=parent)
    return CNPGPostgresDriver(config=CNPGConfig(cluster_driver=fake)), fake


def _spec(slice_id="preview-pr-42") -> SliceSpec:
    return SliceSpec(slice_id=slice_id, parent=_handle())


def _of_kind(manifests, kind):
    return [m for m in manifests if m.get("kind") == kind]


# ---------------------------------------------------------------------------
# The parent-spec safety property
# ---------------------------------------------------------------------------


def test_the_parent_cluster_is_applied_whole():
    """The one that has to hold.

    `server_side_apply` uses a single field manager with
    `force_conflicts=True`, so a partial Cluster apply relinquishes every
    field that manager owns and is not carrying. `spec.instances` and
    `spec.storage` would be pruned off a live production database by the
    first `provision_slice` call.
    """
    driver, fake = _driver(parent=_parent_object())

    driver.provision_slice(_spec())

    (cluster_manifest,) = _of_kind(fake.applied, "Cluster")
    for key, value in PARENT_SPEC.items():
        assert cluster_manifest["spec"][key] == value, f"{key} was dropped from the parent spec"


def test_the_role_is_added_without_disturbing_an_existing_one():
    """Two previews on one cluster. Replacing the list would revoke the
    other preview's login."""
    existing = {"name": "slice_preview_pr_1_owner", "ensure": "present", "login": True}
    driver, fake = _driver(parent=_parent_object(roles=[existing]))

    driver.provision_slice(_spec("preview-pr-2"))

    (cluster_manifest,) = _of_kind(fake.applied, "Cluster")
    names = {r["name"] for r in cluster_manifest["spec"]["managed"]["roles"]}
    assert existing["name"] in names
    assert len(names) == 2


def test_reprovisioning_the_same_slice_does_not_duplicate_its_role():
    """Idempotent: the caller has to be able to ask for the same slice
    twice."""
    driver, fake = _driver(parent=_parent_object())
    driver.provision_slice(_spec())
    first = _of_kind(fake.applied, "Cluster")[0]["spec"]["managed"]["roles"]

    fake.parent = {**_parent_object(), "spec": {**PARENT_SPEC, "managed": {"roles": first}}}
    fake.applied.clear()
    driver.provision_slice(_spec())

    roles = _of_kind(fake.applied, "Cluster")[0]["spec"]["managed"]["roles"]
    assert len(roles) == 1


def test_an_unreadable_parent_skips_the_role_rather_than_patching_blind():
    """None from `get_manifest` must not become a partial apply. The slice
    lands with an owner that does not exist -- visible and repairable --
    where a pruned parent spec is neither."""
    driver, fake = _driver(parent=None)

    driver.provision_slice(_spec())

    assert _of_kind(fake.applied, "Cluster") == []
    assert _of_kind(fake.applied, "Database"), "the slice itself still lands"


def test_the_role_is_applied_before_the_database():
    """CNPG's Database reconciler needs its owner to exist, and the role
    reconciler reads the password Secret."""
    driver, fake = _driver(parent=_parent_object())

    driver.provision_slice(_spec())

    kinds = [m["kind"] for m in fake.applied]
    assert kinds.index("Secret") < kinds.index("Cluster") < kinds.index("Database")


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_the_slice_does_not_reuse_the_parents_credentials():
    """The shorter implementation overrides only POSTGRES_DB and passes
    every guard in SliceResult, while leaving the preview holding a
    credential that opens the parent database."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_USER", "DATABASE_PASSWORD"):
        assert key in result.env_overrides, f"{key} must not fall through to the parent"


def test_both_the_canonical_and_alias_database_keys_are_overridden():
    """A half-applied override is worse than none: it looks isolated while
    a workload reading DATABASE_NAME still points at the parent."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert result.env_overrides["POSTGRES_DB"].literal == result.env_overrides["DATABASE_NAME"].literal


def test_database_url_is_replaced_wholesale():
    """It is one composed string in the parent's Secret and cannot be
    partially overridden, so leaving it would hand the workload the parent's
    full DSN."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert result.env_overrides["DATABASE_URL"].secret_ref
    assert "slice" in result.env_overrides["DATABASE_URL"].secret_ref


def test_host_and_port_are_left_to_the_parent():
    """It is the same instance. Overriding them would be wrong and would
    also hide a misconfiguration."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert "POSTGRES_HOST" not in result.env_overrides
    assert "POSTGRES_PORT" not in result.env_overrides


def test_no_password_literal_is_ever_rendered():
    """The one thing this driver must never emit."""
    driver, fake = _driver(parent=_parent_object())

    driver.provision_slice(_spec())

    (secret,) = _of_kind(fake.applied, "Secret")
    assert set(secret["stringData"]) == {"username"}


# ---------------------------------------------------------------------------
# Naming and teardown
# ---------------------------------------------------------------------------


def test_names_are_derived_from_the_slice_id_not_generated():
    """The caller has to be able to tear down the slice it means."""
    driver, _ = _driver(parent=_parent_object())

    first = driver.provision_slice(_spec()).slice_handle
    second = driver.provision_slice(_spec()).slice_handle

    assert first == second


def test_the_database_name_is_a_legal_postgres_identifier():
    """`preview-pr-42` is not: postgres identifiers may not contain `-`."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())
    database = result.env_overrides["POSTGRES_DB"].literal

    assert "-" not in database
    assert database.replace("_", "").isalnum()


def test_deprovision_removes_the_database_and_secret():
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle

    assert driver.deprovision_slice(_spec(), handle) is True

    kinds = {m["kind"] for m in fake.deleted}
    assert kinds == {"Database", "Secret"}


def test_deprovision_leaves_the_parents_role_list_alone():
    """Removing the role is a read-modify-write on a shared spec from a
    teardown path, which can drop a role another slice added concurrently.
    An orphaned login with no database is a much smaller problem than a live
    preview losing its credentials."""
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle
    fake.applied.clear()

    driver.deprovision_slice(_spec(), handle)

    assert _of_kind(fake.deleted, "Cluster") == []
    assert _of_kind(fake.applied, "Cluster") == []


def test_a_failed_delete_returns_false_rather_than_raising():
    """A teardown workflow has to be able to record the leak and continue:
    a slice left behind is a database nobody sees until someone reads the
    instance's database list."""
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle

    def failing(cluster, namespace, manifests):
        return FakeApplyResult(errors=["boom"])

    fake.delete_manifests = failing

    assert driver.deprovision_slice(_spec(), handle) is False


def test_a_legacy_parent_handle_is_refused():
    """It carries no cluster or namespace locator, so the slice would land
    somewhere this driver cannot find it again."""
    driver, _ = _driver(parent=_parent_object())

    with pytest.raises(ValueError, match="legacy"):
        driver.provision_slice(SliceSpec(slice_id="x", parent=ServiceHandle(handle="postgres/pg-main")))


def test_the_driver_declares_slicing_support():
    """`supports_slicing` requires both verbs; a driver that can create a
    slice and not remove one leaks a database on every teardown."""
    driver, _ = _driver()

    assert driver.supports_slicing() is True
    assert callable(driver.provision_slice)
    assert callable(driver.deprovision_slice)
