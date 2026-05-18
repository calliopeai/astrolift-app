"""Tests for GCP Bigtable managed-service driver (#371).

GCP has no moto-equivalent simulator for Bigtable, so we drive
the SDK via fakes that record calls and return canned responses.
The test surface mirrors the AWS DynamoDB driver tests one-to-one
so cross-cloud-symmetry regressions show up loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.bigtable import (
    KIND,
    BigtableConfig,
    BigtableDriver,
    _final_backup_id,
    _parse_handle,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeInstance:
    name: str
    state: str = "READY"
    display_name: str = ""
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeCluster:
    name: str
    serve_nodes: int = 1


class FakeInstanceAdminClient:
    def __init__(self) -> None:
        self.instances: dict[str, FakeInstance] = {}
        self.clusters: dict[str, list[FakeCluster]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_instance(self, *, parent, instance_id, instance, clusters):
        self.calls.append(
            (
                "create_instance",
                {
                    "parent": parent,
                    "instance_id": instance_id,
                    "instance": instance,
                    "clusters": clusters,
                },
            ),
        )
        full_name = f"{parent}/instances/{instance_id}"
        self.instances[instance_id] = FakeInstance(
            name=full_name,
            state="READY",
            display_name=instance.get("display_name", ""),
            labels=dict(instance.get("labels", {})),
        )
        self.clusters[instance_id] = [
            FakeCluster(
                name=f"{full_name}/clusters/{cid}",
                serve_nodes=int(c["serve_nodes"]),
            )
            for cid, c in clusters.items()
        ]

    def get_instance(self, *, name):
        self.calls.append(("get_instance", {"name": name}))
        # name is "projects/.../instances/<id>"
        instance_id = name.split("/instances/")[-1]
        if instance_id not in self.instances:
            raise RuntimeError("NotFound")
        return self.instances[instance_id]

    def list_clusters(self, *, parent):
        self.calls.append(("list_clusters", {"parent": parent}))
        instance_id = parent.split("/instances/")[-1]
        return list(self.clusters.get(instance_id, []))

    def update_cluster(self, *, name, serve_nodes):
        self.calls.append(
            (
                "update_cluster",
                {
                    "name": name,
                    "serve_nodes": serve_nodes,
                },
            ),
        )
        for clusters in self.clusters.values():
            for c in clusters:
                if c.name == name:
                    c.serve_nodes = int(serve_nodes)
                    return

    def delete_instance(self, *, name):
        self.calls.append(("delete_instance", {"name": name}))
        instance_id = name.split("/instances/")[-1]
        if instance_id not in self.instances:
            raise RuntimeError("NotFound")
        del self.instances[instance_id]
        self.clusters.pop(instance_id, None)


class FakeTableAdminClient:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, list[str]] = {}
        self.restores: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_backup: bool = False

    def create_table(self, *, parent, table_id, table):
        self.calls.append(
            (
                "create_table",
                {
                    "parent": parent,
                    "table_id": table_id,
                    "table": table,
                },
            ),
        )
        self.tables[f"{parent}/tables/{table_id}"] = table

    def create_backup(self, *, parent, backup_id, backup):
        self.calls.append(
            (
                "create_backup",
                {
                    "parent": parent,
                    "backup_id": backup_id,
                    "backup": backup,
                },
            ),
        )
        if self.fail_next_backup:
            self.fail_next_backup = False
            raise RuntimeError("backup synthetic failure")
        self.backups.setdefault(parent, []).append(backup_id)

    def restore_table(self, *, parent, table_id, backup):
        self.calls.append(
            (
                "restore_table",
                {
                    "parent": parent,
                    "table_id": table_id,
                    "backup": backup,
                },
            ),
        )
        if "does-not-exist" in backup:
            raise RuntimeError(
                f"backup {backup} not found",
            )
        self.restores.append(
            {"parent": parent, "table_id": table_id, "backup": backup},
        )


@pytest.fixture
def iadmin() -> FakeInstanceAdminClient:
    return FakeInstanceAdminClient()


@pytest.fixture
def tadmin() -> FakeTableAdminClient:
    return FakeTableAdminClient()


@pytest.fixture
def driver(
    iadmin: FakeInstanceAdminClient,
    tadmin: FakeTableAdminClient,
) -> BigtableDriver:
    return BigtableDriver(
        config=BigtableConfig(project_id="acme-prod", region="us-west1"),
        instance_admin_client=iadmin,
        table_admin_client=tadmin,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="kv",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_instance_and_table(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
    tadmin: FakeTableAdminClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, instance_id = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert instance_id in iadmin.instances
    # Table created inside the instance.
    table_call = [c for op, c in tadmin.calls if op == "create_table"]
    assert table_call
    assert table_call[0]["table_id"] == "data"


def test_provision_idempotent(driver: BigtableDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_default_storage_is_ssd(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    driver.provision(_spec())
    call = next(c for op, c in iadmin.calls if op == "create_instance")
    cluster = next(iter(call["clusters"].values()))
    assert cluster["default_storage_type"] == "SSD"


def test_provision_size_to_nodes(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    driver.provision(_spec(size="large"))
    call = next(c for op, c in iadmin.calls if op == "create_instance")
    cluster = next(iter(call["clusters"].values()))
    assert cluster["serve_nodes"] == 4


def test_provision_honours_storage_type_override(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    driver.provision(_spec(config={"storage_type": "HDD"}))
    call = next(c for op, c in iadmin.calls if op == "create_instance")
    cluster = next(iter(call["clusters"].values()))
    assert cluster["default_storage_type"] == "HDD"


def test_provision_labels_with_astrolift_namespace(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    driver.provision(_spec())
    call = next(c for op, c in iadmin.calls if op == "create_instance")
    labels = call["instance"]["labels"]
    assert labels["astrolift-managed-by"] == "platform"
    assert labels["astrolift-app"] == "api"
    assert labels["astrolift-environment"] == "prod"


def test_provision_surfaces_create_failure() -> None:
    class Boom:
        def get_instance(self, **_):
            raise RuntimeError("NotFound")

        def create_instance(self, **_):
            raise RuntimeError("quota exceeded")

    d = BigtableDriver(
        config=BigtableConfig(project_id="p", region="r"),
        instance_admin_client=Boom(),
        table_admin_client=FakeTableAdminClient(),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "create_instance" in result.message


# ---- update -----------------------------------------------------


def test_update_resize_clusters(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    instance_id = _parse_handle(provisioned.handle)
    assert iadmin.clusters[instance_id][0].serve_nodes == 2


def test_update_explicit_node_count(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"node_count": 7},
        ),
    )
    assert result.ok
    instance_id = _parse_handle(provisioned.handle)
    assert iadmin.clusters[instance_id][0].serve_nodes == 7


def test_update_noop_when_nothing_to_change(
    driver: BigtableDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_backup_single_cluster(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "backup=taken" in result.message
    # Backup was recorded
    backup_calls = [c for op, c in tadmin.calls if op == "create_backup"]
    assert backup_calls


def test_deprovision_delete_data_skips_backup(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message


def test_deprovision_refuses_multi_cluster_without_force(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    instance_id = _parse_handle(provisioned.handle)
    # Simulate operator-added replica.
    iadmin.clusters[instance_id].append(
        FakeCluster(
            name=f"projects/acme-prod/instances/{instance_id}/clusters/c2",
            serve_nodes=1,
        ),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "clusters (replicas)" in result.message


def test_deprovision_force_destroy_multi_cluster(
    driver: BigtableDriver,
    iadmin: FakeInstanceAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    instance_id = _parse_handle(provisioned.handle)
    iadmin.clusters[instance_id].append(
        FakeCluster(
            name=f"projects/acme-prod/instances/{instance_id}/clusters/c2",
            serve_nodes=1,
        ),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: BigtableDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: BigtableDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="kv_store/never"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_tolerates_backup_failure(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    tadmin.fail_next_backup = True
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    # Best-effort: backup failure -> message reflects skipped, delete
    # still proceeds.
    assert result.ok
    assert "backup=skipped" in result.message


# ---- status / binding ------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: BigtableDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="kv_store/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_ready_to_available(
    driver: BigtableDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_binding_returns_connection_envelope(
    driver: BigtableDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "BIGTABLE_PROJECT_ID",
        "BIGTABLE_INSTANCE_ID",
        "BIGTABLE_TABLE_NAME",
        "BIGTABLE_COLUMN_FAMILY",
    ):
        assert key in env
    assert env["BIGTABLE_PROJECT_ID"].literal == "acme-prod"
    assert env["BIGTABLE_TABLE_NAME"].literal == "data"


def test_binding_iam_grants_cover_table_ops(
    driver: BigtableDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    for required in (
        "bigtable.tables.readRows",
        "bigtable.tables.mutateRows",
    ):
        assert required in actions


def test_binding_for_missing_raises(
    driver: BigtableDriver,
) -> None:
    from gcp.managed.bigtable import _BigtableError

    with pytest.raises(_BigtableError):
        driver.binding(ServiceHandle(handle="kv_store/never"))


# ---- snapshot + restore ----------------------------------------


def test_snapshot_creates_backup(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    instance_id = _parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(instance_id[:30])
    backup_calls = [c for op, c in tadmin.calls if op == "create_backup"]
    # provision doesn't backup; only snapshot() does.
    assert any(c["backup_id"] == snap.snapshot_id for c in backup_calls)


def test_snapshot_surfaces_driver_error(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    from gcp.managed.bigtable import _BigtableError

    provisioned = driver.provision(_spec())
    tadmin.fail_next_backup = True
    with pytest.raises(_BigtableError):
        driver.snapshot(ServiceHandle(handle=provisioned.handle))


def test_restore_creates_table_from_backup(
    driver: BigtableDriver,
    tadmin: FakeTableAdminClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    assert tadmin.restores


def test_restore_surfaces_error_on_missing_backup(
    driver: BigtableDriver,
) -> None:
    bad = SnapshotHandle(
        handle="kv_store/anything",
        snapshot_id="does-not-exist",
        created_at="",
    )
    result = driver.restore(bad, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore_table" in result.message


# ---- naming + helpers ------------------------------------------


def test_instance_id_canonicalization(
    driver: BigtableDriver,
) -> None:
    name = driver._instance_id_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="users",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c == "-"
    assert len(name) <= 33
    assert name[0].isalpha()


def test_final_backup_id_is_bounded() -> None:
    name = _final_backup_id(instance_id="x" * 100)
    assert len(name) <= 50


def test_handle_round_trip() -> None:
    from gcp.managed.bigtable import _handle_for

    handle = _handle_for("my-inst")
    assert handle == f"{KIND}/my-inst"
    assert _parse_handle(handle) == "my-inst"


def test_handle_rejects_malformed() -> None:
    from gcp.managed.bigtable import _BigtableError

    with pytest.raises(_BigtableError):
        _parse_handle("no-slash")
    with pytest.raises(_BigtableError):
        _parse_handle("kv_store/")


# ---- schemas ---------------------------------------------------


def test_config_schema_shape(driver: BigtableDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    for key in ("node_count", "storage_type", "zone"):
        assert key in schema["properties"]


def test_binding_schema_lists_all_env_vars(
    driver: BigtableDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "BIGTABLE_PROJECT_ID",
        "BIGTABLE_INSTANCE_ID",
        "BIGTABLE_TABLE_NAME",
        "BIGTABLE_COLUMN_FAMILY",
    ):
        assert key in schema.env_vars
