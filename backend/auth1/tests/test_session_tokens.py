"""Tests for session-token policy: issue / rotate / revoke (#147)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auth1.session_tokens import (
    DEFAULT_ACCESS_TTL_SECONDS,
    DEFAULT_REFRESH_TTL_SECONDS,
    RefreshTokenInvalid,
    is_active,
    issue,
    revoke,
    revoke_all_for_user,
    revoke_chain,
    rotate,
)

UTC = UTC


def _now(*, off_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off_seconds)


# ---- issue ----------------------------------------------------------


def test_issue_returns_distinct_tokens_and_record():
    out = issue(user_id=42, now=_now())
    # Plaintext tokens are returned exactly once
    assert out.access_token != out.refresh_token
    assert out.access_token.startswith("a_")
    assert out.refresh_token.startswith("r_")
    # The record stores the hash, not the raw refresh token
    assert out.record.refresh_token_hash.startswith("sha256:")
    assert out.refresh_token not in out.record.refresh_token_hash
    assert out.record.user_id == 42


def test_issue_default_ttls_match_spec_12():
    out = issue(user_id=1, now=_now())
    access_window = (out.record.expires_at - out.record.issued_at).total_seconds()
    refresh_window = (
        out.record.refresh_expires_at - out.record.issued_at
    ).total_seconds()
    assert int(access_window) == DEFAULT_ACCESS_TTL_SECONDS
    assert int(refresh_window) == DEFAULT_REFRESH_TTL_SECONDS


def test_issue_rejects_naive_now():
    with pytest.raises(ValueError):
        issue(user_id=1, now=datetime(2026, 5, 9))


def test_issue_records_user_agent_and_ip():
    out = issue(user_id=1, now=_now(), user_agent="curl/8", ip_address="1.2.3.4")
    assert out.record.user_agent == "curl/8"
    assert out.record.ip_address == "1.2.3.4"


# ---- is_active ------------------------------------------------------


def test_active_during_refresh_window():
    out = issue(user_id=1, now=_now())
    assert is_active(out.record, now=_now(off_seconds=60)) is True


def test_inactive_after_refresh_expiry():
    out = issue(user_id=1, now=_now())
    later = out.record.refresh_expires_at + timedelta(seconds=1)
    assert is_active(out.record, now=later) is False


def test_inactive_after_revoke():
    out = issue(user_id=1, now=_now())
    revoke(out.record, now=_now(off_seconds=60))
    assert is_active(out.record, now=_now(off_seconds=120)) is False


def test_inactive_after_rotate():
    out = issue(user_id=1, now=_now())
    rotate(
        presented_refresh_token=out.refresh_token,
        record=out.record,
        now=_now(off_seconds=60),
    )
    assert is_active(out.record, now=_now(off_seconds=120)) is False


# ---- rotate ---------------------------------------------------------


def test_rotate_issues_new_pair_and_links_parent():
    first = issue(user_id=1, now=_now())
    second = rotate(
        presented_refresh_token=first.refresh_token,
        record=first.record,
        now=_now(off_seconds=60),
    )
    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    assert second.record.parent_jti == first.record.access_token_jti
    # First record was marked rotated
    assert first.record.rotated_to_jti == second.record.access_token_jti


def test_rotate_with_wrong_token_rejected_not_replay():
    first = issue(user_id=1, now=_now())
    with pytest.raises(RefreshTokenInvalid) as exc:
        rotate(
            presented_refresh_token="r_wrong",
            record=first.record,
            now=_now(off_seconds=60),
        )
    assert exc.value.replay is False


def test_rotate_replay_after_rotation_raises_with_replay_flag():
    """Spec 12 §2.2 — replaying a previously rotated refresh token
    is theft. Caller revokes the chain."""
    first = issue(user_id=1, now=_now())
    rotate(
        presented_refresh_token=first.refresh_token,
        record=first.record,
        now=_now(off_seconds=60),
    )
    # Attacker presents the original refresh token a second time
    with pytest.raises(RefreshTokenInvalid) as exc:
        rotate(
            presented_refresh_token=first.refresh_token,
            record=first.record,
            now=_now(off_seconds=120),
        )
    assert exc.value.replay is True


def test_rotate_after_revoke_rejected_not_replay():
    first = issue(user_id=1, now=_now())
    revoke(first.record, now=_now(off_seconds=10))
    with pytest.raises(RefreshTokenInvalid) as exc:
        rotate(
            presented_refresh_token=first.refresh_token,
            record=first.record,
            now=_now(off_seconds=60),
        )
    assert exc.value.replay is False


def test_rotate_after_refresh_expired_rejected():
    first = issue(user_id=1, now=_now(), refresh_ttl_seconds=10)
    with pytest.raises(RefreshTokenInvalid, match="expired"):
        rotate(
            presented_refresh_token=first.refresh_token,
            record=first.record,
            now=_now(off_seconds=60),
        )


def test_rotate_inherits_user_agent_when_unspecified():
    first = issue(user_id=1, now=_now(), user_agent="curl/8")
    second = rotate(
        presented_refresh_token=first.refresh_token,
        record=first.record,
        now=_now(off_seconds=60),
    )
    assert second.record.user_agent == "curl/8"


# ---- revoke chain ---------------------------------------------------


def test_revoke_chain_walks_parents_and_descendants():
    """Replay scenario: tokens A → B → C, attacker replays B. We
    must revoke A, B, C — and any further descendant. The caller
    assembles the chain from a DB query and hands it to revoke_chain."""
    a = issue(user_id=1, now=_now()).record
    b = issue(user_id=1, now=_now(off_seconds=10), parent_jti=a.access_token_jti).record
    a.rotated_to_jti = b.access_token_jti
    c = issue(user_id=1, now=_now(off_seconds=20), parent_jti=b.access_token_jti).record
    b.rotated_to_jti = c.access_token_jti

    revoked = revoke_chain(
        b, chain=[a, b, c], now=_now(off_seconds=30), reason="replay"
    )
    assert revoked == 3
    assert a.revoked_at is not None and a.revoke_reason == "replay"
    assert b.revoked_at is not None
    assert c.revoked_at is not None


def test_revoke_chain_idempotent_on_already_revoked():
    a = issue(user_id=1, now=_now()).record
    revoke(a, now=_now(off_seconds=10), reason="user logout")
    n = revoke_chain(a, chain=[a], now=_now(off_seconds=20), reason="replay")
    # Already-revoked rows aren't counted again
    assert n == 0
    # Original reason preserved (audit trail)
    assert a.revoke_reason == "user logout"


# ---- revoke ---------------------------------------------------------


def test_revoke_idempotent():
    out = issue(user_id=1, now=_now())
    revoke(out.record, now=_now(off_seconds=10), reason="logout")
    first_revoked_at = out.record.revoked_at
    revoke(out.record, now=_now(off_seconds=20), reason="changed mind")
    # Original timestamp + reason preserved
    assert out.record.revoked_at == first_revoked_at
    assert out.record.revoke_reason == "logout"


def test_revoke_all_returns_newly_revoked_count():
    a = issue(user_id=1, now=_now()).record
    b = issue(user_id=1, now=_now(off_seconds=10)).record
    revoke(b, now=_now(off_seconds=20))
    n = revoke_all_for_user([a, b], now=_now(off_seconds=30))
    assert n == 1  # b was already revoked
    assert a.revoke_reason == "sign-out everywhere"
