"""
First-party session tokens: issuance, rotation, revocation (#147,
spec 12 §2.2 + §2.4).

The *cookie wiring* (HttpOnly / Secure / SameSite / domain scoping)
and the model/view layer are kept separate; this module is the
**policy** — pure functions over a small dataclass that decide what
each lifecycle event does to the session row.

Why pure-policy first:

- Refresh token rotation has subtle invariants (atomic swap, replay
  detection, race-window handling) that are easier to reason about
  in isolation than tangled with Django views + cookie helpers.
- The same logic feeds the GraphQL mutation that lists / revokes
  sessions, the middleware that consumes access tokens, and the
  background job that prunes expired rows.
- Tests run without Django setup.

Storage shape (the caller maps to model rows):

  - ``access_token_jti``     — opaque random id; the access JWT's
    ``jti`` claim. Lookup key when the middleware needs to revoke.
  - ``refresh_token_hash``   — SHA-256 of the refresh token. The
    raw token is returned to the caller exactly once and never
    stored. Hash-at-rest matches our deploy-token + invitation
    pattern.
  - ``rotated_at`` / ``revoked_at`` — timestamps for the audit
    trail and replay detection.
  - ``parent_jti``           — the session that issued this one
    (rotation chain). Replay of a parent triggers global revoke
    of the chain.

Replay handling: spec 12 §2.2 mandates that *use of a previously
rotated refresh token is treated as theft*. We don't just reject
the call — we revoke the entire rotation chain so the attacker
loses access along with the legitimate user (who can sign in fresh).
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

# Spec 12 §2.2 defaults — overridable in settings for shorter
# windows on high-security installs.
DEFAULT_ACCESS_TTL_SECONDS = 15 * 60          # 15 min
DEFAULT_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(prefix: str = "") -> str:
    # 32 bytes urlsafe → ~43 char string. ``prefix`` lets us tag
    # tokens in logs/dashboards (e.g. 'r_' for refresh, 'a_' for
    # access) without leaking what they are.
    return f"{prefix}{secrets.token_urlsafe(32)}"


@dataclasses.dataclass(slots=True)
class SessionRecord:
    """The persistent shape. Caller maps this 1:1 to a model row.

    Mutable on purpose — rotation and revocation flip fields in place
    so the caller can ``.save()`` the same object after a rotate or
    revoke call.
    """

    user_id: int
    access_token_jti: str
    refresh_token_hash: str
    issued_at: datetime
    expires_at: datetime
    refresh_expires_at: datetime
    last_used_at: datetime
    user_agent: str = ""
    ip_address: str = ""
    parent_jti: str = ""
    rotated_to_jti: str = ""
    revoked_at: datetime | None = None
    revoke_reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedTokens:
    """Returned by issue/rotate. The caller serializes to the cookie
    response *exactly once* — these are plaintext values, never
    re-derivable from the stored row."""

    access_token: str
    refresh_token: str
    record: SessionRecord


def issue(
    *,
    user_id: int,
    now: datetime,
    user_agent: str = "",
    ip_address: str = "",
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
    refresh_ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS,
    parent_jti: str = "",
) -> IssuedTokens:
    """Issue a fresh access + refresh token pair for ``user_id``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    jti = _new_token("a_")
    refresh = _new_token("r_")
    record = SessionRecord(
        user_id=user_id,
        access_token_jti=jti,
        refresh_token_hash=_hash(refresh),
        issued_at=now,
        expires_at=now + timedelta(seconds=access_ttl_seconds),
        refresh_expires_at=now + timedelta(seconds=refresh_ttl_seconds),
        last_used_at=now,
        user_agent=user_agent,
        ip_address=ip_address,
        parent_jti=parent_jti,
    )
    return IssuedTokens(access_token=jti, refresh_token=refresh, record=record)


def is_active(record: SessionRecord, *, now: datetime) -> bool:
    """A session is active iff not revoked, not rotated, refresh
    not yet expired."""
    if record.revoked_at is not None:
        return False
    if record.rotated_to_jti:
        return False
    return now < record.refresh_expires_at


class RefreshTokenInvalid(Exception):
    """Raised by :func:`rotate` when the presented refresh token
    can't be honored. The caller turns this into a 401 + clears
    cookies. ``replay`` is True when a *previously rotated* token
    was presented — caller should revoke the entire chain."""

    def __init__(self, *, replay: bool, reason: str):
        self.replay = replay
        self.reason = reason
        super().__init__(reason)


def rotate(
    *,
    presented_refresh_token: str,
    record: SessionRecord,
    now: datetime,
    user_agent: str = "",
    ip_address: str = "",
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
    refresh_ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS,
) -> IssuedTokens:
    """Atomic rotate.

    Order of checks matters — replay detection before expiry so a
    leaked token that was already rotated is flagged as theft, not
    'just expired'.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    presented_hash = _hash(presented_refresh_token)
    if presented_hash != record.refresh_token_hash:
        raise RefreshTokenInvalid(
            replay=False, reason="refresh token does not match record"
        )

    if record.rotated_to_jti:
        # The token matches but the record was already rotated —
        # somebody is replaying an old refresh. Spec 12 §2.2: treat
        # as theft. Caller revokes the chain.
        raise RefreshTokenInvalid(
            replay=True,
            reason="refresh token was previously rotated (replay)",
        )

    if record.revoked_at is not None:
        raise RefreshTokenInvalid(
            replay=False, reason="session has been revoked"
        )

    if now >= record.refresh_expires_at:
        raise RefreshTokenInvalid(
            replay=False, reason="refresh token has expired"
        )

    new = issue(
        user_id=record.user_id,
        now=now,
        user_agent=user_agent or record.user_agent,
        ip_address=ip_address or record.ip_address,
        access_ttl_seconds=access_ttl_seconds,
        refresh_ttl_seconds=refresh_ttl_seconds,
        parent_jti=record.access_token_jti,
    )
    # Mark the parent as rotated. The two writes (parent.rotated_to,
    # new.record) must commit together — caller wraps in a DB
    # transaction. If the transaction fails after this line, the
    # caller hasn't shown the token to the user yet.
    record.rotated_to_jti = new.record.access_token_jti
    record.last_used_at = now
    return new


def revoke(record: SessionRecord, *, now: datetime, reason: str = "") -> None:
    """Mark this single session revoked."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if record.revoked_at is not None:
        return  # idempotent — revoke twice is a no-op
    record.revoked_at = now
    record.revoke_reason = reason


def revoke_chain(
    leaf: SessionRecord,
    *,
    chain: list[SessionRecord],
    now: datetime,
    reason: str = "rotation chain compromised",
) -> int:
    """Revoke ``leaf`` and every ancestor in ``chain`` linked via
    ``parent_jti``. Returns count of revoked rows.

    Used after a replay detection: walk parents and forward
    descendants. Caller is responsible for assembling the chain
    via a query (we don't reach into the DB from this module)."""
    revoked_count = 0
    by_jti = {r.access_token_jti: r for r in chain}
    by_jti[leaf.access_token_jti] = leaf

    visited: set[str] = set()
    stack: list[str] = [leaf.access_token_jti]
    while stack:
        jti = stack.pop()
        if jti in visited:
            continue
        visited.add(jti)
        record = by_jti.get(jti)
        if record is None:
            continue
        if record.revoked_at is None:
            revoke(record, now=now, reason=reason)
            revoked_count += 1
        if record.parent_jti:
            stack.append(record.parent_jti)
        # forward chain: anything that points to this record as
        # its parent
        for r in chain:
            if r.parent_jti == jti:
                stack.append(r.access_token_jti)

    return revoked_count


def revoke_all_for_user(records: list[SessionRecord], *, now: datetime) -> int:
    """Sign-out-everywhere. Returns count of newly-revoked rows."""
    n = 0
    for r in records:
        if r.revoked_at is None:
            revoke(r, now=now, reason="sign-out everywhere")
            n += 1
    return n
