"""Tests for storage tier + durability abstraction (#104, spec 22 §2-3)."""

from __future__ import annotations

import pytest

from astrolift_drivers.storage_tiers import (
    TIER_MIN_IOPS,
    Durability,
    PerformanceTier,
    StorageError,
    TierCompatibility,
    VolumeSpec,
    assert_cluster_supports,
    native_hint,
    parse_durability,
    parse_size,
    parse_tier,
)

# ---- size parser ---------------------------------------------------


@pytest.mark.parametrize(
    "size,expected_bytes",
    [
        ("1", 1),
        ("100Mi", 100 * 1024**2),
        ("20Gi", 20 * 1024**3),
        ("1.5Ti", int(1.5 * 1024**4)),
        ("100Pi", 100 * 1024**5),
    ],
)
def test_parse_size_units(size, expected_bytes):
    assert parse_size(size) == expected_bytes


@pytest.mark.parametrize("bad", ["", "abc", "20G", "20", "20 Gi", "-5Gi"])
def test_parse_size_rejects_malformed(bad):
    if bad == "20":
        # bare number = bytes; that's valid
        assert parse_size(bad) == 20
        return
    with pytest.raises(StorageError):
        parse_size(bad)


# ---- tier + durability parsing -------------------------------------


def test_default_tier_balanced():
    """Spec 22 §2: balanced is the default."""
    assert parse_tier(None) == PerformanceTier.BALANCED
    assert parse_tier("") == PerformanceTier.BALANCED


def test_default_durability_zonal():
    """Spec 22 §3: zonal is the default — survives node
    replacement, doesn't pay for cross-zone replication."""
    assert parse_durability(None) == Durability.ZONAL
    assert parse_durability("") == Durability.ZONAL


def test_unknown_tier_rejected():
    with pytest.raises(StorageError, match="performance_tier"):
        parse_tier("super-fast")


def test_unknown_durability_rejected():
    with pytest.raises(StorageError, match="durability"):
        parse_durability("multi-cloud")


def test_min_iops_locked_per_tier():
    """Lock-test the IOPS expectations so plugins can't silently
    ship a 'balanced' StorageClass with 100 IOPS."""
    assert TIER_MIN_IOPS[PerformanceTier.STANDARD] == 100
    assert TIER_MIN_IOPS[PerformanceTier.BALANCED] == 3_000
    assert TIER_MIN_IOPS[PerformanceTier.HIGH_IOPS] == 16_000
    assert TIER_MIN_IOPS[PerformanceTier.EXTREME] == 64_000


# ---- VolumeSpec construction ---------------------------------------


def test_volume_spec_clean():
    spec = VolumeSpec(
        name="data",
        size="20Gi",
        access_modes=("ReadWriteOnce",),
    )
    assert spec.performance_tier == PerformanceTier.BALANCED
    assert spec.durability == Durability.ZONAL
    assert spec.encryption_at_rest is True


def test_volume_spec_rejects_empty_name():
    with pytest.raises(StorageError, match="name"):
        VolumeSpec(name="", size="10Gi", access_modes=("ReadWriteOnce",))


def test_volume_spec_rejects_empty_access_modes():
    with pytest.raises(StorageError, match="access_mode"):
        VolumeSpec(name="data", size="10Gi", access_modes=())


def test_volume_spec_rejects_unknown_access_mode():
    with pytest.raises(StorageError, match="not one of"):
        VolumeSpec(
            name="data",
            size="10Gi",
            access_modes=("ReadWriteForever",),
        )


def test_volume_spec_validates_size_via_parse():
    """Construction triggers parse_size; bad sizes fail at build."""
    with pytest.raises(StorageError):
        VolumeSpec(name="data", size="oops", access_modes=("ReadWriteOnce",))


# ---- compatibility -------------------------------------------------


def test_supported_pair_passes():
    compat = TierCompatibility(
        cluster_id=1,
        supported=frozenset(
            {
                (PerformanceTier.BALANCED, Durability.ZONAL),
                (PerformanceTier.HIGH_IOPS, Durability.REGIONAL),
            }
        ),
    )
    spec = VolumeSpec(
        name="data",
        size="20Gi",
        access_modes=("ReadWriteOnce",),
        performance_tier=PerformanceTier.BALANCED,
        durability=Durability.ZONAL,
    )
    assert_cluster_supports(spec=spec, compatibility=compat)


def test_unsupported_pair_raises():
    compat = TierCompatibility(
        cluster_id=1,
        supported=frozenset(
            {
                (PerformanceTier.BALANCED, Durability.ZONAL),
            }
        ),
    )
    spec = VolumeSpec(
        name="data",
        size="20Gi",
        access_modes=("ReadWriteOnce",),
        performance_tier=PerformanceTier.EXTREME,
        durability=Durability.REGIONAL,
    )
    with pytest.raises(StorageError, match="does not support"):
        assert_cluster_supports(spec=spec, compatibility=compat)


def test_empty_compatibility_rejects_all():
    """A misconfigured cluster (empty supported set) rejects every
    volume spec — surfaces the misconfig."""
    compat = TierCompatibility(cluster_id=1, supported=frozenset())
    spec = VolumeSpec(
        name="data",
        size="20Gi",
        access_modes=("ReadWriteOnce",),
    )
    with pytest.raises(StorageError):
        assert_cluster_supports(spec=spec, compatibility=compat)


# ---- native hints --------------------------------------------------


def test_native_hint_for_known_clouds():
    assert "gp3" in native_hint(cloud="aws", tier=PerformanceTier.BALANCED)
    assert "pd-balanced" in native_hint(cloud="gcp", tier=PerformanceTier.BALANCED)
    assert "premium" in native_hint(cloud="azure", tier=PerformanceTier.BALANCED)


def test_native_hint_case_insensitive_cloud():
    """Operators may type 'AWS' or 'aws'."""
    assert native_hint(cloud="AWS", tier=PerformanceTier.BALANCED) == native_hint(
        cloud="aws", tier=PerformanceTier.BALANCED
    )


def test_native_hint_unknown_cloud_returns_empty():
    """Vanilla k8s / on-prem clouds fall through to plugin's
    own resolution; we don't have hints for them."""
    assert native_hint(cloud="vanilla-k8s", tier=PerformanceTier.BALANCED) == ""
    assert native_hint(cloud="onprem", tier=PerformanceTier.BALANCED) == ""
