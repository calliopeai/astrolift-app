r"""
SCIM token policy (#273, spec 12 §3.3).

Pure-Python policy. Mirrors \`astrolift_lifecycle/deploy_tokens.py\`
shape but for SCIM 2.0 tokens — multi-token-per-org with
scopes (\`scim.users\` / \`scim.groups\`) and per-token rate
limiting.

* **Issue / rotate / verify** primitives. Hash-at-rest pattern
  with \`alft_scim_\` prefix and SHA-256 storage.
* **Scope check** — SCIM 2.0 endpoints gated by token scope.
* **Rate limit** — 60 req/min/token default; configurable.
* **Last-4 disclosure** — UI shows last 4 of plaintext for
  identification without leaking the secret.
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from collections.abc import Sequence
from enum import StrEnum


class ScimTokenError(ValueError):
    pass


# ---- token format --------------------------------------------------


SCIM_TOKEN_PREFIX = "alft_scim_"
"""Hash-at-rest invariant — token plaintext starts with this prefix
so accidental leak detectors (gitleaks, etc.) can find it."""

DEFAULT_RATE_LIMIT_PER_MINUTE = 60
"""Spec §3.3: 60 req/min default."""


# ---- scopes --------------------------------------------------------


class ScimScope(StrEnum):
    """Spec §3.3 vocabulary."""

    USERS = "scim.users"
    """Required for /Users endpoints (CRUD on User resources)."""

    GROUPS = "scim.groups"
    """Required for /Groups endpoints."""


_SCIM_2_0_USER_PATHS = ("/Users", "/Users/")
_SCIM_2_0_GROUP_PATHS = ("/Groups", "/Groups/")


def required_scope_for_path(*, path: str) -> ScimScope:
    """Map a SCIM 2.0 endpoint path to its required scope.

    Path matching is by prefix: ``/Users/{id}/Members`` requires
    ``scim.users`` because the parent resource is a User.
    """
    if path.startswith(_SCIM_2_0_USER_PATHS):
        return ScimScope.USERS
    if path.startswith(_SCIM_2_0_GROUP_PATHS):
        return ScimScope.GROUPS
    raise ScimTokenError(f"path {path!r} doesn't map to a SCIM 2.0 resource — expected /Users or /Groups")


def has_scope(
    *,
    token_scopes: Sequence[ScimScope],
    required: ScimScope,
) -> bool:
    """Token scope check. Tokens can hold either or both scopes."""
    return required in token_scopes


# ---- issue / verify -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedScimToken:
    r"""Output of \`issue_token\`. Plaintext is exposed ONCE; only
    the hash + last-4 is stored persistently."""

    plaintext: str
    """Returned to caller for one-time display. NEVER logged."""

    token_hash: str
    """SHA-256 of plaintext. What goes in the DB."""

    last_4: str
    """For UI identification ('alft_scim_...3a4f') without
    leaking."""


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def issue_token() -> IssuedScimToken:
    """Mint a fresh SCIM token. 32 bytes of urandom → ~43 base64
    chars after token_urlsafe."""
    body = secrets.token_urlsafe(32)
    plaintext = SCIM_TOKEN_PREFIX + body
    return IssuedScimToken(
        plaintext=plaintext,
        token_hash=_hash(plaintext),
        last_4=plaintext[-4:],
    )


@dataclasses.dataclass(frozen=True, slots=True)
class StoredScimToken:
    """Subset of the DB row the verifier needs. The actual model
    has more fields (created_at, created_by_user, etc.)."""

    token_id: int
    org_id: int
    token_hash: str
    scopes: tuple[ScimScope, ...]
    is_revoked: bool
    expires_at_unix: int | None


class VerifyResult(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    """Hash didn't match any token row."""

    REVOKED = "revoked"
    EXPIRED = "expired"
    INSUFFICIENT_SCOPE = "insufficient_scope"


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyDecision:
    accepted: bool
    result: VerifyResult
    token_id: int | None
    org_id: int | None


def verify_token(
    *,
    plaintext: str,
    required_scope: ScimScope,
    lookup_by_hash,
    now_unix: int,
) -> VerifyDecision:
    """Verify a SCIM bearer token.

    ``lookup_by_hash``: callable(token_hash) -> StoredScimToken | None.
    The view layer wraps the DB query.

    ``required_scope``: typically computed via
    ``required_scope_for_path(path=request.path)`` upstream.
    """
    if not plaintext.startswith(SCIM_TOKEN_PREFIX):
        # Format mismatch — treat as not-found rather than
        # leaking 'this looks like one of ours'. The verifier
        # response is generic.
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.NOT_FOUND,
            token_id=None,
            org_id=None,
        )

    record = lookup_by_hash(_hash(plaintext))
    if record is None:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.NOT_FOUND,
            token_id=None,
            org_id=None,
        )

    if record.is_revoked:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.REVOKED,
            token_id=record.token_id,
            org_id=record.org_id,
        )

    if record.expires_at_unix is not None and now_unix >= record.expires_at_unix:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.EXPIRED,
            token_id=record.token_id,
            org_id=record.org_id,
        )

    if not has_scope(
        token_scopes=record.scopes,
        required=required_scope,
    ):
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.INSUFFICIENT_SCOPE,
            token_id=record.token_id,
            org_id=record.org_id,
        )

    return VerifyDecision(
        accepted=True,
        result=VerifyResult.OK,
        token_id=record.token_id,
        org_id=record.org_id,
    )


# ---- token construction inputs -------------------------------------


def validate_scopes_input(*, scopes: Sequence[str]) -> tuple[ScimScope, ...]:
    """Operator passes a list of scope strings; validate + dedupe.
    Empty list rejected — a no-scope token can't access anything,
    almost certainly a bug."""
    if not scopes:
        raise ScimTokenError(f"scopes is required; at least one of {[s.value for s in ScimScope]}")
    out: list[ScimScope] = []
    seen: set[ScimScope] = set()
    for raw in scopes:
        try:
            scope = ScimScope(raw)
        except ValueError as exc:
            raise ScimTokenError(
                f"unknown SCIM scope {raw!r}; known: {[s.value for s in ScimScope]}"
            ) from exc
        if scope not in seen:
            seen.add(scope)
            out.append(scope)
    return tuple(out)


def validate_token_name(*, name: str) -> str:
    """UI requires a human-readable name per token (operator
    distinguishes 'okta scim' from 'azure ad scim'). Refuse
    empty / whitespace-only."""
    name = name.strip()
    if not name:
        raise ScimTokenError("token name is required")
    if len(name) > 64:
        raise ScimTokenError(f"token name {name!r} exceeds 64 chars")
    return name


# ---- rate limit window --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """Token's per-minute rate window. Token-level so a noisy
    consumer doesn't starve other tokens on the same org."""

    token_id: int
    requests_in_window: int
    window_started_unix: int
    rate_limit: int = DEFAULT_RATE_LIMIT_PER_MINUTE


def is_rate_limited(
    *,
    window: RateLimitWindow,
    now_unix: int,
) -> bool:
    """True when the token has hit its limit within the current
    1-minute window. Caller resets/advances the window when the
    minute rolls over."""
    if window.rate_limit <= 0:
        raise ScimTokenError(f"rate_limit must be positive, got {window.rate_limit}")
    # Window expired = caller will reset; not rate-limited
    if now_unix - window.window_started_unix >= 60:
        return False
    return window.requests_in_window >= window.rate_limit


def remaining_in_window(
    *,
    window: RateLimitWindow,
    now_unix: int,
) -> int:
    """For Retry-After header rendering."""
    if now_unix - window.window_started_unix >= 60:
        return window.rate_limit
    return max(0, window.rate_limit - window.requests_in_window)
