"""Tests for managed-service lifecycle policy (#20, spec 11 §5+§12)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from astrolift_workflows.managed_service_lifecycle import (
    DeprovisionMode,
    SnapshotPolicy,
    SnapshotRecord,
    SpecDelta,
    diff_spec,
    idempotency_key,
    plan_deprovision,
    rotation_requires_redeploy,
    snapshots_to_prune,
)


# ---- idempotency ---------------------------------------------------


def test_idempotency_key_stable_for_same_inputs():
    """Re-firing the deploy must hit the SAME Temporal workflow id
    so retries join the existing run."""
    a = idempotency_key(
        org_slug="acme", app_slug="api", env_slug="prod",
        binding_name="main_db", kind="postgres",
    )
    b = idempotency_key(
        org_slug="acme", app_slug="api", env_slug="prod",
        binding_name="main_db", kind="postgres",
    )
    assert a == b


def test_idempotency_key_changes_with_any_input():
    base = dict(
        org_slug="acme", app_slug="api", env_slug="prod",
        binding_name="main_db", kind="postgres",
    )
    base_key = idempotency_key(**base)
    for field, alt in (
        ("org_slug", "other"),
        ("app_slug", "web"),
        ("env_slug", "stage"),
        ("binding_name", "cache_db"),
        ("kind", "mysql"),
    ):
        kw = dict(base)
        kw[field] = alt
        assert idempotency_key(**kw) != base_key


def test_idempotency_key_starts_with_msvc_prefix():
    """Convention helps operators find the workflow run by
    binding."""
    out = idempotency_key(
        org_slug="acme", app_slug="api", env_slug="prod",
        binding_name="main_db", kind="postgres",
    )
    assert out.startswith("msvc-acme-api-prod-main_db-")


# ---- diff_spec -----------------------------------------------------


def test_diff_empty_when_specs_equal():
    delta = diff_spec(
        current={"size": "medium", "version": "15"},
        desired={"size": "medium", "version": "15"},
    )
    assert delta.is_empty
    assert delta.requires_driver_call is False


def test_diff_detects_changed_field():
    delta = diff_spec(
        current={"size": "medium", "version": "15"},
        desired={"size": "large", "version": "15"},
    )
    assert delta.changed_fields == ("size",)
    assert delta.requires_driver_call is True


def test_diff_detects_added_and_removed_fields():
    delta = diff_spec(
        current={"size": "medium", "old_field": "x"},
        desired={"size": "medium", "new_field": "y"},
    )
    assert set(delta.changed_fields) == {"old_field", "new_field"}


def test_diff_changed_fields_sorted():
    """Stable order makes diff output deterministic in audit logs."""
    delta = diff_spec(
        current={"a": 1, "z": 2, "m": 3},
        desired={"a": 9, "z": 9, "m": 9},
    )
    assert delta.changed_fields == ("a", "m", "z")


# ---- deprovision ---------------------------------------------------


def test_default_is_soft_delete():
    """Operator must explicitly opt in to data deletion."""
    out = plan_deprovision(delete_data=False)
    assert out.mode == DeprovisionMode.SOFT
    assert out.delete_data is False
    assert out.delete_snapshots is False


def test_double_opt_in_required_for_hard_delete():
    """delete_data=True alone is NOT enough — operator must also
    confirm. Single flag too easy to leave on by mistake."""
    out = plan_deprovision(delete_data=True, operator_confirmed=False)
    assert out.mode == DeprovisionMode.SOFT


def test_hard_delete_requires_both_flags():
    out = plan_deprovision(delete_data=True, operator_confirmed=True)
    assert out.mode == DeprovisionMode.HARD
    assert out.delete_data is True
    assert out.delete_snapshots is True


def test_operator_confirmed_alone_does_not_delete():
    """Just confirming without delete_data set still keeps data —
    confirmation is for the destructive case only."""
    out = plan_deprovision(delete_data=False, operator_confirmed=True)
    assert out.mode == DeprovisionMode.SOFT


# ---- snapshot retention --------------------------------------------


def test_snapshot_policy_rejects_zero_retention():
    """retain_count=0 means delete every snapshot — almost
    certainly a misconfig. Reject."""
    with pytest.raises(ValueError, match="retain_count"):
        SnapshotPolicy(schedule="0 2 * * *", retain_count=0)


def test_snapshot_policy_requires_schedule():
    with pytest.raises(ValueError, match="schedule"):
        SnapshotPolicy(schedule="", retain_count=7)


def _snap(snapshot_id: str, created_at_unix: int) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=snapshot_id, created_at_unix=created_at_unix,
    )


def test_no_pruning_when_under_retain():
    policy = SnapshotPolicy(schedule="0 2 * * *", retain_count=7)
    snaps = [_snap(f"s{i}", i) for i in range(3)]
    assert snapshots_to_prune(snapshots=snaps, policy=policy) == ()


def test_prune_oldest_when_over_retain():
    policy = SnapshotPolicy(schedule="0 2 * * *", retain_count=3)
    snaps = [
        _snap("s1", 100),  # oldest
        _snap("s2", 200),
        _snap("s3", 300),
        _snap("s4", 400),
        _snap("s5", 500),
    ]
    out = snapshots_to_prune(snapshots=snaps, policy=policy)
    pruned_ids = [s.snapshot_id for s in out]
    assert pruned_ids == ["s2", "s1"]  # newest-first sort, then take last N


def test_prune_empty_when_no_snapshots():
    policy = SnapshotPolicy(schedule="0 2 * * *", retain_count=7)
    assert snapshots_to_prune(snapshots=[], policy=policy) == ()


# ---- credential rotation redeploy ----------------------------------


def test_password_rotation_does_not_require_redeploy():
    """Most DB clients pick up new passwords on next connect."""
    assert rotation_requires_redeploy(kind="postgres", auth_mode="password") is False
    assert rotation_requires_redeploy(kind="redis", auth_mode="password") is False


def test_mtls_rotation_requires_redeploy():
    """mTLS certs are read at TLS-init time; new cert needs pod
    restart to take effect."""
    assert rotation_requires_redeploy(kind="postgres", auth_mode="mtls") is True
    assert rotation_requires_redeploy(kind="anything", auth_mode="mtls") is True


def test_iam_rotation_does_not_require_redeploy():
    """IAM-mode credentials are re-fetched on each request via
    workload identity — no pod restart needed."""
    assert rotation_requires_redeploy(kind="postgres", auth_mode="iam") is False
