"""Tests for storage pre-flight + alert thresholds (#110, spec 22 §14-15)."""

from __future__ import annotations

import pytest

from astrolift_drivers.storage_preflight import (
    CRITICAL_USED_RATIO,
    SNAPSHOT_AGE_OVER_SCHEDULE_RATIO,
    WARNING_USED_RATIO,
    ClusterStorageCapability,
    ClusterStorageProfile,
    PreflightFailure,
    VolumeAlertSeverity,
    assert_preflight_passes,
    preflight,
    snapshot_age_alarm,
    used_ratio_severity,
)
from astrolift_drivers.storage_tiers import (
    Durability,
    PerformanceTier,
    StorageError,
    VolumeSpec,
)


def _profile(*caps: ClusterStorageCapability) -> ClusterStorageProfile:
    return ClusterStorageProfile(
        cluster_id=1, cluster_slug="prod",
        capabilities=frozenset(caps),
    )


def _vol(**kw) -> VolumeSpec:
    base = dict(
        name="data", size="20Gi",
        access_modes=("ReadWriteOnce",),
    )
    base.update(kw)
    return VolumeSpec(**base)


# ---- preflight RWX -------------------------------------------------


def test_rwx_volume_passes_when_cluster_supports():
    profile = _profile(
        ClusterStorageCapability.HAS_RWX,
        ClusterStorageCapability.HAS_BLOCK,
    )
    vol = _vol(access_modes=("ReadWriteMany",))
    failures = preflight(volumes=[vol], profile=profile)
    assert failures == ()


def test_rwx_volume_fails_when_cluster_lacks():
    profile = _profile(ClusterStorageCapability.HAS_BLOCK)  # no RWX
    vol = _vol(access_modes=("ReadWriteMany",))
    failures = preflight(volumes=[vol], profile=profile)
    assert len(failures) == 1
    assert failures[0].code == "no_rwx_storage_class"
    assert "Longhorn" in failures[0].remediation


def test_rwo_volume_unaffected_by_rwx_absence():
    """Standard RWO volumes shouldn't trip the RWX check."""
    profile = _profile()  # nothing
    vol = _vol(access_modes=("ReadWriteOnce",))
    failures = preflight(volumes=[vol], profile=profile)
    rwx_failures = [f for f in failures if f.code == "no_rwx_storage_class"]
    assert rwx_failures == []


# ---- preflight regional --------------------------------------------


def test_regional_volume_passes_when_cluster_supports():
    profile = _profile(
        ClusterStorageCapability.HAS_REGIONAL,
        ClusterStorageCapability.HAS_BLOCK,
    )
    vol = _vol(durability=Durability.REGIONAL)
    failures = preflight(volumes=[vol], profile=profile)
    assert failures == ()


def test_regional_volume_fails_when_cluster_lacks():
    profile = _profile(ClusterStorageCapability.HAS_BLOCK)
    vol = _vol(durability=Durability.REGIONAL)
    failures = preflight(volumes=[vol], profile=profile)
    assert len(failures) == 1
    assert failures[0].code == "no_regional_storage_class"
    assert "regional" in failures[0].remediation


def test_zonal_volume_unaffected_by_regional_absence():
    """Default zonal volumes don't trip the regional check."""
    profile = _profile()
    vol = _vol(durability=Durability.ZONAL)
    failures = preflight(volumes=[vol], profile=profile)
    regional_failures = [
        f for f in failures if f.code == "no_regional_storage_class"
    ]
    assert regional_failures == []


# ---- preflight in-cluster managed service --------------------------


def test_in_cluster_managed_service_needs_block_class():
    profile = _profile()  # no block
    failures = preflight(
        volumes=[], profile=profile,
        needs_in_cluster_managed_service=True,
    )
    assert len(failures) == 1
    assert failures[0].code == "no_block_storage_class"


def test_in_cluster_managed_service_ok_with_block_class():
    profile = _profile(ClusterStorageCapability.HAS_BLOCK)
    failures = preflight(
        volumes=[], profile=profile,
        needs_in_cluster_managed_service=True,
    )
    assert failures == ()


# ---- multi-failure aggregation -------------------------------------


def test_all_failures_collected_at_once():
    """Operator sees the full list, not one-at-a-time."""
    profile = _profile()  # no caps at all
    volumes = [
        _vol(name="rwx-vol", access_modes=("ReadWriteMany",)),
        _vol(name="regional-vol", durability=Durability.REGIONAL),
    ]
    failures = preflight(
        volumes=volumes, profile=profile,
        needs_in_cluster_managed_service=True,
    )
    codes = {f.code for f in failures}
    assert codes == {
        "no_rwx_storage_class",
        "no_regional_storage_class",
        "no_block_storage_class",
    }


def test_assert_preflight_raises_on_any_failure():
    profile = _profile()
    vol = _vol(access_modes=("ReadWriteMany",))
    with pytest.raises(StorageError, match="preflight failed"):
        assert_preflight_passes(volumes=[vol], profile=profile)


def test_assert_preflight_passes_when_clean():
    profile = _profile(
        ClusterStorageCapability.HAS_RWX,
        ClusterStorageCapability.HAS_REGIONAL,
        ClusterStorageCapability.HAS_BLOCK,
    )
    vol = _vol(access_modes=("ReadWriteMany",), durability=Durability.REGIONAL)
    assert_preflight_passes(volumes=[vol], profile=profile)


# ---- used-ratio severity -------------------------------------------


def test_used_ratio_below_warning_is_ok():
    assert used_ratio_severity(used_ratio=0.5) == VolumeAlertSeverity.OK
    assert used_ratio_severity(used_ratio=0.84) == VolumeAlertSeverity.OK


def test_used_ratio_at_warning_threshold():
    """Equality is included so 'exactly 85%' fires the warning,
    not the OK band."""
    assert used_ratio_severity(used_ratio=0.85) == VolumeAlertSeverity.WARNING


def test_used_ratio_at_critical_threshold():
    assert used_ratio_severity(used_ratio=0.95) == VolumeAlertSeverity.CRITICAL


def test_used_ratio_above_critical():
    assert used_ratio_severity(used_ratio=0.99) == VolumeAlertSeverity.CRITICAL
    assert used_ratio_severity(used_ratio=1.0) == VolumeAlertSeverity.CRITICAL


def test_thresholds_locked():
    assert WARNING_USED_RATIO == 0.85
    assert CRITICAL_USED_RATIO == 0.95
    assert SNAPSHOT_AGE_OVER_SCHEDULE_RATIO == 1.5


# ---- snapshot age alarm --------------------------------------------


def test_snapshot_age_alarm_when_well_past_schedule():
    """1.5x the schedule = strong signal the snapshot workflow
    stopped firing."""
    assert snapshot_age_alarm(
        oldest_snapshot_age_seconds=180_000,  # 50 hours
        schedule_seconds=86_400,  # daily
    ) is True


def test_snapshot_age_no_alarm_within_schedule():
    """The most recent snapshot is at most ``schedule_seconds``
    old in normal operation; ``< 1.5x`` is fine."""
    assert snapshot_age_alarm(
        oldest_snapshot_age_seconds=90_000,  # 25 hours
        schedule_seconds=86_400,  # daily
    ) is False


def test_snapshot_age_no_alarm_when_no_schedule():
    """No scheduled snapshots = operator hasn't opted in;
    don't alarm."""
    assert snapshot_age_alarm(
        oldest_snapshot_age_seconds=999_999,
        schedule_seconds=0,
    ) is False
