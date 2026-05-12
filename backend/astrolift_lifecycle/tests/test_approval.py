"""Tests for ApprovalWorkflow policy + magic-link tokens (#125, spec 06 §4.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astrolift_lifecycle.approval import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    MAGIC_LINK_PREFIX,
    ApprovalDecision,
    ApprovalVote,
    MagicLinkInvalid,
    MagicLinkRecord,
    QuorumError,
    evaluate_quorum,
    is_timed_out,
    mint_magic_link,
    verify_magic_link,
)

UTC = UTC


def _now(*, off_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off_seconds)


# ---- magic link mint ----------------------------------------------


def test_mint_returns_alft_ml_prefixed_token():
    out = mint_magic_link(now=_now())
    assert out.plaintext_token.startswith(MAGIC_LINK_PREFIX)
    assert out.token_hash != out.plaintext_token  # hashed at rest
    assert len(out.last4) == 4
    assert out.last4 == out.plaintext_token[-4:]


def test_mint_default_ttl_matches_spec():
    out = mint_magic_link(now=_now())
    expected = _now() + timedelta(seconds=DEFAULT_APPROVAL_TIMEOUT_SECONDS)
    assert out.expires_at == expected


def test_mint_custom_ttl():
    out = mint_magic_link(now=_now(), ttl_seconds=3600)
    assert (out.expires_at - _now()).total_seconds() == 3600


def test_mint_rejects_naive_now():
    with pytest.raises(ValueError):
        mint_magic_link(now=datetime(2026, 5, 9))


def test_mint_rejects_zero_ttl():
    with pytest.raises(ValueError):
        mint_magic_link(now=_now(), ttl_seconds=0)


def test_mint_distinct_tokens_per_call():
    a = mint_magic_link(now=_now())
    b = mint_magic_link(now=_now())
    assert a.plaintext_token != b.plaintext_token


# ---- magic link verify --------------------------------------------


def _record(token_hash: str, **kw) -> MagicLinkRecord:
    base = {
        "token_hash": token_hash,
        "approver_user_id": 42,
        "expires_at": _now(off_seconds=DEFAULT_APPROVAL_TIMEOUT_SECONDS),
        "consumed_at": None,
    }
    base.update(kw)
    return MagicLinkRecord(**base)


def test_verify_returns_user_id_on_match():
    issued = mint_magic_link(now=_now())
    record = _record(issued.token_hash)
    user_id = verify_magic_link(
        presented_plaintext=issued.plaintext_token,
        record=record,
        now=_now(),
    )
    assert user_id == 42


def test_verify_rejects_wrong_token():
    issued = mint_magic_link(now=_now())
    record = _record("0" * 64)  # different hash
    with pytest.raises(MagicLinkInvalid):
        verify_magic_link(
            presented_plaintext=issued.plaintext_token,
            record=record,
            now=_now(),
        )


def test_verify_rejects_token_without_prefix():
    """Random base64-ish strings don't accidentally pass."""
    record = _record("any")
    with pytest.raises(MagicLinkInvalid):
        verify_magic_link(
            presented_plaintext="random-bytes-here",
            record=record,
            now=_now(),
        )


def test_verify_rejects_consumed_token():
    """Single-use invariant: once consumed, never again."""
    issued = mint_magic_link(now=_now())
    record = _record(
        issued.token_hash,
        consumed_at=_now(off_seconds=-60),
    )
    with pytest.raises(MagicLinkInvalid):
        verify_magic_link(
            presented_plaintext=issued.plaintext_token,
            record=record,
            now=_now(),
        )


def test_verify_rejects_expired_token():
    """Time-limited invariant."""
    issued = mint_magic_link(now=_now(off_seconds=-3600), ttl_seconds=60)
    record = _record(
        issued.token_hash,
        expires_at=issued.expires_at,
    )
    with pytest.raises(MagicLinkInvalid):
        verify_magic_link(
            presented_plaintext=issued.plaintext_token,
            record=record,
            now=_now(),
        )


def test_verify_uses_generic_message_for_all_failures():
    """No distinguishing 'wrong token' vs 'expired' vs 'consumed'
    in the error so an attacker can't probe via timing or message
    inspection."""
    issued = mint_magic_link(now=_now())

    # Wrong token
    record = _record("0" * 64)
    try:
        verify_magic_link(
            presented_plaintext=issued.plaintext_token,
            record=record,
            now=_now(),
        )
        raise AssertionError("expected raise")
    except MagicLinkInvalid as e:
        assert str(e) == "token invalid"

    # Expired
    record = _record(issued.token_hash, expires_at=_now(off_seconds=-1))
    try:
        verify_magic_link(
            presented_plaintext=issued.plaintext_token,
            record=record,
            now=_now(),
        )
        raise AssertionError("expected raise")
    except MagicLinkInvalid as e:
        assert str(e) == "token invalid"


# ---- quorum counting -----------------------------------------------


def test_quorum_pending_when_no_votes():
    state = evaluate_quorum(
        votes=[],
        eligible_approvers=[1, 2, 3],
        min_approvers=2,
    )
    assert state.decision == ApprovalDecision.PENDING
    assert state.approvers_for == ()


def test_quorum_pending_when_below_threshold():
    state = evaluate_quorum(
        votes=[
            ApprovalVote(user_id=1, decision=ApprovalDecision.APPROVED),
        ],
        eligible_approvers=[1, 2, 3],
        min_approvers=2,
    )
    assert state.decision == ApprovalDecision.PENDING


def test_quorum_approved_when_threshold_met():
    state = evaluate_quorum(
        votes=[
            ApprovalVote(user_id=1, decision=ApprovalDecision.APPROVED),
            ApprovalVote(user_id=2, decision=ApprovalDecision.APPROVED),
        ],
        eligible_approvers=[1, 2, 3],
        min_approvers=2,
    )
    assert state.decision == ApprovalDecision.APPROVED
    assert set(state.approvers_for) == {1, 2}


def test_quorum_one_rejection_kills_deploy():
    """Spec rule: ANY rejection short-circuits, even with majority
    approvals. Reject is a strong signal."""
    state = evaluate_quorum(
        votes=[
            ApprovalVote(user_id=1, decision=ApprovalDecision.APPROVED),
            ApprovalVote(user_id=2, decision=ApprovalDecision.APPROVED),
            ApprovalVote(
                user_id=3,
                decision=ApprovalDecision.REJECTED,
                reason="security concern",
            ),
        ],
        eligible_approvers=[1, 2, 3, 4, 5],
        min_approvers=2,
    )
    assert state.decision == ApprovalDecision.REJECTED
    assert "user 3" in state.reason
    assert "security concern" in state.reason


def test_quorum_ignores_non_eligible_voters():
    """Even if a non-eligible user submits a vote (defense in
    depth — the signal handler should have rejected upstream),
    the policy ignores it."""
    state = evaluate_quorum(
        votes=[
            ApprovalVote(user_id=999, decision=ApprovalDecision.APPROVED),
            ApprovalVote(user_id=999, decision=ApprovalDecision.REJECTED),
        ],
        eligible_approvers=[1, 2],
        min_approvers=1,
    )
    assert state.decision == ApprovalDecision.PENDING


def test_quorum_user_can_change_vote():
    """User initially approved, then changed to reject. Latest
    vote wins."""
    state = evaluate_quorum(
        votes=[
            ApprovalVote(user_id=1, decision=ApprovalDecision.APPROVED),
            ApprovalVote(user_id=1, decision=ApprovalDecision.REJECTED),
        ],
        eligible_approvers=[1, 2],
        min_approvers=1,
    )
    assert state.decision == ApprovalDecision.REJECTED


def test_quorum_rejects_zero_min_approvers():
    """Zero min_approvers would auto-approve every deploy —
    misconfig."""
    with pytest.raises(QuorumError):
        evaluate_quorum(
            votes=[],
            eligible_approvers=[1, 2],
            min_approvers=0,
        )


def test_quorum_rejects_min_higher_than_eligible():
    """Asking for more approvers than exist makes approval
    impossible — surface at config time."""
    with pytest.raises(QuorumError, match="exceeds"):
        evaluate_quorum(
            votes=[],
            eligible_approvers=[1, 2],
            min_approvers=3,
        )


# ---- timeout -------------------------------------------------------


def test_not_timed_out_within_window():
    requested = _now(off_seconds=-60)
    assert (
        is_timed_out(
            requested_at=requested,
            now=_now(),
            timeout_seconds=DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        )
        is False
    )


def test_timed_out_at_or_past_window():
    requested = _now(off_seconds=-DEFAULT_APPROVAL_TIMEOUT_SECONDS - 1)
    assert (
        is_timed_out(
            requested_at=requested,
            now=_now(),
            timeout_seconds=DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        )
        is True
    )


def test_timeout_default_is_seven_days():
    assert DEFAULT_APPROVAL_TIMEOUT_SECONDS == 7 * 24 * 3600


def test_timeout_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        is_timed_out(
            requested_at=datetime(2026, 5, 9),
            now=_now(),
        )
    with pytest.raises(ValueError):
        is_timed_out(
            requested_at=_now(),
            now=datetime(2026, 5, 9),
        )
