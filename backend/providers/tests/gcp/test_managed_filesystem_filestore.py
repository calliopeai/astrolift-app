from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.filesystem_filestore import (
    FilestoreConfig,
    FilestoreConflict,
    FilestoreDriver,
    FilestoreError,
    FilestoreNotFound,
    FilestoreRestClient,
    _capacity_error,
    _parse_handle,
    _resource_id,
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="steady-md",
    app_id="app-id",
    app_slug="triage",
    environment_id="env-id",
    environment_name="production",
    tenant_cluster_id="cluster-id",
    service_handle_hint="shared-files",
    size="small",
    binding_id="binding-id",
    managed_service_id="service-id",
)
MSID = SPEC.managed_service_id


class FakeFilestore:
    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.create_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.patch_calls: list[tuple[str, dict[str, Any], list[str]]] = []
        self.delete_calls: list[tuple[str, bool]] = []
        self.backup_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.snapshot_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.revert_calls: list[tuple[str, str]] = []
        self.promote_calls: list[tuple[str, str]] = []
        self.pause_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.conflict_on_create = False

    def get_instance(self, name: str) -> dict[str, Any]:
        try:
            return self.instances[name]
        except KeyError as exc:
            raise FilestoreNotFound(name) from exc

    def create_instance(self, parent: str, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.create_calls.append((parent, instance_id, body))
        name = f"{parent}/instances/{instance_id}"
        if self.conflict_on_create or name in self.instances:
            raise FilestoreConflict(name)
        self.instances[name] = {
            **body,
            "name": name,
            "state": "READY",
            "createTime": "2026-08-14T12:00:00Z",
            "etag": "etag-1",
            "networks": [
                {
                    **body["networks"][0],
                    "ipAddresses": ["10.20.30.40"],
                },
            ],
        }
        return {"name": "operations/create", "done": True, "response": self.instances[name]}

    def patch_instance(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.patch_calls.append((name, body, update_mask))
        current = self.get_instance(name)
        for key, value in body.items():
            if key not in {"name", "etag"}:
                current[key] = value
        current["etag"] = f"etag-{len(self.patch_calls) + 1}"
        return {"name": "operations/patch", "done": True, "response": current}

    def delete_instance(self, name: str, *, force: bool = False) -> dict[str, Any]:
        self.delete_calls.append((name, force))
        if name not in self.instances:
            raise FilestoreNotFound(name)
        del self.instances[name]
        return {"name": "operations/delete", "done": True}

    def create_backup(self, parent: str, backup_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.backup_calls.append((parent, backup_id, body))
        name = f"{parent}/backups/{backup_id}"
        if name in self.backups:
            raise FilestoreConflict(name)
        source = self.instances[body["sourceInstance"]]
        self.backups[name] = {
            **body,
            "name": name,
            "state": "READY",
            "capacityGb": source["fileShares"][0]["capacityGb"],
            "createTime": "2026-08-14T12:01:00Z",
        }
        return {"name": "operations/backup", "done": True, "response": self.backups[name]}

    def get_backup(self, name: str) -> dict[str, Any]:
        try:
            return self.backups[name]
        except KeyError as exc:
            raise FilestoreNotFound(name) from exc

    def create_snapshot(
        self,
        instance_name: str,
        snapshot_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.snapshot_calls.append((instance_name, snapshot_id, body))
        name = f"{instance_name}/snapshots/{snapshot_id}"
        self.snapshots[name] = {
            **body,
            "name": name,
            "state": "READY",
            "createTime": "2026-08-14T12:02:00Z",
        }
        return {"name": "operations/snapshot", "done": True, "response": self.snapshots[name]}

    def get_snapshot(self, name: str) -> dict[str, Any]:
        return self.snapshots[name]

    def revert_instance(self, name: str, snapshot_id: str) -> dict[str, Any]:
        self.revert_calls.append((name, snapshot_id))
        return {"name": "operations/revert", "done": True}

    def promote_replica(self, name: str, peer_instance: str = "") -> dict[str, Any]:
        self.promote_calls.append((name, peer_instance))
        return {"name": "operations/promote", "done": True}

    def pause_replica(self, name: str) -> dict[str, Any]:
        self.pause_calls.append(name)
        return {"name": "operations/pause", "done": True}

    def resume_replica(self, name: str) -> dict[str, Any]:
        self.resume_calls.append(name)
        return {"name": "operations/resume", "done": True}

    def get_operation(self, name: str) -> dict[str, Any]:
        return {"name": name, "done": True}


@pytest.fixture
def client() -> FakeFilestore:
    return FakeFilestore()


@pytest.fixture
def config() -> FilestoreConfig:
    return FilestoreConfig(
        project_id="project-1",
        location="us-central1",
        network="projects/host/global/networks/shared",
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
    )


@pytest.fixture
def driver(config: FilestoreConfig, client: FakeFilestore) -> FilestoreDriver:
    return FilestoreDriver(
        config=config,
        client=client,
        sleep=lambda _: None,
        now=lambda: datetime(2026, 8, 14, 12, 3, tzinfo=UTC),
    )


def _provision(driver: FilestoreDriver, **config: Any):
    return driver.provision(replace(SPEC, config=config))


def test_provision_builds_current_v1_psc_instance_and_binding(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    result = _provision(
        driver,
        tier="REGIONAL",
        protocol="NFS_V4_1",
        capacity_gb=1024,
        address_modes=["MODE_IPV4", "MODE_IPV6"],
        psc_endpoint_project="service-project",
        kms_key_name="projects/p/locations/us/keyRings/r/cryptoKeys/k",
        nfs_export_options=[
            {
                "ip_ranges": ["10.0.0.0/8"],
                "network": "projects/host/global/networks/shared",
                "access_mode": "READ_WRITE",
            },
        ],
        directory_services={
            "domain": "example.internal",
            "servers": ["ldap.example.internal"],
            "users_ou": "users",
        },
        security_flavor="krb5p",
        performance_fixed_iops=3000,
        resource_tags={"123/environment": "production"},
    )

    assert result.ok and result.ready
    assert result.handle.startswith("filesystem/us-central1/")
    parent, _, body = client.create_calls[0]
    assert parent == "projects/project-1/locations/us-central1"
    assert body["tier"] == "REGIONAL"
    assert body["protocol"] == "NFS_V4_1"
    assert body["networks"] == [
        {
            "network": "projects/host/global/networks/shared",
            "modes": ["MODE_IPV4", "MODE_IPV6"],
            "connectMode": "PRIVATE_SERVICE_CONNECT",
            "pscConfig": {"endpointProject": "service-project"},
        },
    ]
    assert body["fileShares"][0]["nfsExportOptions"][0]["anonUid"] == "65534"
    assert body["fileShares"][0]["nfsExportOptions"][0]["squashMode"] == "ROOT_SQUASH"
    assert body["performanceConfig"] == {"fixedIops": {"maxIops": "3000"}}
    assert body["directoryServices"]["ldap"]["usersOu"] == "users"
    assert body["labels"]["astrolift-io-managed-service-id"] == "service-id"
    assert body["deletionProtectionEnabled"] is True

    binding = driver.binding(
        ServiceHandle(result.handle, managed_service_id=MSID),
        {"security_flavor": "krb5p", "read_only": True},
    )
    assert binding.env_vars["FILESYSTEM_ENDPOINT"].literal == "10.20.30.40"
    assert binding.env_vars["FILESYSTEM_EXPORT"].literal == "/data"
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "true"
    assert binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal == "vers=4.1,sec=krb5p,ro"
    assert binding.iam_grants == []
    assert len(binding.pod_volume_mounts) == 1
    volume = binding.pod_volume_mounts[0]
    assert volume.csi_driver == "filestore.csi.storage.gke.io"
    _, instance_id = _parse_handle(result.handle)
    assert volume.volume_handle == f"modeInstance/us-central1/{instance_id}/data"
    assert volume.volume_attributes == {
        "ip": "10.20.30.40",
        "volume": "data",
        "protocol": "NFS_V4_1",
    }
    assert volume.mount_path == "/mnt/shared"
    assert volume.mount_options == ["vers=4.1", "sec=krb5p", "ro"]
    assert volume.read_only is True
    assert volume.capacity == "1024Gi"


def test_all_current_and_legacy_tiers_are_exposed(driver: FilestoreDriver) -> None:
    schema = driver.config_schema()
    assert set(schema["properties"]["tier"]["enum"]) == {
        "STANDARD",
        "PREMIUM",
        "BASIC_HDD",
        "BASIC_SSD",
        "HIGH_SCALE_SSD",
        "ENTERPRISE",
        "ZONAL",
        "REGIONAL",
    }
    export_schema = schema["properties"]["nfs_export_options"]["items"]
    assert export_schema["properties"]["squash_mode"]["default"] == "ROOT_SQUASH"


@pytest.mark.parametrize(
    ("tier", "capacity", "regional_small", "ok"),
    [
        ("BASIC_HDD", 1024, False, True),
        ("BASIC_HDD", 100, False, False),
        ("BASIC_SSD", 2560, False, True),
        ("ENTERPRISE", 1280, False, True),
        ("ENTERPRISE", 1200, False, False),
        ("ZONAL", 9984, False, True),
        ("ZONAL", 10000, False, False),
        ("ZONAL", 10240, False, True),
        ("REGIONAL", 100, True, True),
        ("REGIONAL", 100, False, False),
        ("REGIONAL", 102400, False, True),
    ],
)
def test_capacity_contract(tier: str, capacity: int, regional_small: bool, ok: bool) -> None:
    assert (not _capacity_error(tier, capacity, regional_small=regional_small)) is ok


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"tier": "BASIC_HDD", "protocol": "NFS_V4_1"}, "NFS_V4_1 requires"),
        ({"tier": "BASIC_HDD"}, "Private Service Connect requires"),
        ({"connect_mode": "DIRECT_PEERING", "address_modes": ["MODE_IPV6"]}, "MODE_IPV6 requires"),
        ({"protocol": "NFS_V3", "directory_services": {"domain": "d", "servers": ["s"]}}, "requires NFS_V4_1"),
        ({"security_flavor": "krb5p"}, "requires directory_services"),
        ({"performance_fixed_iops": 1000, "performance_iops_per_tb": 1000}, "choose either"),
        ({"replication_role": "STANDBY"}, "requires replica_peer_instance"),
        (
            {"nfs_export_options": [{"ip_ranges": ["10.0.0.0/8"]}]},
            "PSC NFS export options require network",
        ),
    ],
)
def test_invalid_provider_combinations_fail_before_api_call(
    driver: FilestoreDriver,
    client: FakeFilestore,
    cfg: dict[str, Any],
    expected: str,
) -> None:
    result = _provision(driver, **cfg)
    assert not result.ok
    assert expected in result.message
    assert client.create_calls == []


def test_direct_peering_basic_hdd_is_supported(driver: FilestoreDriver) -> None:
    result = _provision(
        driver,
        tier="BASIC_HDD",
        connect_mode="DIRECT_PEERING",
        capacity_gb=1024,
        share_name="basic_data",
    )
    assert result.ok


def test_external_collision_is_refused_without_operator_adoption(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    instance_id = _resource_id("astrolift-steady-md-triage-production-shared-files")
    name = f"projects/project-1/locations/us-central1/instances/{instance_id}"
    client.instances[name] = {
        "name": name,
        "state": "READY",
        "tier": "REGIONAL",
        "protocol": "NFS_V3",
        "labels": {"owner": "external"},
        "fileShares": [{"name": "data", "capacityGb": "1024"}],
        "networks": [
            {
                "network": "projects/host/global/networks/shared",
                "connectMode": "PRIVATE_SERVICE_CONNECT",
                "ipAddresses": ["10.1.1.1"],
            },
        ],
        "etag": "external",
    }

    refused = _provision(driver)
    assert not refused.ok
    assert "operator-authorized" in refused.message

    # The flag is gone entirely: the schema tenant config is validated against
    # rejects it, and a driver handed one anyway still refuses (#2021).
    validator = Draft202012Validator(driver.config_schema())
    assert validator.is_valid({})
    assert not validator.is_valid({"adopt_existing": True})
    still_refused = _provision(driver, adopt_existing=True)
    assert not still_refused.ok
    assert client.instances[name]["labels"] == {"owner": "external"}


def test_existing_managed_service_cannot_be_reassigned_by_config(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    location, instance_id = _parse_handle(handle)
    name = f"projects/project-1/locations/{location}/instances/{instance_id}"
    client.instances[name]["labels"]["astrolift-io-managed-service-id"] = "other-service"
    refused = _provision(driver)
    assert not refused.ok
    assert "another managed service" in refused.message

    validator = Draft202012Validator(driver.config_schema())
    assert not validator.is_valid({"reassign_existing": True})
    still_refused = _provision(driver, reassign_existing=True)
    assert not still_refused.ok
    assert client.instances[name]["labels"]["astrolift-io-managed-service-id"] == "other-service"


def test_update_cannot_use_adopt_flag_to_bypass_ownership(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    location, instance_id = _parse_handle(handle)
    name = f"projects/project-1/locations/{location}/instances/{instance_id}"
    client.instances[name]["labels"] = {"owner": "external"}
    result = driver.update(
        UpdateSpec(handle, managed_service_id=MSID, config={"adopt_existing": True, "description": "take"})
    )
    assert not result.ok and "not owned" in result.message


def test_update_scales_and_requires_explicit_decrease(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    grown = driver.update(UpdateSpec(handle, managed_service_id=MSID, config={"capacity_gb": 1280}))
    assert grown.ok
    assert client.patch_calls[-1][1]["fileShares"][0]["capacityGb"] == "1280"
    refused = driver.update(UpdateSpec(handle, managed_service_id=MSID, config={"capacity_gb": 1024}))
    assert not refused.ok and "allow_capacity_decrease" in refused.message
    shrunk = driver.update(
        UpdateSpec(
            handle,
            managed_service_id=MSID,
            config={"capacity_gb": 1024, "allow_capacity_decrease": True},
        ),
    )
    assert shrunk.ok


def test_partial_export_update_uses_live_direct_peering_mode(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(
        driver,
        tier="BASIC_HDD",
        connect_mode="DIRECT_PEERING",
        capacity_gb=1024,
    ).handle
    result = driver.update(
        UpdateSpec(
            handle,
            managed_service_id=MSID,
            config={
                "nfs_export_options": [
                    {
                        "ip_ranges": ["10.0.0.0/8"],
                        "squash_mode": "ROOT_SQUASH",
                    },
                ],
            },
        ),
    )
    assert result.ok
    export = client.patch_calls[-1][1]["fileShares"][0]["nfsExportOptions"][0]
    assert "network" not in export


def test_update_description_without_size_does_not_require_capacity(driver: FilestoreDriver) -> None:
    handle = _provision(driver).handle
    result = driver.update(
        UpdateSpec(
            handle,
            managed_service_id=MSID,
            config={
                "description": "new",
                "location": "us-central1",
                "tier": "REGIONAL",
                "protocol": "NFS_V3",
                "share_name": "data",
                "network": "projects/host/global/networks/shared",
                "connect_mode": "PRIVATE_SERVICE_CONNECT",
            },
        ),
    )
    assert result.ok


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tier", "ZONAL"),
        ("location", "us-east1"),
        ("protocol", "NFS_V4_1"),
        ("share_name", "other"),
        ("kms_key_name", "different-key"),
        ("network", "different-network"),
        ("connect_mode", "DIRECT_PEERING"),
    ],
)
def test_immutable_changes_are_rejected(
    driver: FilestoreDriver,
    field: str,
    value: str,
) -> None:
    handle = _provision(driver).handle
    result = driver.update(UpdateSpec(handle, managed_service_id=MSID, config={field: value}))
    assert not result.ok
    assert "immutable" in result.message


def test_safe_delete_requires_force_for_protection_and_retains_backup(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    refused = driver.deprovision(DeprovisionSpec(handle, managed_service_id=MSID))
    assert not refused.ok
    assert refused.errors == ["deletion_protection_enabled"]
    assert client.backup_calls == []

    removed = driver.deprovision(DeprovisionSpec(handle, managed_service_id=MSID), force_destroy=True)
    assert removed.ok
    assert "retained backup" in removed.message
    assert len(client.backup_calls) == 1
    assert client.patch_calls[-1][2] == [
        "deletion_protection_enabled",
        "deletion_protection_reason",
    ]
    assert client.delete_calls[-1][1] is True


def test_safe_delete_retry_reuses_generation_backup(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver, deletion_protection=False).handle
    location, instance_id = _parse_handle(handle)
    current = client.instances[f"projects/project-1/locations/{location}/instances/{instance_id}"]
    first = driver._ensure_final_backup(current)
    second = driver._ensure_final_backup(current)
    assert first["name"] == second["name"]
    assert len(client.backup_calls) == 1


def test_delete_data_skips_backup(driver: FilestoreDriver, client: FakeFilestore) -> None:
    handle = _provision(driver, deletion_protection=False).handle
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=MSID), delete_data=True)
    assert result.ok
    assert client.backup_calls == []
    assert client.delete_calls[-1][1] is False


def test_adopted_delete_has_second_guard(driver: FilestoreDriver, client: FakeFilestore) -> None:
    handle = _provision(driver, deletion_protection=False).handle
    location, instance_id = _parse_handle(handle)
    name = f"projects/project-1/locations/{location}/instances/{instance_id}"
    client.instances[name]["labels"]["astrolift-io-adopted"] = "true"
    refused = driver.deprovision(DeprovisionSpec(handle, managed_service_id=MSID), delete_data=True)
    assert not refused.ok and refused.errors == ["adopted_resource_guard"]
    allowed = driver.deprovision(
        DeprovisionSpec(handle, config={"delete_adopted": True}, managed_service_id=MSID),
        delete_data=True,
    )
    assert allowed.ok


def test_portable_snapshot_is_regional_backup_and_restores_new_instance(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver, deletion_protection=False).handle
    snapshot = driver.snapshot(ServiceHandle(handle, managed_service_id=MSID))
    assert "/locations/us-central1/backups/" in snapshot.snapshot_id
    restored_target = replace(
        SPEC,
        service_handle_hint="restored",
        config={"deletion_protection": False},
    )
    restored = driver.restore(snapshot, restored_target)
    assert restored.ok and restored.ready
    assert client.create_calls[-1][2]["fileShares"][0]["sourceBackup"] == snapshot.snapshot_id


def test_restore_rejects_native_snapshot_and_undersized_target(driver: FilestoreDriver) -> None:
    native = SnapshotHandle(
        "filesystem/us-central1/source",
        "projects/p/locations/l/instances/i/snapshots/s",
        "now",
    )
    assert not driver.restore(native, SPEC).ok

    source = _provision(driver, deletion_protection=False)
    backup = driver.snapshot(ServiceHandle(source.handle, managed_service_id=MSID))
    # Simulate a larger source backup than the requested small target.
    driver._filestore.backups[backup.snapshot_id]["capacityGb"] = "2560"  # type: ignore[attr-defined]
    result = driver.restore(backup, replace(SPEC, service_handle_hint="small-restore"))
    assert not result.ok and "below backup capacity" in result.message


def test_native_snapshot_revert_and_replica_promotion(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    native = driver.create_native_snapshot(ServiceHandle(handle, managed_service_id=MSID), snapshot_id="before-upgrade")
    assert native.snapshot_id.endswith("/snapshots/before-upgrade")
    with pytest.raises(FilestoreError, match="acknowledge_data_loss"):
        driver.revert_native_snapshot(ServiceHandle(handle, managed_service_id=MSID), native.snapshot_id)
    driver.revert_native_snapshot(
        ServiceHandle(handle, managed_service_id=MSID),
        native.snapshot_id,
        acknowledge_data_loss=True,
    )
    with pytest.raises(FilestoreError, match="acknowledge_paused_write_loss"):
        driver.promote_replica(ServiceHandle(handle, managed_service_id=MSID))
    driver.promote_replica(
        ServiceHandle(handle, managed_service_id=MSID),
        peer_instance="projects/p/locations/r/instances/peer",
        acknowledge_paused_write_loss=True,
    )
    with pytest.raises(FilestoreError, match="acknowledge_ephemeral_writes"):
        driver.pause_replica(ServiceHandle(handle, managed_service_id=MSID))
    driver.pause_replica(ServiceHandle(handle, managed_service_id=MSID), acknowledge_ephemeral_writes=True)
    with pytest.raises(FilestoreError, match="acknowledge_data_loss"):
        driver.resume_replica(ServiceHandle(handle, managed_service_id=MSID))
    driver.resume_replica(ServiceHandle(handle, managed_service_id=MSID), acknowledge_data_loss=True)
    assert client.revert_calls[-1][1] == "before-upgrade"
    assert client.promote_calls[-1][1].endswith("/peer")
    assert client.pause_calls and client.resume_calls


@pytest.mark.parametrize(
    ("provider_state", "expected"),
    [
        ("CREATING", "provisioning"),
        ("READY", "available"),
        ("RESTORING", "updating"),
        ("DELETING", "deprovisioning"),
        ("SUSPENDED", "error"),
        ("ERROR", "error"),
    ],
)
def test_status_mapping(
    driver: FilestoreDriver,
    client: FakeFilestore,
    provider_state: str,
    expected: str,
) -> None:
    handle = _provision(driver).handle
    location, instance_id = _parse_handle(handle)
    client.instances[f"projects/project-1/locations/{location}/instances/{instance_id}"]["state"] = provider_state
    assert driver.status(ServiceHandle(handle, managed_service_id=MSID)).state == expected


def test_replica_failure_is_part_of_status(driver: FilestoreDriver, client: FakeFilestore) -> None:
    handle = _provision(driver).handle
    location, instance_id = _parse_handle(handle)
    current = client.instances[f"projects/project-1/locations/{location}/instances/{instance_id}"]
    current["replication"] = {
        "role": "ACTIVE",
        "replicas": [{"state": "FAILED", "stateReasons": ["PEER_INSTANCE_UNREACHABLE"]}],
    }
    status = driver.status(ServiceHandle(handle, managed_service_id=MSID))
    assert status.state == "error"
    assert "PEER_INSTANCE_UNREACHABLE" in status.message


def test_resource_id_and_handle_validation() -> None:
    value = _resource_id("9/" + "Very Long Resource " * 10)
    assert len(value) <= 63 and value.startswith("f-")
    assert _parse_handle("filesystem/us-central1/valid-id") == ("us-central1", "valid-id")
    with pytest.raises(ValueError):
        _parse_handle("filesystem/invalid")


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if payload is not None else b""
        self.text = "provider error"

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_rest_client_uses_v1_shapes_and_typed_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "op"}),
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(409, {"error": {"message": "exists"}}),
            FakeResponse(400, {"error": {"message": "bad tier"}}),
        ],
    )
    client = FilestoreRestClient(session=session)
    client.create_instance("projects/p/locations/r", "instance", {"tier": "REGIONAL"})
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/v1/projects/p/locations/r/instances")
    assert kwargs["params"] == {"instanceId": "instance"}
    with pytest.raises(FilestoreNotFound):
        client.get_instance("projects/p/locations/r/instances/missing")
    with pytest.raises(FilestoreConflict):
        client.create_backup("projects/p/locations/r", "existing", {})
    with pytest.raises(FilestoreError, match="bad tier"):
        client.patch_instance(
            "projects/p/locations/r/instances/i",
            {},
            update_mask=["labels"],
        )


def test_operation_error_is_not_reported_as_success(driver: FilestoreDriver, client: FakeFilestore) -> None:
    client.get_operation = lambda name: {
        "name": name,
        "done": True,
        "error": {"message": "quota exhausted"},
    }
    with pytest.raises(FilestoreError, match="quota exhausted"):
        driver._wait_operation({"name": "operations/wait", "done": False})


def test_registration_catalog_cost_encryption_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from _sdk.storage_encryption import policy_for
    from gcp.cost import SERVICE_ID_BY_VARIANT
    from gcp.plugin import PLUGIN

    assert PLUGIN.managed_service_drivers[("filesystem", "filestore")] is FilestoreDriver
    assert SERVICE_ID_BY_VARIANT[("filesystem", "filestore")] == "D97E-AB26-5D95"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "gcp" and item.kind == "filesystem" and item.variant == "filestore"
    )
    assert entry.status == "ga"
    assert "FILESYSTEM_ENDPOINT" in entry.binding_envs
    policy = policy_for(plugin_id="gcp", kind="filesystem", variant="filestore")
    assert policy is not None and policy.cmek_supported

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-west1",
        provider_config={
            "project_id": "acme-prod",
            "network": "projects/host/global/networks/default",
            "filestore_location": "us-west1-b",
            "filestore_instance_name_prefix": "platform",
            "filestore_share_name_default": "shared",
            "filestore_tier_default": "ZONAL",
            "filestore_protocol_default": "NFS_V4_1",
            "filestore_connect_mode_default": "PRIVATE_SERVICE_ACCESS",
            "filestore_reserved_ip_range": "filestore-range",
            "filestore_kms_key_name": "projects/p/locations/us/keyRings/r/cryptoKeys/k",
            "filestore_deletion_protection_default": False,
            "filestore_backup_location": "us-west1",
            "filestore_backup_kms_key": "projects/p/locations/us/keyRings/r/cryptoKeys/backup",
            "filestore_api_endpoint": "https://filestore.example.test/v1",
            "filestore_operation_timeout_seconds": 123,
            "filestore_operation_poll_interval_seconds": 0.5,
        },
        auth_config={},
    )
    resolved = managed_config_for("gcp", cluster, kind="filesystem", variant="filestore")
    assert resolved == FilestoreConfig(
        project_id="acme-prod",
        location="us-west1-b",
        network="projects/host/global/networks/default",
        instance_name_prefix="platform",
        share_name_default="shared",
        tier_default="ZONAL",
        protocol_default="NFS_V4_1",
        connect_mode_default="PRIVATE_SERVICE_ACCESS",
        reserved_ip_range="filestore-range",
        kms_key_name="projects/p/locations/us/keyRings/r/cryptoKeys/k",
        deletion_protection_default=False,
        backup_location="us-west1",
        backup_kms_key="projects/p/locations/us/keyRings/r/cryptoKeys/backup",
        api_endpoint="https://filestore.example.test/v1",
        operation_timeout_seconds=123,
        poll_interval_seconds=0.5,
    )


# Two-org cases (#2098): tenant labels in the platform namespace are refused,
# and ownership comes from the spec, never from a label read back.


def _instance(handle: str) -> str:
    location, instance_id = _parse_handle(handle)
    return f"projects/project-1/locations/{location}/instances/{instance_id}"


@pytest.mark.parametrize(
    "key",
    [
        "astrolift_io_managed_service_id",
        "astrolift-managed-by",
        "Astrolift.IO.Organization",
        "x-astrolift-managed-service-id",
    ],
)
def test_tenant_labels_in_the_platform_namespace_are_refused(
    driver: FilestoreDriver,
    client: FakeFilestore,
    key: str,
) -> None:
    refused = _provision(driver, labels={key: "service-id"})
    assert not refused.ok and "Astrolift-reserved" in refused.message
    assert client.create_calls == [] and client.instances == {}


def test_another_services_update_teardown_and_mount_leave_its_instance_untouched(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver, deletion_protection=False).handle
    before = {name: dict(row) for name, row in client.instances.items()}
    other = "other-service"

    updated = driver.update(UpdateSpec(handle, managed_service_id=other, config={"description": "take"}))
    assert not updated.ok and "another managed service" in updated.message
    removed = driver.deprovision(DeprovisionSpec(handle, managed_service_id=other), delete_data=True)
    assert not removed.ok
    with pytest.raises(FilestoreError, match="another managed service"):
        driver.binding(ServiceHandle(handle, managed_service_id=other))
    with pytest.raises(FilestoreError, match="another managed service"):
        driver.snapshot(ServiceHandle(handle, managed_service_id=other))

    assert client.instances == before
    assert client.patch_calls == [] and client.delete_calls == [] and client.backup_calls == []


def test_an_unmarked_instance_needs_the_exclusive_record_and_is_then_marked(
    driver: FilestoreDriver,
    client: FakeFilestore,
) -> None:
    handle = _provision(driver).handle
    name = _instance(handle)
    for key in ("astrolift-io-managed-service-id", "astrolift_io_managed_service_id"):
        client.instances[name]["labels"].pop(key, None)
    unproven = driver.update(UpdateSpec(handle, managed_service_id=MSID, config={"description": "new"}))
    assert not unproven.ok and "no managed-service id" in unproven.message
    proven = driver.update(
        UpdateSpec(handle, managed_service_id=MSID, config={"description": "new"}, recorded_handle_exclusive=True),
    )
    assert proven.ok, proven.message
    assert client.instances[name]["labels"]["astrolift-io-managed-service-id"] == "service-id"
