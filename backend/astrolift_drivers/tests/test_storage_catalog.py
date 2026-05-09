"""Tests for StorageClassEntry catalog + expansion (#108, spec 22 §8+§10)."""

from __future__ import annotations

import pytest

from astrolift_drivers.storage_catalog import (
    StorageClassEntry,
    VolumeExpansionError,
    cluster_default,
    find_for_tier,
    plan_expansion,
)
from astrolift_drivers.storage_tiers import (
    Durability,
    PerformanceTier,
    StorageError,
)


def _entry(**kw) -> StorageClassEntry:
    base = dict(
        cluster_id=1,
        name="gp3-balanced",
        csi_driver="ebs.csi.aws.com",
        reclaim_policy="Delete",
        volume_binding_mode="WaitForFirstConsumer",
        allowed_topologies=(),
        parameters=(),
        performance_tier=PerformanceTier.BALANCED,
        durability=Durability.ZONAL,
        access_modes=("ReadWriteOnce",),
    )
    base.update(kw)
    return StorageClassEntry(**base)


# ---- StorageClassEntry construction guards ------------------------


def test_entry_requires_name():
    with pytest.raises(StorageError, match="name"):
        _entry(name="")


def test_entry_requires_positive_cluster_id():
    with pytest.raises(StorageError, match="cluster_id"):
        _entry(cluster_id=0)


def test_entry_rejects_unknown_reclaim_policy():
    with pytest.raises(StorageError, match="reclaim_policy"):
        _entry(reclaim_policy="bogus")


def test_entry_rejects_unknown_binding_mode():
    with pytest.raises(StorageError, match="volume_binding_mode"):
        _entry(volume_binding_mode="bogus")


# ---- find_for_tier -------------------------------------------------


def test_find_returns_match():
    cat = [_entry(name="gp3-balanced")]
    out = find_for_tier(
        catalog=cat, cluster_id=1,
        tier=PerformanceTier.BALANCED, durability=Durability.ZONAL,
    )
    assert out is not None
    assert out.name == "gp3-balanced"


def test_find_returns_none_when_no_match():
    cat = [_entry(performance_tier=PerformanceTier.BALANCED)]
    out = find_for_tier(
        catalog=cat, cluster_id=1,
        tier=PerformanceTier.EXTREME, durability=Durability.ZONAL,
    )
    assert out is None


def test_find_filters_by_cluster_id():
    cat = [
        _entry(cluster_id=1, name="cluster-1-gp3"),
        _entry(cluster_id=2, name="cluster-2-gp3"),
    ]
    out = find_for_tier(
        catalog=cat, cluster_id=2,
        tier=PerformanceTier.BALANCED, durability=Durability.ZONAL,
    )
    assert out.name == "cluster-2-gp3"


def test_find_prefers_default_for_tier_when_multiple():
    """Ambiguity rule: when multiple entries match, the one
    flagged is_default_for_tier wins."""
    cat = [
        _entry(name="gp3-low-iops", is_default_for_tier=False),
        _entry(name="gp3-default", is_default_for_tier=True),
        _entry(name="gp3-high-iops", is_default_for_tier=False),
    ]
    out = find_for_tier(
        catalog=cat, cluster_id=1,
        tier=PerformanceTier.BALANCED, durability=Durability.ZONAL,
    )
    assert out.name == "gp3-default"


def test_find_returns_first_when_no_default_flagged():
    cat = [_entry(name="alpha"), _entry(name="beta")]
    out = find_for_tier(
        catalog=cat, cluster_id=1,
        tier=PerformanceTier.BALANCED, durability=Durability.ZONAL,
    )
    assert out.name == "alpha"


# ---- cluster_default -----------------------------------------------


def test_cluster_default_returns_flagged():
    cat = [
        _entry(name="not-default", is_cluster_default=False),
        _entry(name="the-default", is_cluster_default=True),
    ]
    out = cluster_default(catalog=cat, cluster_id=1)
    assert out.name == "the-default"


def test_cluster_default_none_when_unflagged():
    cat = [_entry(is_cluster_default=False)]
    assert cluster_default(catalog=cat, cluster_id=1) is None


# ---- plan_expansion ------------------------------------------------


def test_plan_expansion_valid():
    sc = _entry(supports_volume_expansion=True)
    plan = plan_expansion(
        pvc_name="data-pvc", namespace="acme-api",
        current_size="10Gi", new_size="20Gi",
        storage_class=sc,
    )
    assert plan.pvc_name == "data-pvc"
    assert plan.new_size_bytes > plan.current_size_bytes


def test_plan_expansion_rejects_when_storage_class_doesnt_support():
    """Some StorageClasses (older io1, certain on-prem) explicitly
    set allowVolumeExpansion=false. Operator must migrate first."""
    sc = _entry(supports_volume_expansion=False)
    with pytest.raises(VolumeExpansionError, match="does not support"):
        plan_expansion(
            pvc_name="data-pvc", namespace="acme-api",
            current_size="10Gi", new_size="20Gi",
            storage_class=sc,
        )


def test_plan_expansion_rejects_shrink():
    """k8s does not support shrinking PVCs. Surface the error
    early with a clear message rather than letting kubectl
    return a confusing 422."""
    sc = _entry(supports_volume_expansion=True)
    with pytest.raises(VolumeExpansionError, match="shrinking"):
        plan_expansion(
            pvc_name="data-pvc", namespace="acme-api",
            current_size="20Gi", new_size="10Gi",
            storage_class=sc,
        )


def test_plan_expansion_rejects_same_size():
    """Same-size 'expansion' is a no-op the workflow shouldn't
    schedule — also rejected for clarity."""
    sc = _entry(supports_volume_expansion=True)
    with pytest.raises(VolumeExpansionError, match="larger than"):
        plan_expansion(
            pvc_name="data-pvc", namespace="acme-api",
            current_size="20Gi", new_size="20Gi",
            storage_class=sc,
        )


def test_plan_expansion_rejects_missing_pvc_name():
    sc = _entry(supports_volume_expansion=True)
    with pytest.raises(VolumeExpansionError, match="pvc_name"):
        plan_expansion(
            pvc_name="", namespace="acme-api",
            current_size="10Gi", new_size="20Gi",
            storage_class=sc,
        )


def test_plan_expansion_statefulset_flag_passes_through():
    """StatefulSet volumes need per-replica rolling expansion."""
    sc = _entry(supports_volume_expansion=True)
    plan = plan_expansion(
        pvc_name="data-pvc", namespace="ns",
        current_size="10Gi", new_size="20Gi",
        storage_class=sc, is_statefulset_volume=True,
    )
    assert plan.is_statefulset_volume is True
