"""Amazon FSx managed filesystem lifecycle.

The three drivers share ownership, idempotency, backup, restore, update, and
deletion semantics while retaining the AWS-native request fragments for each
filesystem engine.  Passwords are never accepted inline.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
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
from aws.managed._base import ManagedServiceError, adoption_refusal, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "filesystem"
_MANAGED_BY = "platform"
_RESOURCE_TOKEN_TAG = "astrolift.io/resource-token"
_UPDATE_PLAN_TAG = "astrolift.io/update-plan-sha256"
_FINAL_BACKUP_TAG = "astrolift.io/final-backup"
_TERMINAL_FILESYSTEM_STATES = {"FAILED", "MISCONFIGURED"}
_TERMINAL_BACKUP_STATES = {"FAILED", "DELETED"}
_STATE = {
    "AVAILABLE": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "FAILED": "error",
    "MISCONFIGURED": "error",
}
_SIZE_CAPACITY = {
    "LUSTRE": {"small": 1200, "medium": 2400, "large": 4800, "xlarge": 9600},
    "OPENZFS": {"small": 64, "medium": 256, "large": 1024, "xlarge": 4096},
    "WINDOWS": {"small": 32, "medium": 256, "large": 1024, "xlarge": 4096},
}


@dataclass(frozen=True)
class FSxConfig(CredentialedConfig):
    region: str
    account_id: str
    subnet_ids: tuple[str, ...] = ()
    security_group_ids: tuple[str, ...] = ()
    kms_key_id: str = ""
    client_token_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    poll_delay_seconds: float = 10
    max_poll_attempts: int = 120


class FSxDriver(ManagedServiceDriver):
    file_system_type = "LUSTRE"
    variant = "fsx_lustre"
    display_name = "Amazon FSx for Lustre"

    def __init__(
        self,
        *,
        config: FSxConfig,
        client: Any | None = None,
        secrets_client: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._config = config
        if client is None:
            client = aws_client("fsx", region=config.region, credential=config.credential)
        self._fsx = client
        self._secrets = secrets_client
        self._sleep = sleep

    @driver_op(
        cloud="aws",
        driver="filesystem_fsx",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_fsx_config"])
        token = self._client_token(spec)
        existing = self._find_by_token(token)
        file_system_id = str(existing.get("FileSystemId") or "")
        created = False
        try:
            if file_system_id:
                file_system = self._await_file_system(file_system_id)
                self._verify_owned_type(file_system)
                # Platform-made is not enough: it must be this service's (#1961).
                refusal = adoption_refusal(file_system.get("Tags") or [], spec, resource="FSx filesystem")
                if refusal is not None:
                    raise ManagedServiceError(refusal)
                self._tag_file_system(file_system, self._tags(spec, token))
                self._apply_update(file_system, cfg, size=spec.size)
            else:
                request = self._create_request(spec, token=token)
                response = self._fsx.create_file_system(**request)
                file_system = dict(response.get("FileSystem") or {})
                file_system_id = str(file_system.get("FileSystemId") or "")
                if not file_system_id:
                    raise ManagedServiceError("CreateFileSystem returned no FileSystemId")
                created = True
                file_system = self._await_file_system(file_system_id)
                self._verify_owned_type(file_system)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=file_system_id) if file_system_id else ""
            action = "create" if created else "reconcile"
            return ProvisionResult(False, handle, f"{action} {self.display_name}: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=file_system_id),
            f"{self.display_name} filesystem {file_system_id} is available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="filesystem_fsx")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        file_system_id = self._file_system_id(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size or "custom", partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_fsx_config"])
        try:
            file_system = self._describe_file_system(file_system_id)
            if file_system is None:
                return UpdateResult(False, spec.handle, f"{self.display_name} filesystem not found", ["not_found"])
            self._verify_owned_type(file_system)
            self._apply_update(file_system, cfg, size=spec.size)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"{self.display_name} filesystem not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update {self.display_name}: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"{self.display_name} filesystem reconciled")

    @driver_op(
        cloud="aws",
        driver="filesystem_fsx",
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
        file_system_id = self._file_system_id(spec.handle)
        cfg = spec.config or {}
        try:
            file_system = self._describe_file_system(file_system_id)
        except Exception as exc:
            return _deprovision_error(spec.handle, f"describe {self.display_name}", exc)
        if file_system is None:
            return DeprovisionResult(True, spec.handle, f"{self.display_name} filesystem already gone")
        if str(file_system.get("Lifecycle") or "").upper() == "DELETING":
            return DeprovisionResult(True, spec.handle, f"{self.display_name} filesystem deletion is in progress")
        try:
            self._verify_owned_type(file_system)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["external_resource_collision"],
                retryable=False,
            )
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"{self.display_name} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data and not self._supports_backups(file_system):
            return DeprovisionResult(
                False,
                spec.handle,
                "FSx for Lustre scratch filesystems cannot create a final backup; set delete_data=true",
                ["retained_filesystem_data_requires_delete_data"],
                retryable=False,
            )
        try:
            request: dict[str, Any] = {
                "FileSystemId": file_system_id,
                "ClientRequestToken": _token(f"delete-{file_system_id}"),
                self._configuration_key(): {
                    "SkipFinalBackup": bool(delete_data),
                },
            }
            engine_request = request[self._configuration_key()]
            if not delete_data:
                engine_request["FinalBackupTags"] = [
                    {"Key": _FINAL_BACKUP_TAG, "Value": "true"},
                    {"Key": "astrolift.io/source-filesystem", "Value": file_system_id},
                ]
            if self.file_system_type == "OPENZFS":
                engine_request["Options"] = ["DELETE_CHILD_VOLUMES_AND_SNAPSHOTS"]
            _validate_request("DeleteFileSystem", request)
            self._fsx.delete_file_system(**request)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"{self.display_name} deletion converged")
            return _deprovision_error(spec.handle, f"delete {self.display_name}", exc)
        retained = " after queuing a final backup" if not delete_data else ""
        return DeprovisionResult(True, spec.handle, f"{self.display_name} deletion queued{retained}")

    @driver_op(cloud="aws", driver="filesystem_fsx")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        file_system_id = self._file_system_id(handle.handle)
        try:
            file_system = self._describe_file_system(file_system_id)
            if file_system is None:
                return ServiceStatus(handle.handle, "deprovisioned", f"{self.display_name} filesystem does not exist")
            self._verify_type(file_system)
            lifecycle = str(file_system.get("Lifecycle") or "FAILED").upper()
            state = _STATE.get(lifecycle, "error")
            actions = list(file_system.get("AdministrativeActions") or [])
            active = [
                action
                for action in actions
                if str(action.get("Status") or "").upper() in {"PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING"}
            ]
            failed = [action for action in actions if str(action.get("Status") or "").upper() == "FAILED"]
            if failed:
                state = "error"
            elif active and state == "available":
                state = "updating"
            return ServiceStatus(
                handle.handle,
                state,
                f"{self.display_name} reports {lifecycle}; {len(active)} administrative action(s) active",
            )
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"{self.display_name} filesystem does not exist")
            return ServiceStatus(handle.handle, "error", f"describe {self.display_name}: {exc}")

    @driver_op(cloud="aws", driver="filesystem_fsx")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        file_system_id = self._file_system_id(handle.handle)
        cfg = config or {}
        file_system = self._describe_file_system(file_system_id)
        if file_system is None:
            raise ManagedServiceError(f"{self.display_name} filesystem not found")
        self._verify_owned_type(file_system)
        endpoint = str(file_system.get("DNSName") or "")
        if not endpoint:
            raise ManagedServiceError(f"{self.display_name} has no DNS endpoint")
        protocol, source, options = self._mount_details(file_system, cfg)
        mount_path = str(cfg.get("mount_path") or "/mnt/shared")
        tls = bool(cfg.get("tls", True))
        arn = str(
            file_system.get("ResourceARN")
            or f"arn:aws:fsx:{self._config.region}:{self._config.account_id}:file-system/{file_system_id}",
        )
        env_vars = {
            "FILESYSTEM_HANDLE": ValueRef(literal=file_system_id),
            "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
            "FILESYSTEM_TLS": ValueRef(literal=str(tls).lower()),
            "FILESYSTEM_PROTOCOL": ValueRef(literal=protocol),
            "FILESYSTEM_ENDPOINT": ValueRef(literal=endpoint),
            "FILESYSTEM_MOUNT_SOURCE": ValueRef(literal=source),
            "FILESYSTEM_MOUNT_OPTIONS": ValueRef(literal=",".join(options)),
            "FSX_FILE_SYSTEM_ID": ValueRef(literal=file_system_id),
            "FSX_FILE_SYSTEM_ARN": ValueRef(literal=arn),
            "FSX_FILE_SYSTEM_TYPE": ValueRef(literal=self.file_system_type),
            "AWS_REGION": ValueRef(literal=self._config.region),
        }
        if self.file_system_type == "WINDOWS":
            if cfg.get("mount_username_secret_ref"):
                env_vars["FILESYSTEM_USERNAME"] = ValueRef(
                    secret_ref=str(cfg["mount_username_secret_ref"]),
                )
            if cfg.get("mount_password_secret_ref"):
                env_vars["FILESYSTEM_PASSWORD"] = ValueRef(
                    secret_ref=str(cfg["mount_password_secret_ref"]),
                )
        volume_mount = self._volume_mount(
            file_system_id=file_system_id,
            file_system=file_system,
            endpoint=endpoint,
            protocol=protocol,
            source=source,
            options=options,
            mount_path=mount_path,
            cfg=cfg,
        )
        return Binding(
            env_vars=env_vars,
            pod_volume_mounts=[volume_mount],
            notes=f"{self.display_name} shared filesystem mounted through its Kubernetes CSI driver",
        )

    @driver_op(
        cloud="aws",
        driver="filesystem_fsx",
        audit=True,
        sensitive_kind="managed_service_snapshot",
    )
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        file_system_id = self._file_system_id(handle.handle)
        file_system = self._describe_file_system(file_system_id)
        if file_system is None:
            raise ManagedServiceError(f"{self.display_name} filesystem not found")
        self._verify_owned_type(file_system)
        if not self._supports_backups(file_system):
            raise ManagedServiceError("FSx for Lustre scratch filesystems do not support backups")
        response = self._fsx.create_backup(
            FileSystemId=file_system_id,
            ClientRequestToken=_token(f"snapshot-{file_system_id}-{time.time_ns()}"),
            Tags=[
                {"Key": "astrolift.io/managed-by", "Value": _MANAGED_BY},
                {"Key": "astrolift.io/source-filesystem", "Value": file_system_id},
            ],
        )
        backup = dict(response.get("Backup") or {})
        backup_id = str(backup.get("BackupId") or "")
        if not backup_id:
            raise ManagedServiceError("CreateBackup returned no BackupId")
        backup = self._await_backup(backup_id)
        created_at = backup.get("CreationTime")
        if isinstance(created_at, datetime):
            created = created_at.astimezone(UTC).isoformat()
        else:
            created = str(created_at or datetime.now(UTC).isoformat())
        return SnapshotHandle(handle.handle, backup_id, created)

    @driver_op(
        cloud="aws",
        driver="filesystem_fsx",
        audit=True,
        sensitive_kind="managed_service_restore",
    )
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = target.config or {}
        error = self._validate_config(cfg, size=target.size, restoring=True)
        if error:
            return ProvisionResult(False, "", error, ["invalid_fsx_restore_config"])
        token = _token(f"{self._client_token(target)}-restore-{snapshot.snapshot_id}")
        existing = self._find_by_token(token)
        file_system_id = str(existing.get("FileSystemId") or "")
        try:
            if file_system_id:
                file_system = self._await_file_system(file_system_id)
                self._verify_owned_type(file_system)
            else:
                backup = self._describe_backup(snapshot.snapshot_id)
                if backup is None:
                    raise ManagedServiceError(f"FSx backup {snapshot.snapshot_id} not found")
                backup_type = str((backup.get("FileSystem") or {}).get("FileSystemType") or "")
                if backup_type != self.file_system_type:
                    raise ManagedServiceError(
                        f"backup contains {backup_type or 'unknown'} data, not {self.file_system_type}",
                    )
                request = self._restore_request(snapshot.snapshot_id, target, token=token)
                response = self._fsx.create_file_system_from_backup(**request)
                file_system = dict(response.get("FileSystem") or {})
                file_system_id = str(file_system.get("FileSystemId") or "")
                if not file_system_id:
                    raise ManagedServiceError("CreateFileSystemFromBackup returned no FileSystemId")
                file_system = self._await_file_system(file_system_id)
                self._verify_owned_type(file_system)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=file_system_id) if file_system_id else ""
            return ProvisionResult(False, handle, f"restore {self.display_name}: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=file_system_id),
            f"{self.display_name} restored from backup {snapshot.snapshot_id}",
            ready=True,
        )

    @driver_op(cloud="aws", driver="filesystem_fsx", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native = {"type": "object", "additionalProperties": True}
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "file_system": native,
                "file_system_update": native,
                "restore_file_system": native,
                "subnet_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "active_directory_password_secret_ref": {"type": "string", "minLength": 1},
                "mount_username_secret_ref": {"type": "string", "minLength": 1},
                "mount_password_secret_ref": {"type": "string", "minLength": 1},
                "mount_path": {"type": "string"},
                "mount_options": {"type": "array", "items": {"type": "string"}},
                "mount_name": {"type": "string"},
                "share_name": {"type": "string", "default": "share"},
                "tls": {"type": "boolean", "default": True},
                "read_only": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="filesystem_fsx", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Portable cloud filesystem identifier",
                "FILESYSTEM_MOUNT_PATH": "Container mount path",
                "FILESYSTEM_TLS": "Whether supported in-transit encryption is requested",
                "FILESYSTEM_PROTOCOL": "lustre, nfs4.1, or smb3",
                "FILESYSTEM_ENDPOINT": "Private FSx DNS endpoint",
                "FILESYSTEM_MOUNT_SOURCE": "Protocol-specific mount source",
                "FILESYSTEM_MOUNT_OPTIONS": "Comma-separated mount options",
                "FSX_FILE_SYSTEM_ID": "Amazon FSx filesystem ID",
                "FSX_FILE_SYSTEM_ARN": "Amazon FSx filesystem ARN",
                "FSX_FILE_SYSTEM_TYPE": "AWS FSx filesystem type",
                "FILESYSTEM_USERNAME": "Optional FSx for Windows mount username secret",
                "FILESYSTEM_PASSWORD": "Optional FSx for Windows mount password secret",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "file_system_update",
            "active_directory_password_secret_ref",
            "mount_username_secret_ref",
            "mount_password_secret_ref",
            "mount_path",
            "mount_options",
            "mount_name",
            "share_name",
            "tls",
            "read_only",
            "deletion_protection",
        ]

    def _create_request(self, spec: ProvisionSpec, *, token: str, validate_only: bool = False) -> dict[str, Any]:
        cfg = spec.config or {}
        request = dict(cfg.get("file_system") or {})
        subnets = list(cfg.get("subnet_ids") or request.get("SubnetIds") or self._config.subnet_ids)
        request.update(
            {
                "ClientRequestToken": token,
                "FileSystemType": self.file_system_type,
                "SubnetIds": self._selected_subnets(request, subnets),
                "Tags": self._tags(spec, token),
            },
        )
        if "StorageCapacity" not in request:
            request["StorageCapacity"] = self._capacity(spec.size)
        request.setdefault("StorageType", "SSD")
        groups = list(
            cfg.get("security_group_ids") or request.get("SecurityGroupIds") or self._config.security_group_ids
        )
        if groups:
            request["SecurityGroupIds"] = groups
        self._apply_engine_create_defaults(request)
        deployment = str((request.get("LustreConfiguration") or {}).get("DeploymentType") or "")
        if (
            self._config.kms_key_id
            and "KmsKeyId" not in request
            and not (self.file_system_type == "LUSTRE" and deployment.startswith("SCRATCH_"))
        ):
            request["KmsKeyId"] = self._config.kms_key_id
        self._materialize_windows_password(request, cfg, validate_only=validate_only)
        return request

    def _restore_request(
        self,
        backup_id: str,
        spec: ProvisionSpec,
        *,
        token: str,
        validate_only: bool = False,
    ) -> dict[str, Any]:
        cfg = spec.config or {}
        request = dict(cfg.get("restore_file_system") or {})
        subnets = list(cfg.get("subnet_ids") or request.get("SubnetIds") or self._config.subnet_ids)
        request.update(
            {
                "BackupId": backup_id,
                "ClientRequestToken": token,
                "SubnetIds": self._selected_subnets(request, subnets),
                "Tags": self._tags(spec, token),
            },
        )
        groups = list(
            cfg.get("security_group_ids") or request.get("SecurityGroupIds") or self._config.security_group_ids
        )
        if groups:
            request["SecurityGroupIds"] = groups
        if self._config.kms_key_id and "KmsKeyId" not in request:
            request["KmsKeyId"] = self._config.kms_key_id
        if self.file_system_type == "WINDOWS" and isinstance(request.get("WindowsConfiguration"), dict):
            request["WindowsConfiguration"].setdefault("ThroughputCapacity", 32)
        if self.file_system_type == "OPENZFS" and isinstance(request.get("OpenZFSConfiguration"), dict):
            request["OpenZFSConfiguration"].setdefault("DeploymentType", "SINGLE_AZ_1")
            request["OpenZFSConfiguration"].setdefault("ThroughputCapacity", 64)
        self._materialize_windows_password(request, cfg, validate_only=validate_only)
        _validate_request("CreateFileSystemFromBackup", request)
        return request

    def _apply_engine_create_defaults(self, request: dict[str, Any]) -> None:
        if self.file_system_type == "LUSTRE":
            request.setdefault("FileSystemTypeVersion", "2.15")
            engine = dict(request.get("LustreConfiguration") or {})
            engine.setdefault("DeploymentType", "PERSISTENT_2")
            if request.get("StorageType") == "SSD":
                engine.setdefault("PerUnitStorageThroughput", 125)
            engine.setdefault("AutomaticBackupRetentionDays", 7)
            engine.setdefault("CopyTagsToBackups", True)
            request["LustreConfiguration"] = engine
        elif self.file_system_type == "OPENZFS":
            engine = dict(request.get("OpenZFSConfiguration") or {})
            engine.setdefault("DeploymentType", "SINGLE_AZ_1")
            engine.setdefault("ThroughputCapacity", 64)
            engine.setdefault("AutomaticBackupRetentionDays", 7)
            engine.setdefault("CopyTagsToBackups", True)
            engine.setdefault("CopyTagsToVolumes", True)
            request["OpenZFSConfiguration"] = engine
        else:
            engine = dict(request.get("WindowsConfiguration") or {})
            engine.setdefault("DeploymentType", "SINGLE_AZ_2")
            engine.setdefault("ThroughputCapacity", 32)
            engine.setdefault("AutomaticBackupRetentionDays", 7)
            engine.setdefault("CopyTagsToBackups", True)
            request["WindowsConfiguration"] = engine

    def _apply_update(
        self,
        file_system: dict[str, Any],
        cfg: dict[str, Any],
        *,
        size: str | None = None,
    ) -> None:
        update = dict(cfg.get("file_system_update") or {})
        if size and size != "custom" and "StorageCapacity" not in update:
            desired_capacity = self._capacity(size)
            if int(file_system.get("StorageCapacity") or 0) != desired_capacity:
                update["StorageCapacity"] = desired_capacity
        if not update:
            return
        file_system_id = str(file_system.get("FileSystemId") or "")
        request = {**update, "FileSystemId": file_system_id}
        request.setdefault("ClientRequestToken", _token(f"update-{file_system_id}-{_digest(update)}"))
        self._materialize_windows_password(request, cfg)
        digest = _digest(request)
        if _tag_map(file_system.get("Tags") or []).get(_UPDATE_PLAN_TAG) == digest:
            return
        _validate_request("UpdateFileSystem", request)
        self._fsx.update_file_system(**request)
        updated = self._await_file_system(file_system_id)
        self._tag_file_system(updated, [{"Key": _UPDATE_PLAN_TAG, "Value": digest}])

    def _materialize_windows_password(
        self,
        request: dict[str, Any],
        cfg: dict[str, Any],
        *,
        validate_only: bool = False,
    ) -> None:
        if self.file_system_type != "WINDOWS":
            return
        key = "WindowsConfiguration"
        windows = request.get(key)
        if not isinstance(windows, dict):
            return
        self_managed = windows.get("SelfManagedActiveDirectoryConfiguration")
        if not isinstance(self_managed, dict):
            return
        password_ref = str(cfg.get("active_directory_password_secret_ref") or "")
        if password_ref:
            if self_managed.get("DomainJoinServiceAccountSecret"):
                raise ManagedServiceError(
                    "active_directory_password_secret_ref and DomainJoinServiceAccountSecret are mutually exclusive",
                )
            self_managed = dict(self_managed)
            self_managed["Password"] = (
                "Astrolift-Validation-Password-123!" if validate_only else self._read_secret_scalar(password_ref)
            )
            windows = dict(windows)
            windows["SelfManagedActiveDirectoryConfiguration"] = self_managed
            request[key] = windows

    def _read_secret_scalar(self, secret_ref: str) -> str:
        if self._secrets is None:
            self._secrets = aws_client("secretsmanager", region=self._config.region, credential=self._config.credential)
        secret_id, _, field = secret_ref.partition("#")
        response = self._secrets.get_secret_value(SecretId=secret_id)
        value = str(response.get("SecretString") or "")
        if field:
            payload = json.loads(value)
            if not isinstance(payload, dict) or field not in payload:
                raise ManagedServiceError(f"secret {secret_id} does not contain field {field}")
            selected = payload[field]
            if isinstance(selected, (dict, list)):
                raise ManagedServiceError(f"secret {secret_id}#{field} must contain a scalar")
            value = str(selected)
        else:
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                selected = payload.get("password")
                if selected is None or isinstance(selected, (dict, list)):
                    raise ManagedServiceError(
                        f"secret {secret_id} contains JSON; select a scalar with #field",
                    )
                value = str(selected)
        if not value:
            raise ManagedServiceError(f"secret {secret_id} is empty")
        return value

    def _selected_subnets(self, request: dict[str, Any], subnets: list[str]) -> list[str]:
        if not subnets:
            raise ManagedServiceError(f"{self.display_name} requires at least one private subnet")
        if self.file_system_type == "WINDOWS":
            deployment = str((request.get("WindowsConfiguration") or {}).get("DeploymentType") or "SINGLE_AZ_2")
            required = 2 if deployment == "MULTI_AZ_1" else 1
        else:
            required = 1
        if len(set(subnets)) < required:
            deployment_label = deployment if self.file_system_type == "WINDOWS" else self.file_system_type
            raise ManagedServiceError(
                f"{self.display_name} {deployment_label} requires {required} distinct subnet(s)",
            )
        unique_subnets = list(dict.fromkeys(subnets))
        if self.file_system_type == "OPENZFS":
            preferred = str((request.get("OpenZFSConfiguration") or {}).get("PreferredSubnetId") or "")
            if preferred:
                if preferred not in unique_subnets:
                    raise ManagedServiceError("OpenZFS PreferredSubnetId must be one of the configured subnets")
                unique_subnets = [preferred, *[subnet for subnet in unique_subnets if subnet != preferred]]
        selected = unique_subnets[:required]
        if self.file_system_type == "WINDOWS" and required == 2:
            windows = request.setdefault("WindowsConfiguration", {})
            preferred = str(windows.get("PreferredSubnetId") or selected[0])
            if preferred not in selected:
                raise ManagedServiceError("Windows PreferredSubnetId must be one of SubnetIds")
            windows["PreferredSubnetId"] = preferred
        return selected

    def _mount_details(self, file_system: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str, list[str]]:
        endpoint = str(file_system.get("DNSName") or "")
        options = list(cfg.get("mount_options") or [])
        if self.file_system_type == "LUSTRE":
            mount_name = str(
                cfg.get("mount_name") or (file_system.get("LustreConfiguration") or {}).get("MountName") or ""
            )
            if not mount_name:
                raise ManagedServiceError("FSx for Lustre has no MountName")
            return "lustre", f"{endpoint}@tcp:/{mount_name}", options
        if self.file_system_type == "OPENZFS":
            root_id = str((file_system.get("OpenZFSConfiguration") or {}).get("RootVolumeId") or "")
            volume_path = "/fsx"
            if root_id:
                volumes = self._fsx.describe_volumes(VolumeIds=[root_id]).get("Volumes", [])
                if volumes:
                    volume_path = str((volumes[0].get("OpenZFSConfiguration") or {}).get("VolumePath") or volume_path)
            if not any(option.startswith("nfsvers=") for option in options):
                options.append("nfsvers=4.1")
            return "nfs4.1", f"{endpoint}:{volume_path}", options
        share = str(cfg.get("share_name") or "share").strip("\\/")
        if not share:
            raise ManagedServiceError("FSx for Windows share_name cannot be empty")
        if not any(option.startswith("vers=") for option in options):
            options.append("vers=3.0")
        return "smb3", f"\\\\{endpoint}\\{share}", options

    def _volume_mount(
        self,
        *,
        file_system_id: str,
        file_system: dict[str, Any],
        endpoint: str,
        protocol: str,
        source: str,
        options: list[str],
        mount_path: str,
        cfg: dict[str, Any],
    ) -> VolumeMount:
        common = {
            "name": f"fsx-{file_system_id}",
            "mount_path": mount_path,
            "source_kind": VolumeSourceKind.CSI,
            "protocol": protocol,
            "mount_options": options,
            "read_only": bool(cfg.get("read_only", False)),
            "capacity": f"{int(file_system.get('StorageCapacity') or 1)}Gi",
        }
        if self.file_system_type == "LUSTRE":
            mount_name = str(
                cfg.get("mount_name") or (file_system.get("LustreConfiguration") or {}).get("MountName") or ""
            )
            return VolumeMount(
                **common,
                csi_driver="fsx.csi.aws.com",
                volume_handle=file_system_id,
                volume_attributes={"dnsname": endpoint, "mountname": mount_name},
            )
        if self.file_system_type == "OPENZFS":
            _, _, share = source.partition(":")
            return VolumeMount(
                **common,
                csi_driver="nfs.csi.k8s.io",
                volume_handle=f"{endpoint}#{share}#",
                volume_attributes={"server": endpoint, "share": share},
            )
        username_ref = str(cfg.get("mount_username_secret_ref") or "")
        password_ref = str(cfg.get("mount_password_secret_ref") or "")
        if not username_ref or not password_ref:
            raise ManagedServiceError(
                "FSx for Windows workload attachment requires mount_username_secret_ref and mount_password_secret_ref",
            )
        share = source.rsplit("\\", 1)[-1]
        return VolumeMount(
            **common,
            csi_driver="smb.csi.k8s.io",
            volume_handle=f"{endpoint}##{share}",
            volume_attributes={"source": f"//{endpoint}/{share}"},
            secret_refs={"username": username_ref, "password": password_ref},
        )

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        size: str,
        partial: bool = False,
        restoring: bool = False,
    ) -> str:
        if self._config.poll_delay_seconds < 0 or self._config.max_poll_attempts < 1:
            return "FSx polling defaults require a non-negative delay and at least one attempt"
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported {self.variant} config fields: {unknown}"
        for field in ("file_system", "file_system_update", "restore_file_system"):
            if field in cfg and not isinstance(cfg[field], dict):
                return f"{field} must be an object"
            if field in cfg and _contains_plaintext_password(cfg[field]):
                return f"{field} cannot contain plaintext password fields; use a Secrets Manager reference"
        for field in ("tls", "read_only", "deletion_protection"):
            if field in cfg and not isinstance(cfg[field], bool):
                return f"{field} must be a boolean"
        for field in ("subnet_ids", "security_group_ids", "mount_options"):
            if field in cfg and (
                not isinstance(cfg[field], list) or any(not isinstance(value, str) or not value for value in cfg[field])
            ):
                return f"{field} must be an array of non-empty strings"
        mount_path = cfg.get("mount_path")
        if mount_path is not None and (not isinstance(mount_path, str) or not mount_path.startswith("/")):
            return "mount_path must be an absolute path"
        if "active_directory_password_secret_ref" in cfg and (
            self.file_system_type != "WINDOWS"
            or not isinstance(cfg["active_directory_password_secret_ref"], str)
            or not cfg["active_directory_password_secret_ref"]
        ):
            return "active_directory_password_secret_ref is a non-empty Windows-only setting"
        if cfg.get("active_directory_password_secret_ref"):
            fragment_key = "restore_file_system" if restoring else ("file_system_update" if partial else "file_system")
            windows = (cfg.get(fragment_key) or {}).get("WindowsConfiguration") or {}
            self_managed = windows.get("SelfManagedActiveDirectoryConfiguration") or {}
            if not self_managed and not partial:
                return (
                    "active_directory_password_secret_ref requires "
                    f"{fragment_key}.WindowsConfiguration.SelfManagedActiveDirectoryConfiguration"
                )
            if self_managed.get("DomainJoinServiceAccountSecret"):
                return "active_directory_password_secret_ref and DomainJoinServiceAccountSecret are mutually exclusive"
        for field in ("mount_username_secret_ref", "mount_password_secret_ref"):
            if field in cfg and (
                self.file_system_type != "WINDOWS" or not isinstance(cfg[field], str) or not cfg[field]
            ):
                return f"{field} is a non-empty Windows-only setting"
        if self.file_system_type != "WINDOWS" and "share_name" in cfg:
            return "share_name is supported only for FSx for Windows"
        if self.file_system_type != "LUSTRE" and "mount_name" in cfg:
            return "mount_name is supported only for FSx for Lustre"
        if size not in {*_SIZE_CAPACITY[self.file_system_type], "custom"}:
            return f"unsupported FSx size {size!r}; use small, medium, large, xlarge, or custom"
        try:
            if not partial and not restoring:
                probe = ProvisionSpec(
                    "org",
                    "org",
                    "app",
                    "app",
                    "env",
                    "prod",
                    "cluster",
                    "filesystem",
                    size,
                    cfg,
                )
                _validate_request(
                    "CreateFileSystem", self._create_request(probe, token="astrolift-validation", validate_only=True)
                )
            if cfg.get("file_system_update"):
                request = {**cfg["file_system_update"], "FileSystemId": "fs-12345678"}
                request.setdefault("ClientRequestToken", "astrolift-validation")
                self._materialize_windows_password(request, cfg, validate_only=True)
                _validate_request("UpdateFileSystem", request)
            if restoring:
                probe = ProvisionSpec(
                    "org",
                    "org",
                    "app",
                    "app",
                    "env",
                    "prod",
                    "cluster",
                    "filesystem",
                    size,
                    cfg,
                )
                self._restore_request(
                    "backup-12345678",
                    probe,
                    token="astrolift-validation",
                    validate_only=True,
                )
        except Exception as exc:
            return f"invalid AWS FSx request: {exc}"
        if not partial and self.file_system_type == "WINDOWS" and not restoring:
            windows = (cfg.get("file_system") or {}).get("WindowsConfiguration") or {}
            self_managed = windows.get("SelfManagedActiveDirectoryConfiguration") or {}
            if windows.get("ActiveDirectoryId") and self_managed:
                return "ActiveDirectoryId and SelfManagedActiveDirectoryConfiguration are mutually exclusive"
            if not windows.get("ActiveDirectoryId") and not self_managed:
                return "FSx for Windows requires ActiveDirectoryId or SelfManagedActiveDirectoryConfiguration"
            if self_managed and not (
                self_managed.get("DomainJoinServiceAccountSecret") or cfg.get("active_directory_password_secret_ref")
            ):
                return (
                    "self-managed Active Directory requires DomainJoinServiceAccountSecret "
                    "or active_directory_password_secret_ref"
                )
        expected_key = self._configuration_key()
        engine_keys = {"LustreConfiguration", "OpenZFSConfiguration", "WindowsConfiguration"}
        for label, fragment in (
            ("file_system", cfg.get("file_system") or {}),
            ("restore_file_system", cfg.get("restore_file_system") or {}),
            ("file_system_update", cfg.get("file_system_update") or {}),
        ):
            wrong = sorted(engine_keys.intersection(fragment) - {expected_key})
            if wrong:
                return f"{label} for {self.variant} cannot contain engine configuration {wrong}"
        if self.file_system_type == "LUSTRE":
            create_fragment = cfg.get("file_system") or {}
            lustre = create_fragment.get("LustreConfiguration") or {}
            deployment = str(lustre.get("DeploymentType") or "")
            if deployment.startswith("SCRATCH_") and create_fragment.get("KmsKeyId"):
                return "FSx for Lustre scratch deployments cannot use KmsKeyId"
            if lustre.get("EfaEnabled") and not cfg.get("security_group_ids"):
                return "EFA-enabled Lustre requires explicit EFA-compatible security_group_ids"
        return ""

    def _find_by_token(self, token: str) -> dict[str, Any]:
        next_token = ""
        matches: list[dict[str, Any]] = []
        while True:
            request = {"MaxResults": 100}
            if next_token:
                request["NextToken"] = next_token
            response = self._fsx.describe_file_systems(**request)
            for file_system in response.get("FileSystems", []):
                if (
                    str(file_system.get("FileSystemType") or "") == self.file_system_type
                    and _tag_map(file_system.get("Tags") or []).get(_RESOURCE_TOKEN_TAG) == token
                ):
                    matches.append(dict(file_system))
            next_token = str(response.get("NextToken") or "")
            if not next_token:
                if len(matches) > 1:
                    raise ManagedServiceError(
                        f"multiple {self.display_name} filesystems use resource token {token!r}",
                    )
                return matches[0] if matches else {}

    def _describe_file_system(self, file_system_id: str) -> dict[str, Any] | None:
        try:
            response = self._fsx.describe_file_systems(FileSystemIds=[file_system_id])
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        systems = list(response.get("FileSystems") or [])
        return dict(systems[0]) if systems else None

    def _describe_backup(self, backup_id: str) -> dict[str, Any] | None:
        try:
            response = self._fsx.describe_backups(BackupIds=[backup_id])
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        backups = list(response.get("Backups") or [])
        return dict(backups[0]) if backups else None

    def _await_file_system(self, file_system_id: str) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._describe_file_system(file_system_id) or {}
            if not last:
                raise ManagedServiceError(f"FSx filesystem {file_system_id} disappeared")
            lifecycle = str(last.get("Lifecycle") or "").upper()
            if lifecycle in _TERMINAL_FILESYSTEM_STATES:
                detail = str((last.get("FailureDetails") or {}).get("Message") or lifecycle)
                raise ManagedServiceError(f"FSx filesystem entered {lifecycle}: {detail}")
            active = [
                action
                for action in last.get("AdministrativeActions") or []
                if str(action.get("Status") or "").upper() in {"PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING"}
            ]
            failed = [
                action
                for action in last.get("AdministrativeActions") or []
                if str(action.get("Status") or "").upper() == "FAILED"
            ]
            if failed:
                raise ManagedServiceError("an FSx administrative action failed")
            if lifecycle == "AVAILABLE" and not active:
                return last
            self._poll_sleep(attempt)
        raise ManagedServiceError(
            f"FSx filesystem did not become available; last={last.get('Lifecycle', 'unknown')}",
        )

    def _await_backup(self, backup_id: str) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._describe_backup(backup_id) or {}
            if not last:
                raise ManagedServiceError(f"FSx backup {backup_id} disappeared")
            lifecycle = str(last.get("Lifecycle") or "").upper()
            if lifecycle == "AVAILABLE":
                return last
            if lifecycle in _TERMINAL_BACKUP_STATES:
                detail = str((last.get("FailureDetails") or {}).get("Message") or lifecycle)
                raise ManagedServiceError(f"FSx backup entered {lifecycle}: {detail}")
            self._poll_sleep(attempt)
        raise ManagedServiceError(f"FSx backup did not become available; last={last.get('Lifecycle', 'unknown')}")

    def _tag_file_system(self, file_system: dict[str, Any], tags: list[dict[str, str]]) -> None:
        arn = str(file_system.get("ResourceARN") or "")
        if not arn:
            raise ManagedServiceError("FSx filesystem has no ResourceARN for ownership tagging")
        self._fsx.tag_resource(ResourceARN=arn, Tags=tags)

    def _verify_owned_type(self, file_system: dict[str, Any]) -> None:
        self._verify_type(file_system)
        if _tag_map(file_system.get("Tags") or []).get("astrolift.io/managed-by") != _MANAGED_BY:
            raise ManagedServiceError("FSx filesystem is not owned by Astrolift")

    def _verify_type(self, file_system: dict[str, Any]) -> None:
        actual = str(file_system.get("FileSystemType") or "")
        if actual != self.file_system_type:
            raise ManagedServiceError(f"FSx filesystem is {actual or 'unknown'}, not {self.file_system_type}")

    def _supports_backups(self, file_system: dict[str, Any]) -> bool:
        if self.file_system_type != "LUSTRE":
            return True
        deployment = str((file_system.get("LustreConfiguration") or {}).get("DeploymentType") or "")
        return deployment.startswith("PERSISTENT_")

    def _tags(self, spec: ProvisionSpec, token: str) -> list[dict[str, str]]:
        return [
            *tags_for(spec),
            {"Key": _RESOURCE_TOKEN_TAG, "Value": token},
            {"Key": "Name", "Value": token},
        ]

    def _client_token(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.client_token_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or self.variant,
                self.variant,
            )
            if part
        )
        return _token(raw)

    def _capacity(self, size: str) -> int:
        if size == "custom":
            raise ManagedServiceError("custom FSx size requires file_system.StorageCapacity")
        return _SIZE_CAPACITY[self.file_system_type][size]

    def _configuration_key(self) -> str:
        return {
            "LUSTRE": "LustreConfiguration",
            "OPENZFS": "OpenZFSConfiguration",
            "WINDOWS": "WindowsConfiguration",
        }[self.file_system_type]

    def _file_system_id(self, handle: str) -> str:
        kind, resource_id = parse_handle(handle)
        if kind != KIND or not resource_id.startswith("fs-"):
            raise ManagedServiceError(f"invalid {self.display_name} handle {handle!r}")
        return resource_id

    def _poll_sleep(self, attempt: int) -> None:
        if attempt + 1 < self._config.max_poll_attempts:
            self._sleep(self._config.poll_delay_seconds)


class FSxLustreDriver(FSxDriver):
    file_system_type = "LUSTRE"
    variant = "fsx_lustre"
    display_name = "Amazon FSx for Lustre"


class FSxOpenZFSDriver(FSxDriver):
    file_system_type = "OPENZFS"
    variant = "fsx_openzfs"
    display_name = "Amazon FSx for OpenZFS"


class FSxWindowsDriver(FSxDriver):
    file_system_type = "WINDOWS"
    variant = "fsx_windows"
    display_name = "Amazon FSx for Windows File Server"


def _token(value: str) -> str:
    safe = "".join(
        character if character.isascii() and (character.isalnum() or character in "_.-") else "-" for character in value
    )
    safe = safe.strip("-.") or "astrolift"
    if len(safe) <= 63:
        return safe
    digest = hashlib.sha256(safe.encode()).hexdigest()[:12]
    return f"{safe[:50].rstrip('-.')}-{digest}"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _tag_map(tags: list[dict[str, Any]]) -> dict[str, str]:
    return {str(tag.get("Key") or ""): str(tag.get("Value") or "") for tag in tags}


def _contains_plaintext_password(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in {"password", "serviceaccountpassword", "fsxadminpassword"}:
                return True
            if _contains_plaintext_password(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_plaintext_password(item) for item in value)
    return False


def _validate_request(operation: str, request: dict[str, Any]) -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    service = Session().get_service_model("fsx")
    validate_parameters(request, service.operation_model(operation).input_shape)


def _not_found(exc: Exception) -> bool:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    code = str(error.get("Code") or "").casefold()
    return (
        code in {"filesystemnotfound", "backupnotfound", "resourcenotfoundexception"}
        or "not found" in str(exc).casefold()
    )


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    code = str(error.get("Code") or "")
    retryable = code not in {"BadRequest", "ValidationException", "AccessDeniedException"}
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [code or str(exc)], retryable=retryable)
