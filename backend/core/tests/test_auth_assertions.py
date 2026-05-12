"""Tests for MFA step-up + freshness + device assertion (#148)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.auth_assertions import (
    AMR_PASSWORD,
    AMR_SMS,
    AMR_TOTP,
    AMR_WEBAUTHN,
    AssertionPolicy,
    SessionAssertion,
    StepUpRequired,
    evaluate,
)

UTC = UTC


def _now(*, off: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off)


# ---- policy guards ---------------------------------------------------


def test_policy_rejects_unknown_amr():
    with pytest.raises(ValueError, match="unknown AMR"):
        AssertionPolicy(required_amr_any=("magic-spell",))


def test_policy_rejects_weak_amr():
    """Password and SMS aren't strong MFA — the spec doesn't allow
    them as step-up requirements (only as baseline auth)."""
    with pytest.raises(ValueError, match="not a strong MFA"):
        AssertionPolicy(required_amr_any=(AMR_PASSWORD,))
    with pytest.raises(ValueError, match="not a strong MFA"):
        AssertionPolicy(required_amr_any=(AMR_SMS,))


def test_policy_rejects_nonpositive_age():
    with pytest.raises(ValueError):
        AssertionPolicy(max_session_age_seconds=0)


# ---- happy path -----------------------------------------------------


def test_no_requirements_passes_anything():
    """Default policy = baseline auth required. Session with just
    pwd satisfies it."""
    policy = AssertionPolicy()
    session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=_now(off=-3600))
    evaluate(policy, session, now=_now())  # no raise


def test_satisfied_amr_passes():
    policy = AssertionPolicy(required_amr_any=(AMR_TOTP, AMR_WEBAUTHN))
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD, AMR_TOTP),
        authenticated_at=_now(off=-3600),
    )
    evaluate(policy, session, now=_now())


def test_session_within_max_age_passes():
    policy = AssertionPolicy(max_session_age_seconds=900)
    session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=_now(off=-300))
    evaluate(policy, session, now=_now())


def test_fresh_device_assertion_passes():
    policy = AssertionPolicy(device_assertion_required=True)
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD,),
        authenticated_at=_now(off=-3600),
        device_assertion_at=_now(off=-30),
    )
    evaluate(policy, session, now=_now())


# ---- failure modes --------------------------------------------------


def test_missing_amr_raises_step_up():
    policy = AssertionPolicy(required_amr_any=(AMR_TOTP, AMR_WEBAUTHN))
    session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=_now())
    with pytest.raises(StepUpRequired) as exc:
        evaluate(policy, session, now=_now())
    assert exc.value.missing_amr_any == (AMR_TOTP, AMR_WEBAUTHN)
    assert exc.value.session_too_old is False
    assert exc.value.device_assertion_required is False


def test_old_session_raises_too_old():
    policy = AssertionPolicy(max_session_age_seconds=60)
    session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=_now(off=-3600))
    with pytest.raises(StepUpRequired) as exc:
        evaluate(policy, session, now=_now())
    assert exc.value.session_too_old is True


def test_missing_device_assertion_raises():
    policy = AssertionPolicy(device_assertion_required=True)
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD,),
        authenticated_at=_now(off=-300),
        device_assertion_at=None,
    )
    with pytest.raises(StepUpRequired) as exc:
        evaluate(policy, session, now=_now())
    assert exc.value.device_assertion_required is True


def test_stale_device_assertion_raises():
    """A WebAuthn touch from yesterday isn't 'fresh enough' for a
    sensitive operation. Default freshness window: 5 minutes (or
    max_session_age_seconds when set)."""
    policy = AssertionPolicy(device_assertion_required=True)
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD,),
        authenticated_at=_now(off=-300),
        device_assertion_at=_now(off=-3600),  # 1 hour ago
    )
    with pytest.raises(StepUpRequired) as exc:
        evaluate(policy, session, now=_now())
    assert exc.value.device_assertion_required is True


def test_device_freshness_uses_max_session_age_when_set():
    """If max_session_age_seconds is set, the device assertion
    freshness window matches it — keeps the two policies aligned."""
    policy = AssertionPolicy(
        device_assertion_required=True,
        max_session_age_seconds=3600,
    )
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD,),
        authenticated_at=_now(off=-100),
        device_assertion_at=_now(off=-1800),  # 30 min ago
    )
    # Fits in 1-hour window
    evaluate(policy, session, now=_now())


# ---- combined failures -----------------------------------------------


def test_all_failures_reported_in_one_exception():
    """Critical UX rule: report everything at once. UI can't loop
    'now MFA' → 'now device' → 'now re-auth'; ask for everything in
    one re-auth modal."""
    policy = AssertionPolicy(
        required_amr_any=(AMR_WEBAUTHN,),
        max_session_age_seconds=60,
        device_assertion_required=True,
    )
    session = SessionAssertion(
        user_id=1,
        amr=(AMR_PASSWORD,),
        authenticated_at=_now(off=-3600),
        device_assertion_at=None,
    )
    with pytest.raises(StepUpRequired) as exc:
        evaluate(policy, session, now=_now())
    assert exc.value.missing_amr_any == (AMR_WEBAUTHN,)
    assert exc.value.session_too_old is True
    assert exc.value.device_assertion_required is True


# ---- guards ---------------------------------------------------------


def test_evaluate_rejects_naive_timestamps():
    policy = AssertionPolicy()
    session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=_now())
    with pytest.raises(ValueError):
        evaluate(policy, session, now=datetime(2026, 5, 9))

    bad_session = SessionAssertion(user_id=1, amr=(AMR_PASSWORD,), authenticated_at=datetime(2026, 5, 9))
    with pytest.raises(ValueError):
        evaluate(policy, bad_session, now=_now())
