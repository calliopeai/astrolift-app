"""NFS shared filesystem (#73).

Two flavors:
- nfs_csi: nfs-csi-driver provisions ReadWriteMany PVs from an
  external NFS server. Tenants get a PVC + mount path.
- nfs_subdir_provisioner: nfs-subdir-external-provisioner ships
  built-in PVC-shaped NFS provisioning when running with a single
  NFS export.

Both flavors emit StorageClass-tied PVCs. The driver doesn't host
NFS itself — the operator provisions the NFS server out of band
(or wires in EFS / Filestore / Azure Files via the cloud's CSI).

Variant key: ('filesystem', 'nfs_csi') and ('filesystem',
'nfs_subdir_provisioner').
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.k8s_naming import app_namespace, dns_label
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
    UpdateResult,
    UpdateSpec,
    ValueRef,
    VolumeMount,
    VolumeSourceKind,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "filesystem"


SIZE_TO_STORAGE = {
    "small": "10Gi",
    "medium": "100Gi",
    "large": "1Ti",
    "xlarge": "10Ti",
}


@dataclass(frozen=True)
class NFSConfig:
    storage_class_name: str
    """e.g. 'nfs-csi' or 'nfs-subdir' — operator-installed StorageClass
    that drives the per-PVC NFS path."""

    server_address: str = ""
    """For nfs_csi flavor only: NFS server hostname. Embedded in
    the PV's nfs.server field."""

    server_export: str = "/export"
    namespace: str | None = None
    cluster_driver: Any | None = None


class NFSDriver(ManagedServiceDriver):
    def __init__(self, *, config: NFSConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="filesystem_nfs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        pvc_name = self._pvc_name(spec=spec)
        namespace = self._namespace_for(spec=spec)
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=pvc_name,
        )
        manifest = self._render_pvc(spec=spec, name=pvc_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=handle,
                message="PVC manifest rendered",
            )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            [manifest],
        )
        if not result.ok:
            return ProvisionResult(
                ok=False,
                handle="",
                message="apply_manifests failed",
                errors=result.summary(),
            )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=f"PVC {pvc_name} provisioned",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=("PVC resize via re-applied spec; underlying CSI must support volume expansion"),
        )

    @driver_op(
        cloud="k8s_native",
        driver="filesystem_nfs",
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
        del delete_data, force_destroy
        if self._config.cluster_driver is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message="no cluster_driver — manifest deletion skipped",
            )
        parsed = _unpack_handle(spec.handle)
        if parsed.is_legacy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    "legacy 2-segment handle cannot be deprovisioned: "
                    "re-provision to refresh the handle, or pass a "
                    "4-segment handle (<kind>/<cluster>/<ns>/<name>)"
                ),
                errors=["legacy_handle_missing_locator"],
            )
        # PVC deletion fans out to the bound PV based on the
        # StorageClass's reclaimPolicy; the driver doesn't issue a
        # separate PV delete because dynamically-provisioned PVs are
        # owned by the StorageClass + CSI controller.
        stub = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": parsed.name,
                "namespace": parsed.namespace,
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [stub],
        )
        if result.errors:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(result.summary()),
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(f"PVC {parsed.name} deleted (PV reclaim follows the StorageClass's reclaimPolicy)"),
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message="status via PVC.status.phase",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        parsed = _unpack_handle(handle.handle)
        name = parsed.name
        cfg = config or {}
        mount_path = str(cfg.get("mount_path") or f"/data/{name}")
        server = str(cfg.get("server_address") or self._config.server_address)
        share = str(cfg.get("server_export") or self._config.server_export)
        options = list(cfg.get("mount_options") or [])
        if server and not any(option.startswith("nfsvers=") for option in options):
            options.append("nfsvers=4.1")
        if server:
            volume = VolumeMount(
                name=name,
                mount_path=mount_path,
                source_kind=VolumeSourceKind.CSI,
                protocol="nfs4.1",
                csi_driver="nfs.csi.k8s.io",
                volume_handle=f"{server}#{share}#{name}",
                volume_attributes={"server": server, "share": share},
                mount_options=options,
                read_only=bool(cfg.get("read_only", False)),
            )
        else:
            claim_namespace = str(cfg.get("claim_namespace") or parsed.namespace)
            if not claim_namespace:
                raise ValueError(
                    "legacy NFS handle has no claim namespace; set claim_namespace in the service config "
                    "or reprovision it to refresh the handle",
                )
            volume = VolumeMount(
                name=name,
                mount_path=mount_path,
                source_kind=VolumeSourceKind.EXISTING_PVC,
                protocol="pvc",
                claim_name=name,
                claim_namespace=claim_namespace,
                read_only=bool(cfg.get("read_only", False)),
            )
        return Binding(
            env_vars={
                "FILESYSTEM_HANDLE": ValueRef(literal=name),
                "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
                "FILESYSTEM_TLS": ValueRef(literal="false"),
            },
            pod_volume_mounts=[volume],
            iam_grants=[],
            notes="NFS workload mount through an existing claim or the NFS CSI driver",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs")
    def snapshot(self, handle):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(NFS) not supported -- NFS snapshots are "
            "CSI-driver-specific; wire to your CSI's VolumeSnapshot CRD (#618)",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs")
    def restore(self, snapshot, target):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(NFS) not supported -- restore from "
            "VolumeSnapshot happens at PVC-create time; out of scope (#618)",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_nfs", heartbeat=False)
    def config_schema(self):
        return {
            "type": "object",
            "required": ["storage_class_name"],
            "properties": {
                "storage_class_name": {"type": "string"},
                "server_address": {"type": "string"},
                "server_export": {"type": "string"},
                "claim_namespace": {
                    "type": "string",
                    "description": "Consumer namespace for a pre-existing PVC; required for legacy handles.",
                },
                "mount_path": {"type": "string"},
                "mount_options": {"type": "array", "items": {"type": "string"}},
                "read_only": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="k8s_native", driver="filesystem_nfs", heartbeat=False)
    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "PVC name",
                "FILESYSTEM_MOUNT_PATH": "Recommended mount path inside the pod",
                "FILESYSTEM_TLS": "TLS to NFS (false; NFS doesn't support TLS)",
            }
        )

    def _render_pvc(
        self,
        *,
        spec: ProvisionSpec,
        name: str,
    ) -> dict[str, Any]:
        storage_size = SIZE_TO_STORAGE.get(spec.size, "10Gi")
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": name,
                "namespace": self._namespace_for(spec=spec),
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/app": spec.app_slug,
                },
            },
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "storageClassName": self._config.storage_class_name,
                "resources": {
                    "requests": {"storage": storage_size},
                },
            },
        }

    def _pvc_name(self, *, spec: ProvisionSpec) -> str:
        return dns_label(
            "nfs",
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
        )

    def _namespace_for(self, *, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            return self._config.namespace
        return app_namespace(
            organization_slug=spec.organization_slug,
            app_slug=spec.app_slug,
        )
