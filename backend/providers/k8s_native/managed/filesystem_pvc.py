"""StorageClass-backed portable filesystems for Kubernetes clusters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
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

_ACCESS_MODES = {
    "ReadWriteOnce",
    "ReadOnlyMany",
    "ReadWriteMany",
    "ReadWriteOncePod",
}


@dataclass(frozen=True)
class PVCConfig:
    storage_class_name: str = ""
    cluster_driver: Any | None = None
    csi_driver: str = ""
    default_access_modes: tuple[str, ...] = ("ReadWriteOnce",)


class _DynamicPVCDriver(ManagedServiceDriver):
    protocol = "pvc"
    shared = False

    def __init__(self, *, config: PVCConfig | None = None) -> None:
        self._config = config or PVCConfig()

    @driver_op(
        cloud="k8s_native",
        driver="filesystem_dynamic_pvc",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            storage_class_name = self._storage_class_name(spec.config)
            self._capacity(spec.size, spec.config)
            self._access_modes(spec.config)
            name = self._volume_name(spec)
        except ValueError as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=str(exc),
                errors=["invalid_filesystem_config"],
            )

        if self._config.cluster_driver is not None:
            error = self._preflight(
                cluster_id=spec.tenant_cluster_id,
                storage_class_name=storage_class_name,
            )
            if error:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=error,
                    errors=["storage_preflight_failed"],
                )

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=self._namespace_for(spec),
            name=name,
        )
        return ProvisionResult(
            ok=True,
            handle=handle,
            ready=True,
            message=(
                f"StorageClass {storage_class_name} volume template validated; "
                "consumer-local claims materialize when attached workloads deploy"
            ),
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            _unpack_handle(spec.handle)
            self._capacity(str(spec.size or spec.config.get("size") or "small"), spec.config)
            self._access_modes(spec.config)
            if "storage_class_name" in spec.config:
                self._storage_class_name(spec.config)
        except ValueError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=["invalid_filesystem_config"],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=("volume attachment settings updated; existing claims expand when consumer manifests reconcile"),
        )

    @driver_op(
        cloud="k8s_native",
        driver="filesystem_dynamic_pvc",
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
        del force_destroy
        parsed = _unpack_handle(spec.handle)
        if parsed.is_legacy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="legacy filesystem handle has no cluster locator",
                errors=["legacy_handle_missing_locator"],
                retryable=False,
            )
        if delete_data and spec.config.get("_dynamic_claim_cleanup_confirmed") is not True:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="delete_data requires lifecycle-confirmed cleanup of every consumer claim",
                errors=["consumer_claim_cleanup_unconfirmed"],
                retryable=False,
            )
        action = "consumer-claim cleanup confirmed" if delete_data else "consumer claims retained"
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"filesystem template {parsed.name} retired; {action}",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _unpack_handle(handle.handle)
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message="volume template is ready for consumer-local PVC reconciliation",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy filesystem handle has no cluster locator")
        cfg = config or {}
        storage_class_name = self._storage_class_name(cfg)
        capacity = self._capacity(str(cfg.get("size") or "small"), cfg)
        access_modes = self._access_modes(cfg)
        mount_path = str(cfg.get("mount_path") or f"/data/{parsed.name}")
        sub_path = str(cfg.get("sub_path") or "") or None
        volume = VolumeMount(
            name=parsed.name,
            mount_path=mount_path,
            sub_path=sub_path,
            source_kind=VolumeSourceKind.DYNAMIC_PVC,
            protocol=self.protocol,
            storage_class_name=storage_class_name,
            csi_driver=self._config.csi_driver,
            read_only=bool(cfg.get("read_only", False)),
            capacity=capacity,
            access_modes=access_modes,
            workload_names=tuple(str(value) for value in cfg.get("workload_names") or ()),
            container_names=tuple(str(value) for value in cfg.get("container_names") or ()),
        )
        return Binding(
            env_vars={
                "FILESYSTEM_HANDLE": ValueRef(literal=parsed.name),
                "FILESYSTEM_MOUNT_PATH": ValueRef(literal=mount_path),
                # In-transit encryption belongs to the StorageClass's
                # provisioner, and this driver cannot observe it: the cluster
                # inventory reports only name / default / provisioner /
                # reclaim policy, and a provisioner string does not imply
                # transport security (efs.csi.aws.com encrypts in transit only
                # when the mount options say tls; the "encrypted" parameter on
                # EBS and Ceph classes is at-rest LUKS, not transport). So the
                # binding reports the conservative value rather than guessing.
                # Under-reporting makes a consumer add protection it may not
                # have needed; over-reporting makes it skip protection it did.
                "FILESYSTEM_TLS": ValueRef(literal="false"),
            },
            pod_volume_mounts=[volume],
            notes=("A stable, consumer-namespace PVC is dynamically provisioned from the selected StorageClass"),
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc")
    def snapshot(self, handle):
        del handle
        raise UnsupportedOperationError(
            "managed_service.snapshot(dynamic PVC) requires the StorageClass's VolumeSnapshotClass (#1287)",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc")
    def restore(self, snapshot, target):
        del snapshot, target
        raise UnsupportedOperationError(
            "managed_service.restore(dynamic PVC) requires a declared VolumeSnapshotClass (#1287)",
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc", heartbeat=False)
    def config_schema(self):
        properties: dict[str, Any] = {
            "storage_class_name": {
                "type": "string",
                "minLength": 1,
                "description": "Existing StorageClass used to provision consumer-local claims.",
            },
            "capacity": {
                "type": "string",
                "pattern": r"^[1-9][0-9]*(?:[EPTGMK]i?|m)?$",
            },
            "access_modes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "uniqueItems": True,
                "items": {"enum": sorted(_ACCESS_MODES)},
            },
            "mount_path": {"type": "string", "pattern": "^/"},
            "sub_path": {"type": "string"},
            "read_only": {"type": "boolean", "default": False},
            "workload_names": {"type": "array", "items": {"type": "string"}},
            "container_names": {"type": "array", "items": {"type": "string"}},
        }
        if self.shared:
            properties["access_modes"]["default"] = ["ReadWriteMany"]
        else:
            properties["access_modes"]["default"] = ["ReadWriteOnce"]
        return {
            "type": "object",
            "required": ["storage_class_name"],
            "properties": properties,
        }

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc", heartbeat=False)
    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "FILESYSTEM_HANDLE": "Logical managed volume name",
                "FILESYSTEM_MOUNT_PATH": "Mount path inside the workload container",
                "FILESYSTEM_TLS": (
                    "In-transit encryption; false because the StorageClass's provisioner is opaque here"
                ),
            },
        )

    @driver_op(cloud="k8s_native", driver="filesystem_dynamic_pvc", heartbeat=False)
    def editable_fields(self) -> list[str]:
        # StorageClass and access mode are immutable once Kubernetes binds a
        # claim. Capacity is expansion-only and Kubernetes rejects shrinkage.
        return [
            "size",
            "capacity",
            "mount_path",
            "sub_path",
            "read_only",
            "workload_names",
            "container_names",
        ]

    def _preflight(self, *, cluster_id: str, storage_class_name: str) -> str:
        try:
            classes = list(self._config.cluster_driver.list_storage_classes(cluster_id))
        except Exception as exc:
            return f"could not inventory StorageClasses: {exc}"
        classes_by_name = {str(getattr(row, "name", "") or ""): row for row in classes if getattr(row, "name", "")}
        if storage_class_name not in classes_by_name:
            return f"StorageClass {storage_class_name!r} not found; cluster reports {sorted(classes_by_name)!r}"
        if self._config.csi_driver:
            storage_class = classes_by_name[storage_class_name]
            provisioner = str(getattr(storage_class, "provisioner", "") or "")
            if not provisioner:
                return f"StorageClass {storage_class_name!r} did not report its provisioner"
            if provisioner != self._config.csi_driver:
                return (
                    f"StorageClass {storage_class_name!r} uses provisioner {provisioner!r}, "
                    f"not required CSI driver {self._config.csi_driver!r}"
                )
            try:
                csi_drivers = set(self._config.cluster_driver.list_csi_drivers(cluster_id))
            except Exception as exc:
                return f"could not inventory CSI drivers: {exc}"
            if self._config.csi_driver not in csi_drivers:
                return f"CSI driver {self._config.csi_driver!r} not found; cluster reports {sorted(csi_drivers)!r}"
        return ""

    def _storage_class_name(self, config: dict[str, Any]) -> str:
        value = str(config.get("storage_class_name") or self._config.storage_class_name).strip()
        if not value:
            raise ValueError("storage_class_name is required")
        if len(value) > 253 or not re.fullmatch(
            r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?",
            value,
        ):
            raise ValueError("storage_class_name must be a Kubernetes DNS subdomain")
        if any(len(label) > 63 for label in value.split(".")):
            raise ValueError("storage_class_name DNS labels cannot exceed 63 characters")
        return value

    def _capacity(self, size: str, config: dict[str, Any]) -> str:
        value = str(config.get("capacity") or SIZE_TO_STORAGE.get(size, ""))
        if not re.fullmatch(r"[1-9][0-9]*(?:[EPTGMK]i?|m)?", value):
            raise ValueError("capacity must be a positive canonical Kubernetes quantity")
        return value

    def _access_modes(self, config: dict[str, Any]) -> tuple[str, ...]:
        raw = config.get("access_modes", self._config.default_access_modes)
        if isinstance(raw, str):
            raw = [raw]
        modes = tuple(str(value) for value in raw)
        if not modes or any(mode not in _ACCESS_MODES for mode in modes):
            raise ValueError("access_modes contains an unsupported Kubernetes access mode")
        if len(modes) != 1:
            raise ValueError("a dynamic PVC must request exactly one access mode")
        if len(set(modes)) != len(modes):
            raise ValueError("access_modes cannot contain duplicates")
        return modes

    def _volume_name(self, spec: ProvisionSpec) -> str:
        prefix = "cephfs" if self.protocol == "cephfs" else "pvc"
        return dns_label(
            prefix,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
        )

    def _namespace_for(self, spec: ProvisionSpec) -> str:
        return app_namespace(
            organization_slug=spec.organization_slug,
            app_slug=spec.app_slug,
        )


class StorageClassPVCDriver(_DynamicPVCDriver):
    """Portable PVC template backed by any operator-selected StorageClass."""


class RookCephFSDriver(_DynamicPVCDriver):
    """Rook CephFS PVC template with RWX and CSI preflight defaults."""

    protocol = "cephfs"
    shared = True
