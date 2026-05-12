"""Tests for TLS certificate expiry monitoring (#155, spec 13 §5.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astrolift_operations.cert_expiry import (
    ESCALATION_THRESHOLD_DAYS,
    EXPIRY_THRESHOLDS_DAYS,
    CertHealth,
    CertSnapshot,
    evaluate,
)

UTC = UTC


def _expires_in(days: int, **kw) -> CertSnapshot:
    base = {"not_after": datetime(2026, 6, 1, tzinfo=UTC) + timedelta(days=days)}
    base.update(kw)
    return CertSnapshot(**base)


def _now(*, days_before_expiry: int) -> datetime:
    """A 'now' that's ``days_before_expiry`` days before 2026-06-01,
    so each test reads naturally as 'with 5 days to go, …'."""
    return datetime(2026, 6, 1, tzinfo=UTC) - timedelta(days=days_before_expiry)


# ---- guards ----------------------------------------------------------


def test_snapshot_rejects_naive_not_after():
    with pytest.raises(ValueError):
        CertSnapshot(not_after=datetime(2026, 6, 1))


def test_evaluate_rejects_naive_now():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        evaluate(snap, now=datetime(2026, 5, 1))


# ---- health buckets --------------------------------------------------


def test_healthy_when_far_from_expiry():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    out = evaluate(snap, now=_now(days_before_expiry=60))
    assert out.health == CertHealth.HEALTHY
    assert out.days_until_expiry == 60


def test_warning_within_30_days():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    out = evaluate(snap, now=_now(days_before_expiry=20))
    assert out.health == CertHealth.WARNING


def test_urgent_within_14_days():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    out = evaluate(snap, now=_now(days_before_expiry=10))
    assert out.health == CertHealth.URGENT


def test_critical_within_7_days():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    out = evaluate(snap, now=_now(days_before_expiry=3))
    assert out.health == CertHealth.CRITICAL


def test_expired_when_past_not_after():
    snap = CertSnapshot(not_after=datetime(2026, 6, 1, tzinfo=UTC))
    out = evaluate(snap, now=_now(days_before_expiry=-2))
    assert out.health == CertHealth.EXPIRED
    assert out.days_until_expiry == -2


def test_renewal_failed_takes_precedence_over_warning():
    """A live cert whose most recent renewal attempt failed should
    surface as RENEWAL_FAILED so operators see the issue before the
    cert actually expires."""
    snap = CertSnapshot(
        not_after=datetime(2026, 6, 1, tzinfo=UTC),
        last_renewal_failed=True,
        renewal_attempts=2,
    )
    out = evaluate(snap, now=_now(days_before_expiry=20))
    assert out.health == CertHealth.RENEWAL_FAILED


def test_expired_beats_renewal_failed():
    """If the cert is already expired, EXPIRED wins over
    RENEWAL_FAILED — the user-visible outage is the priority signal."""
    snap = CertSnapshot(
        not_after=datetime(2026, 6, 1, tzinfo=UTC),
        last_renewal_failed=True,
        renewal_attempts=3,
    )
    out = evaluate(snap, now=_now(days_before_expiry=-1))
    assert out.health == CertHealth.EXPIRED


# ---- threshold crossings --------------------------------------------


def test_first_evaluation_fires_active_thresholds():
    snap = _expires_in(0)  # not_after exactly 2026-06-01
    out = evaluate(snap, now=_now(days_before_expiry=5), last_check_at=None)
    # Within all three windows
    assert out.thresholds_to_fire == (30, 14, 7)


def test_each_threshold_fires_exactly_once_across_polls():
    snap = _expires_in(0)
    # Day -35 (35 days before expiry) — outside all
    out1 = evaluate(snap, now=_now(days_before_expiry=35), last_check_at=None)
    assert out1.thresholds_to_fire == ()

    # Day -29 — just stepped past 30d boundary
    out2 = evaluate(
        snap,
        now=_now(days_before_expiry=29),
        last_check_at=_now(days_before_expiry=31),
    )
    assert out2.thresholds_to_fire == (30,)

    # Day -28 — same window, no new crossing
    out3 = evaluate(
        snap,
        now=_now(days_before_expiry=28),
        last_check_at=_now(days_before_expiry=29),
    )
    assert out3.thresholds_to_fire == ()


def test_skipped_window_fires_all_pending():
    """Worker pause: last check at 35 days, now at 5 days — fires
    every threshold we crossed in the gap."""
    snap = _expires_in(0)
    out = evaluate(
        snap,
        now=_now(days_before_expiry=5),
        last_check_at=_now(days_before_expiry=35),
    )
    assert out.thresholds_to_fire == (30, 14, 7)


# ---- escalation -----------------------------------------------------


def test_escalation_when_within_7_days_and_renewal_failed():
    snap = CertSnapshot(
        not_after=datetime(2026, 6, 1, tzinfo=UTC),
        last_renewal_failed=True,
        renewal_attempts=3,
    )
    out = evaluate(snap, now=_now(days_before_expiry=5))
    assert out.should_escalate is True


def test_no_escalation_when_renewal_succeeded_in_window():
    snap = CertSnapshot(
        not_after=datetime(2026, 6, 1, tzinfo=UTC),
        last_renewal_failed=False,
    )
    out = evaluate(snap, now=_now(days_before_expiry=5))
    assert out.should_escalate is False


def test_no_escalation_outside_7_day_window_even_if_failed():
    """Failed renewals 20 days out are alarming but not yet
    operator-pageable — keep the noise floor low."""
    snap = CertSnapshot(
        not_after=datetime(2026, 6, 1, tzinfo=UTC),
        last_renewal_failed=True,
        renewal_attempts=2,
    )
    out = evaluate(snap, now=_now(days_before_expiry=20))
    assert out.should_escalate is False


def test_thresholds_match_spec_13():
    assert EXPIRY_THRESHOLDS_DAYS == (30, 14, 7)
    assert ESCALATION_THRESHOLD_DAYS == 7
