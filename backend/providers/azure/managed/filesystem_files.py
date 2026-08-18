"""Azure Files top-level provisioned-v2 NFS lifecycle."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    OWNERSHIP_ERROR_CODE,
    AzureOperation,
    AzureOwnershipError,
    owner_of,
    verify_azure_ownership,
)
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
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
    VolumeSourceKind,
)

KIND = "filesystem"
VARIANT = "azure_files"
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]$")
_SIZE_GIB = {"small": 32, "medium": 256, "large": 1024, "xlarge": 4096}
_STATES = {
    "Succeeded": "available",
    "Provisioning": "provisioning",
    "Accepted": "provisioning",
    "Created": "provisioning",
    "Creating": "provisioning",
    "Updating": "updating",
    "Patching": "updating",
    "Posting": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Canceled": "error",
    "TransientFailure": "error",
}
_MANAGED_BY_TAG = "astrolift-managed-by"
_MANAGED_SERVICE_ID_TAG = "astrolift-managed-service-id"


class AzureFilesError(RuntimeError):
    """Azure Files lifecycle or contract failure."""


@dataclass(frozen=True)
class AzureFilesConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    name_prefix: str = "astrolift-files"
    default_storage_gib: int = 32
    default_redundancy: str = "Local"
    default_root_squash: str = "RootSquash"
    encryption_in_transit_required_default: bool = True
    allowed_subnet_ids: tuple[str, ...] = ()
    deletion_protection_default: bool = True
    mgmt_client: Any | None = None
    locks_client: Any | None = None

    def __post_init__(self) -> None:
        if not 32 <= self.default_storage_gib <= 262_144:
            raise ValueError("Azure Files default storage must be between 32 and 262144 GiB")
        if self.default_redundancy not in {"Local", "Zone"}:
            raise ValueError("Azure Files default redundancy must be Local or Zone")
        if self.default_root_squash not in {"NoRootSquash", "RootSquash", "AllSquash"}:
            raise ValueError("unsupported Azure Files default root squash")
        _validate_name(self.name_prefix, "name_prefix", allow_short=True)
        for subnet_id in self.allowed_subnet_ids:
            _validate_subnet_id(subnet_id)


class AzureFilesDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: AzureFilesConfig,
        now: Any | None = None,
    ) -> None:
        self._config = config
        self._now = now or (lambda: datetime.now(UTC))
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.fileshares import FileSharesMgmtClient

            self._mgmt = FileSharesMgmtClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.locks_client is not None:
            self._locks = config.locks_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource.locks import ManagementLockClient

            self._locks = ManagementLockClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )

    @driver_op(
        cloud="azure",
        driver="filesystem_files",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, size=spec.size) or _tag_error(spec.tags)
        if error:
            return ProvisionResult(False, "", error, ["invalid_azure_files_config"])
        name = self._name(spec)
        handle = self._handle(name)
        try:
            current = self._get(name)
            if current is None:
                self._wait(
                    self._mgmt.file_shares.begin_create_or_update(
                        self._config.resource_group,
                        name,
                        self._create_parameters(spec, cfg),
                    ),
                )
            else:
                self._assert_owned(current, spec, AzureOperation.PROVISION, name)
                self._assert_immutable(current, cfg, apply_defaults=True)
                self._assert_downgrade_allowed(current, cfg, size=spec.size)
                self._wait(
                    self._mgmt.file_shares.begin_update(
                        self._config.resource_group,
                        name,
                        self._update_parameters(cfg, tags=_tags(spec), size=spec.size, apply_defaults=True),
                    ),
                )
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile Azure Files share: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Azure Files NFS share {name} reconciled", ready=True)

    @driver_op(cloud="azure", driver="filesystem_files")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            name = self._parse_handle(spec.handle)
        except AzureFilesError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate(cfg, size=spec.size, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_azure_files_config"])
        try:
            current = self._get(name)
            if current is None:
                return UpdateResult(False, spec.handle, "Azure Files share not found", ["not_found"])
            self._assert_owned(current, spec, AzureOperation.UPDATE, name)
            self._assert_immutable(current, cfg, apply_defaults=False)
            self._assert_downgrade_allowed(current, cfg, size=spec.size)
            if cfg or spec.size:
                self._wait(
                    self._mgmt.file_shares.begin_update(
                        self._config.resource_group,
                        name,
                        self._update_parameters(cfg, size=spec.size, apply_defaults=False),
                    ),
                )
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure Files share: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure Files NFS share {name} reconciled")

    @driver_op(
        cloud="azure",
        driver="filesystem_files",
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
            name = self._parse_handle(spec.handle)
        except AzureFilesError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        try:
            current = self._get(name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"describe Azure Files share: {exc}", [str(exc)])
        if current is None:
            return DeprovisionResult(True, spec.handle, f"Azure Files share {name} already gone")
        try:
            self._assert_owned(current, spec, AzureOperation.DELETE, name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                [OWNERSHIP_ERROR_CODE],
                retryable=False,
            )
        cfg = dict(spec.config or {})
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Azure Files share has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                (
                    "Azure Files snapshots are children of the share and the current service has no clone/restore API; "
                    "preserve or copy the share, then set delete_data=true"
                ),
                ["retained_filesystem_data_requires_delete_data"],
                retryable=False,
            )
        try:
            locks = self._resource_locks(name)
            private_connections = self._private_connections(name)
            if locks and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Azure Files share {name} is protected by an Azure resource lock",
                    ["resource_lock_present"],
                    retryable=False,
                )
            if private_connections and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Azure Files share {name} has {len(private_connections)} private endpoint connection(s)",
                    ["private_endpoint_connections_present"],
                    retryable=False,
                )
            if force_destroy:
                self._delete_locks(name, locks)
                for connection in private_connections:
                    connection_name = str(_field(connection, "name", default=""))
                    if connection_name:
                        try:
                            self._wait(
                                self._mgmt.private_endpoint_connections.begin_delete(
                                    self._config.resource_group,
                                    name,
                                    connection_name,
                                ),
                            )
                        except Exception as exc:
                            if not _not_found(exc):
                                raise
            for snapshot in self._snapshots(name):
                snapshot_name = str(_field(snapshot, "name", default=""))
                if snapshot_name:
                    try:
                        self._wait(
                            self._mgmt.file_share_snapshots.begin_delete_file_share_snapshot(
                                self._config.resource_group,
                                name,
                                snapshot_name,
                            ),
                        )
                    except Exception as exc:
                        if not _not_found(exc):
                            raise
            self._wait(self._mgmt.file_shares.begin_delete(self._config.resource_group, name))
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"Azure Files share {name} deletion converged")
            return DeprovisionResult(False, spec.handle, f"delete Azure Files share: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"Azure Files share {name} deleted")

    @driver_op(cloud="azure", driver="filesystem_files")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            name = self._parse_handle(handle.handle)
        except AzureFilesError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        try:
            share = self._get(name)
            if share is None:
                return ServiceStatus(handle.handle, "deprovisioned", "Azure Files share does not exist")
            properties = _field(share, "properties")
            provider_state = _string_value(_field(properties, "provisioning_state", default="Unknown"))
            state = _STATES.get(provider_state, "updating")
            return ServiceStatus(
                handle.handle,
                state,
                (
                    f"Azure reports {provider_state}; "
                    f"{int(_field(properties, 'provisioned_storage_gi_b', default=0))} GiB provisioned"
                ),
            )
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Azure Files share: {exc}")

    @driver_op(cloud="azure", driver="filesystem_files")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        name = self._parse_handle(handle.handle)
        share = self._get(name)
        if share is None:
            raise AzureFilesError("binding requested for missing Azure Files share")
        self._assert_owned(share, handle, AzureOperation.INSPECT, name)
        properties = _field(share, "properties")
        hostname = str(_field(properties, "host_name", default=""))
        mount_name = str(_field(properties, "mount_name", default=name))
        if not hostname:
            raise AzureFilesError("Azure Files share has no mount hostname")
        cfg = config or {}
        mount_path = str(cfg.get("mount_path", "/mnt/shared"))
        if not mount_path.startswith("/"):
            raise AzureFilesError("Azure Files mount_path must be absolute")
        encrypted = (
            _string_value(
                _field(
                    _field(properties, "nfs_protocol_properties"),
                    "encryption_in_transit_required",
                    default="Enabled",
                ),
            )
            == "Enabled"
        )
        options = [
            "sec=sys",
            "vers=4",
            "minorversion=1",
            "nolock",
            "proto=tcp",
            "nofail",
            "_netdev",
            "nconnect=4",
            "rsize=1048576",
            "wsize=1048576",
        ]
        if not encrypted:
            options.append("notls")
        read_only = bool(cfg.get("read_only", False))
        if read_only and "ro" not in options:
            options.append("ro")
        for option in cfg.get("mount_options", []) or []:
            value = str(option)
            if value not in options:
                options.append(value)
        export_path = f"/{mount_name}/{name}"
        resource_id = str(_field(share, "id", default=self._resource_id(name)))
        try:
            capacity_gib = int(_field(properties, "provisioned_storage_gi_b", default=0))
        except (TypeError, ValueError) as exc:
            raise AzureFilesError("Azure Files share has invalid provisioned capacity") from exc
        if capacity_gib < 1:
            raise AzureFilesError("Azure Files share has invalid provisioned capacity")
        return Binding(
            env_vars={
                "FILESYSTEM_HANDLE": ValueRef(literal=resource_id),
                "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
                "FILESYSTEM_TLS": ValueRef(literal=str(encrypted).lower()),
                "FILESYSTEM_PROTOCOL": ValueRef(literal="nfs4.1"),
                "FILESYSTEM_ENDPOINT": ValueRef(literal=hostname),
                "FILESYSTEM_EXPORT_PATH": ValueRef(literal=export_path),
                "FILESYSTEM_SOURCE": ValueRef(literal=f"{hostname}:{export_path}"),
                "FILESYSTEM_MOUNT_OPTIONS": ValueRef(literal=",".join(options)),
                "FILESYSTEM_READ_ONLY": ValueRef(literal=str(read_only).lower()),
                "AZURE_FILE_SHARE_NAME": ValueRef(literal=name),
                "AZURE_FILE_SHARE_MOUNT_NAME": ValueRef(literal=mount_name),
                "AZURE_FILE_SHARE_HOSTNAME": ValueRef(literal=hostname),
                "AZURE_RESOURCE_GROUP": ValueRef(literal=self._config.resource_group),
                "AZURE_LOCATION": ValueRef(literal=str(_field(share, "location", default=self._config.location))),
            },
            pod_volume_mounts=[
                VolumeMount(
                    name=_slug(name),
                    mount_path=mount_path,
                    source_kind=VolumeSourceKind.CSI,
                    protocol="nfs4.1",
                    csi_driver="file.csi.azure.com",
                    # The ARM resource id is unique per share and is what the
                    # binding already publishes as FILESYSTEM_HANDLE.
                    volume_handle=resource_id,
                    # file.csi.azure.com builds the NFS source as
                    # "<server>:/<storageAccount>/<shareName>". This resource
                    # provider has no storage account; its export path is
                    # "/<mount name>/<share>", so the mount name takes that
                    # slot and reproduces FILESYSTEM_SOURCE exactly. The
                    # classic driver instead carries the account in its
                    # "<rg>#<account>#<share>" volume handle.
                    volume_attributes={
                        "shareName": name,
                        "protocol": "nfs",
                        "server": hostname,
                        "storageAccount": mount_name,
                    },
                    # Network-authorized: no secret_refs, nothing to mount with.
                    mount_options=_csi_nfs_mount_options(options),
                    read_only=read_only,
                    capacity=f"{capacity_gib}Gi",
                ),
            ],
            notes=(
                "Microsoft.FileShares provisioned-v2 NFS 4.1 share; access is network-authorized. "
                "Encrypted mounts require the AZNFS mount helper on the workload node."
            ),
        )

    @driver_op(cloud="azure", driver="filesystem_files", audit=True, sensitive_kind="managed_service_snapshot")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        name = self._parse_handle(handle.handle)
        share = self._get(name)
        if share is None:
            raise AzureFilesError("snapshot requested for missing Azure Files share")
        self._assert_owned(share, handle, AzureOperation.SNAPSHOT, name)
        created = self._now()
        snapshot_name = f"snap-{created.strftime('%Y%m%d-%H%M%S-%f')}"
        from azure.mgmt.fileshares import models

        self._wait(
            self._mgmt.file_share_snapshots.begin_create_or_update_file_share_snapshot(
                self._config.resource_group,
                name,
                snapshot_name,
                models.FileShareSnapshot(
                    properties=models.FileShareSnapshotProperties(
                        initiator_id="astrolift",
                        metadata={"astrolift-managed-by": "platform"},
                    ),
                ),
            ),
        )
        return SnapshotHandle(handle.handle, snapshot_name, created.isoformat())

    @driver_op(cloud="azure", driver="filesystem_files", audit=True, sensitive_kind="managed_service_restore")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            (
                "Microsoft.FileShares exposes in-place child snapshots but no clone/restore operation; "
                "copy snapshot data to a separately provisioned target before registration"
            ),
            ["restore_not_supported"],
        )

    @driver_op(cloud="azure", driver="filesystem_files", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "resource_name": {"type": "string", "pattern": _NAME_RE.pattern, "minLength": 3, "maxLength": 63},
                "mount_name": {"type": "string", "pattern": _NAME_RE.pattern, "minLength": 3, "maxLength": 63},
                "media_tier": {"const": "SSD", "default": "SSD"},
                "protocol": {"const": "NFS", "default": "NFS"},
                "redundancy": {"type": "string", "enum": ["Local", "Zone"], "default": "Local"},
                "provisioned_storage_gib": {"type": "integer", "minimum": 32, "maximum": 262144},
                "provisioned_iops": {"type": "integer", "minimum": 1},
                "provisioned_throughput_mib_per_sec": {"type": "integer", "minimum": 1},
                "root_squash": {
                    "type": "string",
                    "enum": ["NoRootSquash", "RootSquash", "AllSquash"],
                    "default": "RootSquash",
                },
                "encryption_in_transit_required": {"type": "boolean", "default": True},
                "public_network_access": {"const": "Enabled", "default": "Enabled"},
                "allowed_subnet_ids": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "mount_path": {"type": "string", "pattern": "^/"},
                "mount_options": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "read_only": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="azure", driver="filesystem_files", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Portable Azure resource identifier",
                "FILESYSTEM_MOUNT_PATH": "Container mount path",
                "FILESYSTEM_TLS": "Whether AZNFS encryption in transit is required",
                "FILESYSTEM_PROTOCOL": "nfs4.1",
                "FILESYSTEM_ENDPOINT": "Azure Files NFS hostname",
                "FILESYSTEM_EXPORT_PATH": "NFS export path",
                "FILESYSTEM_SOURCE": "NFS hostname and export source",
                "FILESYSTEM_MOUNT_OPTIONS": "Comma-separated AZNFS mount options",
                "FILESYSTEM_READ_ONLY": "Whether the workload mount is read-only",
                "AZURE_FILE_SHARE_NAME": "Microsoft.FileShares resource name",
                "AZURE_FILE_SHARE_MOUNT_NAME": "Mount-visible short name",
                "AZURE_FILE_SHARE_HOSTNAME": "Azure Files NFS hostname",
                "AZURE_RESOURCE_GROUP": "Azure resource group",
                "AZURE_LOCATION": "Azure region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "provisioned_storage_gib",
            "provisioned_iops",
            "provisioned_throughput_mib_per_sec",
            "root_squash",
            "encryption_in_transit_required",
            "allowed_subnet_ids",
            "mount_path",
            "mount_options",
            "read_only",
            "deletion_protection",
        ]

    def _validate(
        self,
        cfg: dict[str, Any],
        *,
        size: str | None,
        partial: bool = False,
    ) -> str | None:
        allowed = {
            "location",
            "resource_name",
            "mount_name",
            "media_tier",
            "protocol",
            "redundancy",
            "provisioned_storage_gib",
            "provisioned_iops",
            "provisioned_throughput_mib_per_sec",
            "root_squash",
            "encryption_in_transit_required",
            "public_network_access",
            "allowed_subnet_ids",
            "mount_path",
            "mount_options",
            "read_only",
            "deletion_protection",
        }
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported Azure Files config fields: {', '.join(unknown)}"
        if size and size not in {*_SIZE_GIB, "custom"}:
            return f"unsupported Azure Files size {size!r}"
        if size == "custom" and "provisioned_storage_gib" not in cfg:
            return "Azure Files custom size requires provisioned_storage_gib"
        if "resource_name" in cfg:
            try:
                _validate_name(str(cfg["resource_name"]), "resource_name")
            except ValueError as exc:
                return str(exc)
        if "mount_name" in cfg:
            try:
                _validate_name(str(cfg["mount_name"]), "mount_name")
            except ValueError as exc:
                return str(exc)
        if str(cfg.get("media_tier", "SSD")) != "SSD":
            return "Microsoft.FileShares currently supports only SSD media"
        if str(cfg.get("protocol", "NFS")) != "NFS":
            return "Microsoft.FileShares currently supports only NFS; use the planned classic variant for SMB"
        if str(cfg.get("redundancy", self._config.default_redundancy)) not in {"Local", "Zone"}:
            return "Azure Files redundancy must be Local or Zone"
        storage = cfg.get("provisioned_storage_gib")
        if storage is not None and (not isinstance(storage, int) or not 32 <= storage <= 262_144):
            return "Azure Files provisioned_storage_gib must be between 32 and 262144"
        for field_name in ("provisioned_iops", "provisioned_throughput_mib_per_sec"):
            value = cfg.get(field_name)
            if value is not None and (not isinstance(value, int) or value < 1):
                return f"Azure Files {field_name} must be a positive integer"
        root_squash = str(cfg.get("root_squash", self._config.default_root_squash))
        if root_squash not in {"NoRootSquash", "RootSquash", "AllSquash"}:
            return "unsupported Azure Files root_squash"
        if str(cfg.get("public_network_access", "Enabled")) != "Enabled":
            return "Azure Files private-only access requires managed Private Endpoint and DNS lifecycle"
        subnets = cfg.get("allowed_subnet_ids", self._config.allowed_subnet_ids)
        if not partial or "allowed_subnet_ids" in cfg:
            if not isinstance(subnets, (list, tuple)) or not subnets:
                return "Azure Files NFS requires at least one allowed_subnet_id"
            if len(set(map(str, subnets))) != len(subnets):
                return "Azure Files allowed_subnet_ids must be unique"
            try:
                for subnet_id in subnets:
                    _validate_subnet_id(str(subnet_id))
            except ValueError as exc:
                return str(exc)
        mount_path = cfg.get("mount_path")
        if mount_path is not None and (not isinstance(mount_path, str) or not mount_path.startswith("/")):
            return "Azure Files mount_path must be absolute"
        mount_options = cfg.get("mount_options")
        if mount_options is not None and (
            not isinstance(mount_options, list) or any(not str(value) or "," in str(value) for value in mount_options)
        ):
            return "Azure Files mount_options must be a list of non-empty comma-free values"
        return None

    def _create_parameters(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.fileshares import models

        name = self._name(spec)
        return models.FileShare(
            location=str(cfg.get("location", self._config.location)),
            tags=_tags(spec),
            properties=models.FileShareProperties(
                mount_name=str(cfg.get("mount_name", name)),
                media_tier="SSD",
                redundancy=str(cfg.get("redundancy", self._config.default_redundancy)),
                protocol="NFS",
                provisioned_storage_gi_b=self._storage(cfg, spec.size),
                provisioned_io_per_sec=cfg.get("provisioned_iops"),
                provisioned_throughput_mi_b_per_sec=cfg.get("provisioned_throughput_mib_per_sec"),
                nfs_protocol_properties=self._nfs_properties(cfg, apply_defaults=True),
                public_access_properties=self._public_access(cfg, apply_defaults=True),
                public_network_access="Enabled",
            ),
        )

    def _update_parameters(
        self,
        cfg: dict[str, Any],
        *,
        size: str | None,
        tags: dict[str, str] | None = None,
        apply_defaults: bool,
    ) -> Any:
        from azure.mgmt.fileshares import models

        storage: int | None = None
        if "provisioned_storage_gib" in cfg or size:
            storage = self._storage(cfg, size)
        properties = models.FileShareUpdateProperties(
            provisioned_storage_gi_b=storage,
            provisioned_io_per_sec=(cfg.get("provisioned_iops") if "provisioned_iops" in cfg else None),
            provisioned_throughput_mi_b_per_sec=(
                cfg.get("provisioned_throughput_mib_per_sec") if "provisioned_throughput_mib_per_sec" in cfg else None
            ),
            nfs_protocol_properties=self._nfs_properties(cfg, apply_defaults=apply_defaults),
            public_access_properties=self._public_access(cfg, apply_defaults=apply_defaults),
            public_network_access=("Enabled" if apply_defaults or "public_network_access" in cfg else None),
        )
        return models.FileShareUpdate(tags=tags, properties=properties)

    def _nfs_properties(self, cfg: dict[str, Any], *, apply_defaults: bool) -> Any | None:
        if not apply_defaults and not ({"root_squash", "encryption_in_transit_required"} & set(cfg)):
            return None
        from azure.mgmt.fileshares import models

        return models.NfsProtocolProperties(
            root_squash=str(cfg.get("root_squash", self._config.default_root_squash)),
            encryption_in_transit_required=(
                "Enabled"
                if bool(
                    cfg.get(
                        "encryption_in_transit_required",
                        self._config.encryption_in_transit_required_default,
                    ),
                )
                else "Disabled"
            ),
        )

    def _public_access(self, cfg: dict[str, Any], *, apply_defaults: bool) -> Any | None:
        if not apply_defaults and "allowed_subnet_ids" not in cfg:
            return None
        from azure.mgmt.fileshares import models

        return models.PublicAccessProperties(
            allowed_subnets=[str(value) for value in cfg.get("allowed_subnet_ids", self._config.allowed_subnet_ids)],
        )

    def _assert_immutable(self, current: Any, cfg: dict[str, Any], *, apply_defaults: bool) -> None:
        properties = _field(current, "properties")
        checks = {
            "location": (_string_value(_field(current, "location", default="")), self._config.location),
            "mount_name": (
                _string_value(_field(properties, "mount_name", default="")),
                _string_value(_field(current, "name", default="")),
            ),
            "media_tier": (_string_value(_field(properties, "media_tier", default="")), "SSD"),
            "protocol": (_string_value(_field(properties, "protocol", default="")), "NFS"),
            "redundancy": (
                _string_value(_field(properties, "redundancy", default="")),
                self._config.default_redundancy,
            ),
        }
        for field_name, (actual, default) in checks.items():
            if not apply_defaults and field_name not in cfg:
                continue
            expected = str(cfg.get(field_name, default))
            if actual != expected:
                raise AzureFilesError(f"Azure Files {field_name} is immutable ({actual!r} != {expected!r})")

    def _assert_downgrade_allowed(self, current: Any, cfg: dict[str, Any], *, size: str | None) -> None:
        properties = _field(current, "properties")
        checks = (
            (
                "provisioned_storage_gib",
                self._storage(cfg, size) if "provisioned_storage_gib" in cfg or size else None,
                "provisioned_storage_gi_b",
                "provisioned_storage_next_allowed_downgrade",
            ),
            (
                "provisioned_iops",
                cfg.get("provisioned_iops"),
                "provisioned_io_per_sec",
                "provisioned_io_per_sec_next_allowed_downgrade",
            ),
            (
                "provisioned_throughput_mib_per_sec",
                cfg.get("provisioned_throughput_mib_per_sec"),
                "provisioned_throughput_mi_b_per_sec",
                "provisioned_throughput_next_allowed_downgrade",
            ),
        )
        for label, desired, current_field, allowed_field in checks:
            if desired is None:
                continue
            existing = int(_field(properties, current_field, default=0) or 0)
            allowed_at = _field(properties, allowed_field)
            if int(desired) < existing and _after_now(allowed_at, self._now()):
                raise AzureFilesError(f"Azure Files {label} cannot be reduced until {allowed_at}")

    @staticmethod
    def _assert_owned(current: Any, source: object, operation: AzureOperation, name: str) -> None:
        verify_azure_ownership(
            dict(_field(current, "tags", default={}) or {}),
            owner_of(source),
            operation=operation,
            resource=f"Azure Files share {name}",
        )

    def _get(self, name: str) -> Any | None:
        try:
            return self._mgmt.file_shares.get(self._config.resource_group, name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _snapshots(self, name: str) -> list[Any]:
        try:
            return list(self._mgmt.file_share_snapshots.list_by_file_share(self._config.resource_group, name))
        except Exception as exc:
            if _not_found(exc):
                return []
            raise

    def _private_connections(self, name: str) -> list[Any]:
        try:
            return list(self._mgmt.private_endpoint_connections.list_by_file_share(self._config.resource_group, name))
        except Exception as exc:
            if _not_found(exc):
                return []
            raise

    def _resource_locks(self, name: str) -> list[Any]:
        return list(
            self._locks.management_locks.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.FileShares",
                parent_resource_path="",
                resource_type="fileShares",
                resource_name=name,
            ),
        )

    def _delete_locks(self, name: str, locks: list[Any]) -> None:
        for lock in locks:
            lock_name = str(_field(lock, "name", default=""))
            if not lock_name:
                raise AzureFilesError("Azure Files resource lock has no name")
            try:
                self._locks.management_locks.delete_at_resource_level(
                    resource_group_name=self._config.resource_group,
                    resource_provider_namespace="Microsoft.FileShares",
                    parent_resource_path="",
                    resource_type="fileShares",
                    resource_name=name,
                    lock_name=lock_name,
                )
            except Exception as exc:
                if not _not_found(exc):
                    raise

    @staticmethod
    def _wait(poller: Any) -> Any:
        return poller.result()

    def _storage(self, cfg: dict[str, Any], size: str | None) -> int:
        if "provisioned_storage_gib" in cfg:
            return int(cfg["provisioned_storage_gib"])
        if size and size in _SIZE_GIB:
            return _SIZE_GIB[size]
        return self._config.default_storage_gib

    def _name(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("resource_name", ""))
        if explicit:
            return explicit
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
        digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
        hint = _slug(spec.service_handle_hint or spec.app_slug or "shared")
        prefix = _slug(self._config.name_prefix)
        base = f"{prefix}-{hint}"[:50].rstrip("-")
        return f"{base}-{digest}"

    @staticmethod
    def _handle(name: str) -> str:
        return f"filesystem/{name}"

    @staticmethod
    def _parse_handle(handle: str) -> str:
        parts = handle.split("/")
        if len(parts) != 2 or parts[0] != "filesystem":
            raise AzureFilesError("Azure Files handle must be filesystem/<resource-name>")
        try:
            _validate_name(parts[1], "resource name")
        except ValueError as exc:
            raise AzureFilesError(str(exc)) from exc
        return parts[1]

    def _resource_id(self, name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.FileShares/fileShares/{name}"
        )


def _tags(spec: ProvisionSpec) -> dict[str, str]:
    tags = {
        _MANAGED_BY_TAG: "platform",
        "astrolift-org": spec.organization_slug,
        "astrolift-app": spec.app_slug,
        "astrolift-env": spec.environment_name,
        "astrolift-cluster": spec.tenant_cluster_id,
        "astrolift-isolation": spec.isolation,
    }
    if spec.binding_id:
        tags["astrolift-binding"] = spec.binding_id
    if spec.managed_service_id:
        tags[_MANAGED_SERVICE_ID_TAG] = spec.managed_service_id
    for key, value in (spec.tags or {}).items():
        # Azure Resource Manager forbids ``<>%&\?/`` in tag names. A readable
        # slug plus a digest remains deterministic without silently colliding.
        digest = hashlib.sha256(key.encode()).hexdigest()[:8]
        safe_key = _slug(key)[:64].rstrip("-") or "tag"
        tags[f"astrolift-extra-{safe_key}-{digest}"] = str(value)
    return tags


def _tag_error(tags: dict[str, str] | None) -> str | None:
    values = tags or {}
    if len(values) > 42:
        return "Azure Files supports at most 42 custom tags after Astrolift ownership tags"
    oversized = sorted(str(key) for key, value in values.items() if len(str(value)) > 256)
    if oversized:
        return f"Azure Files custom tag values must be at most 256 characters: {', '.join(oversized)}"
    return None


def _validate_name(value: str, label: str, *, allow_short: bool = False) -> None:
    minimum = 1 if allow_short else 3
    if not minimum <= len(value) <= 63 or (not allow_short and not _NAME_RE.fullmatch(value)):
        raise ValueError(f"Azure Files {label} must be 3-63 lowercase letters, numbers, or hyphens")
    if allow_short and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value):
        raise ValueError(f"Azure Files {label} must use lowercase letters, numbers, or hyphens")


def _validate_subnet_id(value: str) -> None:
    pattern = re.compile(
        r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/"
        r"virtualNetworks/[^/]+/subnets/[^/]+$",
        re.IGNORECASE,
    )
    if not pattern.fullmatch(value):
        raise ValueError("Azure Files allowed_subnet_ids must be complete subnet resource IDs")


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", value.lower())).strip("-") or "share"


def _csi_nfs_mount_options(options: list[str]) -> list[str]:
    """Reduce an fstab-shaped NFS option list to what file.csi.azure.com accepts.

    ``FILESYSTEM_MOUNT_OPTIONS`` describes a manual ``mount``/fstab line and is
    published unfiltered. A CSI attachment is a different contract, and three
    families of option do not belong in it:

    * ``nofail`` and ``_netdev`` are fstab automount directives. The kubelet
      never reads fstab, so they only risk being rejected as unknown options.
    * ``sec=``, ``vers=``, ``nfsvers=`` and ``minorversion=`` are negotiated by
      the CSI driver from the volume's ``protocol``; passing our own values
      conflicts with what it already sets.
    * ``nolock`` and ``proto=tcp`` are the driver's own defaults for Azure Files
      NFS, and ``notls`` is an AZNFS mount-helper flag that plain ``mount.nfs``
      rejects. Encryption in transit is a node-level AZNFS concern.

    ``ro`` is dropped because read-only travels portably as
    ``VolumeMount.read_only``, which the renderer stamps on the CSI source, the
    pod volume, and the container mount.
    """
    driver_owned = {"nolock", "proto=tcp", "nofail", "_netdev", "notls", "ro"}
    driver_owned_prefixes = ("sec=", "vers=", "nfsvers=", "minorversion=")
    return [option for option in options if option not in driver_owned and not option.startswith(driver_owned_prefixes)]


def _field(value: Any, name: str, *, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _string_value(value: Any) -> str:
    """Normalize generated SDK string-enum values without depending on their type."""
    return str(getattr(value, "value", value))


def _not_found(exc: Exception) -> bool:
    status_code: object = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code == 404:
        return True
    class_name = str(type(exc).__name__).lower()
    return "notfound" in class_name


def _after_now(value: Any, now: datetime) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        candidate = value
    else:
        return False
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=UTC)
    return candidate > now
