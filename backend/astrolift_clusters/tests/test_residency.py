"""Tests for data residency policy (#152, spec 12 §14)."""

from __future__ import annotations

import pytest

from astrolift_clusters.residency import (
    NO_CONSTRAINT,
    ResidencyPolicy,
    ResidencyViolation,
    check_binding,
    check_target,
    is_constrained,
)

# ---- defaults / shape ------------------------------------------------


def test_unconfigured_policy_is_unconstrained():
    """Empty ``allowed_regions`` means 'no policy' — deploy anywhere."""
    policy = ResidencyPolicy(org_slug="acme")
    assert policy.allowed_regions == NO_CONSTRAINT
    assert is_constrained(policy) is False


def test_constrained_when_regions_set():
    policy = ResidencyPolicy(
        org_slug="acme", allowed_regions=("us-east-1",)
    )
    assert is_constrained(policy) is True


def test_constrained_when_cluster_ids_set():
    policy = ResidencyPolicy(
        org_slug="acme", allowed_cluster_ids=(1,)
    )
    assert is_constrained(policy) is True


# ---- check_target ----------------------------------------------------


def test_unconstrained_allows_any_region():
    policy = ResidencyPolicy(org_slug="acme")
    check_target(policy, cluster_id=1, cluster_region="anywhere-1")  # no raise


def test_allowed_region_passes():
    policy = ResidencyPolicy(
        org_slug="acme", allowed_regions=("us-east-1", "us-west-2")
    )
    check_target(policy, cluster_id=1, cluster_region="us-east-1")
    check_target(policy, cluster_id=2, cluster_region="us-west-2")


def test_disallowed_region_raises():
    policy = ResidencyPolicy(
        org_slug="acme", allowed_regions=("us-east-1",)
    )
    with pytest.raises(ResidencyViolation) as exc:
        check_target(policy, cluster_id=1, cluster_region="eu-west-1")
    assert exc.value.org_slug == "acme"
    assert exc.value.target_region == "eu-west-1"
    assert "eu-west-1" in str(exc.value)


def test_cluster_id_pin_overrides_region_match():
    """Region is allowed but cluster ID isn't — still rejects.
    The cluster pin is a tighter constraint within an allowed region."""
    policy = ResidencyPolicy(
        org_slug="acme",
        allowed_regions=("us-east-1",),
        allowed_cluster_ids=(7, 8),
    )
    # region matches, cluster matches
    check_target(policy, cluster_id=7, cluster_region="us-east-1")
    # region matches, cluster doesn't
    with pytest.raises(ResidencyViolation, match="not in the allowed cluster"):
        check_target(policy, cluster_id=99, cluster_region="us-east-1")


def test_cluster_pin_alone_still_rejects_wrong_cluster():
    """A policy that only sets allowed_cluster_ids (no regions) still
    enforces the cluster pin."""
    policy = ResidencyPolicy(org_slug="acme", allowed_cluster_ids=(7,))
    with pytest.raises(ResidencyViolation):
        check_target(policy, cluster_id=99, cluster_region="us-east-1")


# ---- cross-region binding -------------------------------------------


def test_cross_region_binding_rejected_for_unconstrained_org():
    """Even orgs with no policy get the cross-region check —
    silent cross-region postgres bindings are an outage waiting
    to happen (latency + egress + GDPR all at once)."""
    policy = ResidencyPolicy(org_slug="acme")
    with pytest.raises(ResidencyViolation, match="cross-region binding"):
        check_binding(
            policy,
            workload_region="us-east-1",
            bound_resource_region="eu-west-1",
            bound_resource_kind="postgres",
        )


def test_same_region_binding_passes():
    policy = ResidencyPolicy(org_slug="acme")
    check_binding(
        policy,
        workload_region="us-east-1",
        bound_resource_region="us-east-1",
        bound_resource_kind="postgres",
    )


def test_global_kind_binding_skips_check():
    """CDN bindings are inherently global — don't reject them as
    cross-region. Same for global KMS."""
    policy = ResidencyPolicy(org_slug="acme")
    for kind in ("cdn", "kms_global"):
        check_binding(
            policy,
            workload_region="us-east-1",
            bound_resource_region="global",
            bound_resource_kind=kind,
        )


def test_cross_region_binding_message_names_kind():
    policy = ResidencyPolicy(org_slug="acme")
    with pytest.raises(ResidencyViolation) as exc:
        check_binding(
            policy,
            workload_region="us-east-1",
            bound_resource_region="eu-west-1",
            bound_resource_kind="postgres",
        )
    assert "postgres" in str(exc.value)
    assert "us-east-1" in str(exc.value)
    assert "eu-west-1" in str(exc.value)


def test_constrained_org_cross_region_still_rejects():
    """A constrained org with both regions allowed STILL can't
    bind across them — residency is per-region, not just 'is the
    region allowed for the org'."""
    policy = ResidencyPolicy(
        org_slug="acme", allowed_regions=("us-east-1", "eu-west-1")
    )
    with pytest.raises(ResidencyViolation, match="cross-region"):
        check_binding(
            policy,
            workload_region="us-east-1",
            bound_resource_region="eu-west-1",
            bound_resource_kind="postgres",
        )
