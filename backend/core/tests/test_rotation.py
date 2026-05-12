"""Tests for rotation policy logic (#150, spec 12 §6.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.secrets.rotation import (
    DEFAULT_REMINDER_THRESHOLDS_DAYS,
    DEFAULT_ROTATION_PERIOD_DAYS,
    RotationPolicy,
    evaluate,
    is_due,
    next_due_at,
    reminders_to_fire,
)

UTC = UTC


def _at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ---- construction guards ----------------------------------------------


def test_policy_rejects_naive_datetime():
    with pytest.raises(ValueError):
        RotationPolicy(last_rotated_at=datetime(2026, 1, 1))


def test_policy_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=0)


def test_policy_rejects_nonpositive_threshold():
    with pytest.raises(ValueError):
        RotationPolicy(
            last_rotated_at=_at(2026, 1, 1),
            reminder_thresholds_days=(14, 0),
        )


# ---- next_due / is_due ------------------------------------------------


def test_default_period_is_90_days():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1))
    assert policy.period_days == DEFAULT_ROTATION_PERIOD_DAYS
    assert next_due_at(policy) == _at(2026, 1, 1) + timedelta(days=90)


def test_custom_period_overrides_default():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=30)
    assert next_due_at(policy) == _at(2026, 1, 31)


def test_is_due_false_before_deadline():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=30)
    assert is_due(policy, now=_at(2026, 1, 30)) is False


def test_is_due_true_at_or_after_deadline():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=30)
    # The exact moment counts as due — workers polling every hour
    # should rotate as soon as they cross, not 'after'.
    assert is_due(policy, now=_at(2026, 1, 31)) is True
    assert is_due(policy, now=_at(2026, 2, 5)) is True


def test_is_due_rejects_naive_now():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1))
    with pytest.raises(ValueError):
        is_due(policy, now=datetime(2026, 5, 1))  # naive


# ---- reminder crossings ----------------------------------------------


def test_first_evaluation_fires_active_reminders():
    """``last_check_at=None`` (first time we look at this policy)
    treats every currently-crossed threshold as just-crossed. Without
    this, a freshly seeded DB would silently skip one reminder cycle."""
    policy = RotationPolicy(
        last_rotated_at=_at(2026, 1, 1),
        period_days=30,
        reminder_thresholds_days=(14, 7, 1),
    )
    # 5 days before due — within both 14d and 7d windows but not 1d
    fired = reminders_to_fire(policy, now=_at(2026, 1, 26), last_check_at=None)
    assert fired == (14, 7)


def test_crossing_fires_threshold_exactly_once():
    """Polling worker calls ``reminders_to_fire`` repeatedly. We
    must fire each threshold exactly once — at the moment of crossing."""
    policy = RotationPolicy(
        last_rotated_at=_at(2026, 1, 1),
        period_days=30,
        reminder_thresholds_days=(14, 7, 1),
    )
    # Worker poll #1: well before any threshold (35 days before due)
    fired1 = reminders_to_fire(policy, now=_at(2025, 12, 27), last_check_at=None)
    assert fired1 == ()  # nothing crossed yet

    # Worker poll #2: just stepped into 14d window
    fired2 = reminders_to_fire(
        policy,
        now=_at(2026, 1, 17, hour=1),
        last_check_at=_at(2026, 1, 16, hour=23),
    )
    assert fired2 == (14,)

    # Worker poll #3: still in 14d window but no NEW crossing
    fired3 = reminders_to_fire(
        policy,
        now=_at(2026, 1, 18),
        last_check_at=_at(2026, 1, 17, hour=1),
    )
    assert fired3 == ()

    # Worker poll #4: stepped into 7d window
    fired4 = reminders_to_fire(
        policy,
        now=_at(2026, 1, 24, hour=1),
        last_check_at=_at(2026, 1, 23, hour=23),
    )
    assert fired4 == (7,)


def test_skipped_window_still_fires_once():
    """If the worker is paused for a week and wakes up past two
    threshold boundaries, both fire on the next evaluation. Better
    to notify late than to silently swallow a missed reminder."""
    policy = RotationPolicy(
        last_rotated_at=_at(2026, 1, 1),
        period_days=30,
        reminder_thresholds_days=(14, 7, 1),
    )
    # Last check at day 15 (before 14d window). Now at day 28 (within
    # 1d window). So we crossed 14, 7, AND 1 since last check.
    fired = reminders_to_fire(
        policy,
        now=_at(2026, 1, 30, hour=12),
        last_check_at=_at(2026, 1, 15),
    )
    assert fired == (14, 7, 1)


def test_reminders_descending_order():
    """Important for notification copy: '14 days' before '7 days'
    so the user reads them in the order they'd naturally fire."""
    policy = RotationPolicy(
        last_rotated_at=_at(2026, 1, 1),
        period_days=30,
        reminder_thresholds_days=(7, 1, 14),  # arbitrary order
    )
    fired = reminders_to_fire(policy, now=_at(2026, 1, 30), last_check_at=None)
    assert fired == (14, 7, 1)


def test_reminders_after_due_still_fire_remaining_thresholds():
    """If the secret is overdue, the 1d threshold should still be
    reported as crossed (in case nobody saw earlier reminders)."""
    policy = RotationPolicy(
        last_rotated_at=_at(2026, 1, 1),
        period_days=30,
        reminder_thresholds_days=(14, 7, 1),
    )
    fired = reminders_to_fire(policy, now=_at(2026, 2, 1), last_check_at=None)
    assert fired == (14, 7, 1)


# ---- evaluate (one-shot) ---------------------------------------------


def test_evaluate_packs_status_for_ui():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=30)
    status = evaluate(policy, now=_at(2026, 1, 25))
    assert status.is_due is False
    assert status.next_due_at == _at(2026, 1, 31)
    assert status.days_until_due == 6


def test_evaluate_negative_days_when_overdue():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1), period_days=30)
    status = evaluate(policy, now=_at(2026, 2, 5))
    assert status.is_due is True
    assert status.days_until_due == -5


def test_evaluate_default_thresholds_match_spec():
    policy = RotationPolicy(last_rotated_at=_at(2026, 1, 1))
    assert policy.reminder_thresholds_days == DEFAULT_REMINDER_THRESHOLDS_DAYS
    assert DEFAULT_REMINDER_THRESHOLDS_DAYS == (14, 7, 1)
