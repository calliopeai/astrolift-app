"""Tests for periodic maintenance workflow policy (#144, spec 06 §5 + 03 §11)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from astrolift_workflows.periodic_maintenance import (
    MIN_REHEAL_INTERVAL_HOURS,
    RehealOutcome,
    classify_test_delivery,
    plan_cost_snapshot,
    plan_prune,
    reheal_due,
    safe_to_delete,
)

UTC = UTC


def _now(*, off_hours: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(hours=off_hours)


# ---- reheal_due -----------------------------------------------------


def test_reheal_not_due_immediately_after_disable():
    """Just-disabled subscription shouldn't be retested instantly."""
    assert (
        reheal_due(
            disabled_at=_now(off_hours=-1),
            last_reheal_attempt_at=None,
            now=_now(),
        )
        is False
    )


def test_reheal_due_after_min_interval():
    assert (
        reheal_due(
            disabled_at=_now(off_hours=-MIN_REHEAL_INTERVAL_HOURS - 1),
            last_reheal_attempt_at=None,
            now=_now(),
        )
        is True
    )


def test_reheal_not_due_too_soon_after_last_attempt():
    """Backoff between consecutive reheal attempts."""
    assert (
        reheal_due(
            disabled_at=_now(off_hours=-72),
            last_reheal_attempt_at=_now(off_hours=-2),
            now=_now(),
        )
        is False
    )


def test_reheal_due_after_interval_from_last_attempt():
    assert (
        reheal_due(
            disabled_at=_now(off_hours=-72),
            last_reheal_attempt_at=_now(off_hours=-MIN_REHEAL_INTERVAL_HOURS - 1),
            now=_now(),
        )
        is True
    )


def test_reheal_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        reheal_due(
            disabled_at=datetime(2026, 5, 1),
            last_reheal_attempt_at=None,
            now=_now(),
        )
    with pytest.raises(ValueError):
        reheal_due(
            disabled_at=_now(off_hours=-72),
            last_reheal_attempt_at=datetime(2026, 5, 1),
            now=_now(),
        )


def test_reheal_interval_locked():
    assert MIN_REHEAL_INTERVAL_HOURS == 6


# ---- classify_test_delivery ----------------------------------------


@pytest.mark.parametrize("code", [200, 201, 204, 299])
def test_2xx_reenables_and_resets_counter(code):
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=code,
        prior_consecutive_failed_reheals=4,
    )
    assert out.outcome == RehealOutcome.REENABLED
    assert out.consecutive_failed_reheals == 0
    assert out.notify_operator is False


@pytest.mark.parametrize("code", [400, 410, 500, 503])
def test_non_2xx_increments_counter(code):
    """410 and 4xx and 5xx all keep the subscription disabled
    (auto-disable already happened upstream — this is just
    re-test of recovery)."""
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=code,
        prior_consecutive_failed_reheals=2,
    )
    assert out.outcome == RehealOutcome.STILL_FAILING
    assert out.consecutive_failed_reheals == 3


def test_connection_error_treated_as_failure():
    """No HTTP response at all — still counts as failed reheal."""
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=None,
        prior_consecutive_failed_reheals=0,
    )
    assert out.outcome == RehealOutcome.STILL_FAILING


def test_notify_operator_at_threshold_only():
    """Notify on Nth failure exactly — not before, not on N+1."""
    # Below threshold
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=500,
        prior_consecutive_failed_reheals=3,
        notify_threshold=5,
    )
    assert out.notify_operator is False  # now 4

    # At threshold
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=500,
        prior_consecutive_failed_reheals=4,
        notify_threshold=5,
    )
    assert out.notify_operator is True  # now 5

    # Above threshold (already notified, don't spam)
    out = classify_test_delivery(
        subscription_id=1,
        test_status_code=500,
        prior_consecutive_failed_reheals=10,
        notify_threshold=5,
    )
    assert out.notify_operator is False  # now 11; threshold already passed


# ---- plan_cost_snapshot --------------------------------------------


def test_cost_snapshot_plan_dedupes_orgs():
    plan = plan_cost_snapshot(
        snapshot_date=date(2026, 5, 9),
        active_org_ids=(2, 1, 2, 3, 1),
    )
    assert plan.organizations_to_capture == (1, 2, 3)


def test_cost_snapshot_plan_empty_when_no_orgs():
    plan = plan_cost_snapshot(
        snapshot_date=date(2026, 5, 9),
        active_org_ids=(),
    )
    assert plan.organizations_to_capture == ()


def test_cost_snapshot_plan_carries_date():
    plan = plan_cost_snapshot(
        snapshot_date=date(2026, 5, 9),
        active_org_ids=(1,),
    )
    assert plan.snapshot_date == date(2026, 5, 9)


# ---- plan_prune ----------------------------------------------------


def test_prune_cutoff_subtracts_retention():
    plan = plan_prune(
        org_id=1,
        retention_days=90,
        archive_destination="s3://acme-audit/2026/",
        now=_now(),
    )
    assert plan.cutoff == _now() - timedelta(days=90)
    assert plan.archive_destination.startswith("s3://")


def test_prune_rejects_zero_retention():
    """Retention of 0 days would delete every audit row in
    history — almost certainly misconfig."""
    with pytest.raises(ValueError, match="retention_days"):
        plan_prune(
            org_id=1,
            retention_days=0,
            archive_destination="s3://x",
            now=_now(),
        )


def test_prune_rejects_naive_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        plan_prune(
            org_id=1,
            retention_days=30,
            archive_destination="s3://x",
            now=datetime(2026, 5, 9),
        )


def test_safe_to_delete_when_archive_configured():
    plan = plan_prune(
        org_id=1,
        retention_days=90,
        archive_destination="s3://acme-audit/",
        now=_now(),
    )
    assert safe_to_delete(plan) is True


def test_unsafe_to_delete_without_archive():
    """Without archive destination, DELETE would silently lose
    audit data. Workflow refuses and surfaces 'configure archive
    first' to operators."""
    plan = plan_prune(
        org_id=1,
        retention_days=90,
        archive_destination="",
        now=_now(),
    )
    assert safe_to_delete(plan) is False


def test_unsafe_to_delete_with_whitespace_destination():
    """Defensive: whitespace-only archive destination is the
    same as empty."""
    plan = plan_prune(
        org_id=1,
        retention_days=90,
        archive_destination="   ",
        now=_now(),
    )
    assert safe_to_delete(plan) is False
