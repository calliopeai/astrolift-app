"""Amazon EFS managed shared-filesystem lifecycle.

The driver creates an encrypted file system, one mount target per selected AZ,
and (by default) a POSIX access point.  Config accepts AWS-native request
fragments while ownership, idempotency, destructive safety, and portable
binding fields remain platform controlled.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
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
    VolumeSourceKind,
)
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for

KIND = "filesystem"
_MANAGED_BY = "platform"
_STATE = {
    "creating": "provisioning",
    "available": "available",
    "updating": "updating",
    "deleting": "deprovisioning",
    "deleted": "deprovisioned",
    "error": "error",
}


@dataclass(frozen=True)
class EFSConfig:
    region: str
    account_id: str
    subnet_ids: tuple[str, ...] = ()
    security_group_ids: tuple[str, ...] = ()
    kms_key_id: str = ""
    creation_token_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    poll_delay_seconds: float = 5
    max_poll_attempts: int = 120


class EFSDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: EFSConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._config = config
        if client is None:
            import boto3

            client = boto3.client("efs", region_name=config.region)
        self._efs = client
        self._sleep = sleep

    @driver_op(
        cloud="aws",
        driver="filesystem_efs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_efs_config"])
        token = self._creation_token(spec)
        file_system = self._find_by_token(token)
        file_system_id = str(file_system.get("FileSystemId") or "")
        access_point_id = ""
        created = False
        try:
            if file_system_id:
                file_system = self._await_file_system(file_system_id, {"available"})
                if not self._is_managed(file_system):
                    raise ManagedServiceError(
                        f"EFS creation token {token!r} belongs to a filesystem outside this declaration",
                    )
                self._efs.tag_resource(ResourceId=file_system_id, Tags=_tag_list(spec))
            else:
                response = self._efs.create_file_system(**self._create_file_system_request(token, spec))
                file_system_id = str(response.get("FileSystemId") or "")
                created = True
                file_system = self._await_file_system(file_system_id, {"available"})
            self._reconcile_policies(file_system_id, cfg)
            self._reconcile_replication(file_system_id, cfg)
            self._reconcile_mount_targets(file_system_id, cfg)
            if cfg.get("create_access_point", True):
                access_point = self._ensure_access_point(file_system_id, token, spec)
                access_point_id = str(access_point.get("AccessPointId") or "")
        except Exception as exc:
            handle = _handle(file_system_id, access_point_id) if file_system_id else ""
            action = "configure new" if created else "reconcile existing"
            return ProvisionResult(False, handle, f"{action} EFS filesystem: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            _handle(file_system_id, access_point_id),
            f"EFS filesystem {file_system_id} and mount topology are available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="filesystem_efs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        file_system_id, _ = _parse_resource(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_efs_config"])
        try:
            file_system = self._describe_file_system(file_system_id)
            if file_system is None:
                return UpdateResult(False, spec.handle, "EFS filesystem not found", ["not_found"])
            if not self._is_managed(file_system):
                raise ManagedServiceError("EFS filesystem is not owned by Astrolift")
            update = dict(cfg.get("file_system_update") or {})
            if update:
                request = {**update, "FileSystemId": file_system_id}
                _validate_request("UpdateFileSystem", request)
                self._efs.update_file_system(**request)
                self._await_file_system(file_system_id, {"available"})
            self._reconcile_policies(file_system_id, cfg)
            if "replication_configuration" in cfg:
                self._reconcile_replication(file_system_id, cfg)
            if "mount_targets" in cfg or "mount_target_security_group_ids" in cfg:
                self._reconcile_mount_targets(file_system_id, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "EFS filesystem not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update EFS filesystem: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "EFS filesystem reconciled")

    @driver_op(
        cloud="aws",
        driver="filesystem_efs",
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
        file_system_id, _ = _parse_resource(spec.handle)
        cfg = spec.config or {}
        file_system = self._describe_file_system(file_system_id)
        if file_system is None:
            return DeprovisionResult(True, spec.handle, "EFS filesystem already gone")
        if not self._is_managed(file_system):
            return DeprovisionResult(
                False,
                spec.handle,
                "EFS filesystem is not owned by Astrolift and will not be deleted",
                ["external_resource_collision"],
                retryable=False,
            )
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "EFS filesystem has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "EFS has no native snapshot API; preserve the filesystem or take an AWS Backup recovery point, "
                "then set delete_data=true",
                ["retained_filesystem_data_requires_delete_data"],
                retryable=False,
            )
        try:
            self._delete_replication(file_system_id, cfg)
            access_points = self._access_points(file_system_id)
            for access_point in access_points:
                candidate = str(access_point.get("AccessPointId") or "")
                if candidate and self._is_managed(access_point):
                    self._delete_access_point(candidate)
            targets = self._mount_targets(file_system_id)
            for target in targets:
                mount_target_id = str(target.get("MountTargetId") or "")
                if mount_target_id:
                    self._efs.delete_mount_target(MountTargetId=mount_target_id)
            self._await_no_mount_targets(file_system_id)
            self._efs.delete_file_system(FileSystemId=file_system_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "EFS filesystem deletion converged")
            return _deprovision_error(spec.handle, "delete EFS filesystem", exc)
        return DeprovisionResult(True, spec.handle, "EFS filesystem deletion queued")

    @driver_op(cloud="aws", driver="filesystem_efs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        file_system_id, access_point_id = _parse_resource(handle.handle)
        try:
            file_system = self._describe_file_system(file_system_id)
            if file_system is None:
                return ServiceStatus(handle.handle, "deprovisioned", "EFS filesystem does not exist")
            provider_state = str(file_system.get("LifeCycleState") or "error")
            state = _STATE.get(provider_state, "error")
            if state == "available":
                targets = self._mount_targets(file_system_id)
                target_states = {str(target.get("LifeCycleState") or "error") for target in targets}
                if not targets or target_states - {"available"}:
                    state = "provisioning" if target_states <= {"creating", "updating"} else "error"
                if access_point_id:
                    access_point = self._describe_access_point(access_point_id)
                    ap_state = str((access_point or {}).get("LifeCycleState") or "deleted")
                    if ap_state != "available":
                        state = "provisioning" if ap_state == "creating" else "error"
            return ServiceStatus(
                handle.handle,
                state,
                f"EFS reports {provider_state} with {len(self._mount_targets(file_system_id))} mount target(s)",
            )
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "EFS filesystem does not exist")
            return ServiceStatus(handle.handle, "error", f"describe EFS filesystem: {exc}")

    @driver_op(cloud="aws", driver="filesystem_efs")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        file_system_id, access_point_id = _parse_resource(handle.handle)
        cfg = config or {}
        file_system = self._describe_file_system(file_system_id)
        if file_system is None:
            raise ManagedServiceError("EFS filesystem not found")
        if not self._is_managed(file_system):
            raise ManagedServiceError("EFS filesystem is not owned by Astrolift")
        mount_path = str(cfg.get("mount_path") or "/mnt/shared")
        tls = bool(cfg.get("tls", True))
        options = list(cfg.get("mount_options") or [])
        if tls and "tls" not in options:
            options.append("tls")
        if access_point_id:
            if "accesspoint=" + access_point_id not in options:
                options.append(f"accesspoint={access_point_id}")
            if tls and "iam" not in options:
                options.append("iam")
        dns_name = f"{file_system_id}.efs.{self._config.region}.amazonaws.com"
        file_system_arn = str(
            file_system.get("FileSystemArn")
            or (
                f"arn:aws:elasticfilesystem:{self._config.region}:{self._config.account_id}"
                f":file-system/{file_system_id}"
            ),
        )
        actions = ["elasticfilesystem:ClientMount"]
        if cfg.get("read_only") is not True:
            actions.append("elasticfilesystem:ClientWrite")
        if cfg.get("client_root_access"):
            actions.append("elasticfilesystem:ClientRootAccess")
        return Binding(
            env_vars={
                "FILESYSTEM_HANDLE": ValueRef(literal=file_system_id),
                "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
                "FILESYSTEM_TLS": ValueRef(literal=str(tls).lower()),
                "FILESYSTEM_PROTOCOL": ValueRef(literal="nfs4"),
                "FILESYSTEM_ENDPOINT": ValueRef(literal=dns_name),
                "FILESYSTEM_MOUNT_OPTIONS": ValueRef(literal=",".join(options)),
                "EFS_FILE_SYSTEM_ID": ValueRef(literal=file_system_id),
                "EFS_ACCESS_POINT_ID": ValueRef(literal=access_point_id),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[Grant(file_system_arn, actions)],
            pod_volume_mounts=[
                VolumeMount(
                    name=f"efs-{file_system_id}",
                    mount_path=mount_path,
                    source_kind=VolumeSourceKind.CSI,
                    protocol="nfs4",
                    csi_driver="efs.csi.aws.com",
                    volume_handle=(f"{file_system_id}::{access_point_id}" if access_point_id else file_system_id),
                    mount_options=options,
                    read_only=bool(cfg.get("read_only", False)),
                    capacity="1Gi",
                ),
            ],
            notes="Amazon EFS NFSv4 shared filesystem; mount through the EFS CSI driver",
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "EFS exposes recovery points through AWS Backup, not the EFS API; configure an AWS Backup plan",
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError(
            "EFS recovery points must be restored through AWS Backup before registering the restored filesystem",
        )

    @driver_op(cloud="aws", driver="filesystem_efs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native = {"type": "object", "additionalProperties": True}
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "file_system": native,
                "file_system_update": native,
                "mount_targets": {"type": "array", "items": native, "minItems": 1},
                "mount_target_security_group_ids": {"type": "array", "items": {"type": "string"}},
                "prune_mount_targets": {"type": "boolean", "default": False},
                "create_access_point": {"type": "boolean", "default": True},
                "access_point": native,
                "root_path": {"type": "string", "default": "/astrolift"},
                "posix_uid": {"type": "integer", "minimum": 0},
                "posix_gid": {"type": "integer", "minimum": 0},
                "root_permissions": {"type": "string", "pattern": "^[0-7]{3,4}$"},
                "lifecycle_policies": {"type": "array", "items": native, "maxItems": 3},
                "backup_policy": {"type": "string", "enum": ["ENABLED", "DISABLED"]},
                "replication_overwrite_protection": {
                    "type": "string",
                    "enum": ["ENABLED", "DISABLED", "REPLICATING"],
                },
                "file_system_policy": native,
                "delete_file_system_policy": {"type": "boolean", "default": False},
                "bypass_policy_lockout_safety_check": {"type": "boolean", "default": False},
                "replication_configuration": native,
                "replication_deletion_mode": {
                    "type": "string",
                    "enum": ["ALL_CONFIGURATIONS", "LOCAL_CONFIGURATION_ONLY"],
                },
                "mount_path": {"type": "string"},
                "mount_options": {"type": "array", "items": {"type": "string"}},
                "tls": {"type": "boolean", "default": True},
                "read_only": {"type": "boolean", "default": False},
                "client_root_access": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="filesystem_efs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Portable cloud filesystem identifier",
                "FILESYSTEM_MOUNT_PATH": "Container mount path",
                "FILESYSTEM_TLS": "Whether in-transit encryption is enabled",
                "FILESYSTEM_PROTOCOL": "nfs4",
                "FILESYSTEM_ENDPOINT": "Regional EFS DNS name",
                "FILESYSTEM_MOUNT_OPTIONS": "Comma-separated EFS mount options",
                "EFS_FILE_SYSTEM_ID": "Amazon EFS filesystem ID",
                "EFS_ACCESS_POINT_ID": "Amazon EFS access point ID",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "file_system_update",
            "mount_targets",
            "mount_target_security_group_ids",
            "prune_mount_targets",
            "lifecycle_policies",
            "backup_policy",
            "replication_overwrite_protection",
            "file_system_policy",
            "delete_file_system_policy",
            "bypass_policy_lockout_safety_check",
            "mount_path",
            "mount_options",
            "tls",
            "read_only",
            "client_root_access",
            "deletion_protection",
        ]

    def _create_file_system_request(self, token: str, spec: ProvisionSpec) -> dict[str, Any]:
        cfg = spec.config or {}
        request = dict(cfg.get("file_system") or {})
        request.update(
            {
                "CreationToken": token,
                "Encrypted": bool(request.get("Encrypted", True)),
                "Tags": _tag_list(spec),
            },
        )
        request.setdefault("PerformanceMode", "generalPurpose")
        request.setdefault("ThroughputMode", "elastic")
        if self._config.kms_key_id and "KmsKeyId" not in request:
            request["KmsKeyId"] = self._config.kms_key_id
        return request

    def _reconcile_policies(self, file_system_id: str, cfg: dict[str, Any]) -> None:
        if "lifecycle_policies" in cfg:
            request = {
                "FileSystemId": file_system_id,
                "LifecyclePolicies": list(cfg.get("lifecycle_policies") or []),
            }
            _validate_request("PutLifecycleConfiguration", request)
            self._efs.put_lifecycle_configuration(**request)
        if "backup_policy" in cfg:
            self._efs.put_backup_policy(
                FileSystemId=file_system_id,
                BackupPolicy={"Status": str(cfg["backup_policy"])},
            )
        if "replication_overwrite_protection" in cfg:
            self._efs.update_file_system_protection(
                FileSystemId=file_system_id,
                ReplicationOverwriteProtection=str(cfg["replication_overwrite_protection"]),
            )
        if cfg.get("delete_file_system_policy"):
            self._efs.delete_file_system_policy(FileSystemId=file_system_id)
        elif "file_system_policy" in cfg:
            policy = json.dumps(cfg["file_system_policy"], sort_keys=True, separators=(",", ":"))
            self._efs.put_file_system_policy(
                FileSystemId=file_system_id,
                Policy=policy,
                BypassPolicyLockoutSafetyCheck=bool(cfg.get("bypass_policy_lockout_safety_check", False)),
            )

    def _reconcile_replication(self, file_system_id: str, cfg: dict[str, Any]) -> None:
        replication = cfg.get("replication_configuration")
        if not replication:
            return
        request = {**dict(replication), "SourceFileSystemId": file_system_id}
        _validate_request("CreateReplicationConfiguration", request)
        existing = self._replication_configurations(file_system_id)
        if existing:
            if not _replication_matches(request, existing[0]):
                raise ManagedServiceError(
                    "EFS replication destination differs from the immutable live configuration; reprovision it",
                )
            return
        try:
            self._efs.create_replication_configuration(**request)
        except Exception as exc:
            if not _already_exists(exc):
                raise

    def _delete_replication(self, file_system_id: str, cfg: dict[str, Any]) -> None:
        if not self._replication_configurations(file_system_id):
            return
        self._efs.delete_replication_configuration(
            SourceFileSystemId=file_system_id,
            DeletionMode=str(cfg.get("replication_deletion_mode") or "ALL_CONFIGURATIONS"),
        )

    def _reconcile_mount_targets(self, file_system_id: str, cfg: dict[str, Any]) -> None:
        existing = self._mount_targets(file_system_id)
        by_subnet = {str(target.get("SubnetId") or ""): target for target in existing}
        configured = cfg.get("mount_targets")
        if configured is None:
            requests = [{"SubnetId": subnet_id} for subnet_id in self._config.subnet_ids]
        else:
            requests = [dict(request) for request in configured]
        security_groups = list(
            cfg.get("mount_target_security_group_ids") or self._config.security_group_ids,
        )
        desired_subnets = {str(request.get("SubnetId") or "") for request in requests}
        if cfg.get("prune_mount_targets"):
            for target in existing:
                subnet_id = str(target.get("SubnetId") or "")
                mount_target_id = str(target.get("MountTargetId") or "")
                if subnet_id not in desired_subnets and mount_target_id:
                    self._efs.delete_mount_target(MountTargetId=mount_target_id)
            if any(str(target.get("SubnetId") or "") not in desired_subnets for target in existing):
                self._await_mount_target_subnets(file_system_id, desired_subnets)
                existing = self._mount_targets(file_system_id)
                by_subnet = {str(target.get("SubnetId") or ""): target for target in existing}
        for request in requests:
            subnet_id = str(request.get("SubnetId") or "")
            if subnet_id in by_subnet:
                mount_target_id = str(by_subnet[subnet_id].get("MountTargetId") or "")
                if mount_target_id and security_groups:
                    self._efs.modify_mount_target_security_groups(
                        MountTargetId=mount_target_id,
                        SecurityGroups=security_groups,
                    )
                continue
            request["FileSystemId"] = file_system_id
            if security_groups and "SecurityGroups" not in request:
                request["SecurityGroups"] = security_groups
            _validate_request("CreateMountTarget", request)
            try:
                self._efs.create_mount_target(**request)
            except Exception as exc:
                if not _already_exists(exc):
                    raise
                refreshed = {str(target.get("SubnetId") or "") for target in self._mount_targets(file_system_id)}
                if subnet_id not in refreshed:
                    raise ManagedServiceError(
                        f"EFS already has a conflicting mount target in the requested AZ for subnet {subnet_id}",
                    ) from exc
        self._await_mount_targets(file_system_id, expected=max(len(existing), len(requests)))

    def _ensure_access_point(
        self,
        file_system_id: str,
        token: str,
        spec: ProvisionSpec,
    ) -> dict[str, Any]:
        access_token = _token(f"{token}-access-point")
        for access_point in self._access_points(file_system_id):
            if access_point.get("ClientToken") == access_token:
                if not self._is_managed(access_point):
                    raise ManagedServiceError("EFS access point collision is not Astrolift-owned")
                return self._await_access_point(str(access_point["AccessPointId"]), {"available"})
        cfg = spec.config or {}
        request = dict(cfg.get("access_point") or {})
        uid = int(cfg.get("posix_uid", 1000))
        gid = int(cfg.get("posix_gid", 1000))
        root_path = str(cfg.get("root_path") or "/astrolift")
        request.update(
            {
                "ClientToken": access_token,
                "FileSystemId": file_system_id,
                "Tags": _tag_list(spec),
            },
        )
        request.setdefault("PosixUser", {"Uid": uid, "Gid": gid})
        request.setdefault(
            "RootDirectory",
            {
                "Path": root_path,
                "CreationInfo": {
                    "OwnerUid": uid,
                    "OwnerGid": gid,
                    "Permissions": str(cfg.get("root_permissions") or "0750"),
                },
            },
        )
        _validate_request("CreateAccessPoint", request)
        try:
            response = self._efs.create_access_point(**request)
        except Exception as exc:
            if not _already_exists(exc):
                raise
            match = next(
                (
                    point
                    for point in self._access_points(file_system_id)
                    if point.get("ClientToken") == access_token and self._is_managed(point)
                ),
                None,
            )
            if match is None:
                raise ManagedServiceError("EFS access point collision is not Astrolift-owned") from exc
            response = match
        return self._await_access_point(str(response.get("AccessPointId") or ""), {"available"})

    def _find_by_token(self, token: str) -> dict[str, Any]:
        response = self._efs.describe_file_systems(CreationToken=token)
        systems = response.get("FileSystems") or []
        return dict(systems[0]) if systems else {}

    def _describe_file_system(self, file_system_id: str) -> dict[str, Any] | None:
        try:
            response = self._efs.describe_file_systems(FileSystemId=file_system_id)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        systems = response.get("FileSystems") or []
        return dict(systems[0]) if systems else None

    def _access_points(self, file_system_id: str) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"FileSystemId": file_system_id, "MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self._efs.describe_access_points(**request)
            points.extend(dict(point) for point in response.get("AccessPoints") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return points

    def _describe_access_point(self, access_point_id: str) -> dict[str, Any] | None:
        try:
            response = self._efs.describe_access_points(AccessPointId=access_point_id)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        points = response.get("AccessPoints") or []
        return dict(points[0]) if points else None

    def _mount_targets(self, file_system_id: str) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        marker = ""
        while True:
            request: dict[str, Any] = {"FileSystemId": file_system_id, "MaxItems": 100}
            if marker:
                request["Marker"] = marker
            response = self._efs.describe_mount_targets(**request)
            targets.extend(dict(target) for target in response.get("MountTargets") or [])
            marker = str(response.get("NextMarker") or "")
            if not marker:
                return targets

    def _replication_configurations(self, file_system_id: str) -> list[dict[str, Any]]:
        response = self._efs.describe_replication_configurations(FileSystemId=file_system_id)
        return [dict(replication) for replication in response.get("Replications") or []]

    def _await_file_system(self, file_system_id: str, desired: set[str]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            described = self._describe_file_system(file_system_id)
            if described is None:
                if "deleted" in desired:
                    return {"FileSystemId": file_system_id, "LifeCycleState": "deleted"}
                raise ManagedServiceError(f"EFS filesystem {file_system_id} disappeared")
            last = described
            state = str(last.get("LifeCycleState") or "error")
            if state in desired:
                return last
            if state in {"error", "deleted"}:
                raise ManagedServiceError(f"EFS filesystem entered terminal state {state}")
            self._poll_sleep(attempt)
        raise ManagedServiceError(
            f"EFS filesystem did not reach {sorted(desired)}; last={last.get('LifeCycleState', 'unknown')}",
        )

    def _await_access_point(self, access_point_id: str, desired: set[str]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            described = self._describe_access_point(access_point_id)
            if described is None:
                if "deleted" in desired:
                    return {"AccessPointId": access_point_id, "LifeCycleState": "deleted"}
                raise ManagedServiceError(f"EFS access point {access_point_id} disappeared")
            last = described
            state = str(last.get("LifeCycleState") or "error")
            if state in desired:
                return last
            if state in {"error", "deleted"}:
                raise ManagedServiceError(f"EFS access point entered terminal state {state}")
            self._poll_sleep(attempt)
        raise ManagedServiceError(
            f"EFS access point did not reach {sorted(desired)}; last={last.get('LifeCycleState', 'unknown')}",
        )

    def _await_mount_targets(self, file_system_id: str, *, expected: int) -> None:
        last: list[dict[str, Any]] = []
        for attempt in range(self._config.max_poll_attempts):
            last = self._mount_targets(file_system_id)
            states = {str(target.get("LifeCycleState") or "error") for target in last}
            if len(last) >= expected and states <= {"available"}:
                return
            if "error" in states:
                raise ManagedServiceError("an EFS mount target entered the error state")
            self._poll_sleep(attempt)
        raise ManagedServiceError(f"EFS mount targets did not become available; observed={last}")

    def _await_no_mount_targets(self, file_system_id: str) -> None:
        for attempt in range(self._config.max_poll_attempts):
            if not self._mount_targets(file_system_id):
                return
            self._poll_sleep(attempt)
        raise ManagedServiceError("EFS mount targets did not finish deleting")

    def _await_mount_target_subnets(self, file_system_id: str, desired_subnets: set[str]) -> None:
        for attempt in range(self._config.max_poll_attempts):
            live = {str(target.get("SubnetId") or "") for target in self._mount_targets(file_system_id)}
            if live <= desired_subnets:
                return
            self._poll_sleep(attempt)
        raise ManagedServiceError("EFS mount targets selected for pruning did not finish deleting")

    def _delete_access_point(self, access_point_id: str) -> None:
        try:
            self._efs.delete_access_point(AccessPointId=access_point_id)
            self._await_access_point(access_point_id, {"deleted"})
        except Exception as exc:
            if not _not_found(exc):
                raise

    def _poll_sleep(self, attempt: int) -> None:
        if attempt + 1 < self._config.max_poll_attempts:
            self._sleep(self._config.poll_delay_seconds)

    def _is_managed(self, resource: dict[str, Any]) -> bool:
        return _tag_map(resource.get("Tags") or []).get("astrolift.io/managed-by") == _MANAGED_BY

    def _creation_token(self, spec: ProvisionSpec) -> str:
        return _token(
            "-".join(
                part
                for part in (
                    self._config.creation_token_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "filesystem",
                )
                if part
            ),
        )

    def _validate_config(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        if self._config.poll_delay_seconds < 0 or self._config.max_poll_attempts < 1:
            return "EFS polling defaults require a non-negative delay and at least one attempt"
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported EFS config fields: {unknown}"
        for field in ("file_system", "file_system_update", "access_point"):
            if field in cfg and not isinstance(cfg[field], dict):
                return f"{field} must be an object"
        for field in (
            "create_access_point",
            "tls",
            "read_only",
            "client_root_access",
            "deletion_protection",
            "delete_file_system_policy",
            "bypass_policy_lockout_safety_check",
            "prune_mount_targets",
        ):
            if field in cfg and not isinstance(cfg[field], bool):
                return f"{field} must be a boolean"
        mount_path = cfg.get("mount_path")
        if mount_path is not None and (not isinstance(mount_path, str) or not mount_path.startswith("/")):
            return "mount_path must be an absolute path"
        root_path = cfg.get("root_path")
        if root_path is not None and (not isinstance(root_path, str) or not root_path.startswith("/")):
            return "root_path must be an absolute path"
        if "mount_options" in cfg and (
            not isinstance(cfg["mount_options"], list)
            or any(not isinstance(option, str) or not option for option in cfg["mount_options"])
        ):
            return "mount_options must be an array of non-empty strings"
        if cfg.get("tls") is False and "tls" in (cfg.get("mount_options") or []):
            return "mount_options cannot request tls when tls=false"
        if cfg.get("create_access_point", True) and cfg.get("tls") is False:
            return "EFS access points require tls=true"
        if cfg.get("create_access_point") is False and any(
            field in cfg for field in ("access_point", "root_path", "posix_uid", "posix_gid", "root_permissions")
        ):
            return "access-point settings require create_access_point=true"
        targets = cfg.get("mount_targets")
        if targets is not None:
            if not isinstance(targets, list) or not targets:
                return "mount_targets must be a non-empty array"
            if any(not isinstance(target, dict) or not target.get("SubnetId") for target in targets):
                return "each mount target requires an AWS SubnetId"
            subnets = [str(target["SubnetId"]) for target in targets]
            if len(subnets) != len(set(subnets)):
                return "mount_targets cannot repeat a subnet"
        elif not partial and not self._config.subnet_ids:
            return "EFS requires operator subnet_ids or explicit mount_targets"
        one_zone = bool((cfg.get("file_system") or {}).get("AvailabilityZoneName"))
        if one_zone and (targets is None or len(targets) != 1):
            return "one-zone EFS requires exactly one explicit mount target"
        groups = cfg.get("mount_target_security_group_ids")
        if groups is not None and (
            not isinstance(groups, list) or any(not isinstance(group, str) or not group for group in groups)
        ):
            return "mount_target_security_group_ids must be an array of non-empty strings"
        policies = cfg.get("lifecycle_policies")
        if policies is not None and (
            not isinstance(policies, list)
            or len(policies) > 3
            or any(not isinstance(policy, dict) for policy in policies)
        ):
            return "lifecycle_policies must contain at most three objects"
        if "backup_policy" in cfg and cfg["backup_policy"] not in {"ENABLED", "DISABLED"}:
            return "backup_policy must be ENABLED or DISABLED"
        if "replication_overwrite_protection" in cfg and cfg["replication_overwrite_protection"] not in {
            "ENABLED",
            "DISABLED",
            "REPLICATING",
        }:
            return "invalid replication_overwrite_protection"
        policy = cfg.get("file_system_policy")
        if policy is not None and not isinstance(policy, dict):
            return "file_system_policy must be an object"
        if policy is not None and cfg.get("delete_file_system_policy"):
            return "file_system_policy and delete_file_system_policy are mutually exclusive"
        replication = cfg.get("replication_configuration")
        if replication is not None and (not isinstance(replication, dict) or not replication):
            return "replication_configuration must be a non-empty object"
        try:
            if cfg.get("file_system_update"):
                _validate_request(
                    "UpdateFileSystem",
                    {**cfg["file_system_update"], "FileSystemId": "fs-12345678"},
                )
            if targets:
                for target in targets:
                    _validate_request(
                        "CreateMountTarget",
                        {**target, "FileSystemId": "fs-12345678"},
                    )
            if policies is not None:
                _validate_request(
                    "PutLifecycleConfiguration",
                    {"FileSystemId": "fs-12345678", "LifecyclePolicies": policies},
                )
            if policy is not None:
                _validate_request(
                    "PutFileSystemPolicy",
                    {
                        "FileSystemId": "fs-12345678",
                        "Policy": json.dumps(policy, sort_keys=True, separators=(",", ":")),
                        "BypassPolicyLockoutSafetyCheck": bool(
                            cfg.get("bypass_policy_lockout_safety_check", False),
                        ),
                    },
                )
            if replication:
                _validate_request(
                    "CreateReplicationConfiguration",
                    {**replication, "SourceFileSystemId": "fs-12345678"},
                )
            if not partial:
                validation_spec = ProvisionSpec(
                    organization_id="validation",
                    organization_slug="validation",
                    app_id="validation",
                    app_slug="validation",
                    environment_id="validation",
                    environment_name="validation",
                    tenant_cluster_id="validation",
                    service_handle_hint="validation",
                    size="small",
                    config=cfg,
                )
                _validate_request(
                    "CreateFileSystem",
                    self._create_file_system_request("astrolift-validation", validation_spec),
                )
                if cfg.get("create_access_point", True):
                    access = dict(cfg.get("access_point") or {})
                    access.update(
                        {
                            "ClientToken": "astrolift-validation-access-point",
                            "FileSystemId": "fs-12345678",
                        },
                    )
                    access.setdefault("PosixUser", {"Uid": 1000, "Gid": 1000})
                    access.setdefault(
                        "RootDirectory",
                        {
                            "Path": str(cfg.get("root_path") or "/astrolift"),
                            "CreationInfo": {
                                "OwnerUid": int(cfg.get("posix_uid", 1000)),
                                "OwnerGid": int(cfg.get("posix_gid", 1000)),
                                "Permissions": str(cfg.get("root_permissions") or "0750"),
                            },
                        },
                    )
                    _validate_request("CreateAccessPoint", access)
        except Exception as exc:
            return f"invalid AWS EFS request structure: {exc}"
        return ""


def _validate_request(operation: str, request: dict[str, Any]) -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    service = Session().get_service_model("efs")
    validate_parameters(request, service.operation_model(operation).input_shape)


def _tag_list(spec: ProvisionSpec) -> list[dict[str, str]]:
    return tags_for(spec)


def _tag_map(tags: list[dict[str, Any]]) -> dict[str, str]:
    return {str(tag.get("Key")): str(tag.get("Value")) for tag in tags if tag.get("Key")}


def _replication_matches(request: dict[str, Any], live: dict[str, Any]) -> bool:
    desired = list(request.get("Destinations") or [])
    current = list(live.get("Destinations") or [])
    if len(desired) != len(current):
        return False
    unmatched = [dict(destination) for destination in current]
    for destination in desired:
        desired_region = str(destination.get("Region") or "")
        availability_zone = str(destination.get("AvailabilityZoneName") or "")
        if not desired_region and availability_zone:
            desired_region = availability_zone[:-1]
        desired_file_system = str(destination.get("FileSystemId") or "")
        desired_role = str(destination.get("RoleArn") or "")
        match_index = next(
            (
                index
                for index, candidate in enumerate(unmatched)
                if (not desired_region or candidate.get("Region") == desired_region)
                and (not desired_file_system or candidate.get("FileSystemId") == desired_file_system)
                and (not desired_role or candidate.get("RoleArn") == desired_role)
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return not unmatched


def _token(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in value).strip("-_")
    if not clean:
        clean = "astrolift"
    if len(clean) <= 64:
        return clean
    digest = hashlib.sha256(clean.encode()).hexdigest()[:12]
    return f"{clean[:51]}-{digest}"


def _handle(file_system_id: str, access_point_id: str = "") -> str:
    resource = file_system_id if not access_point_id else f"{file_system_id}/{access_point_id}"
    return handle_for(kind=KIND, resource_id=resource)


def _parse_resource(handle: str) -> tuple[str, str]:
    _, resource = parse_handle(handle)
    file_system_id, _, access_point_id = resource.partition("/")
    if not file_system_id:
        raise ManagedServiceError("EFS handle is missing the filesystem ID")
    return file_system_id, access_point_id


def _already_exists(exc: Exception) -> bool:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    code = str(error.get("Code") or "")
    return code in {"MountTargetConflict", "AccessPointAlreadyExists", "FileSystemAlreadyExists"} or (
        "already" in str(error.get("Message") or "").lower() and "exist" in str(error.get("Message") or "").lower()
    )


def _not_found(exc: Exception) -> bool:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    code = str(error.get("Code") or "")
    return code in {
        "FileSystemNotFound",
        "AccessPointNotFound",
        "MountTargetNotFound",
        "ResourceNotFoundException",
    } or ("not found" in str(error.get("Message") or "").lower())


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    code = str(error.get("Code") or "")
    retryable = code not in {"BadRequest", "ValidationException", "AccessDeniedException"}
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [code or str(exc)], retryable=retryable)
