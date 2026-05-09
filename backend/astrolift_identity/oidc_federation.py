"""
OIDC federation policy for CI → platform auth (#60, spec 14 §17).

Pure-Python module. ``auth.exchange_oidc`` (the GraphQL/REST
mutation) hands a presented OIDC token to this module + the
calling org's federation config, and gets back a federated
``DeployTokenIssuance`` if the token is acceptable.

The actual JWT signature verification + JWKS fetch lives in a
dedicated verifier (depends on a JWT library); this module is
the policy + claim-matching layer that runs after the token is
verified.

Eliminates static deploy tokens from CI secret stores: GitHub
Actions / GitLab CI / Buildkite / CircleCI mint short-lived
OIDC tokens at job start; the platform exchanges them for an
even-shorter-lived deploy credential scoped to one app.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

# Spec 14 §17 issuers we recognize. Each has a known JWKS URL +
# claim shape. Per-org config picks a subset; only those are
# acceptable on that org's federation endpoint.
KNOWN_ISSUERS: tuple[str, ...] = (
    "https://token.actions.githubusercontent.com",
    "https://gitlab.com",  # Self-hosted GitLab uses configured issuer
    "https://oidc.circleci.com",
    "https://agent.buildkite.com",
)


class FederationError(ValueError):
    """The presented OIDC token doesn't satisfy this org's
    federation policy. The caller turns this into a 401 with a
    generic message — never leaks claim details."""


# ---- subject pattern matching --------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a simple glob (* matches non-slash; / preserved) to
    a regex. We keep the language tiny — full regex would let an
    org admin author claim patterns that don't behave the way they
    expect (anchoring, escapes). Glob is what people actually
    write in CI claim allow-lists."""
    if not pattern:
        raise FederationError("subject pattern is required")
    # Escape regex meta-chars except '*'
    escaped = re.escape(pattern).replace(r"\*", "[^/]*")
    return re.compile(f"^{escaped}$")


def matches_subject(*, allowed_pattern: str, presented_sub: str) -> bool:
    """Does the presented OIDC ``sub`` match an allow-list pattern?

    Patterns use globs (*) per segment. Examples:
      'repo:acme/api:*'           — any branch/tag/PR for that repo
      'repo:acme/api:ref:refs/heads/main'  — only main branch
      'project_path:acme-org/*'   — any GitLab project under acme-org
    """
    if not presented_sub:
        return False
    return bool(_glob_to_regex(allowed_pattern).match(presented_sub))


# ---- federation config ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class FederationConfig:
    """Per-org config for OIDC token acceptance."""

    org_id: int
    issuer: str
    """Exact issuer URL we trust for this org. Must be one of
    KNOWN_ISSUERS or an explicitly-allowlisted self-hosted
    issuer."""

    allowed_subject_patterns: tuple[str, ...]
    """Glob patterns matched against the OIDC ``sub`` claim. At
    least one must match for the token to be accepted."""

    audience: str
    """The audience the org's CI is configured to mint tokens
    for. Caller verifies the token's ``aud`` claim matches —
    binds tokens to this platform install."""

    target_app_pattern: str = ""
    """Optional restriction: OIDC tokens from this issuer +
    subject can only deploy apps whose slug matches this glob.
    Empty = unrestricted within the org."""

    def __post_init__(self) -> None:
        if self.org_id <= 0:
            raise FederationError("org_id must be positive")
        if not self.issuer or not self.audience:
            raise FederationError(
                "issuer and audience are required"
            )
        if not self.allowed_subject_patterns:
            raise FederationError(
                "allowed_subject_patterns must not be empty (would "
                "accept any subject from this issuer; misconfig)"
            )


# ---- token claims --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class OidcClaims:
    """Verified claims from the presented OIDC JWT. The verifier
    upstream fills these in after signature + expiry checks."""

    iss: str
    aud: str
    sub: str
    exp_unix: int
    extra_claims: Mapping[str, str] = dataclasses.field(default_factory=dict)


# ---- exchange policy -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class FederatedDeploy:
    """The output of a successful exchange. Caller mints a
    short-lived deploy token bound to these constraints."""

    org_id: int
    issuer: str
    subject: str
    """Recorded on the audit log: ``actor.kind = ci_oidc`` with
    issuer + subject so an operator can trace 'this deploy came
    from main branch of acme/api on GitHub Actions'."""

    target_app_pattern: str
    """Empty = any app in the org; else the deploy token can only
    target apps matching this glob."""


def evaluate_exchange(
    *,
    config: FederationConfig,
    claims: OidcClaims,
    target_app_slug: str,
) -> FederatedDeploy:
    """Decide whether to accept ``claims`` as a federated deploy
    credential against ``config`` for ``target_app_slug``.

    Order of checks:
      1. Issuer matches config.issuer (already-verified upstream;
         we double-check so a misconfigured verifier can't slip).
      2. Audience matches config.audience.
      3. At least one allowed_subject_patterns matches.
      4. If target_app_pattern is set, target_app_slug matches it.

    Raises ``FederationError`` with a generic message on any
    failure. The audit log gets the structured details
    separately (caller writes them after success).
    """
    if claims.iss != config.issuer:
        raise FederationError("issuer not allowed for this org")

    if claims.aud != config.audience:
        raise FederationError("audience mismatch")

    if not any(
        matches_subject(allowed_pattern=p, presented_sub=claims.sub)
        for p in config.allowed_subject_patterns
    ):
        raise FederationError("subject not allowed by federation policy")

    if config.target_app_pattern:
        if not matches_subject(
            allowed_pattern=config.target_app_pattern,
            presented_sub=target_app_slug,
        ):
            raise FederationError(
                "target app not allowed by federation policy"
            )

    return FederatedDeploy(
        org_id=config.org_id,
        issuer=claims.iss,
        subject=claims.sub,
        target_app_pattern=config.target_app_pattern,
    )


# ---- well-known issuer JWKS URLs -----------------------------------


def jwks_url_for(issuer: str) -> str:
    """RFC 8414 OIDC discovery: ``<issuer>/.well-known/jwks.json``
    is the convention every issuer follows. Caller fetches this
    once + caches with the token's max-age.

    Returned even for unknown issuers — callers that allow
    self-hosted issuers (custom GitLab, etc.) construct the URL
    the same way."""
    if not issuer:
        raise FederationError("issuer is required")
    issuer_clean = issuer.rstrip("/")
    # GitHub Actions follows the standard convention.
    if issuer_clean == "https://token.actions.githubusercontent.com":
        return f"{issuer_clean}/.well-known/jwks"
    return f"{issuer_clean}/.well-known/jwks.json"
