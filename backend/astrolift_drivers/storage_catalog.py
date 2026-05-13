"""
StorageClassEntry catalog + volume expansion policy (#108, spec 22 §8+§10).

Pure-Python policy. Cluster registration probes capabilities and
populates a catalog of StorageClassEntry rows; the bind-time
resolver (#62) and volume-expansion workflow consult this module.

Pairs with #104 (storage tiers + durability) and #110 (preflight
gating).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from astrolift_drivers.storage_tiers import (
    Durability,
    PerformanceTier,
    StorageError,
    parse_size,
)


@dataclasses.dataclass(frozen=True, slots=True)
class StorageClassEntry:
    """Catalog row populated by cluster capability probe.

    The cluster onboarding workflow probes each StorageClass on
    the cluster and writes one of these per discovered class.
    """

    cluster_id: int
    name: str
    csi_driver: str
    reclaim_policy: str  # 'Delete' | 'Retain'
    volume_binding_mode: str  # 'Immediate' | 'WaitForFirstConsumer'
    allowed_topologies: tuple[str, ...]
    parameters: tuple[tuple[str, str], ...]
    """Driver-specific parameters from the StorageClass spec
    (e.g. AWS gp3 carries iops + throughput keys). Stored as a
    tuple of pairs to be hashable/frozen-friendly."""

    performance_tier: PerformanceTier
    durability: Durability
    access_modes: tuple[str, ...]
    is_default_for_tier: bool = False
    """Multiple StorageClasses may map to the same tier on a
    cluster (gp3 with different IOPS settings, etc.); one is
    declared default for tier-only manifests."""

    is_cluster_default: bool = False
    supports_snapshots: bool = False
    supports_volume_expansion: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise StorageError("StorageClass name is required")
        if self.cluster_id <= 0:
            raise StorageError("cluster_id must be positive")
        if self.reclaim_policy not in ("Delete", "Retain"):
            raise StorageError(f"reclaim_policy {self.reclaim_policy!r} must be 'Delete' or 'Retain'")
        if self.volume_binding_mode not in (
            "Immediate",
            "WaitForFirstConsumer",
        ):
            raise StorageError(
                f"volume_binding_mode {self.volume_binding_mode!r} "
                "must be 'Immediate' or 'WaitForFirstConsumer'"
            )


def find_for_tier(
    *,
    catalog: Sequence[StorageClassEntry],
    cluster_id: int,
    tier: PerformanceTier,
    durability: Durability,
) -> StorageClassEntry | None:
    """Resolve a (tier, durability) request to the catalog entry
    that handles it. Prefers ``is_default_for_tier=True`` when
    multiple entries match.
    """
    candidates = [
        e
        for e in catalog
        if e.cluster_id == cluster_id and e.performance_tier == tier and e.durability == durability
    ]
    if not candidates:
        return None
    # Prefer the explicit default; else first match.
    for c in candidates:
        if c.is_default_for_tier:
            return c
    return candidates[0]


def cluster_default(
    *,
    catalog: Sequence[StorageClassEntry],
    cluster_id: int,
) -> StorageClassEntry | None:
    """Return the cluster's globally-default StorageClass (the
    one used when manifest doesn't declare a tier)."""
    for e in catalog:
        if e.cluster_id == cluster_id and e.is_cluster_default:
            return e
    return None


# ---- volume expansion ----------------------------------------------


class VolumeExpansionError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class ExpansionPlan:
    """Inputs the expansion workflow consumes after policy
    validation passes."""

    pvc_name: str
    namespace: str
    current_size_bytes: int
    new_size_bytes: int
    storage_class: StorageClassEntry
    is_statefulset_volume: bool = False
    """When True, the workflow rolls expansion per-replica
    (each replica's PVC resizes + filesystem grows before the
    next; cuts blast radius if the resize fails mid-flight)."""


def plan_expansion(
    *,
    pvc_name: str,
    namespace: str,
    current_size: str,
    new_size: str,
    storage_class: StorageClassEntry,
    is_statefulset_volume: bool = False,
) -> ExpansionPlan:
    """Validate the requested expansion + return a typed plan.

    Rules:
      1. StorageClass must support volume expansion (CSI
         ``allowVolumeExpansion``); otherwise fail.
      2. ``new_size > current_size`` (k8s rejects shrinking;
         we surface the error early with a clear message).
      3. PVC name + namespace required.
    """
    if not pvc_name or not namespace:
        raise VolumeExpansionError("pvc_name and namespace are required")

    if not storage_class.supports_volume_expansion:
        raise VolumeExpansionError(
            f"StorageClass {storage_class.name!r} does not support "
            "volume expansion (CSI allowVolumeExpansion=false); "
            "operator must migrate the PVC to an expansion-capable "
            "class first"
        )

    current_bytes = parse_size(current_size)
    new_bytes = parse_size(new_size)

    if new_bytes <= current_bytes:
        raise VolumeExpansionError(
            f"new_size ({new_size}={new_bytes} bytes) must be larger "
            f"than current_size ({current_size}={current_bytes} bytes); "
            "k8s does not support shrinking PVCs"
        )

    return ExpansionPlan(
        pvc_name=pvc_name,
        namespace=namespace,
        current_size_bytes=current_bytes,
        new_size_bytes=new_bytes,
        storage_class=storage_class,
        is_statefulset_volume=is_statefulset_volume,
    )
