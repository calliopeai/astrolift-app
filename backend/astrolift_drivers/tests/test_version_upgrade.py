"""Tests for plugin version upgrade policy (#168, spec 02 §8)."""

from __future__ import annotations

import pytest

from astrolift_drivers.version_upgrade import (
    DEFAULT_PHASES,
    RolloutBatch,
    RolloutCluster,
    UpgradeKind,
    Version,
    VersionParseError,
    check_compatibility,
    parse_version,
    plan_rollout,
    remaining_clusters,
    upgrade_kind,
)

# ---- semver parsing -------------------------------------------------


def test_parse_strict_xyz():
    assert parse_version("1.2.3") == Version(1, 2, 3)
    assert parse_version("0.0.1") == Version(0, 0, 1)
    assert parse_version("  10.20.30  ") == Version(10, 20, 30)


@pytest.mark.parametrize(
    "bad",
    [
        "1.2",
        "1.2.3.4",
        "1.2.3-rc1",
        "1.2.3+build",
        "v1.2.3",
        "latest",
        "",
    ],
)
def test_parse_rejects_anything_non_strict(bad):
    with pytest.raises(VersionParseError):
        parse_version(bad)


def test_version_is_orderable():
    assert Version(1, 0, 0) < Version(1, 0, 1)
    assert Version(1, 0, 1) < Version(1, 1, 0)
    assert Version(1, 99, 99) < Version(2, 0, 0)


# ---- upgrade kind ---------------------------------------------------


def test_kind_patch():
    assert upgrade_kind(from_v=Version(1, 2, 3), to_v=Version(1, 2, 4)) == UpgradeKind.PATCH
    assert upgrade_kind(from_v=Version(1, 2, 3), to_v=Version(1, 2, 3)) == UpgradeKind.PATCH


def test_kind_minor():
    assert upgrade_kind(from_v=Version(1, 2, 3), to_v=Version(1, 3, 0)) == UpgradeKind.MINOR


def test_kind_major():
    assert upgrade_kind(from_v=Version(1, 2, 3), to_v=Version(2, 0, 0)) == UpgradeKind.MAJOR


def test_kind_downgrade():
    assert upgrade_kind(from_v=Version(2, 0, 0), to_v=Version(1, 9, 9)) == UpgradeKind.DOWNGRADE


# ---- compatibility ---------------------------------------------------


def _ok_kwargs(**overrides):
    base = dict(
        from_v=Version(1, 2, 3),
        to_v=Version(1, 2, 4),
        cluster_capabilities={"k8s.workloads", "ingress.nginx"},
        required_capabilities={"k8s.workloads"},
        min_platform_version=Version(1, 0, 0),
        current_platform_version=Version(1, 5, 0),
    )
    base.update(overrides)
    return base


def test_clean_patch_upgrade_passes():
    report = check_compatibility(**_ok_kwargs())
    assert report.ok is True
    assert report.kind == UpgradeKind.PATCH


def test_major_upgrade_reports_approval_required():
    report = check_compatibility(**_ok_kwargs(from_v=Version(1, 9, 9), to_v=Version(2, 0, 0)))
    assert report.ok is False
    codes = {i.code for i in report.issues}
    assert "major_upgrade_requires_approval" in codes


def test_downgrade_blocked_without_explicit_flag():
    report = check_compatibility(**_ok_kwargs(from_v=Version(2, 0, 0), to_v=Version(1, 9, 9)))
    codes = {i.code for i in report.issues}
    assert "downgrade_blocked" in codes


def test_downgrade_allowed_with_flag():
    report = check_compatibility(
        **_ok_kwargs(
            from_v=Version(2, 0, 1),
            to_v=Version(2, 0, 0),
            allow_downgrade=True,
        )
    )
    assert report.ok is True
    assert report.kind == UpgradeKind.DOWNGRADE


def test_missing_capability_blocks():
    report = check_compatibility(
        **_ok_kwargs(
            cluster_capabilities={"k8s.workloads"},
            required_capabilities={"k8s.workloads", "service-mesh.istio"},
        )
    )
    codes = {i.code for i in report.issues}
    assert "missing_cluster_capabilities" in codes
    msg = next(i.message for i in report.issues if i.code == "missing_cluster_capabilities")
    assert "service-mesh.istio" in msg


def test_platform_too_old_blocks():
    report = check_compatibility(
        **_ok_kwargs(
            min_platform_version=Version(2, 0, 0),
            current_platform_version=Version(1, 5, 0),
        )
    )
    codes = {i.code for i in report.issues}
    assert "platform_too_old" in codes


def test_platform_min_none_means_any_version_ok():
    """Plugins without a min_platform pin pass the platform check."""
    report = check_compatibility(
        **_ok_kwargs(
            min_platform_version=None,
            current_platform_version=Version(0, 1, 0),
        )
    )
    assert report.ok is True


def test_all_blockers_reported_at_once():
    """Critical UX rule: don't make the operator fix one issue at
    a time. Surface every blocker in one report."""
    report = check_compatibility(
        from_v=Version(1, 0, 0),
        to_v=Version(2, 0, 0),
        cluster_capabilities=set(),
        required_capabilities={"k8s.workloads"},
        min_platform_version=Version(2, 0, 0),
        current_platform_version=Version(1, 5, 0),
    )
    codes = {i.code for i in report.issues}
    assert codes >= {
        "major_upgrade_requires_approval",
        "missing_cluster_capabilities",
        "platform_too_old",
    }


# ---- phased rollout -------------------------------------------------


def _cluster(cluster_id: int, **kw) -> RolloutCluster:
    return RolloutCluster(cluster_id=cluster_id, slug=f"c-{cluster_id}", **kw)


def test_rollout_canary_phase_picks_canary_clusters():
    candidates = [
        _cluster(1, is_canary=True, tenant_count=0),
        _cluster(2, is_canary=False, tenant_count=10),
    ]
    plan = plan_rollout(candidates=candidates)
    assert plan[0].phase == "canary"
    assert plan[0].cluster_ids == (1,)


def test_rollout_smallest_clusters_first():
    """10% phase ships to small clusters first to limit blast radius
    if the version is bad."""
    candidates = [
        _cluster(1, is_canary=False, tenant_count=100),
        _cluster(2, is_canary=False, tenant_count=10),
        _cluster(3, is_canary=False, tenant_count=50),
        _cluster(4, is_canary=False, tenant_count=5),
        _cluster(5, is_canary=False, tenant_count=200),
        _cluster(6, is_canary=False, tenant_count=80),
        _cluster(7, is_canary=False, tenant_count=300),
        _cluster(8, is_canary=False, tenant_count=20),
        _cluster(9, is_canary=False, tenant_count=500),
        _cluster(10, is_canary=False, tenant_count=1),
    ]
    plan = plan_rollout(candidates=candidates)
    # Phase 1 (10pct) = 1 cluster (the tiniest one, id=10 with 1 tenant)
    ten_pct_batch = next(b for b in plan if b.phase == "ring_1_10pct")
    assert ten_pct_batch.cluster_ids == (10,)


def test_rollout_phases_dont_repeat_clusters():
    """Each cluster is shipped to exactly once across all phases."""
    candidates = [_cluster(i, is_canary=(i == 1), tenant_count=i * 10) for i in range(1, 11)]
    plan = plan_rollout(candidates=candidates)
    seen: set[int] = set()
    for batch in plan:
        for cid in batch.cluster_ids:
            assert cid not in seen, f"cluster {cid} shipped twice"
            seen.add(cid)
    # Final phase covers everyone
    assert seen == {c.cluster_id for c in candidates}


def test_default_phases_match_spec_02():
    expected_names = ("canary", "ring_1_10pct", "ring_2_50pct", "ring_3_100pct")
    assert tuple(name for name, _ in DEFAULT_PHASES) == expected_names


def test_remaining_after_partial_rollout():
    candidates = [_cluster(i) for i in range(1, 6)]
    completed = [
        RolloutBatch(phase="canary", cluster_ids=(1,)),
        RolloutBatch(phase="ring_1", cluster_ids=(2, 3)),
    ]
    out = remaining_clusters(candidates=candidates, completed_batches=completed)
    assert set(out) == {4, 5}


def test_remaining_when_nothing_completed():
    candidates = [_cluster(i) for i in range(1, 4)]
    out = remaining_clusters(candidates=candidates, completed_batches=[])
    assert set(out) == {1, 2, 3}
