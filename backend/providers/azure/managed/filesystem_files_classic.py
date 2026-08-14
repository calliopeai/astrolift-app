"""Classic Azure Files (``Microsoft.Storage``) SMB/NFS lifecycle.

The top-level ``Microsoft.FileShares`` driver is the simple NFS-only path.
This variant owns a storage account plus one classic file share so workloads
can choose SMB or NFS and the older HDD/SSD billing and redundancy families.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
    VolumeMount,
)
from azure.managed.filesystem_files import _field, _not_found, _slug, _string_value, _tag_error, _tags

KIND = "filesystem"
VARIANT = "azure_files_classic"

_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
_SHARE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]$")
_STANDARD_SKUS = {
    "Standard_LRS",
    "Standard_GRS",
    "Standard_RAGRS",
    "Standard_ZRS",
    "Standard_GZRS",
    "Standard_RAGZRS",
}
_PREMIUM_SKUS = {"Premium_LRS", "Premium_ZRS"}
_SKUS = _STANDARD_SKUS | _PREMIUM_SKUS
_STANDARD_TIERS = {"TransactionOptimized", "Hot", "Cool"}
_SIZE_GIB = {"small": 100, "medium": 512, "large": 2048, "xlarge": 8192}
_MANAGED_BY_TAG = "astrolift-managed-by"
_MANAGED_SERVICE_ID_TAG = "astrolift-managed-service-id"
_META_MANAGED_BY = "astrolift_managed_by"
_META_MANAGED_SERVICE_ID = "astrolift_managed_service_id"


class AzureFilesClassicError(RuntimeError):
    """Classic Azure Files lifecycle or ownership failure."""


@dataclass(frozen=True)
class AzureFilesClassicConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    account_name_prefix: str = "astroliftfs"
    share_name_prefix: str = "astrolift-files"
    default_protocol: str = "SMB"
    default_sku: str = "Standard_LRS"
    default_quota_gib: int = 100
    default_access_tier: str = "TransactionOptimized"
    default_root_squash: str = "RootSquash"
    encryption_in_transit_required_default: bool = True
    allowed_subnet_ids: tuple[str, ...] = ()
    allow_public_access_default: bool = False
    soft_delete_retention_days: int = 14
    deletion_protection_default: bool = True
    keyvault_url: str = ""
    secret_name_prefix: str = "astrolift-files"
    mgmt_client: Any | None = None
    secret_client: Any | None = None
    share_client_factory: Any | None = None

    def __post_init__(self) -> None:
        _validate_account_prefix(self.account_name_prefix)
        _validate_share_name(self.share_name_prefix, "share_name_prefix", allow_short=True)
        if self.default_protocol not in {"SMB", "NFS"}:
            raise ValueError("classic Azure Files default protocol must be SMB or NFS")
        if self.default_sku not in _SKUS:
            raise ValueError("unsupported classic Azure Files default SKU")
        if self.default_protocol == "NFS" and self.default_sku not in _PREMIUM_SKUS:
            raise ValueError("classic NFS requires Premium_LRS or Premium_ZRS")
        if not 1 <= self.default_quota_gib <= 102_400:
            raise ValueError("classic Azure Files default quota must be 1-102400 GiB")
        if self.default_access_tier not in _STANDARD_TIERS | {"Premium"}:
            raise ValueError("unsupported classic Azure Files access tier")
        if self.default_sku in _PREMIUM_SKUS and self.default_access_tier != "Premium":
            raise ValueError("classic premium Azure Files requires default_access_tier=Premium")
        if self.default_sku in _STANDARD_SKUS and self.default_access_tier not in _STANDARD_TIERS:
            raise ValueError("classic standard Azure Files does not support the Premium access tier")
        if self.default_sku in _PREMIUM_SKUS and self.default_quota_gib < 100:
            raise ValueError("classic premium Azure Files default quota must be at least 100 GiB")
        if self.default_root_squash not in {"NoRootSquash", "RootSquash", "AllSquash"}:
            raise ValueError("unsupported classic Azure Files root squash")
        if not 1 <= self.soft_delete_retention_days <= 365:
            raise ValueError("classic Azure Files soft-delete retention must be 1-365 days")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,92}", self.secret_name_prefix):
            raise ValueError(
                "classic Azure Files secret_name_prefix must be 1-92 letters, numbers, or hyphens",
            )
        for subnet_id in self.allowed_subnet_ids:
            _validate_subnet_id(subnet_id)


class AzureFilesClassicDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureFilesClassicConfig, now: Any | None = None) -> None:
        self._config = config
        self._now = now or (lambda: datetime.now(UTC))
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.storage import StorageManagementClient

            self._mgmt = StorageManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.secret_client is not None:
            self._secrets = config.secret_client
        elif config.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=config.keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None
        self._share_client_factory = config.share_client_factory or self._production_share_client

    @driver_op(
        cloud="azure",
        driver="filesystem_files_classic",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, size=spec.size) or _tag_error(spec.tags)
        if error:
            return ProvisionResult(False, "", error, ["invalid_azure_files_classic_config"])
        protocol = self._protocol(cfg)
        if protocol == "SMB" and self._secrets is None:
            return ProvisionResult(
                False,
                "",
                "classic Azure Files SMB requires Key Vault for account-key rotation",
                ["no_secret_backend"],
            )
        account_name, share_name = self._names(spec)
        handle = self._handle(account_name, share_name)
        try:
            account = self._get_account(account_name)
            if account is None:
                account = self._wait(
                    self._mgmt.storage_accounts.begin_create(
                        self._config.resource_group,
                        account_name,
                        self._account_create_parameters(spec, cfg),
                    ),
                )
            else:
                self._assert_account_owned(account, spec)
                self._assert_account_immutable(account, cfg)
                account = self._mgmt.storage_accounts.update(
                    self._config.resource_group,
                    account_name,
                    self._account_update_parameters(spec, cfg),
                )
            self._reconcile_file_service(account_name, cfg)
            share = self._get_share(account_name, share_name)
            if share is None:
                share = self._mgmt.file_shares.create(
                    self._config.resource_group,
                    account_name,
                    share_name,
                    self._share_parameters(spec, cfg),
                )
            else:
                self._assert_share_owned(share, spec)
                self._assert_share_immutable(share, cfg)
                share = self._mgmt.file_shares.update(
                    self._config.resource_group,
                    account_name,
                    share_name,
                    self._share_parameters(spec, cfg),
                )
            if protocol == "SMB":
                self._store_account_keys(account_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile classic Azure Files: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"classic Azure Files {protocol} share {account_name}/{share_name} reconciled",
            ready=True,
        )

    @driver_op(cloud="azure", driver="filesystem_files_classic")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            account_name, share_name = self._parse_handle(spec.handle)
        except AzureFilesClassicError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate(cfg, size=spec.size, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_azure_files_classic_config"])
        try:
            account = self._get_account(account_name)
            share = self._get_share(account_name, share_name)
            if account is None or share is None:
                return UpdateResult(False, spec.handle, "classic Azure Files share not found", ["not_found"])
            self._assert_platform_account(account)
            self._assert_platform_share(share)
            self._assert_account_immutable(account, cfg, partial=True)
            self._assert_share_immutable(share, cfg, partial=True)
            if {"allowed_subnet_ids", "allow_public_access"} & set(cfg):
                self._mgmt.storage_accounts.update(
                    self._config.resource_group,
                    account_name,
                    self._account_update_parameters(None, cfg),
                )
            if {"quota_gib", "access_tier", "root_squash", "paid_bursting"} & set(cfg) or spec.size:
                self._mgmt.file_shares.update(
                    self._config.resource_group,
                    account_name,
                    share_name,
                    self._share_parameters(None, cfg, size=spec.size, partial=True),
                )
            if "encryption_in_transit_required" in cfg:
                self._reconcile_file_service(account_name, cfg)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update classic Azure Files: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"classic Azure Files share {account_name}/{share_name} updated")

    @driver_op(
        cloud="azure",
        driver="filesystem_files_classic",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        try:
            account_name, share_name = self._parse_handle(spec.handle)
        except AzureFilesClassicError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        try:
            account = self._get_account(account_name)
            share = self._get_share(account_name, share_name) if account is not None else None
            if account is None:
                return DeprovisionResult(True, spec.handle, "classic Azure Files share already gone")
            self._assert_platform_account(account)
            if share is not None:
                self._assert_platform_share(share)
        except AzureFilesClassicError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["external_resource_collision"], retryable=False)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"describe classic Azure Files: {exc}", [str(exc)])
        cfg = dict(spec.config or {})
        delete_account = bool(cfg.get("delete_storage_account", False))
        if share is None:
            try:
                self._delete_key_secrets(account_name)
                if delete_account:
                    if not force_destroy:
                        return DeprovisionResult(
                            False,
                            spec.handle,
                            "deleting the parent storage account requires force_destroy=true",
                            ["storage_account_delete_requires_force"],
                            retryable=False,
                        )
                    self._assert_parent_deletable(account, account_name, share_name)
                    self._delete_empty_filestorage_account(account_name)
            except Exception as exc:
                if _not_found(exc):
                    return DeprovisionResult(True, spec.handle, "classic Azure Files deletion converged")
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"finish deleting classic Azure Files: {exc}",
                    [str(exc)],
                )
            return DeprovisionResult(True, spec.handle, "classic Azure Files deletion converged")
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "classic Azure Files deletion protection is enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "classic Azure Files snapshots are children of the share; preserve/copy it, then set delete_data=true",
                ["retained_filesystem_data_requires_delete_data"],
                retryable=False,
            )
        try:
            if delete_account:
                if not force_destroy:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        "deleting the parent storage account requires force_destroy=true",
                        ["storage_account_delete_requires_force"],
                        retryable=False,
                    )
                self._assert_parent_deletable(account, account_name, share_name)
            self._mgmt.file_shares.delete(
                self._config.resource_group,
                account_name,
                share_name,
                include="snapshots",
            )
            self._delete_key_secrets(account_name)
            if delete_account:
                self._delete_empty_filestorage_account(account_name)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "classic Azure Files deletion converged")
            return DeprovisionResult(False, spec.handle, f"delete classic Azure Files: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"classic Azure Files share {account_name}/{share_name} deleted")

    @driver_op(cloud="azure", driver="filesystem_files_classic")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            account_name, share_name = self._parse_handle(handle.handle)
            account = self._get_account(account_name)
            share = self._get_share(account_name, share_name) if account is not None else None
            if account is None or share is None:
                return ServiceStatus(handle.handle, "deprovisioned", "classic Azure Files share does not exist")
            state = str(_field(account, "provisioning_state", default="Unknown"))
            if state == "Succeeded":
                return ServiceStatus(handle.handle, "available", f"Azure reports {state}")
            if state in {"Creating", "ResolvingDNS", "Provisioning"}:
                return ServiceStatus(handle.handle, "provisioning", f"Azure reports {state}")
            if state in {"Failed", "Canceled"}:
                return ServiceStatus(handle.handle, "error", f"Azure reports {state}")
            return ServiceStatus(handle.handle, "updating", f"Azure reports {state}")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe classic Azure Files: {exc}")

    @driver_op(cloud="azure", driver="filesystem_files_classic")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        account_name, share_name = self._parse_handle(handle.handle)
        account = self._get_account(account_name)
        share = self._get_share(account_name, share_name)
        if account is None or share is None:
            raise AzureFilesClassicError("binding requested for missing classic Azure Files share")
        self._assert_platform_account(account)
        self._assert_platform_share(share)
        cfg = config or {}
        protocol = _string_value(_field(share, "enabled_protocols", default=self._protocol(cfg))).upper()
        mount_path = str(cfg.get("mount_path", "/mnt/shared"))
        if not mount_path.startswith("/"):
            raise AzureFilesClassicError("classic Azure Files mount_path must be absolute")
        endpoint = self._file_endpoint(account, account_name)
        hostname = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        read_only = bool(cfg.get("read_only", False))
        env_vars: dict[str, ValueRef] = {
            "FILESYSTEM_HANDLE": ValueRef(literal=self._resource_id(account_name, share_name)),
            "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
            "FILESYSTEM_PROTOCOL": ValueRef(literal=("nfs4.1" if protocol == "NFS" else "smb3.1.1")),
            "FILESYSTEM_ENDPOINT": ValueRef(literal=hostname),
            "FILESYSTEM_READ_ONLY": ValueRef(literal=str(read_only).lower()),
            "AZURE_STORAGE_ACCOUNT": ValueRef(literal=account_name),
            "AZURE_FILE_SHARE_NAME": ValueRef(literal=share_name),
            "AZURE_RESOURCE_GROUP": ValueRef(literal=self._config.resource_group),
            "AZURE_LOCATION": ValueRef(literal=str(_field(account, "location", default=self._config.location))),
        }
        grants: list[Grant] = []
        options: list[str]
        if protocol == "NFS":
            export_path = f"/{account_name}/{share_name}"
            source = f"{hostname}:{export_path}"
            encrypted = self._encryption_required(account_name, "NFS")
            options = [
                "sec=sys",
                "vers=4",
                "minorversion=1",
                "nolock",
                "proto=tcp",
                "nofail",
                "_netdev",
                "nconnect=4",
            ]
            if not encrypted:
                options.append("notls")
            env_vars["FILESYSTEM_EXPORT_PATH"] = ValueRef(literal=export_path)
            env_vars["FILESYSTEM_TLS"] = ValueRef(literal=str(encrypted).lower())
        else:
            source = f"//{hostname}/{share_name}"
            options = ["vers=3.1.1", "sec=ntlmssp", "serverino", "nosharesock", "mfsymlinks", "actimeo=30"]
            env_vars["FILESYSTEM_USERNAME"] = ValueRef(literal=account_name)
            env_vars["FILESYSTEM_PASSWORD"] = ValueRef(secret_ref=self._key_secret(account_name, "primary"))
            env_vars["FILESYSTEM_PASSWORD_SECONDARY"] = ValueRef(
                secret_ref=self._key_secret(account_name, "secondary"),
            )
            grants.extend(
                [
                    Grant(
                        resource=self._key_secret(account_name, which),
                        actions=["Microsoft.KeyVault/vaults/secrets/getSecret"],
                    )
                    for which in ("primary", "secondary")
                ],
            )
            env_vars["FILESYSTEM_TLS"] = ValueRef(literal=str(self._encryption_required(account_name, "SMB")).lower())
        if read_only:
            options.append("ro")
        for option in cfg.get("mount_options", []) or []:
            value = str(option)
            if value not in options:
                options.append(value)
        env_vars["FILESYSTEM_SOURCE"] = ValueRef(literal=source)
        env_vars["FILESYSTEM_MOUNT_OPTIONS"] = ValueRef(literal=",".join(options))
        return Binding(
            env_vars=env_vars,
            pod_volume_mounts=[VolumeMount(name=_slug(share_name), mount_path=mount_path)],
            iam_grants=grants,
            notes=(
                "Classic Microsoft.Storage Azure Files share. SMB credentials are rotating Key Vault refs; "
                "NFS is network-authorized and encrypted mounts require AZNFS."
            ),
        )

    @driver_op(
        cloud="azure",
        driver="filesystem_files_classic",
        audit=True,
        sensitive_kind="managed_service_snapshot",
    )
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        account_name, share_name = self._parse_handle(handle.handle)
        account = self._get_account(account_name)
        share = self._get_share(account_name, share_name)
        if account is None or share is None:
            raise AzureFilesClassicError("snapshot requested for missing classic Azure Files share")
        self._assert_platform_account(account)
        self._assert_platform_share(share)
        protocol = _string_value(_field(share, "enabled_protocols", default="SMB")).upper()
        credential = self._account_keys(account_name)[0] if protocol == "SMB" else None
        result = self._share_client_factory(account_name, share_name, credential).create_snapshot(
            metadata={_META_MANAGED_BY: "platform"},
        )
        created = self._now()
        snapshot_id = str(result.get("snapshot") or result.get("x-ms-snapshot") or "")
        if not snapshot_id:
            raise AzureFilesClassicError("Azure Files snapshot response omitted snapshot identity")
        return SnapshotHandle(handle.handle, snapshot_id, created.isoformat())

    @driver_op(
        cloud="azure",
        driver="filesystem_files_classic",
        audit=True,
        sensitive_kind="managed_service_restore",
    )
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "classic Azure Files share snapshots do not provide an atomic portable clone operation",
            ["restore_not_supported"],
        )

    @driver_op(cloud="azure", driver="filesystem_files_classic", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "pattern": _ACCOUNT_RE.pattern},
                "share_name": {"type": "string", "pattern": _SHARE_RE.pattern},
                "protocol": {"type": "string", "enum": ["SMB", "NFS"], "default": "SMB"},
                "sku": {"type": "string", "enum": sorted(_SKUS), "default": "Standard_LRS"},
                "quota_gib": {"type": "integer", "minimum": 1, "maximum": 102400},
                "access_tier": {
                    "type": "string",
                    "enum": ["TransactionOptimized", "Hot", "Cool", "Premium"],
                },
                "root_squash": {
                    "type": "string",
                    "enum": ["NoRootSquash", "RootSquash", "AllSquash"],
                },
                "encryption_in_transit_required": {"type": "boolean", "default": True},
                "allowed_subnet_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                "allow_public_access": {"type": "boolean", "default": False},
                "paid_bursting": {"type": "boolean"},
                "mount_path": {"type": "string", "pattern": "^/"},
                "mount_options": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                "read_only": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "delete_storage_account": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="azure", driver="filesystem_files_classic", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Azure file-share resource ID",
                "FILESYSTEM_MOUNT_PATH": "Container mount path",
                "FILESYSTEM_TLS": "Whether transport encryption is required",
                "FILESYSTEM_PROTOCOL": "nfs4.1 or smb3.1.1",
                "FILESYSTEM_ENDPOINT": "Azure Files hostname",
                "FILESYSTEM_EXPORT_PATH": "NFS export path when protocol=NFS",
                "FILESYSTEM_SOURCE": "NFS or SMB mount source",
                "FILESYSTEM_MOUNT_OPTIONS": "Comma-separated mount options",
                "FILESYSTEM_READ_ONLY": "Whether the workload mount is read-only",
                "FILESYSTEM_USERNAME": "SMB storage-account username",
                "FILESYSTEM_PASSWORD": "SMB primary account-key secret ref",
                "FILESYSTEM_PASSWORD_SECONDARY": "SMB secondary account-key secret ref",
                "AZURE_STORAGE_ACCOUNT": "Parent storage account",
                "AZURE_FILE_SHARE_NAME": "Classic file-share name",
                "AZURE_RESOURCE_GROUP": "Azure resource group",
                "AZURE_LOCATION": "Azure region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "quota_gib",
            "access_tier",
            "root_squash",
            "encryption_in_transit_required",
            "allowed_subnet_ids",
            "allow_public_access",
            "paid_bursting",
            "mount_path",
            "mount_options",
            "read_only",
            "deletion_protection",
            "delete_storage_account",
        ]

    def _validate(self, cfg: dict[str, Any], *, size: str | None, partial: bool = False) -> str | None:
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported classic Azure Files config fields: {', '.join(unknown)}"
        try:
            if "account_name" in cfg:
                _validate_account_name(str(cfg["account_name"]))
            if "share_name" in cfg:
                _validate_share_name(str(cfg["share_name"]), "share_name")
            for subnet_id in cfg.get("allowed_subnet_ids", ()) or ():
                _validate_subnet_id(str(subnet_id))
        except ValueError as exc:
            return str(exc)
        if size and size not in {*_SIZE_GIB, "custom"}:
            return f"unsupported classic Azure Files size {size!r}"
        if size == "custom" and "quota_gib" not in cfg:
            return "classic Azure Files custom size requires quota_gib"
        quota = cfg.get("quota_gib")
        if quota is not None and (not isinstance(quota, int) or not 1 <= quota <= 102_400):
            return "classic Azure Files quota_gib must be 1-102400"
        protocol = str(cfg.get("protocol", self._config.default_protocol))
        sku = str(cfg.get("sku", self._config.default_sku))
        tier = str(cfg.get("access_tier", self._config.default_access_tier))
        if protocol not in {"SMB", "NFS"}:
            return "classic Azure Files protocol must be SMB or NFS"
        if sku not in _SKUS:
            return f"unsupported classic Azure Files SKU {sku!r}"
        if protocol == "NFS" and sku not in _PREMIUM_SKUS:
            return "classic Azure Files NFS requires Premium_LRS or Premium_ZRS"
        if sku in _PREMIUM_SKUS and tier != "Premium":
            return "classic premium Azure Files requires access_tier=Premium"
        effective_quota = int(quota) if quota is not None else self._quota(cfg, size)
        if sku in _PREMIUM_SKUS and effective_quota < 100:
            return "classic premium Azure Files quota_gib must be at least 100"
        if sku in _STANDARD_SKUS and tier not in _STANDARD_TIERS:
            return "classic standard Azure Files access tier must be TransactionOptimized, Hot, or Cool"
        if bool(cfg.get("paid_bursting", False)) and sku not in _PREMIUM_SKUS:
            return "classic Azure Files paid_bursting requires a Premium SKU"
        if protocol == "NFS" and str(cfg.get("root_squash", self._config.default_root_squash)) not in {
            "NoRootSquash",
            "RootSquash",
            "AllSquash",
        }:
            return "unsupported classic Azure Files root_squash"
        subnets = tuple(str(value) for value in cfg.get("allowed_subnet_ids", self._config.allowed_subnet_ids) or ())
        allow_public = bool(cfg.get("allow_public_access", self._config.allow_public_access_default))
        if not partial or {"allowed_subnet_ids", "allow_public_access"} & set(cfg):
            if not subnets and not allow_public:
                return "classic Azure Files requires allowed_subnet_ids unless allow_public_access=true"
            if len(set(subnets)) != len(subnets):
                return "classic Azure Files allowed_subnet_ids must be unique"
        mount_path = cfg.get("mount_path")
        if mount_path is not None and (not isinstance(mount_path, str) or not mount_path.startswith("/")):
            return "classic Azure Files mount_path must be absolute"
        options = cfg.get("mount_options")
        if options is not None and (
            not isinstance(options, list) or any(not str(value) or "," in str(value) for value in options)
        ):
            return "classic Azure Files mount_options must be non-empty comma-free strings"
        return None

    def _account_create_parameters(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.storage import models

        protocol = self._protocol(cfg)
        sku = self._sku(cfg)
        tags = _tags(spec)
        if len(tags) > 15 or any(len(key) > 128 for key in tags):
            raise AzureFilesClassicError("storage accounts support at most 15 tags with 128-character names")
        return models.StorageAccountCreateParameters(
            sku=models.Sku(name=sku),
            kind=("FileStorage" if sku in _PREMIUM_SKUS else "StorageV2"),
            location=self._config.location,
            tags=tags,
            properties=models.StorageAccountPropertiesCreateParameters(
                enable_https_traffic_only=True,
                minimum_tls_version="TLS1_2",
                allow_blob_public_access=False,
                allow_cross_tenant_replication=False,
                allow_shared_key_access=(protocol == "SMB"),
                large_file_shares_state="Enabled",
                public_network_access="Enabled",
                network_rule_set=self._network_rules(cfg),
            ),
        )

    def _account_update_parameters(self, spec: ProvisionSpec | None, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.storage import models

        kwargs: dict[str, Any] = {}
        if spec is not None:
            kwargs["tags"] = _tags(spec)
        return models.StorageAccountUpdateParameters(
            properties=models.StorageAccountPropertiesUpdateParameters(
                network_rule_set=self._network_rules(cfg),
                public_network_access="Enabled",
            ),
            **kwargs,
        )

    def _share_parameters(
        self,
        spec: ProvisionSpec | None,
        cfg: dict[str, Any],
        *,
        size: str | None = None,
        partial: bool = False,
    ) -> Any:
        from azure.mgmt.storage import models

        metadata = None
        if spec is not None:
            metadata = {
                _META_MANAGED_BY: "platform",
                _META_MANAGED_SERVICE_ID: spec.managed_service_id,
                "astrolift_binding": spec.binding_id,
            }
        kwargs: dict[str, Any] = {"metadata": metadata}
        if not partial or "quota_gib" in cfg or size:
            kwargs["share_quota"] = self._quota(cfg, size or (spec.size if spec else None))
        if not partial or "protocol" in cfg:
            kwargs["enabled_protocols"] = self._protocol(cfg)
        if not partial or "access_tier" in cfg:
            kwargs["access_tier"] = self._tier(cfg)
        if self._protocol(cfg) == "NFS" and (not partial or "root_squash" in cfg):
            kwargs["root_squash"] = str(cfg.get("root_squash", self._config.default_root_squash))
        if "paid_bursting" in cfg:
            kwargs["file_share_paid_bursting"] = models.FileSharePropertiesFileSharePaidBursting(
                paid_bursting_enabled=bool(cfg["paid_bursting"]),
            )
        return models.FileShare(**kwargs)

    def _reconcile_file_service(self, account_name: str, cfg: dict[str, Any]) -> None:
        from azure.mgmt.storage import models

        required = bool(
            cfg.get("encryption_in_transit_required", self._config.encryption_in_transit_required_default),
        )
        protocol_settings = models.ProtocolSettings(
            smb=models.SmbSetting(
                versions="SMB3.0;SMB3.1.1",
                authentication_methods="NTLMv2;Kerberos",
                channel_encryption="AES-128-GCM;AES-256-GCM",
                encryption_in_transit=models.EncryptionInTransit(required=required),
            ),
            nfs=models.NfsSetting(
                encryption_in_transit=models.EncryptionInTransit(required=required),
            ),
        )
        self._mgmt.file_services.set_service_properties(
            self._config.resource_group,
            account_name,
            models.FileServiceProperties(
                file_service_properties=models.FileServicePropertiesProperties(
                    protocol_settings=protocol_settings,
                    share_delete_retention_policy=models.DeleteRetentionPolicy(
                        enabled=True,
                        days=self._config.soft_delete_retention_days,
                    ),
                ),
            ),
        )

    def _network_rules(self, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.storage import models

        subnets = tuple(str(value) for value in cfg.get("allowed_subnet_ids", self._config.allowed_subnet_ids) or ())
        allow_public = bool(cfg.get("allow_public_access", self._config.allow_public_access_default))
        return models.NetworkRuleSet(
            bypass="AzureServices",
            default_action=("Allow" if allow_public else "Deny"),
            virtual_network_rules=[
                models.VirtualNetworkRule(virtual_network_resource_id=value, action="Allow") for value in subnets
            ],
        )

    def _assert_account_owned(self, account: Any, spec: ProvisionSpec) -> None:
        self._assert_platform_account(account)
        tags = dict(_field(account, "tags", default={}) or {})
        existing = str(tags.get(_MANAGED_SERVICE_ID_TAG, ""))
        if spec.managed_service_id and existing and existing != spec.managed_service_id:
            raise AzureFilesClassicError("classic Azure Files account belongs to another managed service")

    @staticmethod
    def _assert_platform_account(account: Any) -> None:
        tags = dict(_field(account, "tags", default={}) or {})
        if tags.get(_MANAGED_BY_TAG) != "platform":
            raise AzureFilesClassicError("classic Azure Files account is not owned by Astrolift")

    def _assert_share_owned(self, share: Any, spec: ProvisionSpec) -> None:
        self._assert_platform_share(share)
        metadata = dict(_field(share, "metadata", default={}) or {})
        existing = str(metadata.get(_META_MANAGED_SERVICE_ID, ""))
        if spec.managed_service_id and existing and existing != spec.managed_service_id:
            raise AzureFilesClassicError("classic Azure Files share belongs to another managed service")

    @staticmethod
    def _assert_platform_share(share: Any) -> None:
        metadata = dict(_field(share, "metadata", default={}) or {})
        if metadata.get(_META_MANAGED_BY) != "platform":
            raise AzureFilesClassicError("classic Azure Files share is not owned by Astrolift")

    def _assert_account_immutable(self, account: Any, cfg: dict[str, Any], *, partial: bool = False) -> None:
        expected_sku = self._sku(cfg)
        actual_sku = _string_value(_field(_field(account, "sku"), "name", default=""))
        actual_kind = _string_value(_field(account, "kind", default=""))
        expected_kind = "FileStorage" if expected_sku in _PREMIUM_SKUS else "StorageV2"
        if (not partial or "sku" in cfg) and actual_sku and actual_sku != expected_sku:
            raise AzureFilesClassicError(f"classic Azure Files SKU is immutable ({actual_sku} != {expected_sku})")
        if not partial and actual_kind and actual_kind != expected_kind:
            raise AzureFilesClassicError(
                f"classic Azure Files account kind is immutable ({actual_kind} != {expected_kind})"
            )

    def _assert_share_immutable(self, share: Any, cfg: dict[str, Any], *, partial: bool = False) -> None:
        if partial and "protocol" not in cfg:
            return
        actual = _string_value(_field(share, "enabled_protocols", default="")).upper()
        expected = self._protocol(cfg)
        if actual and actual != expected:
            raise AzureFilesClassicError(f"classic Azure Files protocol is immutable ({actual} != {expected})")

    def _get_account(self, account_name: str) -> Any | None:
        try:
            return self._mgmt.storage_accounts.get_properties(self._config.resource_group, account_name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _get_share(self, account_name: str, share_name: str) -> Any | None:
        try:
            return self._mgmt.file_shares.get(self._config.resource_group, account_name, share_name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _store_account_keys(self, account_name: str) -> None:
        if self._secrets is None:
            raise AzureFilesClassicError("SMB account keys require Key Vault")
        keys = self._account_keys(account_name)
        self._secrets.set_secret(self._key_secret(account_name, "primary"), keys[0])
        self._secrets.set_secret(self._key_secret(account_name, "secondary"), keys[1])

    def _account_keys(self, account_name: str) -> tuple[str, str]:
        response = self._mgmt.storage_accounts.list_keys(self._config.resource_group, account_name)
        values = [str(_field(value, "value", default="")) for value in (_field(response, "keys", default=[]) or [])]
        values = [value for value in values if value]
        if len(values) < 2:
            raise AzureFilesClassicError("Azure storage account did not return both rotation keys")
        return values[0], values[1]

    def _delete_key_secrets(self, account_name: str) -> None:
        if self._secrets is None:
            return
        for which in ("primary", "secondary"):
            try:
                self._secrets.begin_delete_secret(self._key_secret(account_name, which))
            except Exception as exc:
                if not _not_found(exc):
                    raise

    def _delete_empty_filestorage_account(self, account_name: str) -> None:
        account = self._get_account(account_name)
        if account is None:
            return
        shares = list(self._mgmt.file_shares.list(self._config.resource_group, account_name))
        live = [value for value in shares if not bool(_field(value, "deleted", default=False))]
        if live:
            raise AzureFilesClassicError("classic Azure Files parent account still contains file shares")
        self._mgmt.storage_accounts.delete(self._config.resource_group, account_name)

    def _assert_parent_deletable(self, account: Any, account_name: str, share_name: str) -> None:
        if _string_value(_field(account, "kind", default="")) != "FileStorage":
            raise AzureFilesClassicError(
                "StorageV2 accounts may contain non-file resources; parent account deletion is refused",
            )
        shares = list(self._mgmt.file_shares.list(self._config.resource_group, account_name))
        unexpected = [
            str(_field(value, "name", default=""))
            for value in shares
            if not bool(_field(value, "deleted", default=False))
            and str(_field(value, "name", default="")) != share_name
        ]
        if unexpected:
            raise AzureFilesClassicError(
                f"classic Azure Files parent account contains other shares: {', '.join(sorted(unexpected))}",
            )

    def _encryption_required(self, account_name: str, protocol: str) -> bool:
        properties = self._mgmt.file_services.get_service_properties(self._config.resource_group, account_name)
        settings = _field(properties, "protocol_settings")
        selected = _field(settings, protocol.lower())
        transit = _field(selected, "encryption_in_transit")
        return bool(_field(transit, "required", default=self._config.encryption_in_transit_required_default))

    def _names(self, spec: ProvisionSpec) -> tuple[str, str]:
        cfg = dict(spec.config or {})
        explicit_account = str(cfg.get("account_name", ""))
        explicit_share = str(cfg.get("share_name", ""))
        identity = "/".join(
            [
                self._config.subscription_id,
                self._config.resource_group,
                spec.organization_id,
                spec.app_id,
                spec.environment_id,
                spec.managed_service_id or spec.binding_id or spec.service_handle_hint,
            ],
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()
        account = explicit_account or f"{self._config.account_name_prefix}{digest[:10]}"[:24]
        share = (
            explicit_share or f"{_slug(self._config.share_name_prefix)}-{_slug(spec.service_handle_hint)}-{digest[:10]}"
        )
        share = share[:63].rstrip("-")
        _validate_account_name(account)
        _validate_share_name(share, "share name")
        return account, share

    def _protocol(self, cfg: dict[str, Any]) -> str:
        return str(cfg.get("protocol", self._config.default_protocol)).upper()

    def _sku(self, cfg: dict[str, Any]) -> str:
        return str(cfg.get("sku", self._config.default_sku))

    def _tier(self, cfg: dict[str, Any]) -> str:
        if self._sku(cfg) in _PREMIUM_SKUS:
            return str(cfg.get("access_tier", "Premium"))
        return str(cfg.get("access_tier", self._config.default_access_tier))

    def _quota(self, cfg: dict[str, Any], size: str | None) -> int:
        if "quota_gib" in cfg:
            return int(cfg["quota_gib"])
        if size in _SIZE_GIB:
            return _SIZE_GIB[str(size)]
        return self._config.default_quota_gib

    def _key_secret(self, account_name: str, which: str) -> str:
        return f"{self._config.secret_name_prefix}-{account_name}-{which}"

    def _file_endpoint(self, account: Any, account_name: str) -> str:
        endpoints = _field(account, "primary_endpoints")
        return str(_field(endpoints, "file", default=f"https://{account_name}.file.core.windows.net/"))

    def _resource_id(self, account_name: str, share_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Storage/storageAccounts/{account_name}/fileServices/default/shares/{share_name}"
        )

    @staticmethod
    def _handle(account_name: str, share_name: str) -> str:
        return f"filesystem/{account_name}/{share_name}"

    @staticmethod
    def _parse_handle(handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != "filesystem":
            raise AzureFilesClassicError("classic Azure Files handle must be filesystem/<account>/<share>")
        try:
            _validate_account_name(parts[1])
            _validate_share_name(parts[2], "share name")
        except ValueError as exc:
            raise AzureFilesClassicError(str(exc)) from exc
        return parts[1], parts[2]

    @staticmethod
    def _wait(poller: Any) -> Any:
        return poller.result()

    @staticmethod
    def _production_share_client(account_name: str, share_name: str, credential: str | None) -> Any:
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        from azure.storage.fileshare import ShareClient

        return ShareClient(
            account_url=f"https://{account_name}.file.core.windows.net",
            share_name=share_name,
            credential=credential,
            token_intent="backup" if not isinstance(credential, str) else None,
        )


def _validate_account_prefix(value: str) -> None:
    if not re.fullmatch(r"[a-z0-9]{3,14}", value):
        raise ValueError("classic Azure Files account_name_prefix must be 3-14 lowercase letters or numbers")


def _validate_account_name(value: str) -> None:
    if not _ACCOUNT_RE.fullmatch(value):
        raise ValueError("classic Azure Files account name must be 3-24 lowercase letters or numbers")


def _validate_share_name(value: str, label: str, *, allow_short: bool = False) -> None:
    if allow_short:
        if not 1 <= len(value) <= 50 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value):
            raise ValueError(f"classic Azure Files {label} must use lowercase letters, numbers, or hyphens")
        return
    if not _SHARE_RE.fullmatch(value):
        raise ValueError(f"classic Azure Files {label} must be 3-63 lowercase letters, numbers, or hyphens")


def _validate_subnet_id(value: str) -> None:
    pattern = re.compile(
        r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/"
        r"virtualNetworks/[^/]+/subnets/[^/]+$",
        re.IGNORECASE,
    )
    if not pattern.fullmatch(value):
        raise ValueError("classic Azure Files allowed_subnet_ids must be complete subnet resource IDs")
