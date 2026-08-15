"""Lifecycle and adversarial tests for classic Azure Files."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from astrolift_manifest.env_injection import envelope_keys_for

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from azure.managed.filesystem_files_classic import (
    AzureFilesClassicConfig,
    AzureFilesClassicDriver,
    AzureFilesClassicError,
)

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
SUBNET_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/rg-network/providers/"
    "Microsoft.Network/virtualNetworks/platform/subnets/aks"
)
NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


class ResourceNotFoundError(Exception):
    status_code = 404


@dataclass
class Poller:
    value: Any

    def result(self) -> Any:
        return self.value


@dataclass
class FakeStorageAccounts:
    values: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[Any] = field(default_factory=list)
    update_calls: list[Any] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def begin_create(self, resource_group: str, name: str, parameters: Any) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.create_calls.append(parameters)
        value = SimpleNamespace(
            name=name,
            id=(
                f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
                f"providers/Microsoft.Storage/storageAccounts/{name}"
            ),
            location=parameters.location,
            kind=parameters.kind,
            sku=parameters.sku,
            tags=dict(parameters.tags or {}),
            network_rule_set=parameters.network_rule_set,
            provisioning_state="Succeeded",
            primary_endpoints=SimpleNamespace(file=f"https://{name}.file.core.windows.net/"),
        )
        self.values[name] = value
        return Poller(value)

    def get_properties(self, resource_group: str, name: str) -> SimpleNamespace:
        assert resource_group == RESOURCE_GROUP
        try:
            return self.values[name]
        except KeyError as exc:
            raise ResourceNotFoundError(name) from exc

    def update(self, resource_group: str, name: str, parameters: Any) -> SimpleNamespace:
        value = self.get_properties(resource_group, name)
        self.update_calls.append(parameters)
        if parameters.tags is not None:
            value.tags = dict(parameters.tags)
        if parameters.network_rule_set is not None:
            value.network_rule_set = parameters.network_rule_set
        return value

    def list_keys(self, resource_group: str, name: str) -> SimpleNamespace:
        self.get_properties(resource_group, name)
        return SimpleNamespace(
            keys=[SimpleNamespace(value=f"{name}-primary"), SimpleNamespace(value=f"{name}-secondary")],
        )

    def delete(self, resource_group: str, name: str) -> None:
        self.get_properties(resource_group, name)
        self.delete_calls.append(name)
        self.values.pop(name)


@dataclass
class FakeFileServices:
    values: dict[str, Any] = field(default_factory=dict)
    set_calls: list[Any] = field(default_factory=list)

    def set_service_properties(self, resource_group: str, account: str, parameters: Any) -> Any:
        assert resource_group == RESOURCE_GROUP
        self.set_calls.append(parameters)
        self.values[account] = parameters
        return parameters

    def get_service_properties(self, resource_group: str, account: str) -> Any:
        assert resource_group == RESOURCE_GROUP
        return self.values[account]


@dataclass
class FakeFileShares:
    accounts: FakeStorageAccounts
    values: dict[tuple[str, str], Any] = field(default_factory=dict)
    create_calls: list[Any] = field(default_factory=list)
    update_calls: list[Any] = field(default_factory=list)
    delete_calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def create(self, resource_group: str, account: str, share: str, file_share: Any) -> Any:
        self.accounts.get_properties(resource_group, account)
        self.create_calls.append(file_share)
        file_share.name = share
        file_share.id = (
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Storage/"
            f"storageAccounts/{account}/fileServices/default/shares/{share}"
        )
        self.values[(account, share)] = file_share
        return file_share

    def get(self, resource_group: str, account: str, share: str) -> Any:
        self.accounts.get_properties(resource_group, account)
        try:
            return self.values[(account, share)]
        except KeyError as exc:
            raise ResourceNotFoundError(share) from exc

    def update(self, resource_group: str, account: str, share: str, file_share: Any) -> Any:
        current = self.get(resource_group, account, share)
        self.update_calls.append(file_share)
        for name in (
            "metadata",
            "share_quota",
            "enabled_protocols",
            "root_squash",
            "access_tier",
            "file_share_paid_bursting",
        ):
            value = getattr(file_share, name, None)
            if value is not None:
                setattr(current, name, value)
        return current

    def delete(
        self,
        resource_group: str,
        account: str,
        share: str,
        *,
        include: str | None = None,
    ) -> None:
        self.get(resource_group, account, share)
        self.delete_calls.append((account, share, include))
        self.values.pop((account, share))

    def list(self, resource_group: str, account: str) -> list[Any]:
        self.accounts.get_properties(resource_group, account)
        return [value for (parent, _), value in self.values.items() if parent == account]


@dataclass
class FakeMgmt:
    storage_accounts: FakeStorageAccounts = field(default_factory=FakeStorageAccounts)
    file_services: FakeFileServices = field(default_factory=FakeFileServices)
    file_shares: FakeFileShares = field(init=False)

    def __post_init__(self) -> None:
        self.file_shares = FakeFileShares(self.storage_accounts)


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def set_secret(self, name: str, value: str) -> None:
        self.values[name] = value

    def begin_delete_secret(self, name: str) -> Poller:
        if name not in self.values:
            raise ResourceNotFoundError(name)
        self.deleted.append(name)
        self.values.pop(name)
        return Poller(None)


@dataclass
class FakeShareClient:
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def create_snapshot(self, *, metadata: dict[str, str]) -> dict[str, str]:
        self.snapshots.append(metadata)
        return {"snapshot": "2026-08-14T15:00:00.0000000Z"}


@dataclass
class FakeShareClientFactory:
    client: FakeShareClient = field(default_factory=FakeShareClient)
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def __call__(self, account: str, share: str, credential: str | None) -> FakeShareClient:
        self.calls.append((account, share, credential))
        return self.client


def _driver(**config: Any) -> tuple[AzureFilesClassicDriver, FakeMgmt, FakeSecrets, FakeShareClientFactory]:
    mgmt = FakeMgmt()
    secrets = FakeSecrets()
    share_factory = FakeShareClientFactory()
    return (
        AzureFilesClassicDriver(
            config=AzureFilesClassicConfig(
                subscription_id=SUBSCRIPTION_ID,
                resource_group=RESOURCE_GROUP,
                location="eastus2",
                allowed_subnet_ids=(SUBNET_ID,),
                keyvault_url="https://vault.example",
                mgmt_client=mgmt,
                secret_client=secrets,
                share_client_factory=share_factory,
                **config,
            ),
            now=lambda: NOW,
        ),
        mgmt,
        secrets,
        share_factory,
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


def _provisioned(driver: AzureFilesClassicDriver, **config: Any) -> str:
    result = driver.provision(_spec(**config))
    assert result.ok, result
    return result.handle


def test_smb_provisions_hardened_account_share_keys_and_binding() -> None:
    driver, mgmt, secrets, _ = _driver()
    handle = _provisioned(driver, protocol="SMB", sku="Standard_ZRS", quota_gib=2048, access_tier="Hot")
    _, account, share = handle.split("/")

    account_request = mgmt.storage_accounts.create_calls[0]
    assert account_request.kind.value == "StorageV2"
    assert account_request.sku.name.value == "Standard_ZRS"
    assert account_request.minimum_tls_version == "TLS1_2"
    assert account_request.enable_https_traffic_only is True
    assert account_request.allow_blob_public_access is False
    assert account_request.allow_shared_key_access is True
    assert account_request.network_rule_set.default_action == "Deny"
    assert account_request.network_rule_set.virtual_network_rules[0].virtual_network_resource_id == SUBNET_ID
    assert account_request.tags["astrolift-managed-service-id"] == "service-id"

    share_request = mgmt.file_shares.create_calls[0]
    assert share_request.enabled_protocols.value == "SMB"
    assert share_request.share_quota == 2048
    assert share_request.access_tier.value == "Hot"
    assert share_request.metadata["astrolift_managed_by"] == "platform"
    assert mgmt.file_services.set_calls[0].protocol_settings.smb.encryption_in_transit.required is True

    assert secrets.values[f"astrolift-files-{account}-primary"] == f"{account}-primary"
    assert secrets.values[f"astrolift-files-{account}-secondary"] == f"{account}-secondary"
    binding = driver.binding(ServiceHandle(handle))
    assert binding.env_vars["FILESYSTEM_SOURCE"].literal == f"//{account}.file.core.windows.net/{share}"
    assert binding.env_vars["FILESYSTEM_PROTOCOL"].literal == "smb3.1.1"
    assert binding.env_vars["FILESYSTEM_PASSWORD"].secret_ref == f"astrolift-files-{account}-primary"
    assert binding.env_vars["FILESYSTEM_PASSWORD_SECONDARY"].secret_ref.endswith("-secondary")
    assert len(binding.iam_grants) == 2
    # #1003: SMB and NFS populate FILESYSTEM_TLS on separate branches, so the
    # canonical envelope is checked on both.
    assert set(envelope_keys_for("filesystem")) <= set(binding.env_vars)

    volume = binding.pod_volume_mounts[0]
    assert volume.mount_path == "/mnt/shared"
    assert volume.csi_driver == "file.csi.azure.com"
    assert volume.volume_handle == f"{RESOURCE_GROUP}#{account}#{share}"
    assert volume.volume_attributes == {"shareName": share}
    assert volume.secret_refs == {"azurestorageaccountkey": f"astrolift-files-{account}-primary"}
    assert volume.secret_literals == {"azurestorageaccountname": account}
    # SMB options are cifs options the CSI driver forwards as-is; the NFS
    # option filter must not reach this branch or the mount loses its dialect
    # and security flavour.
    assert volume.mount_options == [
        "vers=3.1.1",
        "sec=ntlmssp",
        "serverino",
        "nosharesock",
        "mfsymlinks",
        "actimeo=30",
    ]
    # Sizes the rendered PV and PVC, so it tracks the share quota.
    assert volume.capacity == "2048Gi"


def test_nfs_requires_premium_uses_network_auth_and_aznfs_shape() -> None:
    driver, mgmt, secrets, _ = _driver(
        default_protocol="NFS",
        default_sku="Premium_ZRS",
        default_access_tier="Premium",
    )
    handle = _provisioned(driver, root_squash="AllSquash", encryption_in_transit_required=False)
    _, account, share = handle.split("/")
    request = mgmt.storage_accounts.create_calls[0]
    assert request.kind.value == "FileStorage"
    assert request.allow_shared_key_access is False
    file_share = mgmt.file_shares.create_calls[0]
    assert file_share.enabled_protocols.value == "NFS"
    assert file_share.access_tier.value == "Premium"
    assert file_share.root_squash.value == "AllSquash"
    assert secrets.values == {}

    binding = driver.binding(ServiceHandle(handle))
    assert binding.env_vars["FILESYSTEM_SOURCE"].literal == (f"{account}.file.core.windows.net:/{account}/{share}")
    assert binding.env_vars["FILESYSTEM_PROTOCOL"].literal == "nfs4.1"
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "false"
    assert "notls" in str(binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal)
    assert "FILESYSTEM_PASSWORD" not in binding.env_vars
    assert binding.iam_grants == []
    assert set(envelope_keys_for("filesystem")) <= set(binding.env_vars)

    volume = binding.pod_volume_mounts[0]
    assert volume.csi_driver == "file.csi.azure.com"
    assert volume.volume_handle == f"{RESOURCE_GROUP}#{account}#{share}"
    assert volume.volume_attributes == {
        "shareName": share,
        "protocol": "nfs",
        "server": f"{account}.file.core.windows.net",
    }
    # The fstab-shaped list stays in FILESYSTEM_MOUNT_OPTIONS; the CSI mount
    # only keeps what file.csi.azure.com does not own itself.
    assert volume.mount_options == ["nconnect=4"]
    # Network-authorized, so the CSI attachment references no secret at all.
    assert volume.secret_refs == {}
    assert volume.secret_literals == {}
    assert volume.capacity == "512Gi"


def test_binding_refuses_a_share_without_a_usable_quota() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(driver)
    _, account, share = handle.split("/")
    stored = mgmt.file_shares.values[(account, share)]

    stored.share_quota = 0
    with pytest.raises(AzureFilesClassicError, match="invalid quota"):
        driver.binding(ServiceHandle(handle))

    stored.share_quota = "unlimited"
    with pytest.raises(AzureFilesClassicError, match="invalid quota"):
        driver.binding(ServiceHandle(handle))


def test_provision_is_idempotent_and_refuses_account_or_share_collision() -> None:
    driver, mgmt, _, _ = _driver()
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    assert len(mgmt.storage_accounts.create_calls) == 1
    assert len(mgmt.file_shares.create_calls) == 1

    _, account, share = first.handle.split("/")
    mgmt.storage_accounts.values[account].tags["astrolift-managed-service-id"] = "foreign"
    rejected = driver.provision(_spec())
    assert not rejected.ok and "another managed service" in rejected.message
    mgmt.storage_accounts.values[account].tags["astrolift-managed-service-id"] = "service-id"
    mgmt.file_shares.values[(account, share)].metadata["astrolift_managed_service_id"] = "foreign"
    rejected = driver.provision(_spec())
    assert not rejected.ok and "another managed service" in rejected.message


def test_partial_update_changes_network_quota_tier_and_encryption() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(driver)
    updated = driver.update(
        UpdateSpec(
            handle,
            size="large",
            config={
                "access_tier": "Cool",
                "allow_public_access": True,
                "encryption_in_transit_required": False,
            },
        ),
    )
    assert updated.ok
    share = mgmt.file_shares.update_calls[-1]
    assert share.share_quota == 2048
    assert share.access_tier.value == "Cool"
    assert share.file_share_paid_bursting is None
    assert mgmt.storage_accounts.update_calls[-1].network_rule_set.default_action == "Allow"
    assert mgmt.file_services.set_calls[-1].protocol_settings.smb.encryption_in_transit.required is False


def test_snapshot_uses_ephemeral_account_key_and_requires_ownership() -> None:
    driver, mgmt, _, share_factory = _driver()
    handle = _provisioned(driver)
    snapshot = driver.snapshot(ServiceHandle(handle))
    assert snapshot.snapshot_id == "2026-08-14T15:00:00.0000000Z"
    _, account, share = handle.split("/")
    assert share_factory.calls == [(account, share, f"{account}-primary")]
    assert share_factory.client.snapshots == [{"astrolift_managed_by": "platform"}]

    mgmt.file_shares.values[(account, share)].metadata.clear()
    with pytest.raises(AzureFilesClassicError, match="not owned"):
        driver.snapshot(ServiceHandle(handle))


def test_nfs_snapshot_uses_control_plane_identity_not_disabled_shared_key() -> None:
    driver, _, _, share_factory = _driver(
        default_protocol="NFS",
        default_sku="Premium_LRS",
        default_access_tier="Premium",
    )
    handle = _provisioned(driver, paid_bursting=True)
    snapshot = driver.snapshot(ServiceHandle(handle))
    assert snapshot.snapshot_id == "2026-08-14T15:00:00.0000000Z"
    _, account, share = handle.split("/")
    assert share_factory.calls == [(account, share, None)]


def test_deprovision_guards_data_parent_account_and_secrets() -> None:
    driver, mgmt, secrets, _ = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    retained = driver.deprovision(DeprovisionSpec(handle))
    assert not retained.ok and retained.errors == ["retained_filesystem_data_requires_delete_data"]

    removed = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert removed.ok
    assert mgmt.file_shares.delete_calls[-1][2] == "snapshots"
    assert len(secrets.deleted) == 2
    _, account, _ = handle.split("/")
    assert account in mgmt.storage_accounts.values

    nfs, nfs_mgmt, _, _ = _driver(
        default_protocol="NFS",
        default_sku="Premium_LRS",
        default_access_tier="Premium",
        deletion_protection_default=False,
    )
    nfs_handle = _provisioned(nfs)
    deleted = nfs.deprovision(
        DeprovisionSpec(nfs_handle, config={"delete_storage_account": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert deleted.ok
    _, nfs_account, _ = nfs_handle.split("/")
    assert nfs_mgmt.storage_accounts.delete_calls == [nfs_account]


def test_deprovision_retry_finishes_secret_and_parent_cleanup_after_share_is_gone() -> None:
    driver, mgmt, secrets, _ = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    _, account, share = handle.split("/")
    mgmt.file_shares.values.pop((account, share))

    retried = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert retried.ok
    assert secrets.values == {}
    assert len(secrets.deleted) == 2

    nfs, nfs_mgmt, _, _ = _driver(
        default_protocol="NFS",
        default_sku="Premium_LRS",
        default_access_tier="Premium",
        deletion_protection_default=False,
    )
    nfs_handle = _provisioned(nfs)
    _, nfs_account, nfs_share = nfs_handle.split("/")
    nfs_mgmt.file_shares.values.pop((nfs_account, nfs_share))
    parent_retry = nfs.deprovision(
        DeprovisionSpec(nfs_handle, config={"delete_storage_account": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert parent_retry.ok
    assert nfs_mgmt.storage_accounts.delete_calls == [nfs_account]


def test_storagev2_parent_account_delete_is_refused_even_with_force() -> None:
    driver, mgmt, _, _ = _driver(deletion_protection_default=False)
    handle = _provisioned(driver)
    result = driver.deprovision(
        DeprovisionSpec(handle, config={"delete_storage_account": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok
    assert "non-file resources" in result.message
    _, account, share = handle.split("/")
    assert account in mgmt.storage_accounts.values
    assert (account, share) in mgmt.file_shares.values


def test_filestorage_parent_delete_refuses_another_share_before_mutation() -> None:
    driver, mgmt, _, _ = _driver(
        default_protocol="NFS",
        default_sku="Premium_LRS",
        default_access_tier="Premium",
        deletion_protection_default=False,
    )
    handle = _provisioned(driver)
    _, account, share = handle.split("/")
    foreign = SimpleNamespace(
        name="operator-share",
        enabled_protocols="NFS",
        metadata={"astrolift_managed_by": "operator"},
        deleted=False,
    )
    mgmt.file_shares.values[(account, "operator-share")] = foreign
    result = driver.deprovision(
        DeprovisionSpec(handle, config={"delete_storage_account": True}),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok and "other shares" in result.message
    assert (account, share) in mgmt.file_shares.values
    assert mgmt.file_shares.delete_calls == []


def test_deletion_protection_and_unowned_resources_fail_before_mutation() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(driver)
    protected = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    _, account, _ = handle.split("/")
    mgmt.storage_accounts.values[account].tags.clear()
    foreign = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert not foreign.ok and foreign.errors == ["external_resource_collision"]
    assert mgmt.file_shares.delete_calls == []


def test_status_binding_missing_restore_and_handle_errors_are_honest() -> None:
    driver, _, _, _ = _driver()
    handle = _provisioned(driver)
    assert driver.status(ServiceHandle(handle)).state == "available"
    assert driver.status(ServiceHandle("filesystem/bad")).state == "error"
    restore = driver.restore(
        SnapshotHandle(handle, "snapshot", NOW.isoformat()),
        _spec(),
    )
    assert not restore.ok and restore.errors == ["restore_not_supported"]
    with pytest.raises(AzureFilesClassicError, match="handle"):
        driver.binding(ServiceHandle("filesystem/bad"))


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"protocol": "FTP"}, "protocol"),
        ({"protocol": "NFS", "sku": "Standard_LRS"}, "requires Premium"),
        ({"sku": "Premium_LRS", "access_tier": "Hot"}, "requires access_tier=Premium"),
        ({"sku": "Premium_LRS", "access_tier": "Premium", "quota_gib": 99}, "at least 100"),
        ({"paid_bursting": True}, "requires a Premium SKU"),
        ({"quota_gib": 0}, "quota_gib"),
        ({"allowed_subnet_ids": [], "allow_public_access": False}, "requires allowed_subnet_ids"),
        ({"mount_path": "relative"}, "absolute"),
        ({"unknown": True}, "unsupported"),
    ],
)
def test_invalid_configs_fail_before_cloud_mutation(config: dict[str, Any], message: str) -> None:
    driver, mgmt, _, _ = _driver()
    result = driver.provision(replace(_spec(), config=config))
    assert not result.ok and message in result.message
    assert mgmt.storage_accounts.create_calls == []


def test_smb_requires_keyvault_and_config_constructor_validates_defaults() -> None:
    mgmt = FakeMgmt()
    driver = AzureFilesClassicDriver(
        config=AzureFilesClassicConfig(
            SUBSCRIPTION_ID,
            RESOURCE_GROUP,
            allowed_subnet_ids=(SUBNET_ID,),
            mgmt_client=mgmt,
        ),
    )
    result = driver.provision(_spec())
    assert not result.ok and result.errors == ["no_secret_backend"]
    with pytest.raises(ValueError, match="requires Premium"):
        AzureFilesClassicConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_protocol="NFS")
    with pytest.raises(ValueError, match="prefix"):
        AzureFilesClassicConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, account_name_prefix="bad-prefix")
    with pytest.raises(ValueError, match="secret_name_prefix"):
        AzureFilesClassicConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, secret_name_prefix="bad/prefix")


def test_catalogue_schema_and_binding_contract_are_executable() -> None:
    from _sdk.availability import MATRIX

    entry = next(
        item for item in MATRIX.managed_services if item.plugin_id == "azure" and item.variant == "azure_files_classic"
    )
    assert entry.status == "preview"
    assert {"FILESYSTEM_SOURCE", "AZURE_STORAGE_ACCOUNT", "FILESYSTEM_PASSWORD"} <= set(entry.binding_envs)
    driver, _, _, _ = _driver()
    assert driver.config_schema()["properties"]["protocol"]["enum"] == ["SMB", "NFS"]
    assert "FILESYSTEM_PASSWORD_SECONDARY" in driver.binding_schema().env_vars
    assert "protocol" not in driver.editable_fields()
