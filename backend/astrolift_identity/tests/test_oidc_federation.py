"""Tests for OIDC federation policy (#60, spec 14 §17)."""

from __future__ import annotations

import pytest

from astrolift_identity.oidc_federation import (
    KNOWN_ISSUERS,
    FederationConfig,
    FederationError,
    OidcClaims,
    evaluate_exchange,
    jwks_url_for,
    matches_subject,
)


def _config(**kw) -> FederationConfig:
    base = dict(
        org_id=1,
        issuer="https://token.actions.githubusercontent.com",
        allowed_subject_patterns=("repo:acme/api:ref:refs/heads/main",),
        audience="https://platform.acme.com",
        target_app_pattern="",
    )
    base.update(kw)
    return FederationConfig(**base)


def _claims(**kw) -> OidcClaims:
    base = dict(
        iss="https://token.actions.githubusercontent.com",
        aud="https://platform.acme.com",
        sub="repo:acme/api:ref:refs/heads/main",
        exp_unix=1_700_000_000,
        extra_claims={},
    )
    base.update(kw)
    return OidcClaims(**base)


# ---- subject pattern matching --------------------------------------


def test_glob_segment_match():
    """``*`` matches a non-slash segment."""
    assert matches_subject(
        allowed_pattern="repo:acme/api:*",
        presented_sub="repo:acme/api:ref:refs/heads/main",
    ) is False  # * doesn't span '/'

    # Single-segment glob: matches any leaf
    assert matches_subject(
        allowed_pattern="repo:acme/api:ref:refs/heads/*",
        presented_sub="repo:acme/api:ref:refs/heads/main",
    ) is True


def test_glob_exact_pattern():
    """Pattern without ``*`` must match exactly."""
    assert matches_subject(
        allowed_pattern="repo:acme/api:ref:refs/heads/main",
        presented_sub="repo:acme/api:ref:refs/heads/main",
    ) is True
    assert matches_subject(
        allowed_pattern="repo:acme/api:ref:refs/heads/main",
        presented_sub="repo:acme/api:ref:refs/heads/develop",
    ) is False


def test_glob_handles_special_regex_chars():
    """A pattern containing dots/dashes shouldn't be treated as
    regex. ``acme.org`` should match literally, not 'acme[any]org'."""
    assert matches_subject(
        allowed_pattern="repo:acme.org/api:*",
        presented_sub="repo:acmeXorg/api:ref:refs/heads/main",
    ) is False


def test_empty_pattern_rejected():
    with pytest.raises(FederationError):
        matches_subject(allowed_pattern="", presented_sub="x")


def test_empty_sub_no_match():
    assert matches_subject(allowed_pattern="*", presented_sub="") is False


# ---- federation config guards --------------------------------------


def test_config_requires_subject_patterns():
    """Empty patterns would accept any subject from this issuer —
    a misconfig that's worth catching at construction."""
    with pytest.raises(FederationError, match="subject_patterns"):
        FederationConfig(
            org_id=1,
            issuer="https://x", audience="https://y",
            allowed_subject_patterns=(),
        )


def test_config_requires_issuer_and_audience():
    with pytest.raises(FederationError):
        FederationConfig(
            org_id=1, issuer="", audience="x",
            allowed_subject_patterns=("*",),
        )
    with pytest.raises(FederationError):
        FederationConfig(
            org_id=1, issuer="x", audience="",
            allowed_subject_patterns=("*",),
        )


def test_config_requires_positive_org_id():
    with pytest.raises(FederationError):
        FederationConfig(
            org_id=0, issuer="x", audience="y",
            allowed_subject_patterns=("*",),
        )


# ---- evaluate_exchange ---------------------------------------------


def test_clean_exchange():
    out = evaluate_exchange(
        config=_config(), claims=_claims(),
        target_app_slug="api",
    )
    assert out.org_id == 1
    assert out.subject == "repo:acme/api:ref:refs/heads/main"


def test_issuer_mismatch_rejected():
    with pytest.raises(FederationError, match="issuer"):
        evaluate_exchange(
            config=_config(),
            claims=_claims(iss="https://malicious.example"),
            target_app_slug="api",
        )


def test_audience_mismatch_rejected():
    """Without audience binding, a token minted for one Astrolift
    install could be replayed against another."""
    with pytest.raises(FederationError, match="audience"):
        evaluate_exchange(
            config=_config(),
            claims=_claims(aud="https://other-platform.com"),
            target_app_slug="api",
        )


def test_subject_not_in_allowlist_rejected():
    with pytest.raises(FederationError, match="subject"):
        evaluate_exchange(
            config=_config(),
            claims=_claims(sub="repo:acme/api:ref:refs/heads/develop"),
            target_app_slug="api",
        )


def test_subject_glob_matches_branch_wildcard():
    """A wildcard branch pattern works."""
    out = evaluate_exchange(
        config=_config(allowed_subject_patterns=(
            "repo:acme/api:ref:refs/heads/*",
        )),
        claims=_claims(sub="repo:acme/api:ref:refs/heads/feature-x"),
        target_app_slug="api",
    )
    assert out.subject == "repo:acme/api:ref:refs/heads/feature-x"


def test_target_app_pattern_restricts():
    """When set, the deploy token can only target apps matching
    the pattern. CI repo-level scoping."""
    config = _config(target_app_pattern="api")
    out = evaluate_exchange(
        config=config, claims=_claims(), target_app_slug="api",
    )
    assert out.target_app_pattern == "api"

    with pytest.raises(FederationError, match="target app"):
        evaluate_exchange(
            config=config, claims=_claims(), target_app_slug="other-app",
        )


def test_target_app_pattern_empty_unrestricted():
    """Empty pattern → any app in the org allowed."""
    out = evaluate_exchange(
        config=_config(target_app_pattern=""),
        claims=_claims(),
        target_app_slug="any-app",
    )
    assert out.target_app_pattern == ""


# ---- JWKS URL convention -------------------------------------------


def test_jwks_url_for_github_actions():
    """GitHub uses /.well-known/jwks (no .json suffix)."""
    out = jwks_url_for("https://token.actions.githubusercontent.com")
    assert out == "https://token.actions.githubusercontent.com/.well-known/jwks"


def test_jwks_url_standard_convention():
    out = jwks_url_for("https://gitlab.com")
    assert out == "https://gitlab.com/.well-known/jwks.json"


def test_jwks_url_strips_trailing_slash():
    out = jwks_url_for("https://gitlab.com/")
    assert out == "https://gitlab.com/.well-known/jwks.json"


def test_jwks_url_self_hosted_issuer():
    """Self-hosted issuers (custom GitLab, custom auth) use the
    same convention; this module returns a URL even for unknown
    issuers."""
    out = jwks_url_for("https://gitlab.acme-internal.com")
    assert out == "https://gitlab.acme-internal.com/.well-known/jwks.json"


def test_jwks_url_empty_issuer_rejected():
    with pytest.raises(FederationError):
        jwks_url_for("")


def test_known_issuers_locked():
    """Lock-test the recognized issuer list. New entries should be
    a deliberate review."""
    assert "https://token.actions.githubusercontent.com" in KNOWN_ISSUERS
    assert "https://gitlab.com" in KNOWN_ISSUERS
