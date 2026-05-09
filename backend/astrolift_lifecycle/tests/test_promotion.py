"""Tests for promotion + lineage policy (#63, spec 14 §14)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.promotion import (
    MAX_LINEAGE_HOPS,
    DeploymentRef,
    LineageCycle,
    PromotionError,
    PromotionTarget,
    env_for_branch,
    plan_promotion,
    root_of_lineage,
    validate_promotion,
    walk_lineage,
)


GOOD_DIGEST = "sha256:" + "a" * 64


def _ref(deployment_id: int, **kw) -> DeploymentRef:
    base = dict(
        app_id=1, environment_id=1, image_digest=GOOD_DIGEST,
        promoted_from_id=None,
    )
    base.update(kw)
    return DeploymentRef(deployment_id=deployment_id, **base)


def _target(env_id: int, **kw) -> PromotionTarget:
    base = dict(app_id=1, requires_approval=False)
    base.update(kw)
    return PromotionTarget(environment_id=env_id, **base)


# ---- validate_promotion ---------------------------------------------


def test_promotion_rejected_across_apps():
    with pytest.raises(PromotionError, match="source app .* != target app"):
        validate_promotion(
            source=_ref(1, app_id=1), target=_target(2, app_id=99),
        )


def test_promotion_rejected_to_same_environment():
    """No-op promotion is almost certainly a misclick — surface it."""
    with pytest.raises(PromotionError, match="source environment"):
        validate_promotion(
            source=_ref(1, environment_id=5), target=_target(5),
        )


def test_promotion_rejected_when_source_unpinned():
    """Spec 12 §9 + #26 — promotion guarantees byte-identical bytes,
    which requires the source to be pinned. Reject otherwise."""
    with pytest.raises(PromotionError, match="no image digest"):
        validate_promotion(
            source=_ref(1, image_digest=""), target=_target(2),
        )


def test_promotion_passes_when_valid():
    validate_promotion(source=_ref(1), target=_target(2))


# ---- plan_promotion -------------------------------------------------


def test_plan_carries_digest_and_lineage():
    plan = plan_promotion(
        source=_ref(7, image_digest=GOOD_DIGEST),
        target=_target(2, requires_approval=True),
    )
    assert plan.image_digest == GOOD_DIGEST
    assert plan.promoted_from_id == 7
    assert plan.target_environment_id == 2
    assert plan.needs_approval is True


def test_plan_emits_no_approval_when_target_doesnt_require_one():
    plan = plan_promotion(source=_ref(1), target=_target(2, requires_approval=False))
    assert plan.needs_approval is False


# ---- branch-per-env -------------------------------------------------


def test_branch_to_env_mapping():
    mapping = {"main": 1, "staging": 2}
    assert env_for_branch(branch_name="main", branch_to_env=mapping) == 1
    assert env_for_branch(branch_name="staging", branch_to_env=mapping) == 2


def test_branch_with_no_mapping_returns_none():
    """Caller (push webhook) treats None as 'no auto-deploy for
    this branch' — consistent with the spec's opt-in routing."""
    mapping = {"main": 1}
    assert env_for_branch(branch_name="feature-x", branch_to_env=mapping) is None


def test_branch_match_is_case_sensitive_by_default():
    """``main`` and ``Main`` are different branches in git; the
    routing treats them the same way."""
    mapping = {"main": 1}
    assert env_for_branch(branch_name="Main", branch_to_env=mapping) is None


# ---- walk_lineage ---------------------------------------------------


def test_walk_lineage_returns_full_chain():
    """build (id=1) -> staging (id=2 promoted_from=1) -> prod (id=3 promoted_from=2)."""
    a = _ref(1)
    b = _ref(2, promoted_from_id=1, environment_id=2)
    c = _ref(3, promoted_from_id=2, environment_id=3)
    chain = walk_lineage(leaf=c, by_id={1: a, 2: b, 3: c})
    assert tuple(d.deployment_id for d in chain) == (3, 2, 1)


def test_walk_terminates_at_missing_parent():
    """Old deployment pruned by retention — chain stops there
    rather than failing."""
    leaf = _ref(3, promoted_from_id=99)
    # parent id 99 not in by_id
    chain = walk_lineage(leaf=leaf, by_id={3: leaf})
    assert tuple(d.deployment_id for d in chain) == (3,)


def test_walk_terminates_at_root():
    """Deployment with promoted_from_id=None is the root."""
    leaf = _ref(1, promoted_from_id=None)
    chain = walk_lineage(leaf=leaf, by_id={1: leaf})
    assert tuple(d.deployment_id for d in chain) == (1,)


def test_walk_detects_cycle():
    """Corrupted chain: 1 -> 2 -> 1. Surface loudly."""
    a = _ref(1, promoted_from_id=2)
    b = _ref(2, promoted_from_id=1)
    with pytest.raises(LineageCycle):
        walk_lineage(leaf=a, by_id={1: a, 2: b})


def test_walk_detects_self_cycle():
    a = _ref(1, promoted_from_id=1)
    with pytest.raises(LineageCycle):
        walk_lineage(leaf=a, by_id={1: a})


def test_walk_caps_at_max_hops():
    """Synthetic deeply-nested chain to confirm we don't loop
    forever on pathological data."""
    refs = {}
    for i in range(MAX_LINEAGE_HOPS + 5):
        refs[i] = _ref(
            i, promoted_from_id=(i - 1) if i > 0 else None,
            environment_id=i + 1,
        )
    leaf = refs[MAX_LINEAGE_HOPS + 4]
    with pytest.raises(LineageCycle, match="exceeded"):
        walk_lineage(leaf=leaf, by_id=refs)


def test_root_of_lineage_returns_chain_head():
    a = _ref(1)
    b = _ref(2, promoted_from_id=1)
    c = _ref(3, promoted_from_id=2)
    assert root_of_lineage(leaf=c, by_id={1: a, 2: b, 3: c}).deployment_id == 1
