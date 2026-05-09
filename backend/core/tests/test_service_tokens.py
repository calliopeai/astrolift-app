"""Tests for platform service tokens (#149, spec 12 §3.4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.service_tokens import (
    DEFAULT_TTL_SECONDS,
    ServiceTokenInvalid,
    issue,
    needs_rotation,
    verify,
)


UTC = timezone.utc
SECRET = b"k" * 32
ISSUER = "platform.acme.com"


def _now(*, off_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off_seconds)


# ---- issue/verify happy path ---------------------------------------


def test_issue_returns_three_dot_separated_jwt():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    assert token.count(".") == 2
    # Header and payload are base64url — no '+', '/', or '=' chars
    for part in token.split("."):
        assert "+" not in part and "/" not in part and "=" not in part


def test_verify_returns_claims_for_valid_token():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    claims = verify(
        token,
        expected_audience="graphql",
        expected_issuer=ISSUER,
        secret=SECRET,
        now=_now(off_seconds=10),
    )
    assert claims.sub == "workers"
    assert claims.aud == "graphql"
    assert claims.iss == ISSUER
    assert claims.jti  # populated


def test_default_ttl_is_one_hour():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    claims = verify(
        token,
        expected_audience="graphql",
        expected_issuer=ISSUER,
        secret=SECRET,
        now=_now(),
    )
    assert claims.exp - claims.iat == DEFAULT_TTL_SECONDS


# ---- issuance guards -----------------------------------------------


def test_issue_rejects_empty_sub():
    with pytest.raises(ValueError, match="sub"):
        issue(sub="", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now())


def test_issue_rejects_empty_aud():
    with pytest.raises(ValueError, match="aud"):
        issue(sub="workers", aud="", issuer=ISSUER, secret=SECRET, now=_now())


def test_issue_rejects_zero_ttl():
    with pytest.raises(ValueError):
        issue(
            sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
            now=_now(), ttl_seconds=0,
        )


def test_issue_rejects_naive_now():
    with pytest.raises(ValueError):
        issue(
            sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
            now=datetime(2026, 5, 9),
        )


# ---- verification failure modes ------------------------------------
# All failures uniformly raise ServiceTokenInvalid — recipients
# return identical 401s without leaking which check failed.


def test_verify_rejects_malformed_token_shape():
    with pytest.raises(ServiceTokenInvalid):
        verify(
            "not.a.jwt.with.too.many.parts",
            expected_audience="graphql",
            expected_issuer=ISSUER,
            secret=SECRET,
            now=_now(),
        )


def test_verify_rejects_wrong_signature():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    with pytest.raises(ServiceTokenInvalid):
        verify(
            token,
            expected_audience="graphql",
            expected_issuer=ISSUER,
            secret=b"different" * 4,
            now=_now(),
        )


def test_verify_rejects_wrong_audience():
    """The whole point of audience: a token issued for 'graphql' must
    not authenticate against the 'events' service."""
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    with pytest.raises(ServiceTokenInvalid):
        verify(
            token,
            expected_audience="events",
            expected_issuer=ISSUER,
            secret=SECRET,
            now=_now(),
        )


def test_verify_rejects_wrong_issuer():
    """Defends against cross-install token confusion: an attacker who
    obtains a token from install A can't authenticate to install B."""
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET, now=_now()
    )
    with pytest.raises(ServiceTokenInvalid):
        verify(
            token,
            expected_audience="graphql",
            expected_issuer="other.acme.com",
            secret=SECRET,
            now=_now(),
        )


def test_verify_rejects_expired_token():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
        now=_now(), ttl_seconds=10,
    )
    with pytest.raises(ServiceTokenInvalid):
        verify(
            token,
            expected_audience="graphql",
            expected_issuer=ISSUER,
            secret=SECRET,
            now=_now(off_seconds=20),
        )


def test_verify_rejects_alg_confusion():
    """A token with alg=none or alg=RS256 must be rejected — alg
    confusion is the classic JWT vuln."""
    import base64
    import json
    # Manually craft a token with alg=none
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sub": "workers",
                "aud": "graphql",
                "iss": ISSUER,
                "iat": 100,
                "exp": 9999999999,
                "jti": "x",
            },
            separators=(",", ":"),
        ).encode()
    ).rstrip(b"=").decode()
    bad_token = f"{header}.{payload}."  # empty signature
    with pytest.raises(ServiceTokenInvalid):
        verify(
            bad_token,
            expected_audience="graphql",
            expected_issuer=ISSUER,
            secret=SECRET,
            now=_now(),
        )


# ---- rotation hint --------------------------------------------------


def test_needs_rotation_false_early_in_lifetime():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
        now=_now(), ttl_seconds=4000,
    )
    claims = verify(
        token, expected_audience="graphql", expected_issuer=ISSUER,
        secret=SECRET, now=_now(),
    )
    # 100s elapsed of a 4000s token = 97.5% remaining
    assert needs_rotation(claims, now=_now(off_seconds=100)) is False


def test_needs_rotation_true_in_last_quarter():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
        now=_now(), ttl_seconds=4000,
    )
    claims = verify(
        token, expected_audience="graphql", expected_issuer=ISSUER,
        secret=SECRET, now=_now(),
    )
    # 3001s elapsed of 4000s token = 25% remaining → rotate
    assert needs_rotation(claims, now=_now(off_seconds=3001)) is True


def test_needs_rotation_true_after_expiry():
    token = issue(
        sub="workers", aud="graphql", issuer=ISSUER, secret=SECRET,
        now=_now(), ttl_seconds=10,
    )
    claims = verify(
        token, expected_audience="graphql", expected_issuer=ISSUER,
        secret=SECRET, now=_now(),
    )
    assert needs_rotation(claims, now=_now(off_seconds=20)) is True
