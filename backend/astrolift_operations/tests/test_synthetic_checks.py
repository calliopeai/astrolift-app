"""Tests for synthetic-check policy + aggregation (#23, spec 08 §11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from astrolift_operations.synthetic_checks import (
    DEFAULT_INTERVALS_SECONDS,
    TLS_WARN_THRESHOLDS_DAYS,
    CheckKind,
    CheckSchedule,
    HealthBadge,
    ProbeOutcome,
    health_badge,
    is_expired,
    tls_thresholds_to_emit,
)


UTC = timezone.utc


def _at(off_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off_seconds)


# ---- defaults ------------------------------------------------------


def test_default_intervals_match_spec_08():
    assert DEFAULT_INTERVALS_SECONDS[CheckKind.HTTP] == 30
    assert DEFAULT_INTERVALS_SECONDS[CheckKind.TLS_EXPIRY] == 24 * 3600
    assert DEFAULT_INTERVALS_SECONDS[CheckKind.DNS] == 5 * 60


def test_tls_thresholds_match_spec_08():
    assert TLS_WARN_THRESHOLDS_DAYS == (30, 7)


# ---- schedule ------------------------------------------------------


def test_schedule_falls_back_to_default():
    sched = CheckSchedule()
    assert sched.for_kind(CheckKind.HTTP) == 30
    assert sched.for_kind(CheckKind.DNS) == 300


def test_schedule_per_kind_override():
    sched = CheckSchedule(http_interval_seconds=15, dns_interval_seconds=60)
    assert sched.for_kind(CheckKind.HTTP) == 15
    assert sched.for_kind(CheckKind.DNS) == 60
    # TLS unchanged
    assert sched.for_kind(CheckKind.TLS_EXPIRY) == 24 * 3600


def test_schedule_zero_or_negative_falls_back_to_default():
    """Defensive: 0 or negative override would mean 'fire immediately
    every loop iteration' — interpret as 'no override' instead."""
    sched = CheckSchedule(http_interval_seconds=0)
    assert sched.for_kind(CheckKind.HTTP) == 30


# ---- ProbeOutcome --------------------------------------------------


def test_probe_outcome_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=datetime(2026, 5, 9))


# ---- health badge --------------------------------------------------


def test_no_recent_outcomes_is_unknown():
    """Better to show 'unknown' than stale data."""
    out = health_badge(outcomes=[], now=_at())
    assert out == HealthBadge.UNKNOWN


def test_all_passing_is_healthy():
    outcomes = [
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-30)),
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-60)),
    ]
    assert health_badge(outcomes=outcomes, now=_at()) == HealthBadge.HEALTHY


def test_minority_failures_degraded():
    outcomes = [
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-10)),
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-20)),
        ProbeOutcome(kind=CheckKind.HTTP, ok=False, ts=_at(off_seconds=-30)),
    ]
    assert health_badge(outcomes=outcomes, now=_at()) == HealthBadge.DEGRADED


def test_majority_failures_down():
    """50% counts as DOWN — when half the regions can't reach the
    app, treating it as 'merely degraded' would be misleading."""
    outcomes = [
        ProbeOutcome(kind=CheckKind.HTTP, ok=False, ts=_at(off_seconds=-10)),
        ProbeOutcome(kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-20)),
    ]
    assert health_badge(outcomes=outcomes, now=_at()) == HealthBadge.DOWN


def test_stale_outcomes_excluded_from_badge():
    """Outcomes older than stale_after_seconds don't count."""
    stale = ProbeOutcome(
        kind=CheckKind.HTTP, ok=True, ts=_at(off_seconds=-3600)
    )
    fresh = ProbeOutcome(
        kind=CheckKind.HTTP, ok=False, ts=_at(off_seconds=-30)
    )
    out = health_badge(
        outcomes=[stale, fresh], now=_at(), stale_after_seconds=300
    )
    # Only `fresh` counted → 1/1 failure → DOWN
    assert out == HealthBadge.DOWN


def test_health_badge_rejects_naive_now():
    with pytest.raises(ValueError):
        health_badge(outcomes=[], now=datetime(2026, 5, 9))


# ---- TLS thresholds -------------------------------------------------


def _expires_in(days: int) -> datetime:
    return _at() + timedelta(days=days)


def test_tls_first_evaluation_fires_active_thresholds():
    # 5 days to expiry, no prior check → 30 and 7 both crossed
    fired = tls_thresholds_to_emit(
        not_after=_expires_in(5), now=_at(), last_check_at=None
    )
    assert fired == (30, 7)


def test_tls_threshold_fires_only_at_crossing():
    """Daily TLS scan loop must NOT spam the same email every day.
    Each threshold fires exactly once at the moment of crossing."""
    not_after = _expires_in(0)  # 'now' is exactly expiry
    # Last check 35 days before expiry; now 29 days before
    fired = tls_thresholds_to_emit(
        not_after=not_after,
        now=_at() - timedelta(days=29),
        last_check_at=_at() - timedelta(days=35),
    )
    assert fired == (30,)

    # Next day: no new crossing
    fired = tls_thresholds_to_emit(
        not_after=not_after,
        now=_at() - timedelta(days=28),
        last_check_at=_at() - timedelta(days=29),
    )
    assert fired == ()


def test_tls_skipped_window_fires_all_pending():
    """If the daily worker was paused for a week, both thresholds
    fire on the next run rather than silently skipped."""
    not_after = _expires_in(0)
    fired = tls_thresholds_to_emit(
        not_after=not_after,
        now=_at() - timedelta(days=5),
        last_check_at=_at() - timedelta(days=35),
    )
    assert fired == (30, 7)


# ---- expiry --------------------------------------------------------


def test_is_expired_true_at_or_after_not_after():
    not_after = _at()
    assert is_expired(not_after=not_after, now=_at()) is True
    assert is_expired(not_after=not_after, now=_at(off_seconds=60)) is True


def test_is_expired_false_before_not_after():
    not_after = _at(off_seconds=60)
    assert is_expired(not_after=not_after, now=_at()) is False


def test_is_expired_rejects_naive():
    with pytest.raises(ValueError):
        is_expired(not_after=datetime(2026, 5, 9), now=_at())
    with pytest.raises(ValueError):
        is_expired(not_after=_at(), now=datetime(2026, 5, 9))
