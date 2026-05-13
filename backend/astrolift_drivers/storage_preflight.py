"""
Storage pre-flight validation + alert thresholds (#110, spec 22 §14-15).

Pure-Python policy. Bind-time validation refuses incompatible
volumes BEFORE the deploy workflow tries to provision (catches
'no RWX StorageClass on this cluster' early). Default alert
thresholds for the per-volume metrics rule set live here too.

Pairs with #104 (storage tier + durability) and #62 (storage
class resolver). This module is the bind-time gate AND the
default-alert template source.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum

from astrolift_drivers.storage_tiers import (
    Durability,
    StorageError,
    VolumeSpec,
)

# Spec 22 §15 alert thresholds. Lock-tested.
WARNING_USED_RATIO = 0.85
CRITICAL_USED_RATIO = 0.95
SNAPSHOT_AGE_OVER_SCHEDULE_RATIO = 1.5


class ClusterStorageCapability(StrEnum):
    """Per-cluster capability flags the pre-flight checks."""

    HAS_RWX = "has_rwx"
    """Cluster has at least one StorageClass that supports
    ReadWriteMany (NFS, longhorn-rwx, EFS via the EFS CSI
    driver, GCP Filestore)."""

    HAS_REGIONAL = "has_regional"
    """Cluster has a regional StorageClass (multi-AZ replicated)."""

    HAS_BLOCK = "has_block"
    """Cluster has a block-storage StorageClass usable by
    in-cluster managed services (a baseline; almost always
    present)."""


@dataclasses.dataclass(frozen=True, slots=True)
class ClusterStorageProfile:
    """The cluster's storage capability snapshot. Plugin probe
    fills this in at register time + refresh."""

    cluster_id: int
    cluster_slug: str
    capabilities: frozenset[ClusterStorageCapability]


# ---- pre-flight ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreflightFailure:
    """One reason the bind would fail. Multiple failures across
    multiple volumes get collected so the operator sees the full
    set in one error."""

    volume_name: str
    code: str
    message: str
    remediation: str


def preflight(
    *,
    volumes: Sequence[VolumeSpec],
    profile: ClusterStorageProfile,
    needs_in_cluster_managed_service: bool = False,
) -> tuple[PreflightFailure, ...]:
    """Bind-time check. Returns a list of failures (empty when
    everything's OK). Operator sees ALL problems at once instead
    of fixing them one at a time."""
    out: list[PreflightFailure] = []

    for volume in volumes:
        # RWX requirement
        if "ReadWriteMany" in volume.access_modes:
            if ClusterStorageCapability.HAS_RWX not in profile.capabilities:
                out.append(
                    PreflightFailure(
                        volume_name=volume.name,
                        code="no_rwx_storage_class",
                        message=(
                            f"volume {volume.name!r} requires ReadWriteMany "
                            f"but cluster {profile.cluster_slug!r} has no "
                            "RWX-capable StorageClass"
                        ),
                        remediation=(
                            "install a CSI driver with RWX support: "
                            "Longhorn (longhorn-rwx), rook-ceph-filesystem, "
                            "AWS EFS CSI, or GCP Filestore CSI; or pick a "
                            "different cluster"
                        ),
                    )
                )

        # Regional durability requirement
        if volume.durability == Durability.REGIONAL:
            if ClusterStorageCapability.HAS_REGIONAL not in profile.capabilities:
                out.append(
                    PreflightFailure(
                        volume_name=volume.name,
                        code="no_regional_storage_class",
                        message=(
                            f"volume {volume.name!r} requires regional "
                            "durability but cluster has no regional "
                            "StorageClass"
                        ),
                        remediation=(
                            "select a managed regional StorageClass on "
                            "the cluster (e.g. AWS gp3 with multi-AZ EBS "
                            "snapshot replication, GCP regional pd-ssd, "
                            "Azure ZRS) or pick a different cluster"
                        ),
                    )
                )

    # In-cluster managed service block-storage requirement
    if needs_in_cluster_managed_service:
        if ClusterStorageCapability.HAS_BLOCK not in profile.capabilities:
            out.append(
                PreflightFailure(
                    volume_name="",
                    code="no_block_storage_class",
                    message=(
                        "in-cluster managed service variant selected but "
                        "cluster has no usable block StorageClass"
                    ),
                    remediation=(
                        "install a CSI block driver (rook-ceph-block, "
                        "longhorn block) or pick an out-of-cluster "
                        "managed-service variant"
                    ),
                )
            )

    return tuple(out)


def assert_preflight_passes(
    *,
    volumes: Sequence[VolumeSpec],
    profile: ClusterStorageProfile,
    needs_in_cluster_managed_service: bool = False,
) -> None:
    """Raise StorageError concatenating every failure if any.
    Convenience for the bind workflow."""
    failures = preflight(
        volumes=volumes,
        profile=profile,
        needs_in_cluster_managed_service=needs_in_cluster_managed_service,
    )
    if failures:
        details = "; ".join(f.message for f in failures)
        raise StorageError(f"storage preflight failed: {details}")


# ---- alert evaluators ----------------------------------------------


class VolumeAlertSeverity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


def used_ratio_severity(*, used_ratio: float) -> VolumeAlertSeverity:
    """Spec 22 §15 used-bytes / capacity-bytes thresholds.
    > 0.85 → warning; > 0.95 → critical. Equality is included
    in the higher band so an exactly-95% volume reads as
    critical (volume's about to fill)."""
    if used_ratio >= CRITICAL_USED_RATIO:
        return VolumeAlertSeverity.CRITICAL
    if used_ratio >= WARNING_USED_RATIO:
        return VolumeAlertSeverity.WARNING
    return VolumeAlertSeverity.OK


def snapshot_age_alarm(
    *,
    oldest_snapshot_age_seconds: float,
    schedule_seconds: int,
) -> bool:
    """True when the oldest snapshot is staler than 1.5x the
    declared schedule period — strong signal the snapshot
    workflow stopped firing.

    Returns False when there's no schedule (schedule_seconds <=
    0) — operator hasn't opted in to scheduled snapshots."""
    if schedule_seconds <= 0:
        return False
    threshold = schedule_seconds * SNAPSHOT_AGE_OVER_SCHEDULE_RATIO
    return oldest_snapshot_age_seconds > threshold
