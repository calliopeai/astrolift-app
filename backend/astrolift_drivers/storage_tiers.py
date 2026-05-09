"""
Storage tier + durability abstraction (#104, spec 22 §2-3).

Pure-Python policy. Manifests declare a portable storage shape
(``performance_tier``, ``durability``); per-cloud plugins resolve
to a concrete StorageClass at provision time.

Pairs with #62 ``primitives.resolve_storage_class`` (#62 picks
which StorageClass wins; this module owns the abstract vocabulary
+ per-tier expectations).
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum


class PerformanceTier(str, Enum):
    """Spec 22 §2 vocabulary. Locked: changing names would break
    every manifest."""

    STANDARD = "standard"
    BALANCED = "balanced"   # default
    HIGH_IOPS = "high_iops"
    EXTREME = "extreme"


class Durability(str, Enum):
    """Spec 22 §3 — the three durability levels."""

    LOCAL = "local"          # node-bound; lost on node replacement
    ZONAL = "zonal"          # AZ-bound; default
    REGIONAL = "regional"    # multi-AZ replicated


# Spec 22 §2 expectations: minimum IOPS guarantee per tier the
# plugin's resolver must meet (or beat). Keeps the abstraction
# meaningful: switching from AWS gp3 to GCP pd-balanced should
# not silently lose 90% of the IOPS.
TIER_MIN_IOPS: dict[PerformanceTier, int] = {
    PerformanceTier.STANDARD: 100,
    PerformanceTier.BALANCED: 3_000,
    PerformanceTier.HIGH_IOPS: 16_000,
    PerformanceTier.EXTREME: 64_000,
}


# ---- volume spec validation ----------------------------------------


_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)(Mi|Gi|Ti|Pi)?$")
_ACCESS_MODES = frozenset({
    "ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany", "ReadWriteOncePod",
})


class StorageError(ValueError):
    pass


def parse_size(size: str) -> int:
    """Parse k8s-style size strings to bytes. ``20Gi`` → bytes.

    Used by the validator to enforce min/max sizes per tier and
    by the cost-estimation panel.
    """
    if not size:
        raise StorageError("size is required")
    m = _SIZE_RE.match(size.strip())
    if m is None:
        raise StorageError(
            f"size {size!r} not in valid form "
            "(<number>[Mi|Gi|Ti|Pi]; bare number = bytes)"
        )
    value = float(m.group(1))
    unit = m.group(2) or ""
    multipliers = {
        "": 1, "Mi": 1024 ** 2, "Gi": 1024 ** 3,
        "Ti": 1024 ** 4, "Pi": 1024 ** 5,
    }
    return int(value * multipliers[unit])


@dataclasses.dataclass(frozen=True, slots=True)
class VolumeSpec:
    """Manifest-projected volume declaration."""

    name: str
    size: str
    """k8s-style size string."""

    access_modes: tuple[str, ...]
    performance_tier: PerformanceTier = PerformanceTier.BALANCED
    durability: Durability = Durability.ZONAL
    encryption_at_rest: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise StorageError("volume name is required")
        if not self.size:
            raise StorageError("volume size is required")
        # Trigger size parse for validation
        size_bytes = parse_size(self.size)
        if size_bytes <= 0:
            raise StorageError("volume size must be positive")
        if not self.access_modes:
            raise StorageError("at least one access_mode required")
        for mode in self.access_modes:
            if mode not in _ACCESS_MODES:
                raise StorageError(
                    f"access_mode {mode!r} not one of "
                    f"{sorted(_ACCESS_MODES)}"
                )


def parse_tier(value: str | None) -> PerformanceTier:
    """Parse manifest performance_tier with sensible default."""
    if not value:
        return PerformanceTier.BALANCED
    try:
        return PerformanceTier(value)
    except ValueError as exc:
        raise StorageError(
            f"performance_tier {value!r} not one of "
            f"{[t.value for t in PerformanceTier]}"
        ) from exc


def parse_durability(value: str | None) -> Durability:
    if not value:
        return Durability.ZONAL
    try:
        return Durability(value)
    except ValueError as exc:
        raise StorageError(
            f"durability {value!r} not one of "
            f"{[d.value for d in Durability]}"
        ) from exc


# ---- cross-tier compatibility --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TierCompatibility:
    """Per-cluster declaration of which (tier, durability) pairs the
    cluster's plugin can satisfy. Empty support means the cluster
    can't take this combination — caller surfaces as 'cluster does
    not support this volume'."""

    cluster_id: int
    supported: frozenset[tuple[PerformanceTier, Durability]]

    def supports(
        self, *,
        tier: PerformanceTier, durability: Durability,
    ) -> bool:
        return (tier, durability) in self.supported


def assert_cluster_supports(
    *,
    spec: VolumeSpec,
    compatibility: TierCompatibility,
) -> None:
    """Raise StorageError when a cluster can't satisfy the spec.
    Catches portability/capability mismatches at bind time
    (mirrors #59 capability check for managed services)."""
    if not compatibility.supports(
        tier=spec.performance_tier,
        durability=spec.durability,
    ):
        raise StorageError(
            f"cluster {compatibility.cluster_id} does not support "
            f"({spec.performance_tier.value}, "
            f"{spec.durability.value}) volumes"
        )


# ---- typical native-class hints ------------------------------------


# Lookup hints per spec 22 §2 — used by the catalog UI to show
# 'on AWS this becomes gp3' so operators understand cost
# implications without reading per-driver code.
TIER_NATIVE_HINTS: dict[tuple[str, PerformanceTier], str] = {
    ("aws", PerformanceTier.STANDARD): "gp2/gp3 low IOPS",
    ("aws", PerformanceTier.BALANCED): "gp3 (3000 IOPS)",
    ("aws", PerformanceTier.HIGH_IOPS): "gp3 (16K IOPS) or io2",
    ("aws", PerformanceTier.EXTREME): "io2 / io2 Block Express",
    ("gcp", PerformanceTier.STANDARD): "pd-standard / pd-balanced",
    ("gcp", PerformanceTier.BALANCED): "pd-balanced",
    ("gcp", PerformanceTier.HIGH_IOPS): "pd-ssd / hyperdisk",
    ("gcp", PerformanceTier.EXTREME): "pd-extreme / hyperdisk-extreme",
    ("azure", PerformanceTier.STANDARD): "disk_standard_ssd",
    ("azure", PerformanceTier.BALANCED): "disk_premium_ssd_v2",
    ("azure", PerformanceTier.HIGH_IOPS): "disk_premium_ssd / v2",
    ("azure", PerformanceTier.EXTREME): "ultra_disk",
}


def native_hint(*, cloud: str, tier: PerformanceTier) -> str:
    """Returns the human-readable hint, or empty string for
    unknown clouds (vanilla k8s, on-prem — caller falls back to
    the per-cluster plugin's own resolution)."""
    return TIER_NATIVE_HINTS.get((cloud.lower(), tier), "")
