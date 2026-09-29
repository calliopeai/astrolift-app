"""Google Cloud Filestore managed shared-filesystem lifecycle.

The driver targets the public ``file.googleapis.com/v1`` surface.  It keeps
the portable kind ``filesystem`` while exposing the provider capabilities that
matter operationally: every public tier, NFS v3/v4.1, PSC, export ACLs, CMEK,
custom performance, deletion protection, replication, snapshots, and backups.

Filestore snapshots are tied to an instance and therefore cannot protect data
when the instance is deleted.  The portable ``snapshot`` operation and the
safe-delete path intentionally create regional Filestore backups instead.
Provider-native snapshots and reverts remain available as explicit driver
methods for callers that want an in-place recovery point.
"""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._ownership import is_platform_label_key, label_identity_refusal, reserved_label_keys

KIND = "filesystem"
_API_ROOT = "https://file.googleapis.com/v1"
_MISSING_IDENTITY = "Filestore needs the managed-service id to mark the instance it owns"
_TIERS = {
    "STANDARD",
    "PREMIUM",
    "BASIC_HDD",
    "BASIC_SSD",
    "HIGH_SCALE_SSD",
    "ENTERPRISE",
    "ZONAL",
    "REGIONAL",
}
_TIER_CANONICAL = {
    "STANDARD": "BASIC_HDD",
    "PREMIUM": "BASIC_SSD",
    "HIGH_SCALE_SSD": "ZONAL",
}
_PROTOCOLS = {"NFS_V3", "NFS_V4_1"}
_CONNECT_MODES = {
    "DIRECT_PEERING",
    "PRIVATE_SERVICE_ACCESS",
    "PRIVATE_SERVICE_CONNECT",
}
_ADDRESS_MODES = {"MODE_IPV4", "MODE_IPV6"}
_SIZE_GB = {
    "small": 1024,
    "medium": 2560,
    "large": 10240,
    "xlarge": 20480,
}
_UPDATING_STATES = {
    "REPAIRING",
    "RESTORING",
    "SUSPENDING",
    "RESUMING",
    "REVERTING",
    "PROMOTING",
}


class FilestoreError(RuntimeError):
    """A Filestore API or lifecycle contract error."""


class FilestoreNotFound(FilestoreError):
    """The requested Filestore resource does not exist."""


class FilestoreConflict(FilestoreError):
    """The requested Filestore resource already exists."""


@dataclass(frozen=True)
class FilestoreConfig:
    project_id: str
    location: str
    network: str
    instance_name_prefix: str = "astrolift"
    share_name_default: str = "data"
    tier_default: str = "REGIONAL"
    protocol_default: str = "NFS_V3"
    connect_mode_default: str = "PRIVATE_SERVICE_CONNECT"
    reserved_ip_range: str = ""
    psc_endpoint_project: str = ""
    kms_key_name: str = ""
    deletion_protection_default: bool = True
    backup_location: str = ""
    backup_kms_key: str = ""
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 3600.0
    poll_interval_seconds: float = 5.0


class FilestoreRestClient:
    """Small authenticated adapter around the public Filestore v1 REST API."""

    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_instance(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_instance(
        self,
        parent: str,
        instance_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/instances",
            params={"instanceId": instance_id},
            json=body,
        )

    def patch_instance(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def delete_instance(self, name: str, *, force: bool = False) -> dict[str, Any]:
        return self._request(
            "DELETE",
            name,
            params={"force": "true"} if force else None,
        )

    def create_backup(
        self,
        parent: str,
        backup_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/backups",
            params={"backupId": backup_id},
            json=body,
        )

    def get_backup(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_snapshot(
        self,
        instance_name: str,
        snapshot_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{instance_name}/snapshots",
            params={"snapshotId": snapshot_id},
            json=body,
        )

    def get_snapshot(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def revert_instance(self, name: str, snapshot_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{name}:revert",
            json={"targetSnapshotId": snapshot_id},
        )

    def promote_replica(self, name: str, peer_instance: str = "") -> dict[str, Any]:
        body = {"peerInstance": peer_instance} if peer_instance else {}
        return self._request("POST", f"{name}:promoteReplica", json=body)

    def pause_replica(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"{name}:pauseReplica", json={})

    def resume_replica(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"{name}:resumeReplica", json={})

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise FilestoreNotFound(resource)
        if response.status_code == 409:
            raise FilestoreConflict(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise FilestoreError(f"Filestore HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class FilestoreDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: FilestoreConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
        now: Any | None = None,
    ) -> None:
        self._config = config
        self._filestore = client or FilestoreRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(UTC))

    @driver_op(
        cloud="gcp",
        driver="filesystem_filestore",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_filestore_config"])
        if not spec.managed_service_id:
            return ProvisionResult(False, "", _MISSING_IDENTITY, ["invalid_filestore_config"])
        location = str(cfg.get("location") or self._config.location)
        instance_id = self._instance_id(spec)
        name = self._instance_name(location, instance_id)
        handle = _handle(location, instance_id)
        record_proves = spec.recorded_handle_exclusive and spec.recorded_handle == handle
        try:
            current = self._get_instance(name)
            if current is None:
                operation = self._filestore.create_instance(
                    self._location_parent(location),
                    instance_id,
                    self._instance_body(spec, cfg),
                )
                self._wait_operation(operation)
                current = self._filestore.get_instance(name)
            else:
                self._assert_owned(current, spec.managed_service_id, record_proves=record_proves)
                self._assert_immutable_matches(current, cfg)
                self._reconcile_mutable(
                    name,
                    current,
                    cfg,
                    size=spec.size,
                    claim_labels=self._labels(spec, cfg),
                )
        except FilestoreConflict:
            try:
                current = self._filestore.get_instance(name)
                self._assert_owned(current, spec.managed_service_id, record_proves=record_proves)
                self._assert_immutable_matches(current, cfg)
                self._reconcile_mutable(
                    name,
                    current,
                    cfg,
                    size=spec.size,
                    claim_labels=self._labels(spec, cfg),
                )
            except Exception as exc:
                return ProvisionResult(False, handle, f"adopt Filestore collision: {exc}", [str(exc)])
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Filestore: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Filestore instance {instance_id} reconciled",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="filesystem_filestore")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            location, instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, size=spec.size, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_filestore_config"])
        name = self._instance_name(location, instance_id)
        try:
            current = self._filestore.get_instance(name)
            self._assert_owned(current, spec.managed_service_id, record_proves=spec.recorded_handle_exclusive)
            self._assert_immutable_matches(current, cfg)
            # An identity planted on the instance is written over with the
            # spec's, never read back (#2098).
            self._reconcile_mutable(
                name,
                current,
                cfg,
                size=spec.size,
                claim_labels=_identity_labels(spec.managed_service_id),
            )
        except FilestoreNotFound:
            return UpdateResult(False, spec.handle, "Filestore instance not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Filestore: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Filestore instance {instance_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="filesystem_filestore",
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
            location, instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_handle"],
                retryable=False,
            )
        cfg = dict(spec.config or {})
        name = self._instance_name(location, instance_id)
        try:
            current = self._get_instance(name)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Filestore", exc)
        if current is None:
            return DeprovisionResult(True, spec.handle, "Filestore instance already gone")
        try:
            self._assert_owned(current, spec.managed_service_id, record_proves=spec.recorded_handle_exclusive)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["ownership_guard"],
                retryable=False,
            )
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-adopted") == "true" and not cfg.get("delete_adopted"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted Filestore instances require delete_adopted=true before deletion",
                ["adopted_resource_guard"],
                retryable=False,
            )
        protected = bool(current.get("deletionProtectionEnabled"))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Filestore deletion protection is enabled; use force_destroy to disable it",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        retained = ""
        try:
            if not delete_data:
                backup = self._ensure_final_backup(current)
                retained = str(backup.get("name") or "")
            if protected:
                protection_body: dict[str, Any] = {
                    "deletionProtectionEnabled": False,
                    "deletionProtectionReason": "",
                }
                if current.get("etag"):
                    protection_body["etag"] = current["etag"]
                operation = self._filestore.patch_instance(
                    name,
                    protection_body,
                    update_mask=[
                        "deletion_protection_enabled",
                        "deletion_protection_reason",
                    ],
                )
                self._wait_operation(operation)
            operation = self._filestore.delete_instance(name, force=force_destroy)
            self._wait_operation(operation)
        except FilestoreNotFound:
            pass
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Filestore", exc)
        message = f"Filestore instance {instance_id} deleted"
        if retained:
            message += f"; retained backup {retained}"
        return DeprovisionResult(True, spec.handle, message)

    @driver_op(cloud="gcp", driver="filesystem_filestore")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            location, instance_id = _parse_handle(handle.handle)
            current = self._filestore.get_instance(self._instance_name(location, instance_id))
        except FilestoreNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Filestore instance does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Filestore: {exc}")
        state = str(current.get("state") or "STATE_UNSPECIFIED")
        message = str(current.get("statusMessage") or f"Filestore is {state}")
        if state == "READY":
            replication = current.get("replication") or {}
            for replica in replication.get("replicas") or []:
                replica_state = str(replica.get("state") or "STATE_UNSPECIFIED")
                if replica_state == "FAILED":
                    reasons = ", ".join(replica.get("stateReasons") or [])
                    return ServiceStatus(
                        handle.handle,
                        "error",
                        f"Filestore replica failed: {reasons or 'unknown reason'}",
                    )
                if replica_state not in {"READY", "PAUSED"}:
                    return ServiceStatus(
                        handle.handle,
                        "updating",
                        f"Filestore replica is {replica_state}",
                    )
            return ServiceStatus(handle.handle, "available", message)
        if state == "CREATING":
            return ServiceStatus(handle.handle, "provisioning", message)
        if state == "DELETING":
            return ServiceStatus(handle.handle, "deprovisioning", message)
        if state in _UPDATING_STATES:
            return ServiceStatus(handle.handle, "updating", message)
        return ServiceStatus(handle.handle, "error", message)

    @driver_op(cloud="gcp", driver="filesystem_filestore")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        location, instance_id = _parse_handle(handle.handle)
        current = self._filestore.get_instance(self._instance_name(location, instance_id))
        # The mount below is the payoff of a handle two services record.
        self._assert_owned(current, handle.managed_service_id, record_proves=handle.recorded_handle_exclusive)
        if str(current.get("state") or "") != "READY":
            raise FilestoreError(f"Filestore instance {instance_id} is not ready")
        cfg = dict(config or {})
        networks = list(current.get("networks") or [])
        shares = list(current.get("fileShares") or [])
        if not networks or not shares:
            raise FilestoreError("Filestore instance has no mount endpoint or file share")
        addresses = [str(item) for item in networks[0].get("ipAddresses") or [] if item]
        if not addresses:
            raise FilestoreError("Filestore instance has no assigned IP address")
        endpoint = addresses[0]
        share_name = str(shares[0].get("name") or "")
        if not share_name:
            raise FilestoreError("Filestore instance file share has no name")
        try:
            capacity_gb = int(shares[0].get("capacityGb") or 0)
        except (TypeError, ValueError) as exc:
            raise FilestoreError("Filestore instance file share has invalid capacity") from exc
        if capacity_gb < 1:
            raise FilestoreError("Filestore instance file share has invalid capacity")
        protocol = str(current.get("protocol") or "NFS_V3")
        mount_path = str(cfg.get("mount_path") or "/mnt/shared")
        security_flavor = str(cfg.get("security_flavor") or "sys")
        options = [str(value) for value in cfg.get("mount_options") or []]
        version_option = "vers=4.1" if protocol == "NFS_V4_1" else "vers=3"
        if not any(value.startswith(("vers=", "nfsvers=")) for value in options):
            options.append(version_option)
        if security_flavor != "sys" and not any(value.startswith("sec=") for value in options):
            options.append(f"sec={security_flavor}")
        if cfg.get("read_only") and "ro" not in options:
            options.append("ro")
        resource_name = str(current.get("name") or self._instance_name(location, instance_id))
        return Binding(
            env_vars={
                "FILESYSTEM_HANDLE": ValueRef(literal=handle.handle),
                "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
                "FILESYSTEM_TLS": ValueRef(literal=str(security_flavor == "krb5p").lower()),
                "FILESYSTEM_PROTOCOL": ValueRef(
                    literal="nfs4.1" if protocol == "NFS_V4_1" else "nfs3",
                ),
                "FILESYSTEM_ENDPOINT": ValueRef(literal=endpoint),
                "FILESYSTEM_EXPORT": ValueRef(literal=f"/{share_name}"),
                "FILESYSTEM_MOUNT_OPTIONS": ValueRef(literal=",".join(options)),
                "FILESTORE_INSTANCE": ValueRef(literal=instance_id),
                "FILESTORE_SHARE": ValueRef(literal=share_name),
                "FILESTORE_IP": ValueRef(literal=endpoint),
                "GCP_PROJECT_ID": ValueRef(literal=self._config.project_id),
                "GCP_LOCATION": ValueRef(literal=location),
            },
            pod_volume_mounts=[
                VolumeMount(
                    name=_resource_id(f"filestore-{instance_id}"),
                    mount_path=mount_path,
                    source_kind=VolumeSourceKind.CSI,
                    protocol="nfs4.1" if protocol == "NFS_V4_1" else "nfs3",
                    csi_driver="filestore.csi.storage.gke.io",
                    volume_handle=f"modeInstance/{location}/{instance_id}/{share_name}",
                    volume_attributes={
                        "ip": endpoint,
                        "volume": share_name,
                        "protocol": protocol,
                    },
                    mount_options=options,
                    read_only=bool(cfg.get("read_only", False)),
                    capacity=f"{capacity_gb}Gi",
                ),
            ],
            iam_grants=[],
            notes=(
                "Filestore is authorized by VPC reachability and NFS export rules; "
                f"mount {endpoint}:/{share_name} through the GKE Filestore CSI driver. "
                f"Resource: {resource_name}."
            ),
        )

    @driver_op(cloud="gcp", driver="filesystem_filestore")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        location, instance_id = _parse_handle(handle.handle)
        current = self._filestore.get_instance(self._instance_name(location, instance_id))
        # A backup is a copy of the data, retained under this service's record.
        self._assert_owned(current, handle.managed_service_id, record_proves=handle.recorded_handle_exclusive)
        timestamp = self._now()
        backup_id = _resource_id(
            f"snap-{instance_id}-{timestamp.strftime('%Y%m%d%H%M%S%f')}",
        )
        backup = self._create_backup(current, backup_id)
        return SnapshotHandle(
            handle.handle,
            str(backup.get("name") or self._backup_name(backup_id, location)),
            str(backup.get("createTime") or timestamp.isoformat()),
        )

    @driver_op(cloud="gcp", driver="filesystem_filestore")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        error = self._validate_config(cfg, size=target.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_filestore_config"])
        if "/backups/" not in snapshot.snapshot_id:
            return ProvisionResult(
                False,
                "",
                "Filestore portable restore requires a regional backup resource name",
                ["invalid_snapshot"],
            )
        location = str(cfg.get("location") or self._config.location)
        instance_id = self._instance_id(target)
        name = self._instance_name(location, instance_id)
        handle = _handle(location, instance_id)
        try:
            if self._get_instance(name) is not None:
                raise FilestoreError(f"restore target {instance_id} already exists")
            backup = self._filestore.get_backup(snapshot.snapshot_id)
            capacity = int(backup.get("capacityGb") or 0)
            requested = self._capacity_gb(target.size, cfg)
            if capacity and requested < capacity:
                raise FilestoreError(
                    f"restore target capacity {requested} GiB is below backup capacity {capacity} GiB",
                )
            body = self._instance_body(target, cfg)
            body["fileShares"][0]["sourceBackup"] = snapshot.snapshot_id
            operation = self._filestore.create_instance(
                self._location_parent(location),
                instance_id,
                body,
            )
            self._wait_operation(operation)
        except Exception as exc:
            return ProvisionResult(False, handle, f"restore Filestore: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Filestore instance {instance_id} restored from {snapshot.snapshot_id}",
            ready=True,
        )

    def create_native_snapshot(self, handle: ServiceHandle, *, snapshot_id: str = "") -> SnapshotHandle:
        """Create an instance-local snapshot for fast in-place recovery."""
        location, instance_id = _parse_handle(handle.handle)
        name = self._instance_name(location, instance_id)
        self._filestore.get_instance(name)
        timestamp = self._now()
        native_id = _resource_id(
            snapshot_id or f"snapshot-{instance_id}-{timestamp.strftime('%Y%m%d%H%M%S%f')}",
        )
        operation = self._filestore.create_snapshot(
            name,
            native_id,
            {
                "description": "Astrolift managed Filestore snapshot",
                "labels": {"astrolift-io-managed-by": "platform"},
            },
        )
        self._wait_operation(operation)
        resource = self._filestore.get_snapshot(f"{name}/snapshots/{native_id}")
        return SnapshotHandle(
            handle.handle,
            str(resource.get("name") or f"{name}/snapshots/{native_id}"),
            str(resource.get("createTime") or timestamp.isoformat()),
        )

    def revert_native_snapshot(
        self,
        handle: ServiceHandle,
        snapshot_id: str,
        *,
        acknowledge_data_loss: bool = False,
    ) -> ServiceStatus:
        """Revert the existing share to an instance-local snapshot."""
        if not acknowledge_data_loss:
            raise FilestoreError("native snapshot revert requires acknowledge_data_loss=true")
        location, instance_id = _parse_handle(handle.handle)
        name = self._instance_name(location, instance_id)
        native_id = snapshot_id.rsplit("/", 1)[-1]
        self._wait_operation(self._filestore.revert_instance(name, native_id))
        return self.status(handle)

    def promote_replica(
        self,
        handle: ServiceHandle,
        *,
        peer_instance: str = "",
        acknowledge_paused_write_loss: bool = False,
    ) -> ServiceStatus:
        """Promote a standby replica through the explicit provider operation."""
        if not acknowledge_paused_write_loss:
            raise FilestoreError(
                "replica promotion may discard writes made while paused; set acknowledge_paused_write_loss=true",
            )
        location, instance_id = _parse_handle(handle.handle)
        name = self._instance_name(location, instance_id)
        self._wait_operation(self._filestore.promote_replica(name, peer_instance))
        return self.status(handle)

    def pause_replica(
        self,
        handle: ServiceHandle,
        *,
        acknowledge_ephemeral_writes: bool = False,
    ) -> ServiceStatus:
        """Pause replication without deleting the replica relationship."""
        if not acknowledge_ephemeral_writes:
            raise FilestoreError(
                "a paused standby becomes writable but those writes are ephemeral; "
                "set acknowledge_ephemeral_writes=true",
            )
        location, instance_id = _parse_handle(handle.handle)
        name = self._instance_name(location, instance_id)
        self._wait_operation(self._filestore.pause_replica(name))
        return self.status(handle)

    def resume_replica(
        self,
        handle: ServiceHandle,
        *,
        acknowledge_data_loss: bool = False,
    ) -> ServiceStatus:
        """Resume a previously paused replica relationship."""
        if not acknowledge_data_loss:
            raise FilestoreError(
                "resuming replication discards writes made while paused; set acknowledge_data_loss=true",
            )
        location, instance_id = _parse_handle(handle.handle)
        name = self._instance_name(location, instance_id)
        self._wait_operation(self._filestore.resume_replica(name))
        return self.status(handle)

    @driver_op(cloud="gcp", driver="filesystem_filestore", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        string_map = {"type": "object", "additionalProperties": {"type": "string"}}
        export_option = {
            "type": "object",
            "properties": {
                "ip_ranges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 64,
                },
                "network": {"type": "string"},
                "access_mode": {
                    "type": "string",
                    "enum": ["READ_ONLY", "READ_WRITE"],
                },
                "squash_mode": {
                    "type": "string",
                    "enum": ["NO_ROOT_SQUASH", "ROOT_SQUASH"],
                    "default": "ROOT_SQUASH",
                },
                "anon_uid": {"type": "integer", "minimum": 0},
                "anon_gid": {"type": "integer", "minimum": 0},
            },
            "required": ["ip_ranges"],
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "minLength": 1, "maxLength": 63},
                "delete_adopted": {"type": "boolean", "default": False},
                "location": {"type": "string"},
                "tier": {"type": "string", "enum": sorted(_TIERS)},
                "capacity_gb": {"type": "integer", "minimum": 100, "maximum": 102400},
                "regional_small_capacity_feature": {"type": "boolean", "default": False},
                "share_name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,62}$"},
                "network": {"type": "string"},
                "connect_mode": {"type": "string", "enum": sorted(_CONNECT_MODES)},
                "reserved_ip_range": {"type": "string"},
                "address_modes": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(_ADDRESS_MODES)},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "psc_endpoint_project": {"type": "string"},
                "protocol": {"type": "string", "enum": sorted(_PROTOCOLS)},
                "kms_key_name": {"type": "string"},
                "description": {"type": "string", "maxLength": 2048},
                "labels": string_map,
                "resource_tags": string_map,
                "nfs_export_options": {
                    "type": "array",
                    "items": export_option,
                    "maxItems": 10,
                },
                "directory_services": {
                    "type": "object",
                    "required": ["domain", "servers"],
                    "properties": {
                        "domain": {"type": "string"},
                        "servers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "users_ou": {"type": "string"},
                        "groups_ou": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "security_flavor": {
                    "type": "string",
                    "enum": ["sys", "krb5", "krb5i", "krb5p"],
                    "default": "sys",
                },
                "performance_iops_per_tb": {"type": "integer", "minimum": 1},
                "performance_fixed_iops": {
                    "type": "integer",
                    "minimum": 1000,
                    "multipleOf": 1000,
                },
                "deletion_protection": {"type": "boolean"},
                "deletion_protection_reason": {"type": "string"},
                "replication_role": {"type": "string", "enum": ["ACTIVE", "STANDBY"]},
                "replica_peer_instance": {"type": "string"},
                "allow_capacity_decrease": {"type": "boolean", "default": False},
                "mount_path": {"type": "string", "default": "/mnt/shared"},
                "mount_options": {"type": "array", "items": {"type": "string"}},
                "read_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="filesystem_filestore", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Portable location and Filestore instance identifier",
                "FILESYSTEM_MOUNT_PATH": "Recommended container mount path",
                "FILESYSTEM_TLS": "True only for NFSv4.1 krb5p privacy",
                "FILESYSTEM_PROTOCOL": "nfs3 or nfs4.1",
                "FILESYSTEM_ENDPOINT": "Private Filestore IP address",
                "FILESYSTEM_EXPORT": "NFS export path",
                "FILESYSTEM_MOUNT_OPTIONS": "Comma-separated NFS mount options",
                "FILESTORE_INSTANCE": "Filestore instance ID",
                "FILESTORE_SHARE": "Filestore share name",
                "FILESTORE_IP": "Private Filestore IP address",
                "GCP_PROJECT_ID": "Google Cloud project ID",
                "GCP_LOCATION": "Filestore zone or region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "capacity_gb",
            "nfs_export_options",
            "description",
            "labels",
            "performance_iops_per_tb",
            "performance_fixed_iops",
            "deletion_protection",
            "deletion_protection_reason",
            "allow_capacity_decrease",
            "mount_path",
            "mount_options",
            "read_only",
            "security_flavor",
        ]

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        size: str | None,
        update: bool = False,
    ) -> str:
        tier = str(cfg.get("tier") or self._config.tier_default).upper()
        if tier not in _TIERS:
            return f"invalid Filestore tier {tier!r}"
        protocol = str(cfg.get("protocol") or self._config.protocol_default).upper()
        if protocol not in _PROTOCOLS:
            return f"invalid Filestore protocol {protocol!r}"
        canonical_tier = _canonical_tier(tier)
        if protocol == "NFS_V4_1" and canonical_tier not in {"ZONAL", "REGIONAL", "ENTERPRISE"}:
            return "NFS_V4_1 requires ZONAL, REGIONAL, or ENTERPRISE Filestore"
        network = str(cfg.get("network") or self._config.network)
        if not network:
            return "Filestore requires a VPC network"
        connect_mode = str(
            cfg.get("connect_mode") or self._config.connect_mode_default,
        ).upper()
        if connect_mode not in _CONNECT_MODES:
            return f"invalid Filestore connect_mode {connect_mode!r}"
        if connect_mode == "PRIVATE_SERVICE_CONNECT" and canonical_tier not in {
            "ZONAL",
            "REGIONAL",
            "ENTERPRISE",
        }:
            return "Private Service Connect requires ZONAL, REGIONAL, or ENTERPRISE Filestore"
        modes = [str(item).upper() for item in cfg.get("address_modes") or ["MODE_IPV4"]]
        if any(mode not in _ADDRESS_MODES for mode in modes):
            return "Filestore address_modes accepts only MODE_IPV4 and MODE_IPV6"
        if "MODE_IPV6" in modes and connect_mode != "PRIVATE_SERVICE_CONNECT":
            return "Filestore MODE_IPV6 requires PRIVATE_SERVICE_CONNECT"
        if cfg.get("psc_endpoint_project") and connect_mode != "PRIVATE_SERVICE_CONNECT":
            return "psc_endpoint_project requires PRIVATE_SERVICE_CONNECT"
        if cfg.get("directory_services") and protocol != "NFS_V4_1":
            return "Filestore directory_services requires NFS_V4_1"
        security_flavor = str(cfg.get("security_flavor") or "sys")
        if security_flavor not in {"sys", "krb5", "krb5i", "krb5p"}:
            return f"invalid NFS security_flavor {security_flavor!r}"
        if security_flavor != "sys" and not cfg.get("directory_services"):
            return f"NFS security_flavor {security_flavor} requires directory_services"
        if cfg.get("performance_iops_per_tb") and cfg.get("performance_fixed_iops"):
            return "choose either performance_iops_per_tb or performance_fixed_iops, not both"
        if (cfg.get("performance_iops_per_tb") or cfg.get("performance_fixed_iops")) and canonical_tier not in {
            "ZONAL",
            "REGIONAL",
        }:
            return "custom Filestore performance requires ZONAL or REGIONAL tier"
        if cfg.get("replication_role") == "STANDBY" and not cfg.get("replica_peer_instance"):
            return "STANDBY replication requires replica_peer_instance"
        exports = list(cfg.get("nfs_export_options") or [])
        if len(exports) > 10:
            return "Filestore supports at most 10 NFS export options"
        ip_count = 0
        for option in exports:
            ranges = list(option.get("ip_ranges") or [])
            if not ranges:
                return "each NFS export option requires at least one ip_ranges entry"
            ip_count += len(ranges)
            if (
                connect_mode == "PRIVATE_SERVICE_CONNECT"
                and not option.get("network")
                and (not update or str(cfg.get("connect_mode") or "").upper() == "PRIVATE_SERVICE_CONNECT")
            ):
                return "PSC NFS export options require network"
            squash = str(option.get("squash_mode") or "ROOT_SQUASH")
            if squash not in {"NO_ROOT_SQUASH", "ROOT_SQUASH"}:
                return f"invalid NFS squash_mode {squash!r}"
            if squash != "ROOT_SQUASH" and ("anon_uid" in option or "anon_gid" in option):
                return "anon_uid and anon_gid require ROOT_SQUASH"
        if ip_count > 64:
            return "Filestore supports at most 64 IP ranges across all export options"
        if size is not None or "capacity_gb" in cfg:
            try:
                capacity = self._capacity_gb(size or "custom", cfg)
            except (TypeError, ValueError) as exc:
                return str(exc)
            capacity_error = _capacity_error(
                canonical_tier,
                capacity,
                regional_small=bool(cfg.get("regional_small_capacity_feature")),
            )
            if capacity_error:
                return capacity_error
        if not update or "share_name" in cfg:
            share_name = str(cfg.get("share_name") or self._config.share_name_default)
            share_limit = 16 if canonical_tier in {"BASIC_HDD", "BASIC_SSD"} else 63
            if not re.fullmatch(r"[a-z][a-z0-9_]*", share_name) or len(share_name) > share_limit:
                return f"share_name must be 1-{share_limit} lowercase letters, digits, or underscores"
        reserved = reserved_label_keys(cfg.get("labels") or {})
        if reserved:
            return f"labels cannot set Astrolift-reserved keys: {', '.join(reserved)}"
        instance_id = str(cfg.get("instance_id") or "")
        if instance_id and not _valid_resource_id(instance_id):
            return "instance_id must start with a letter and contain up to 63 lowercase letters, digits, or hyphens"
        if len(str(cfg.get("description") or "")) > 2048:
            return "Filestore description cannot exceed 2048 characters"
        return ""

    def _instance_body(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, Any]:
        tier = str(cfg.get("tier") or self._config.tier_default).upper()
        protocol = str(cfg.get("protocol") or self._config.protocol_default).upper()
        connect_mode = str(
            cfg.get("connect_mode") or self._config.connect_mode_default,
        ).upper()
        network = str(cfg.get("network") or self._config.network)
        share: dict[str, Any] = {
            "name": str(cfg.get("share_name") or self._config.share_name_default),
            "capacityGb": str(self._capacity_gb(spec.size, cfg)),
        }
        exports = self._export_options(cfg, network=network, connect_mode=connect_mode)
        if exports:
            share["nfsExportOptions"] = exports
        network_config: dict[str, Any] = {
            "network": network,
            "modes": [str(item).upper() for item in cfg.get("address_modes") or ["MODE_IPV4"]],
            "connectMode": connect_mode,
        }
        reserved = str(cfg.get("reserved_ip_range") or self._config.reserved_ip_range)
        if reserved:
            network_config["reservedIpRange"] = reserved
        endpoint_project = str(
            cfg.get("psc_endpoint_project") or self._config.psc_endpoint_project,
        )
        if connect_mode == "PRIVATE_SERVICE_CONNECT" and endpoint_project:
            network_config["pscConfig"] = {"endpointProject": endpoint_project}
        body: dict[str, Any] = {
            "description": str(
                cfg.get("description")
                or f"Astrolift filesystem for {spec.organization_slug}/{spec.app_slug}/{spec.environment_name}"
            ),
            "tier": tier,
            "labels": self._labels(spec, cfg),
            "fileShares": [share],
            "networks": [network_config],
            "protocol": protocol,
            "deletionProtectionEnabled": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
        }
        if body["deletionProtectionEnabled"]:
            body["deletionProtectionReason"] = str(
                cfg.get("deletion_protection_reason") or "Managed by Astrolift",
            )
        kms_key = str(cfg.get("kms_key_name") or self._config.kms_key_name)
        if kms_key:
            body["kmsKeyName"] = kms_key
        if cfg.get("resource_tags"):
            body["tags"] = dict(cfg["resource_tags"])
        performance = self._performance_config(cfg)
        if performance:
            body["performanceConfig"] = performance
        if cfg.get("directory_services"):
            directory = cfg["directory_services"]
            ldap: dict[str, Any] = {
                "domain": str(directory["domain"]),
                "servers": [str(item) for item in directory["servers"]],
            }
            if directory.get("users_ou"):
                ldap["usersOu"] = str(directory["users_ou"])
            if directory.get("groups_ou"):
                ldap["groupsOu"] = str(directory["groups_ou"])
            body["directoryServices"] = {"ldap": ldap}
        role = str(cfg.get("replication_role") or "")
        peer = str(cfg.get("replica_peer_instance") or "")
        if role or peer:
            replication: dict[str, Any] = {}
            if role:
                replication["role"] = role
            if peer:
                replication["replicas"] = [{"peerInstance": peer}]
            body["replication"] = replication
        return body

    def _reconcile_mutable(
        self,
        name: str,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        size: str | None,
        claim_labels: dict[str, str] | None,
    ) -> None:
        body: dict[str, Any] = {}
        if current.get("etag"):
            body["etag"] = current["etag"]
        mask: list[str] = []
        if "description" in cfg and str(current.get("description") or "") != str(cfg["description"]):
            body["description"] = str(cfg["description"])
            mask.append("description")
        labels = dict(current.get("labels") or {})
        desired_labels = _platform_last(
            {**labels, **(claim_labels or {})},
            _tenant_labels(cfg) if "labels" in cfg else {},
        )
        if desired_labels != labels:
            body["labels"] = desired_labels
            mask.append("labels")
        current_shares = list(current.get("fileShares") or [])
        if not current_shares:
            raise FilestoreError("existing Filestore instance has no file share")
        current_share = current_shares[0]
        requested_capacity: int | None = None
        if "capacity_gb" in cfg or size:
            requested_capacity = self._capacity_gb(size or "custom", cfg)
        current_capacity = int(current_share.get("capacityGb") or 0)
        export_requested = "nfs_export_options" in cfg
        if (
            requested_capacity is not None
            and requested_capacity < current_capacity
            and not cfg.get(
                "allow_capacity_decrease",
            )
        ):
            raise FilestoreError(
                "capacity decrease requires allow_capacity_decrease=true after verifying used space",
            )
        if (requested_capacity is not None and requested_capacity != current_capacity) or export_requested:
            share: dict[str, Any] = {
                "name": str(current_share.get("name") or ""),
                "capacityGb": str(requested_capacity or current_capacity),
            }
            if export_requested:
                network = str((current.get("networks") or [{}])[0].get("network") or self._config.network)
                connect_mode = str(
                    (current.get("networks") or [{}])[0].get("connectMode") or self._config.connect_mode_default
                )
                share["nfsExportOptions"] = self._export_options(
                    cfg,
                    network=network,
                    connect_mode=connect_mode,
                )
            elif "nfsExportOptions" in current_share:
                share["nfsExportOptions"] = current_share["nfsExportOptions"]
            body["fileShares"] = [share]
            mask.append("file_shares")
        performance = self._performance_config(cfg)
        if performance and performance != (current.get("performanceConfig") or {}):
            body["performanceConfig"] = performance
            mask.append("performance_config")
        if "deletion_protection" in cfg:
            desired = bool(cfg["deletion_protection"])
            if desired != bool(current.get("deletionProtectionEnabled")):
                body["deletionProtectionEnabled"] = desired
                mask.append("deletion_protection_enabled")
        if "deletion_protection_reason" in cfg:
            reason = str(cfg["deletion_protection_reason"])
            if reason != str(current.get("deletionProtectionReason") or ""):
                body["deletionProtectionReason"] = reason
                mask.append("deletion_protection_reason")
        if not mask:
            return
        self._wait_operation(
            self._filestore.patch_instance(name, body, update_mask=mask),
        )

    @staticmethod
    def _assert_owned(current: dict[str, Any], managed_service_id: str, *, record_proves: bool) -> None:
        # instance_id is tenant-settable, so an existing instance is either
        # this service's or refused: neither one Astrolift never provisioned
        # nor another managed service's may be claimed from here. Adoption of
        # an existing resource is a separate, operator-authorized operation
        # (#1365) that no tenant config flag may grant (#2021). An instance
        # with no managed-service id, as a tenant could leave one before
        # #2098, is this service's only when the platform's exclusive record
        # of the handle says so (#2086).
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-managed-by") != "platform":
            raise FilestoreError(
                "existing Filestore instance is not owned by Astrolift; adoption is a separate, "
                "operator-authorized operation and cannot be granted by tenant config",
            )
        refusal = label_identity_refusal(
            labels,
            managed_service_id,
            record_proves=record_proves,
            resource="Filestore instance",
        )
        if refusal:
            raise FilestoreError(refusal)

    def _assert_immutable_matches(self, current: dict[str, Any], cfg: dict[str, Any]) -> None:
        if "location" in cfg:
            current_name = str(current.get("name") or "")
            current_location = ""
            if "/locations/" in current_name:
                current_location = current_name.split("/locations/", 1)[1].split("/", 1)[0]
            if current_location and current_location != str(cfg["location"]):
                raise FilestoreError("Filestore location is immutable; reprovision the resource")
        if "tier" in cfg and _canonical_tier(str(current.get("tier") or "")) != _canonical_tier(
            str(cfg["tier"]),
        ):
            raise FilestoreError("Filestore tier is immutable; reprovision the resource")
        if "protocol" in cfg and str(current.get("protocol") or "NFS_V3") != str(cfg["protocol"]):
            raise FilestoreError("Filestore protocol is immutable; reprovision the resource")
        if "share_name" in cfg:
            shares = list(current.get("fileShares") or [])
            if not shares or str(shares[0].get("name") or "") != str(cfg["share_name"]):
                raise FilestoreError("Filestore share_name is immutable; reprovision the resource")
        if "kms_key_name" in cfg and str(current.get("kmsKeyName") or "") != str(cfg["kms_key_name"]):
            raise FilestoreError("Filestore kms_key_name is immutable; reprovision the resource")
        networks = list(current.get("networks") or [])
        if any(key in cfg for key in ("network", "connect_mode", "reserved_ip_range", "psc_endpoint_project")):
            if not networks:
                raise FilestoreError("existing Filestore instance has no network configuration")
            network = networks[0]
            requested_network = str(cfg.get("network") or self._config.network)
            if "network" in cfg and not _network_matches(str(network.get("network") or ""), requested_network):
                raise FilestoreError("Filestore network is immutable; reprovision the resource")
            if "connect_mode" in cfg and str(network.get("connectMode") or "DIRECT_PEERING") != str(
                cfg["connect_mode"],
            ):
                raise FilestoreError("Filestore connect_mode is immutable; reprovision the resource")
            if "reserved_ip_range" in cfg and str(network.get("reservedIpRange") or "") != str(
                cfg["reserved_ip_range"],
            ):
                raise FilestoreError("Filestore reserved_ip_range is immutable; reprovision the resource")
            if "psc_endpoint_project" in cfg:
                current_project = str((network.get("pscConfig") or {}).get("endpointProject") or "")
                if current_project != str(cfg["psc_endpoint_project"]):
                    raise FilestoreError("Filestore PSC endpoint project is immutable; reprovision the resource")
        if any(key in cfg for key in ("replication_role", "replica_peer_instance")):
            current_replication = current.get("replication") or {}
            requested_role = str(cfg.get("replication_role") or "")
            if requested_role and str(current_replication.get("role") or "") != requested_role:
                raise FilestoreError("Filestore replication role is immutable; reprovision the resource")
            requested_peer = str(cfg.get("replica_peer_instance") or "")
            peers = {str(item.get("peerInstance") or "") for item in current_replication.get("replicas") or []}
            if requested_peer and requested_peer not in peers:
                raise FilestoreError("Filestore replica peer is immutable; reprovision the resource")

    def _ensure_final_backup(self, current: dict[str, Any]) -> dict[str, Any]:
        name = str(current.get("name") or "")
        instance_id = name.rsplit("/", 1)[-1]
        generation = str(current.get("createTime") or name)
        suffix = hashlib.sha256(generation.encode()).hexdigest()[:10]
        backup_id = _resource_id(f"final-{instance_id}-{suffix}")
        location = name.split("/locations/", 1)[1].split("/", 1)[0]
        backup_name = self._backup_name(backup_id, location)
        try:
            backup = self._filestore.get_backup(backup_name)
            state = str(backup.get("state") or "STATE_UNSPECIFIED")
            if state == "READY":
                return backup
            if state == "INVALID":
                raise FilestoreError(f"retained backup {backup_name} is INVALID")
            return self._wait_backup(backup_name)
        except FilestoreNotFound:
            return self._create_backup(current, backup_id)

    def _create_backup(self, current: dict[str, Any], backup_id: str) -> dict[str, Any]:
        name = str(current.get("name") or "")
        if not name:
            raise FilestoreError("Filestore instance response has no resource name")
        shares = list(current.get("fileShares") or [])
        if not shares or not shares[0].get("name"):
            raise FilestoreError("Filestore instance has no source file share")
        location = name.split("/locations/", 1)[1].split("/", 1)[0]
        backup_location = self._config.backup_location or _region_for(location)
        parent = f"projects/{self._config.project_id}/locations/{backup_location}"
        body: dict[str, Any] = {
            "description": f"Astrolift retained backup for {name}",
            "sourceInstance": name,
            "sourceFileShare": str(shares[0]["name"]),
            "labels": {
                "astrolift-io-managed-by": "platform",
                "astrolift-io-source-instance": _label_value(name.rsplit("/", 1)[-1]),
            },
        }
        if self._config.backup_kms_key:
            body["kmsKey"] = self._config.backup_kms_key
        backup_name = f"{parent}/backups/{backup_id}"
        try:
            operation = self._filestore.create_backup(parent, backup_id, body)
            self._wait_operation(operation)
        except FilestoreConflict:
            pass
        return self._wait_backup(backup_name)

    def _wait_backup(self, name: str) -> dict[str, Any]:
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        last: dict[str, Any] = {}
        while self._monotonic() <= deadline:
            last = self._filestore.get_backup(name)
            state = str(last.get("state") or "STATE_UNSPECIFIED")
            if state == "READY":
                return last
            if state == "INVALID":
                raise FilestoreError(f"Filestore backup {name} is INVALID")
            self._sleep(self._config.poll_interval_seconds)
        raise FilestoreError(
            f"Filestore backup {name} did not become READY; last={last.get('state', 'unknown')}",
        )

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise FilestoreError("Filestore operation response has no name")
            if self._monotonic() > deadline:
                raise FilestoreError(f"Filestore operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._filestore.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise FilestoreError(
                f"Filestore operation {name} failed: {error.get('message') or error}",
            )
        response = current.get("response") or {}
        return dict(response) if isinstance(response, dict) else {}

    def _get_instance(self, name: str) -> dict[str, Any] | None:
        try:
            return self._filestore.get_instance(name)
        except FilestoreNotFound:
            return None

    def _capacity_gb(self, size: str, cfg: dict[str, Any]) -> int:
        if "capacity_gb" in cfg:
            return int(cfg["capacity_gb"])
        if size == "custom":
            raise ValueError("custom Filestore size requires capacity_gb")
        if size not in _SIZE_GB:
            raise ValueError(f"unknown Filestore size {size!r}")
        capacity = _SIZE_GB[size]
        if _canonical_tier(str(cfg.get("tier") or self._config.tier_default)) == "BASIC_SSD":
            capacity = max(capacity, 2560)
        return capacity

    def _instance_id(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("instance_id") or "")
        if explicit:
            return explicit
        parts = [
            self._config.instance_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
        ]
        return _resource_id("-".join(part for part in parts if part))

    def _instance_name(self, location: str, instance_id: str) -> str:
        return f"{self._location_parent(location)}/instances/{instance_id}"

    def _location_parent(self, location: str) -> str:
        return f"projects/{self._config.project_id}/locations/{location}"

    def _backup_name(self, backup_id: str, source_location: str) -> str:
        backup_location = self._config.backup_location or _region_for(source_location)
        return f"projects/{self._config.project_id}/locations/{backup_location}/backups/{backup_id}"

    def _labels(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
        labels = {
            "astrolift-io-organization": _label_value(spec.organization_slug),
            "astrolift-io-app": _label_value(spec.app_slug),
            "astrolift-io-environment": _label_value(spec.environment_name),
            **_identity_labels(spec.managed_service_id),
        }
        if spec.binding_id:
            labels["astrolift-io-binding"] = _label_value(spec.binding_id)
        return _platform_last(labels, _tenant_labels(cfg))

    @staticmethod
    def _performance_config(cfg: dict[str, Any]) -> dict[str, Any]:
        if cfg.get("performance_iops_per_tb"):
            return {"iopsPerTb": {"maxIopsPerTb": str(int(cfg["performance_iops_per_tb"]))}}
        if cfg.get("performance_fixed_iops"):
            return {"fixedIops": {"maxIops": str(int(cfg["performance_fixed_iops"]))}}
        return {}

    @staticmethod
    def _export_options(
        cfg: dict[str, Any],
        *,
        network: str,
        connect_mode: str,
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for raw in cfg.get("nfs_export_options") or []:
            option: dict[str, Any] = {
                "ipRanges": [str(value) for value in raw.get("ip_ranges") or []],
                "accessMode": str(raw.get("access_mode") or "READ_WRITE"),
                "squashMode": str(raw.get("squash_mode") or "ROOT_SQUASH"),
            }
            source_network = str(raw.get("network") or "")
            if connect_mode == "PRIVATE_SERVICE_CONNECT":
                option["network"] = source_network or network
            elif source_network:
                option["network"] = source_network
            if option["squashMode"] == "ROOT_SQUASH":
                option["anonUid"] = str(int(raw.get("anon_uid", 65534)))
                option["anonGid"] = str(int(raw.get("anon_gid", 65534)))
            options.append(option)
        return options


def _handle(location: str, instance_id: str) -> str:
    return f"{KIND}/{location}/{instance_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _valid_resource_id(parts[2]):
        raise ValueError("Filestore handle must be filesystem/<location>/<instance-id>")
    return parts[1], parts[2]


def _resource_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    if not clean or not clean[0].isalpha():
        clean = f"f-{clean}"
    if len(clean) > 63:
        digest = hashlib.sha256(clean.encode()).hexdigest()[:8]
        clean = f"{clean[:54].rstrip('-')}-{digest}"
    return clean.rstrip("-")


def _valid_resource_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value)) and not value.endswith("-")


def _canonical_tier(tier: str) -> str:
    value = tier.upper()
    return _TIER_CANONICAL.get(value, value)


def _capacity_error(tier: str, capacity: int, *, regional_small: bool) -> str:
    if tier == "BASIC_HDD":
        if not 1024 <= capacity <= 65433:
            return "BASIC_HDD capacity must be between 1024 and 65433 GiB"
        return ""
    if tier == "BASIC_SSD":
        if not 2560 <= capacity <= 65433:
            return "BASIC_SSD capacity must be between 2560 and 65433 GiB"
        return ""
    if tier == "ENTERPRISE":
        if not 1024 <= capacity <= 10240 or capacity % 256:
            return "ENTERPRISE capacity must be 1024-10240 GiB in 256 GiB increments"
        return ""
    if tier in {"ZONAL", "REGIONAL"}:
        minimum = 100 if tier == "REGIONAL" and regional_small else 1024
        if minimum <= capacity <= 9984:
            step = 1 if tier == "REGIONAL" and regional_small else 256
            origin = minimum if step == 1 else 1024
            if (capacity - origin) % step:
                return f"{tier} lower-range capacity must use {step} GiB increments"
            return ""
        if 10240 <= capacity <= 102400 and capacity % 2560 == 0:
            return ""
        return (
            f"{tier} capacity must be {minimum}-9984 GiB in "
            f"{'1' if tier == 'REGIONAL' and regional_small else '256'} GiB increments, "
            "or 10240-102400 GiB in 2560 GiB increments"
        )
    return f"unsupported Filestore tier {tier!r}"


def _region_for(location: str) -> str:
    return re.sub(r"-[a-z]$", "", location)


def _network_matches(current: str, requested: str) -> bool:
    return current == requested or current.rsplit("/", 1)[-1] == requested.rsplit("/", 1)[-1]


def _label_key(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return (clean or "label")[:63]


def _label_value(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return clean[:63]


def _tenant_labels(cfg: dict[str, Any]) -> dict[str, str]:
    return {_label_key(str(key)): _label_value(str(value)) for key, value in (cfg.get("labels") or {}).items()}


def _identity_labels(managed_service_id: str) -> dict[str, str]:
    """The labels ownership decides on, from the spec only."""
    return {
        "astrolift-io-managed-by": "platform",
        "astrolift-io-managed-service-id": _label_value(managed_service_id),
        MANAGED_SERVICE_ID_LABEL: _label_value(managed_service_id),
    }


def _platform_last(base: dict[str, str], tenant: dict[str, str]) -> dict[str, str]:
    """``base`` with ``tenant`` applied to its tenant keys only: platform labels always win (#2098)."""
    merged = {key: value for key, value in base.items() if not is_platform_label_key(key)}
    merged.update({key: value for key, value in tenant.items() if not is_platform_label_key(key)})
    merged.update({key: value for key, value in base.items() if is_platform_label_key(key)})
    return merged


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    if isinstance(exc, FilestoreNotFound):
        return DeprovisionResult(True, handle, "Filestore instance already gone")
    return DeprovisionResult(False, handle, f"{action}: {exc}", [str(exc)])
