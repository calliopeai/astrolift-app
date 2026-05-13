"""
Container security context + volume/PVC rendering (#140, spec 05 §7-7.1).

Pure-Python rendering. The manifest renderer consults this for:

* **Security context** — secure defaults (run_as_non_root,
  read-only rootfs, no privilege escalation) with explicit
  opt-out for legacy workloads that need root.
* **Volume declarations** — parse ``[[workloads.<name>.volumes]]``
  and render PVCs / emptyDir / configMap / secret volume types.

Pairs with #104 (storage tiers) and #110 (storage preflight)
for the PVC's underlying StorageClass selection.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum

from astrolift_drivers.storage_tiers import (
    Durability,
    PerformanceTier,
    StorageError,
    parse_durability,
    parse_size,
    parse_tier,
)

# ---- security context ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SecurityContext:
    """Manifest-projected security flags. Defaults are
    deliberately secure — operators must explicitly opt out."""

    read_only_root_fs: bool = True
    run_as_non_root: bool = True
    run_as_user: int | None = None
    """Specific UID to run as. None = let k8s pick (uses image's
    USER directive). Setting this to 0 (root) is allowed only
    when run_as_non_root=False (validation enforces)."""

    allow_privilege_escalation: bool = False
    """The biggest foot-gun. Default off; legacy workloads that
    need it (Docker-in-Docker, etc.) must opt in explicitly."""

    capabilities_drop: tuple[str, ...] = ("ALL",)
    """Drop all Linux capabilities by default. Workloads that
    need NET_BIND_SERVICE etc. add via capabilities_add."""

    capabilities_add: tuple[str, ...] = ()


def parse_security_context(raw: Mapping) -> SecurityContext:
    """Project a TOML ``[workloads.<name>.security]`` block onto
    SecurityContext. Validates internal consistency: can't be
    run_as_non_root + run_as_user=0 (root)."""
    if not isinstance(raw, Mapping):
        raise StorageError("security context must be a mapping")

    run_as_non_root = bool(raw.get("run_as_non_root", True))
    run_as_user = raw.get("run_as_user")
    if run_as_user is not None:
        if not isinstance(run_as_user, int):
            raise StorageError("run_as_user must be an int (UID)")
        if run_as_user < 0:
            raise StorageError("run_as_user must be non-negative")
        if run_as_non_root and run_as_user == 0:
            raise StorageError("run_as_non_root=True conflicts with run_as_user=0 (uid 0 is root)")

    return SecurityContext(
        read_only_root_fs=bool(raw.get("read_only_root_fs", True)),
        run_as_non_root=run_as_non_root,
        run_as_user=run_as_user,
        allow_privilege_escalation=bool(raw.get("allow_privilege_escalation", False)),
        capabilities_drop=tuple(raw.get("capabilities_drop", ("ALL",))),
        capabilities_add=tuple(raw.get("capabilities_add", ())),
    )


def render_security_context(sec: SecurityContext) -> dict:
    """K8s SecurityContext shape. Caller embeds in PodSpec or
    ContainerSpec depending on which fields apply where —
    runAsUser/runAsNonRoot are pod-level by default, the rest
    are container-level."""
    out: dict = {
        "readOnlyRootFilesystem": sec.read_only_root_fs,
        "runAsNonRoot": sec.run_as_non_root,
        "allowPrivilegeEscalation": sec.allow_privilege_escalation,
        "capabilities": {
            "drop": list(sec.capabilities_drop),
        },
    }
    if sec.run_as_user is not None:
        out["runAsUser"] = sec.run_as_user
    if sec.capabilities_add:
        out["capabilities"]["add"] = list(sec.capabilities_add)
    return out


# ---- volume declarations -------------------------------------------


class VolumeKind(StrEnum):
    """Locked vocabulary so the renderer can switch reliably."""

    PVC = "pvc"
    EMPTY_DIR = "empty_dir"
    CONFIG_MAP = "config_map"
    SECRET = "secret"


@dataclasses.dataclass(frozen=True, slots=True)
class VolumeDecl:
    """Manifest-projected volume entry."""

    name: str
    mount_path: str
    kind: VolumeKind = VolumeKind.PVC

    # PVC-specific
    size: str = ""
    storage_class: str = ""
    access_mode: str = "ReadWriteOnce"
    performance_tier: PerformanceTier = PerformanceTier.BALANCED
    durability: Durability = Durability.ZONAL

    # configMap / secret-specific
    source_name: str = ""
    """Name of the ConfigMap or Secret to mount."""

    # emptyDir-specific
    size_limit: str = ""
    """Optional ``sizeLimit`` for emptyDir to bound memory-backed
    or disk-backed scratch volumes."""

    def __post_init__(self) -> None:
        if not self.name:
            raise StorageError("volume name is required")
        if not self.mount_path or not self.mount_path.startswith("/"):
            raise StorageError(f"volume mount_path {self.mount_path!r} must be an absolute path")

        if self.kind == VolumeKind.PVC:
            if not self.size:
                raise StorageError(f"PVC volume {self.name!r} requires size")
            # Validate parseable
            parse_size(self.size)
        elif self.kind in (VolumeKind.CONFIG_MAP, VolumeKind.SECRET):
            if not self.source_name:
                raise StorageError(
                    f"{self.kind.value} volume {self.name!r} requires "
                    "source_name (the ConfigMap/Secret to mount)"
                )
        elif self.kind == VolumeKind.EMPTY_DIR:
            # No required fields beyond name + mount_path
            if self.size_limit:
                # Validate parseable when set
                parse_size(self.size_limit)


def parse_volume(raw: Mapping) -> VolumeDecl:
    """Project a TOML volume entry onto VolumeDecl."""
    if not isinstance(raw, Mapping):
        raise StorageError("volume entry must be a mapping")

    kind_raw = str(raw.get("kind", "pvc"))
    try:
        kind = VolumeKind(kind_raw)
    except ValueError as exc:
        raise StorageError(f"volume kind {kind_raw!r} not one of {[k.value for k in VolumeKind]}") from exc

    return VolumeDecl(
        name=str(raw.get("name", "")),
        mount_path=str(raw.get("mount_path", "")),
        kind=kind,
        size=str(raw.get("size", "")),
        storage_class=str(raw.get("storage_class", "")),
        access_mode=str(raw.get("access_mode", "ReadWriteOnce")),
        performance_tier=parse_tier(raw.get("performance_tier")),
        durability=parse_durability(raw.get("durability")),
        source_name=str(raw.get("source_name", "")),
        size_limit=str(raw.get("size_limit", "")),
    )


def render_pvc(volume: VolumeDecl, *, namespace: str) -> dict:
    """Render the PVC manifest for a PVC-kind volume. PVC name
    is the volume name; the same name is used in
    PodSpec.volumes for the claim ref."""
    if volume.kind != VolumeKind.PVC:
        raise StorageError(f"render_pvc called on non-PVC volume kind {volume.kind!r}")
    spec: dict = {
        "accessModes": [volume.access_mode],
        "resources": {"requests": {"storage": volume.size}},
    }
    if volume.storage_class:
        spec["storageClassName"] = volume.storage_class
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": volume.name,
            "namespace": namespace,
        },
        "spec": spec,
    }


def render_pod_volume(volume: VolumeDecl) -> dict:
    """Render the PodSpec.volumes entry — what attaches a volume
    source to the pod."""
    entry: dict = {"name": volume.name}

    if volume.kind == VolumeKind.PVC:
        entry["persistentVolumeClaim"] = {"claimName": volume.name}
    elif volume.kind == VolumeKind.EMPTY_DIR:
        empty_dir: dict = {}
        if volume.size_limit:
            empty_dir["sizeLimit"] = volume.size_limit
        entry["emptyDir"] = empty_dir
    elif volume.kind == VolumeKind.CONFIG_MAP:
        entry["configMap"] = {"name": volume.source_name}
    elif volume.kind == VolumeKind.SECRET:
        entry["secret"] = {"secretName": volume.source_name}
    else:
        raise StorageError(f"unsupported volume kind {volume.kind!r}")

    return entry


def render_volume_mount(volume: VolumeDecl) -> dict:
    """Render the ContainerSpec.volumeMounts entry — what
    attaches the volume into a container's filesystem."""
    return {"name": volume.name, "mountPath": volume.mount_path}
