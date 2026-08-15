"""Lifecycle and adversarial tests for top-level Azure Files NFS shares."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
    VolumeSourceKind,
)
from azure.managed.filesystem_files import AzureFilesConfig, AzureFilesDriver, AzureFilesError

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
SUBNET_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/rg-network/providers/"
    "Microsoft.Network/virtualNetworks/platform/subnets/aks"
)
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class SdkEncryptionInTransit(str, Enum):  # noqa: UP042 (mirrors the generated SDK's str/Enum mixin)
    """Shaped like the generated SDK's string enums, which mix in ``str``.

    Deliberately not a ``StrEnum``: ``str()`` on a ``StrEnum`` member returns
    the wire value, which is precisely the difference under test.
    """

    ENABLED = "Enabled"
    DISABLED = "Disabled"


class ResourceNotFoundError(Exception):
    status_code = 404


class Poller:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def result(self) -> Any:
        return self.value


@dataclass
class FakeFileShares:
    values: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(self, resource_group: str, name: str) -> SimpleNamespace:
        assert resource_group == RESOURCE_GROUP
        try:
            return self.values[name]
        except KeyError as exc:
            raise ResourceNotFoundError(name) from exc

    def begin_create_or_update(self, resource_group: str, name: str, resource: Any) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.create_calls.append({"name": name, "resource": resource})
        properties = resource.properties
        properties.host_name = f"{name[:16]}.eastus2.file.storage.azure.net"
        properties.provisioning_state = "Succeeded"
        value = SimpleNamespace(
            name=name,
            id=(
                f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
                f"providers/Microsoft.FileShares/fileShares/{name}"
            ),
            location=resource.location,
            tags=dict(resource.tags or {}),
            properties=properties,
        )
        self.values[name] = value
        return Poller(value)

    def begin_update(self, resource_group: str, name: str, properties: Any) -> Poller:
        value = self.get(resource_group, name)
        self.update_calls.append({"name": name, "properties": properties})
        if properties.tags is not None:
            value.tags = dict(properties.tags)
        update = properties.properties
        for field_name in (
            "provisioned_storage_gi_b",
            "provisioned_io_per_sec",
            "provisioned_throughput_mi_b_per_sec",
            "nfs_protocol_properties",
            "public_access_properties",
            "public_network_access",
        ):
            changed = getattr(update, field_name, None)
            if changed is not None:
                setattr(value.properties, field_name, changed)
        value.properties.provisioning_state = "Succeeded"
        return Poller(value)

    def begin_delete(self, resource_group: str, name: str) -> Poller:
        self.get(resource_group, name)
        self.delete_calls.append(name)
        self.values.pop(name)
        return Poller()


@dataclass
class FakeSnapshots:
    shares: FakeFileShares
    values: dict[tuple[str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def begin_create_or_update_file_share_snapshot(
        self,
        resource_group: str,
        share_name: str,
        name: str,
        resource: Any,
    ) -> Poller:
        self.shares.get(resource_group, share_name)
        self.create_calls.append({"share": share_name, "name": name, "resource": resource})
        value = SimpleNamespace(name=name, properties=resource.properties)
        self.values[(share_name, name)] = value
        return Poller(value)

    def begin_delete_file_share_snapshot(
        self,
        resource_group: str,
        share_name: str,
        name: str,
    ) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.delete_calls.append((share_name, name))
        self.values.pop((share_name, name), None)
        return Poller()

    def list_by_file_share(self, resource_group: str, share_name: str) -> list[SimpleNamespace]:
        self.shares.get(resource_group, share_name)
        return [value for (parent, _), value in self.values.items() if parent == share_name]


@dataclass
class FakePrivateConnections:
    shares: FakeFileShares
    values: dict[str, list[SimpleNamespace]] = field(default_factory=dict)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def list_by_file_share(self, resource_group: str, share_name: str) -> list[SimpleNamespace]:
        self.shares.get(resource_group, share_name)
        return list(self.values.get(share_name, []))

    def begin_delete(
        self,
        resource_group: str,
        share_name: str,
        connection_name: str,
    ) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.delete_calls.append((share_name, connection_name))
        self.values[share_name] = [value for value in self.values.get(share_name, []) if value.name != connection_name]
        return Poller()


@dataclass
class FakeMgmt:
    file_shares: FakeFileShares = field(default_factory=FakeFileShares)
    file_share_snapshots: FakeSnapshots = field(init=False)
    private_endpoint_connections: FakePrivateConnections = field(init=False)

    def __post_init__(self) -> None:
        self.file_share_snapshots = FakeSnapshots(self.file_shares)
        self.private_endpoint_connections = FakePrivateConnections(self.file_shares)


@dataclass
class FakeManagementLocks:
    values: list[SimpleNamespace] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def list_at_resource_level(self, **kwargs: Any) -> list[SimpleNamespace]:
        assert kwargs["resource_provider_namespace"] == "Microsoft.FileShares"
        assert kwargs["resource_type"] == "fileShares"
        return list(self.values)

    def delete_at_resource_level(self, **kwargs: Any) -> None:
        name = str(kwargs["lock_name"])
        self.delete_calls.append(name)
        self.values = [value for value in self.values if value.name != name]


@dataclass
class FakeLocks:
    management_locks: FakeManagementLocks = field(default_factory=FakeManagementLocks)


def _driver(**config: Any) -> tuple[AzureFilesDriver, FakeMgmt, FakeLocks]:
    mgmt = FakeMgmt()
    locks = FakeLocks()
    return (
        AzureFilesDriver(
            config=AzureFilesConfig(
                subscription_id=SUBSCRIPTION_ID,
                resource_group=RESOURCE_GROUP,
                location="eastus2",
                allowed_subnet_ids=(SUBNET_ID,),
                mgmt_client=mgmt,
                locks_client=locks,
                **config,
            ),
            now=lambda: NOW,
        ),
        mgmt,
        locks,
    )


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="steadymd",
        app_id="app-id",
        app_slug="emr-triage",
        environment_id="env-id",
        environment_name="production",
        tenant_cluster_id="cluster-id",
        service_handle_hint="shared-files",
        size="medium",
        isolation="dedicated",
        config=config,
        binding_id="binding-id",
        managed_service_id="service-id",
        tags={"cost-center": "engineering"},
    )


def _provisioned(driver: AzureFilesDriver, **config: Any) -> str:
    result = driver.provision(_spec(**config))
    assert result.ok, result
    return result.handle


def test_provisions_top_level_nfs_share_and_emits_portable_binding() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(
        driver,
        redundancy="Zone",
        provisioned_storage_gib=1024,
        provisioned_iops=5000,
        provisioned_throughput_mib_per_sec=200,
        root_squash="AllSquash",
        encryption_in_transit_required=True,
        mount_name="triage-data",
    )
    name = handle.split("/")[1]
    share = mgmt.file_shares.values[name]
    assert share.location == "eastus2"
    assert share.tags["astrolift-managed-service-id"] == "service-id"
    extra_tags = {key: value for key, value in share.tags.items() if key.startswith("astrolift-extra-cost-center-")}
    assert list(extra_tags.values()) == ["engineering"]
    assert share.properties.media_tier == "SSD"
    assert share.properties.protocol == "NFS"
    assert share.properties.redundancy == "Zone"
    assert share.properties.provisioned_storage_gi_b == 1024
    assert share.properties.provisioned_io_per_sec == 5000
    assert share.properties.provisioned_throughput_mi_b_per_sec == 200
    assert share.properties.nfs_protocol_properties.root_squash == "AllSquash"
    assert share.properties.nfs_protocol_properties.encryption_in_transit_required == "Enabled"
    assert share.properties.public_access_properties.allowed_subnets == [SUBNET_ID]

    binding = driver.binding(
        ServiceHandle(handle),
        {"mount_path": "/data", "mount_options": ["actimeo=30"], "read_only": True},
    )
    assert binding.env_vars["FILESYSTEM_HANDLE"].literal == share.id
    assert binding.env_vars["FILESYSTEM_PROTOCOL"].literal == "nfs4.1"
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "true"
    assert binding.env_vars["FILESYSTEM_SOURCE"].literal.endswith(f":/triage-data/{name}")
    assert "nconnect=4" in binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal
    assert "actimeo=30" in binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal
    assert "ro" in binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal
    assert binding.env_vars["FILESYSTEM_READ_ONLY"].literal == "true"
    assert binding.iam_grants == []
    assert "AZNFS" in binding.notes

    assert len(binding.pod_volume_mounts) == 1
    volume = binding.pod_volume_mounts[0]
    assert volume.name == name
    assert volume.mount_path == "/data"
    assert volume.source_kind is VolumeSourceKind.CSI
    assert volume.protocol == "nfs4.1"
    assert volume.csi_driver == "file.csi.azure.com"
    assert volume.volume_handle == share.id
    assert volume.volume_attributes == {
        "shareName": name,
        "protocol": "nfs",
        "server": share.properties.host_name,
        "storageAccount": "triage-data",
    }
    # file.csi.azure.com composes the NFS source as
    # "<server>:/<storageAccount>/<shareName>", which has to land on the same
    # export path the environment contract publishes.
    attributes = volume.volume_attributes
    assert (
        f"{attributes['server']}:/{attributes['storageAccount']}/{attributes['shareName']}"
        == binding.env_vars["FILESYSTEM_SOURCE"].literal
    )
    # The CSI list drops the fstab-only and driver-owned flags that
    # FILESYSTEM_MOUNT_OPTIONS keeps for manual mounts, and read-only travels
    # as the portable flag rather than as an "ro" option.
    assert volume.mount_options == ["nconnect=4", "rsize=1048576", "wsize=1048576", "actimeo=30"]
    assert volume.read_only is True
    assert volume.capacity == "1024Gi"
    # NFS here is network-authorized; nothing about the mount is a credential.
    assert volume.secret_refs == {}
    assert volume.secret_literals == {}

    from _sdk.availability import MATRIX

    availability = next(
        entry for entry in MATRIX.managed_services if entry.plugin_id == "azure" and entry.variant == "azure_files"
    )
    assert set(availability.binding_envs) == set(driver.binding_schema().env_vars)


def test_unencrypted_binding_adds_notls_and_missing_hostname_fails() -> None:
    driver, mgmt, _ = _driver(encryption_in_transit_required_default=False)
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    binding = driver.binding(ServiceHandle(handle))
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "false"
    assert "notls" in binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal
    # notls is an AZNFS mount-helper flag that plain mount.nfs rejects, so it
    # stays in the manual-mount contract and out of the CSI attachment.
    assert "notls" not in binding.pod_volume_mounts[0].mount_options

    mgmt.file_shares.values[name].properties.host_name = ""
    with pytest.raises(AzureFilesError, match="no mount hostname"):
        driver.binding(ServiceHandle(handle))


def test_binding_reads_sdk_enum_encryption_state() -> None:
    """The generated SDK deserializes this field into a string-mixin enum.

    ``str()`` on such a member renders the member path rather than the wire
    value, so reading it with ``str()`` reports every encrypted share as
    unencrypted and injects ``notls`` into its mount options.
    """
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver, provisioned_storage_gib=512)
    share = mgmt.file_shares.values[handle.split("/")[1]]
    assert str(SdkEncryptionInTransit.ENABLED) != SdkEncryptionInTransit.ENABLED.value
    share.properties.nfs_protocol_properties.encryption_in_transit_required = SdkEncryptionInTransit.ENABLED

    binding = driver.binding(ServiceHandle(handle))
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "true"
    assert "notls" not in binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal

    volume = binding.pod_volume_mounts[0]
    assert volume.mount_options == ["nconnect=4", "rsize=1048576", "wsize=1048576"]
    assert volume.read_only is False
    assert volume.capacity == "512Gi"


def test_binding_refuses_a_share_without_usable_provisioned_capacity() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    properties = mgmt.file_shares.values[handle.split("/")[1]].properties

    properties.provisioned_storage_gi_b = 0
    with pytest.raises(AzureFilesError, match="invalid provisioned capacity"):
        driver.binding(ServiceHandle(handle))

    properties.provisioned_storage_gi_b = "unbounded"
    with pytest.raises(AzureFilesError, match="invalid provisioned capacity"):
        driver.binding(ServiceHandle(handle))


def test_provision_is_idempotent_and_refuses_foreign_collision() -> None:
    driver, mgmt, _ = _driver()
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    assert len(mgmt.file_shares.create_calls) == 1
    assert len(mgmt.file_shares.update_calls) == 1

    name = first.handle.split("/")[1]
    mgmt.file_shares.values[name].tags = {"owner": "external"}
    collision = driver.provision(_spec())
    assert not collision.ok and "not owned" in collision.message


def test_generated_names_keep_the_identity_digest_with_long_prefixes() -> None:
    first_driver, _, _ = _driver(name_prefix="a" * 63)
    second_driver, _, _ = _driver(name_prefix="a" * 63)
    first = first_driver.provision(_spec())
    second = second_driver.provision(replace(_spec(), managed_service_id="another-service"))
    assert first.ok and second.ok
    assert first.handle != second.handle
    assert len(first.handle.removeprefix("filesystem/")) <= 63


def test_managed_service_identity_collision_is_not_adopted() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    mgmt.file_shares.values[name].tags["astrolift-managed-service-id"] = "other-service"

    result = driver.provision(_spec())

    assert not result.ok and "another managed service" in result.message


def test_partial_update_changes_only_mutable_fields() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver, provisioned_storage_gib=256, root_squash="RootSquash")
    updated = driver.update(
        UpdateSpec(
            handle,
            config={
                "provisioned_storage_gib": 512,
                "provisioned_iops": 3500,
                "root_squash": "NoRootSquash",
                "allowed_subnet_ids": [SUBNET_ID],
            },
        ),
    )
    assert updated.ok, updated
    name = handle.split("/")[1]
    properties = mgmt.file_shares.values[name].properties
    assert properties.provisioned_storage_gi_b == 512
    assert properties.provisioned_io_per_sec == 3500
    assert properties.nfs_protocol_properties.root_squash == "NoRootSquash"
    assert properties.redundancy == "Local"


def test_update_size_mapping_and_immutable_drift_fail_closed() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver, provisioned_storage_gib=256)
    sized = driver.update(UpdateSpec(handle, size="large"))
    assert sized.ok, sized
    name = handle.split("/")[1]
    assert mgmt.file_shares.values[name].properties.provisioned_storage_gi_b == 1024

    immutable = driver.update(UpdateSpec(handle, config={"redundancy": "Zone"}))
    assert not immutable.ok and "immutable" in immutable.message


def test_downgrade_cooldown_is_checked_before_cloud_mutation() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver, provisioned_storage_gib=1024, provisioned_iops=5000)
    name = handle.split("/")[1]
    properties = mgmt.file_shares.values[name].properties
    properties.provisioned_storage_next_allowed_downgrade = NOW + timedelta(days=1)
    properties.provisioned_io_per_sec_next_allowed_downgrade = NOW + timedelta(days=2)
    previous_calls = len(mgmt.file_shares.update_calls)

    storage = driver.update(UpdateSpec(handle, config={"provisioned_storage_gib": 512}))
    iops = driver.update(UpdateSpec(handle, config={"provisioned_iops": 4000}))

    assert not storage.ok and "cannot be reduced until" in storage.message
    assert not iops.ok and "cannot be reduced until" in iops.message
    assert len(mgmt.file_shares.update_calls) == previous_calls


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": True}, "unsupported"),
        ({"resource_name": "Bad_Name"}, "lowercase"),
        ({"resource_name": "bad--name"}, "lowercase"),
        ({"mount_name": "x"}, "3-63"),
        ({"media_tier": "HDD"}, "only SSD"),
        ({"protocol": "SMB"}, "classic variant"),
        ({"redundancy": "Geo"}, "Local or Zone"),
        ({"provisioned_storage_gib": 31}, "between 32"),
        ({"provisioned_iops": 0}, "positive"),
        ({"provisioned_throughput_mib_per_sec": 0}, "positive"),
        ({"root_squash": "Maybe"}, "root_squash"),
        ({"public_network_access": "Disabled"}, "Private Endpoint"),
        ({"allowed_subnet_ids": []}, "at least one"),
        ({"allowed_subnet_ids": ["not-an-id"]}, "resource IDs"),
        ({"mount_path": "relative"}, "absolute"),
        ({"mount_options": ["a,b"]}, "comma-free"),
    ],
)
def test_invalid_configs_fail_before_cloud_mutation(config: dict[str, Any], message: str) -> None:
    driver, mgmt, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok and message in result.message
    assert not mgmt.file_shares.create_calls


def test_azure_tag_limits_fail_before_cloud_mutation() -> None:
    driver, mgmt, _ = _driver()
    too_many = replace(_spec(), tags={f"tag-{index}": "value" for index in range(43)})
    result = driver.provision(too_many)
    assert not result.ok and "at most 42" in result.message

    too_long = replace(_spec(), tags={"oversized": "x" * 257})
    result = driver.provision(too_long)
    assert not result.ok and "at most 256" in result.message
    assert not mgmt.file_shares.create_calls


def test_custom_size_and_missing_default_network_fail_closed() -> None:
    driver, _, _ = _driver()
    custom = ProvisionSpec(**{**_spec().__dict__, "size": "custom"})
    result = driver.provision(custom)
    assert not result.ok and "requires provisioned_storage_gib" in result.message

    no_network_mgmt = FakeMgmt()
    no_network = AzureFilesDriver(
        config=AzureFilesConfig(
            SUBSCRIPTION_ID,
            RESOURCE_GROUP,
            mgmt_client=no_network_mgmt,
            locks_client=FakeLocks(),
        ),
    )
    result = no_network.provision(_spec())
    assert not result.ok and "allowed_subnet_id" in result.message
    assert not no_network_mgmt.file_shares.create_calls


def test_status_reports_lifecycle_missing_and_invalid_handles() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    assert driver.status(ServiceHandle(handle)).state == "available"
    mgmt.file_shares.values[name].properties.provisioning_state = "Updating"
    assert driver.status(ServiceHandle(handle)).state == "updating"
    mgmt.file_shares.values.pop(name)
    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert driver.status(ServiceHandle("bad/handle/shape")).state == "error"
    invalid = driver.update(UpdateSpec("bad/handle/shape"))
    assert not invalid.ok and invalid.errors == ["invalid_handle"]


def test_snapshot_is_native_but_portable_restore_fails_honestly() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    snapshot = driver.snapshot(ServiceHandle(handle))
    assert snapshot.snapshot_id == "snap-20260814-120000-000000"
    call = mgmt.file_share_snapshots.create_calls[0]
    assert call["resource"].properties.initiator_id == "astrolift"
    assert call["resource"].properties.metadata["astrolift-managed-by"] == "platform"

    restored = driver.restore(snapshot, _spec(resource_name="restored-share"))
    assert not restored.ok and restored.errors == ["restore_not_supported"]


def test_snapshot_and_binding_refuse_missing_or_unowned_shares() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    mgmt.file_shares.values[name].tags = {}
    with pytest.raises(AzureFilesError, match="not owned"):
        driver.snapshot(ServiceHandle(handle))
    with pytest.raises(AzureFilesError, match="not owned"):
        driver.binding(ServiceHandle(handle))

    mgmt.file_shares.values.pop(name)
    with pytest.raises(AzureFilesError, match="missing"):
        driver.snapshot(ServiceHandle(handle))
    with pytest.raises(AzureFilesError, match="missing"):
        driver.binding(ServiceHandle(handle))


def test_deprovision_requires_guard_and_explicit_data_loss() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver)
    protected = driver.deprovision(DeprovisionSpec(handle))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]

    retained = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
    )
    assert not retained.ok and retained.errors == ["retained_filesystem_data_requires_delete_data"]
    assert not mgmt.file_shares.delete_calls


def test_deprovision_guards_locks_and_private_endpoint_connections() -> None:
    driver, mgmt, locks = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    locks.management_locks.values = [SimpleNamespace(name="protect")]
    locked = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not locked.ok and locked.errors == ["resource_lock_present"]
    assert not locks.management_locks.delete_calls

    locks.management_locks.values = []
    mgmt.private_endpoint_connections.values[name] = [SimpleNamespace(name="private-link")]
    connected = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not connected.ok and connected.errors == ["private_endpoint_connections_present"]


def test_deprovision_converges_when_listed_child_snapshot_disappears(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, mgmt, _ = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    driver.snapshot(ServiceHandle(handle))

    def disappeared(*args: Any, **kwargs: Any) -> Poller:
        raise ResourceNotFoundError("snapshot already gone")

    monkeypatch.setattr(mgmt.file_share_snapshots, "begin_delete_file_share_snapshot", disappeared)
    result = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert result.ok
    assert not mgmt.file_shares.values


def test_deprovision_reports_transient_describe_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, mgmt, _ = _driver()

    def unavailable(*args: Any, **kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("control plane unavailable")

    monkeypatch.setattr(mgmt.file_shares, "get", unavailable)
    result = driver.deprovision(DeprovisionSpec("filesystem/valid-share"), delete_data=True)
    assert not result.ok and result.retryable
    assert "control plane unavailable" in result.message


def test_force_deprovision_removes_guards_snapshots_and_converges() -> None:
    driver, mgmt, locks = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    locks.management_locks.values = [SimpleNamespace(name="protect")]
    mgmt.private_endpoint_connections.values[name] = [SimpleNamespace(name="private-link")]
    snapshot = driver.snapshot(ServiceHandle(handle))

    deleted = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=True,
        force_destroy=True,
    )
    assert deleted.ok, deleted
    assert locks.management_locks.delete_calls == ["protect"]
    assert mgmt.private_endpoint_connections.delete_calls == [(name, "private-link")]
    assert mgmt.file_share_snapshots.delete_calls == [(name, snapshot.snapshot_id)]
    assert mgmt.file_shares.delete_calls == [name]
    again = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert again.ok and "already gone" in again.message


def test_deprovision_refuses_unowned_resource() -> None:
    driver, mgmt, _ = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    name = handle.split("/")[1]
    mgmt.file_shares.values[name].tags = {}
    result = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not result.ok and result.errors == ["external_resource_collision"]


def test_schema_constructor_and_deterministic_names() -> None:
    driver, _, _ = _driver()
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["protocol"]["const"] == "NFS"
    assert "FILESYSTEM_SOURCE" in driver.binding_schema().env_vars
    assert "provisioned_iops" in driver.editable_fields()
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.handle == second.handle
    assert len(first.handle.split("/")[1]) <= 63

    with pytest.raises(ValueError, match="between 32"):
        AzureFilesConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_storage_gib=1)
    with pytest.raises(ValueError, match="redundancy"):
        AzureFilesConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_redundancy="Geo")
    with pytest.raises(ValueError, match="root squash"):
        AzureFilesConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_root_squash="Maybe")
    with pytest.raises(ValueError, match="resource IDs"):
        AzureFilesConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, allowed_subnet_ids=("bad",))


def test_restore_rejects_even_well_formed_snapshot_without_mutating_cloud() -> None:
    driver, mgmt, _ = _driver()
    result = driver.restore(
        SnapshotHandle("filesystem/source", "snap-id", NOW.isoformat()),
        _spec(resource_name="restored-share"),
    )
    assert not result.ok and not mgmt.file_shares.create_calls
