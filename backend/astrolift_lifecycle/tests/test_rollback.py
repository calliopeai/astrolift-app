"""Tests for rollback target resolution + plan (#68, spec 14 §15)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.rollback import (
    DeploymentSnapshot,
    DeploymentState,
    RollbackError,
    plan_rollback,
    resolve_target,
)

GOOD_DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SNAPSHOT = "sha256:" + "c" * 64


def _snap(deployment_id: int, **kw) -> DeploymentSnapshot:
    base = dict(
        app_id=1,
        environment_id=1,
        state=DeploymentState.RUNNING,
        image_digest=GOOD_DIGEST,
        config_snapshot=SNAPSHOT,
        is_current=False,
        created_at_unix=1_700_000_000 + deployment_id,
    )
    base.update(kw)
    return DeploymentSnapshot(deployment_id=deployment_id, **base)


# ---- explicit target ------------------------------------------------


def test_explicit_target_wins():
    candidates = [
        _snap(1, state=DeploymentState.RUNNING, is_current=False),
        _snap(2, state=DeploymentState.RUNNING, is_current=True),
    ]
    out = resolve_target(
        app_id=1, environment_id=1,
        candidates=candidates, explicit_target_id=1,
    )
    assert out.deployment_id == 1


def test_explicit_target_not_in_scope_raises():
    """A target id that doesn't match the app/env or doesn't exist
    in the candidate list raises rather than silently picking an
    arbitrary deployment."""
    candidates = [_snap(1)]
    with pytest.raises(RollbackError, match="not a rollback candidate"):
        resolve_target(
            app_id=1, environment_id=1,
            candidates=candidates, explicit_target_id=99,
        )


def test_explicit_target_without_digest_raises():
    candidates = [_snap(1, image_digest="")]
    with pytest.raises(RollbackError, match="no image digest"):
        resolve_target(
            app_id=1, environment_id=1,
            candidates=candidates, explicit_target_id=1,
        )


# ---- default target -------------------------------------------------


def test_default_picks_most_recent_running_not_current():
    candidates = [
        _snap(1, state=DeploymentState.RUNNING, created_at_unix=100),
        _snap(2, state=DeploymentState.RUNNING, created_at_unix=200),
        _snap(3, state=DeploymentState.RUNNING, is_current=True, created_at_unix=300),
    ]
    out = resolve_target(app_id=1, environment_id=1, candidates=candidates)
    assert out.deployment_id == 2  # most recent running, not current


def test_default_skips_failed():
    """Failed deployments aren't valid rollback targets — rolling
    back to a known-broken state defeats the point."""
    candidates = [
        _snap(1, state=DeploymentState.RUNNING),
        _snap(2, state=DeploymentState.FAILED),
        _snap(3, state=DeploymentState.RUNNING, is_current=True),
    ]
    out = resolve_target(app_id=1, environment_id=1, candidates=candidates)
    assert out.deployment_id == 1


def test_default_skips_pending():
    """Pending deployments haven't been observed running yet."""
    candidates = [
        _snap(1, state=DeploymentState.RUNNING),
        _snap(2, state=DeploymentState.PENDING),
        _snap(3, state=DeploymentState.RUNNING, is_current=True),
    ]
    out = resolve_target(app_id=1, environment_id=1, candidates=candidates)
    assert out.deployment_id == 1


def test_default_skips_unpinned():
    """Spec 12 §9 + #26 — rollback requires a digest."""
    candidates = [
        _snap(1, image_digest=""),
        _snap(2, state=DeploymentState.RUNNING, is_current=True),
    ]
    with pytest.raises(RollbackError, match="no rollback target"):
        resolve_target(app_id=1, environment_id=1, candidates=candidates)


def test_no_candidates_raises():
    with pytest.raises(RollbackError, match="no rollback target"):
        resolve_target(app_id=1, environment_id=1, candidates=[])


def test_only_current_deployment_raises():
    """Single deployment that's currently live → can't roll back
    to itself."""
    candidates = [_snap(1, is_current=True)]
    with pytest.raises(RollbackError, match="no rollback target"):
        resolve_target(app_id=1, environment_id=1, candidates=candidates)


def test_filters_by_app_and_env():
    """Different apps' deployments can't be rollback targets for
    each other, even in the same install."""
    candidates = [
        _snap(1, app_id=99, state=DeploymentState.RUNNING),
        _snap(2, environment_id=99, state=DeploymentState.RUNNING),
    ]
    with pytest.raises(RollbackError):
        resolve_target(app_id=1, environment_id=1, candidates=candidates)


# ---- plan_rollback -------------------------------------------------


def test_plan_carries_target_digest_and_lineage():
    current = _snap(10, is_current=True, image_digest=OTHER_DIGEST)
    target = _snap(5, image_digest=GOOD_DIGEST)
    plan = plan_rollback(current=current, target=target)
    assert plan.image_digest == GOOD_DIGEST
    assert plan.config_snapshot == SNAPSHOT
    assert plan.rolled_back_from_id == 10
    assert plan.app_id == 1
    assert plan.environment_id == 1


def test_plan_rejects_cross_app():
    with pytest.raises(RollbackError, match="app"):
        plan_rollback(
            current=_snap(1, app_id=1, is_current=True),
            target=_snap(2, app_id=99),
        )


def test_plan_rejects_cross_env():
    with pytest.raises(RollbackError, match="env"):
        plan_rollback(
            current=_snap(1, environment_id=1, is_current=True),
            target=_snap(2, environment_id=99),
        )


def test_plan_rejects_target_without_digest():
    with pytest.raises(RollbackError, match="no image digest"):
        plan_rollback(
            current=_snap(1, is_current=True),
            target=_snap(2, image_digest=""),
        )
