"""Tests for SCIM token policy (#273, spec 12 §3.3)."""

from __future__ import annotations

import pytest

from astrolift_identity.scim_tokens import (
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    SCIM_TOKEN_PREFIX,
    RateLimitWindow,
    ScimScope,
    ScimTokenError,
    StoredScimToken,
    VerifyResult,
    has_scope,
    is_rate_limited,
    issue_token,
    remaining_in_window,
    required_scope_for_path,
    validate_scopes_input,
    validate_token_name,
    verify_token,
)

# ---- token format --------------------------------------------------


def test_scim_token_prefix_locked():
    """Locked so secret-leak detectors can find tokens by prefix."""
    assert SCIM_TOKEN_PREFIX == "alft_scim_"


def test_default_rate_limit_locked():
    assert DEFAULT_RATE_LIMIT_PER_MINUTE == 60


# ---- issue ---------------------------------------------------------


def test_issue_returns_plaintext_with_prefix():
    issued = issue_token()
    assert issued.plaintext.startswith(SCIM_TOKEN_PREFIX)


def test_issue_returns_distinct_tokens():
    """Each call mints a fresh token."""
    a = issue_token()
    b = issue_token()
    assert a.plaintext != b.plaintext
    assert a.token_hash != b.token_hash


def test_issue_token_hash_matches_sha256():
    """Hash-at-rest invariant: stored hash matches SHA-256 of
    plaintext."""
    import hashlib
    issued = issue_token()
    expected = hashlib.sha256(issued.plaintext.encode()).hexdigest()
    assert issued.token_hash == expected


def test_issue_last_4_matches_plaintext():
    issued = issue_token()
    assert issued.last_4 == issued.plaintext[-4:]


# ---- scope mapping -------------------------------------------------


def test_users_path_requires_users_scope():
    assert required_scope_for_path(path="/Users") == ScimScope.USERS


def test_users_subpath_requires_users_scope():
    assert required_scope_for_path(
        path="/Users/abc-123",
    ) == ScimScope.USERS


def test_groups_path_requires_groups_scope():
    assert required_scope_for_path(
        path="/Groups",
    ) == ScimScope.GROUPS


def test_unknown_path_rejected():
    with pytest.raises(ScimTokenError):
        required_scope_for_path(path="/Schemas")


def test_has_scope_present():
    assert has_scope(
        token_scopes=(ScimScope.USERS, ScimScope.GROUPS),
        required=ScimScope.USERS,
    ) is True


def test_has_scope_absent():
    assert has_scope(
        token_scopes=(ScimScope.USERS,),
        required=ScimScope.GROUPS,
    ) is False


# ---- verify --------------------------------------------------------


def _stored(
    *, scopes=(ScimScope.USERS, ScimScope.GROUPS),
    is_revoked=False, expires_at_unix=None,
) -> StoredScimToken:
    return StoredScimToken(
        token_id=1, org_id=1,
        token_hash="hash", scopes=scopes,
        is_revoked=is_revoked,
        expires_at_unix=expires_at_unix,
    )


def test_verify_happy_path():
    issued = issue_token()
    record = StoredScimToken(
        token_id=1, org_id=10,
        token_hash=issued.token_hash,
        scopes=(ScimScope.USERS,),
        is_revoked=False, expires_at_unix=None,
    )
    decision = verify_token(
        plaintext=issued.plaintext,
        required_scope=ScimScope.USERS,
        lookup_by_hash=lambda h: record if h == issued.token_hash else None,
        now_unix=1_700_000_000,
    )
    assert decision.accepted is True
    assert decision.token_id == 1
    assert decision.org_id == 10


def test_verify_rejects_unknown_token():
    """Unknown hash returns NOT_FOUND, not 'invalid format' —
    no info leak."""
    decision = verify_token(
        plaintext="alft_scim_xyz",
        required_scope=ScimScope.USERS,
        lookup_by_hash=lambda h: None,
        now_unix=0,
    )
    assert decision.result == VerifyResult.NOT_FOUND


def test_verify_rejects_wrong_prefix_as_not_found():
    """Wrong prefix → still NOT_FOUND (don't leak 'this looks
    like ours but format is off')."""
    decision = verify_token(
        plaintext="github_pat_abc",
        required_scope=ScimScope.USERS,
        lookup_by_hash=lambda h: _stored(),
        now_unix=0,
    )
    assert decision.result == VerifyResult.NOT_FOUND


def test_verify_rejects_revoked():
    issued = issue_token()
    record = StoredScimToken(
        token_id=1, org_id=1,
        token_hash=issued.token_hash,
        scopes=(ScimScope.USERS,),
        is_revoked=True,
        expires_at_unix=None,
    )
    decision = verify_token(
        plaintext=issued.plaintext,
        required_scope=ScimScope.USERS,
        lookup_by_hash=lambda h: record,
        now_unix=0,
    )
    assert decision.result == VerifyResult.REVOKED


def test_verify_rejects_expired():
    issued = issue_token()
    record = StoredScimToken(
        token_id=1, org_id=1,
        token_hash=issued.token_hash,
        scopes=(ScimScope.USERS,),
        is_revoked=False,
        expires_at_unix=1_700_000_000,
    )
    decision = verify_token(
        plaintext=issued.plaintext,
        required_scope=ScimScope.USERS,
        lookup_by_hash=lambda h: record,
        now_unix=1_700_000_001,
    )
    assert decision.result == VerifyResult.EXPIRED


def test_verify_rejects_insufficient_scope():
    """Token has users scope only; request needs groups."""
    issued = issue_token()
    record = StoredScimToken(
        token_id=1, org_id=1,
        token_hash=issued.token_hash,
        scopes=(ScimScope.USERS,),
        is_revoked=False, expires_at_unix=None,
    )
    decision = verify_token(
        plaintext=issued.plaintext,
        required_scope=ScimScope.GROUPS,
        lookup_by_hash=lambda h: record,
        now_unix=0,
    )
    assert decision.result == VerifyResult.INSUFFICIENT_SCOPE


# ---- input validation ----------------------------------------------


def test_validate_scopes_input_basic():
    out = validate_scopes_input(scopes=["scim.users", "scim.groups"])
    assert out == (ScimScope.USERS, ScimScope.GROUPS)


def test_validate_scopes_input_dedupes():
    out = validate_scopes_input(
        scopes=["scim.users", "scim.users"],
    )
    assert out == (ScimScope.USERS,)


def test_validate_scopes_input_empty_rejected():
    """No-scope token can't access anything → almost certainly
    a bug."""
    with pytest.raises(ScimTokenError):
        validate_scopes_input(scopes=[])


def test_validate_scopes_input_unknown_rejected():
    with pytest.raises(ScimTokenError, match="unknown"):
        validate_scopes_input(scopes=["scim.admin"])


def test_validate_token_name_basic():
    assert validate_token_name(name="okta scim") == "okta scim"


def test_validate_token_name_strips_whitespace():
    assert validate_token_name(name="  okta scim  ") == "okta scim"


def test_validate_token_name_empty_rejected():
    with pytest.raises(ScimTokenError):
        validate_token_name(name="")


def test_validate_token_name_whitespace_only_rejected():
    with pytest.raises(ScimTokenError):
        validate_token_name(name="   ")


def test_validate_token_name_too_long_rejected():
    with pytest.raises(ScimTokenError, match="64 chars"):
        validate_token_name(name="x" * 65)


# ---- rate limit ---------------------------------------------------


def test_rate_limit_under_threshold():
    window = RateLimitWindow(
        token_id=1,
        requests_in_window=30,
        window_started_unix=1_700_000_000,
    )
    assert is_rate_limited(window=window, now_unix=1_700_000_005) is False


def test_rate_limit_at_threshold():
    """At the limit (60) — refuse the next request."""
    window = RateLimitWindow(
        token_id=1,
        requests_in_window=60,
        window_started_unix=1_700_000_000,
    )
    assert is_rate_limited(window=window, now_unix=1_700_000_005) is True


def test_rate_limit_window_expired():
    """Past 1 minute = caller will reset window; not currently
    rate-limited."""
    window = RateLimitWindow(
        token_id=1,
        requests_in_window=60,
        window_started_unix=1_700_000_000,
    )
    assert is_rate_limited(window=window, now_unix=1_700_000_061) is False


def test_remaining_in_window_returns_difference():
    window = RateLimitWindow(
        token_id=1,
        requests_in_window=20,
        window_started_unix=1_700_000_000,
    )
    assert remaining_in_window(
        window=window, now_unix=1_700_000_005,
    ) == 40


def test_remaining_full_after_window_expired():
    window = RateLimitWindow(
        token_id=1,
        requests_in_window=60,
        window_started_unix=1_700_000_000,
    )
    assert remaining_in_window(
        window=window, now_unix=1_700_000_061,
    ) == DEFAULT_RATE_LIMIT_PER_MINUTE


def test_rate_limit_zero_rejected():
    """Defensive: 0 limit would refuse every request → not
    a sensible config."""
    window = RateLimitWindow(
        token_id=1, requests_in_window=0,
        window_started_unix=0, rate_limit=0,
    )
    with pytest.raises(ScimTokenError):
        is_rate_limited(window=window, now_unix=10)
