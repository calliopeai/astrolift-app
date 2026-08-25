"""Mint and exchange the cross-domain session handoff (#1631).

Two functions and a rejection enum. The security properties live here
rather than in a view, because the auth host and the control plane are
different callers and a check that lives in one caller is a check the
other does not have.

Rejections are a single opaque outcome to the caller by design: an
exchange endpoint that distinguishes "no such token" from "expired" from
"wrong audience" tells an attacker which of those it got wrong. The reason
is returned for the audit log and the server-side log, never for the HTTP
body.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import secrets
from typing import Any

from django.db import transaction
from django.utils import timezone

from astrolift_lifecycle.models.domain_handoff import (
    HANDOFF_TTL,
    DomainSessionHandoff,
)

# 32 bytes of urandom, urlsafe-encoded. The browser carries this in a query
# string, so it must be URL-safe; it is an opaque lookup id and not a
# credential the domain could forge, since only its hash is stored.
_TOKEN_BYTES = 32


class HandoffRejection(enum.StrEnum):
    """Why an exchange failed. For the audit trail, not the response body."""

    UNKNOWN_TOKEN = "unknown_token"
    EXPIRED = "expired"
    ALREADY_CONSUMED = "already_consumed"
    WRONG_AUDIENCE = "wrong_audience"
    DOMAIN_INACTIVE = "domain_inactive"


@dataclasses.dataclass(frozen=True, slots=True)
class HandoffIdentity:
    """The verified identity a successful exchange yields."""

    subject: str
    email: str
    hostname: str


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def mint_domain_handoff(
    *,
    custom_domain: Any,
    subject: str,
    email: str = "",
) -> str:
    """Create a handoff for ``custom_domain`` and return the plaintext once.

    The plaintext is returned and never stored, so this is the only moment
    it exists. Callers put it in the redirect URL and drop it.

    ``subject`` is the IdP-verified identity. Passing an unverified value
    here would make the whole chain an open session-minting endpoint, which
    is why the auth host must call this only after its own callback has
    completed.
    """
    if not (subject or "").strip():
        # Not defensive: a blank subject would mint a valid grant for
        # nobody, and the domain's proxy would set a session for that
        # nobody rather than refusing.
        raise ValueError("a handoff needs a verified subject")

    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    DomainSessionHandoff.objects.create(
        custom_domain=custom_domain,
        hostname=(custom_domain.hostname or "").strip().rstrip(".").lower(),
        token_hash=_hash(plaintext),
        subject=subject.strip(),
        email=(email or "").strip(),
        expires_at=timezone.now() + HANDOFF_TTL,
    )
    return plaintext


def consume_domain_handoff(
    plaintext: str,
    *,
    hostname: str,
) -> tuple[HandoffIdentity | None, HandoffRejection | None]:
    """Spend a handoff and return the identity it grants.

    Returns ``(identity, None)`` on success and ``(None, reason)`` on every
    failure. Never raises for a bad token: the caller is an HTTP endpoint
    on the auth path, and an exception there is a 500 that tells an
    attacker more than a uniform refusal does.

    Single use is a **conditional UPDATE**, not a read then a write::

        UPDATE ... SET consumed_at = now() WHERE id = %s AND consumed_at IS NULL

    Two concurrent redirects can both pass a ``SELECT``-then-``save()``
    check and both get a session, and a single-use token whose second use
    succeeds is not single-use. The database decides, once, and zero rows
    affected is the replay.

    Order matters too. The audience and expiry are checked **before** the
    consume, so a token aimed at the wrong host is not silently burned:
    otherwise any domain able to guess a token could spend a legitimate
    user's grant and lock them out of their own login -- a denial of
    service dressed as a security check.
    """
    want = (hostname or "").strip().rstrip(".").lower()
    row = (
        DomainSessionHandoff.objects.filter(token_hash=_hash(plaintext or ""))
        .select_related("custom_domain")
        .first()
    )
    if row is None:
        return None, HandoffRejection.UNKNOWN_TOKEN
    if row.hostname != want:
        return None, HandoffRejection.WRONG_AUDIENCE
    if row.consumed_at is not None:
        return None, HandoffRejection.ALREADY_CONSUMED
    if row.expires_at <= timezone.now():
        return None, HandoffRejection.EXPIRED
    if not getattr(row.custom_domain, "is_active", False):
        # A deactivated domain must stop minting sessions immediately
        # rather than at the end of the TTL of tokens already out.
        return None, HandoffRejection.DOMAIN_INACTIVE

    with transaction.atomic():
        claimed = DomainSessionHandoff.objects.filter(
            pk=row.pk,
            consumed_at__isnull=True,
        ).update(consumed_at=timezone.now())

    if not claimed:
        # Lost the race. The other request has the session; this one gets
        # nothing, which is the correct outcome for both.
        return None, HandoffRejection.ALREADY_CONSUMED

    return (
        HandoffIdentity(subject=row.subject, email=row.email, hostname=row.hostname),
        None,
    )


def purge_expired_handoffs() -> int:
    """Delete spent and expired rows; returns how many went.

    A hard delete, and the one place in the app where that is right: this
    is not a business object with a history worth keeping, it is a spent
    credential, and the table is write-heavy on the login path. Soft
    deletion here would grow an index on the hot path forever.
    """
    now = timezone.now()
    deleted, _ = DomainSessionHandoff.objects.filter(expires_at__lte=now).delete()
    return deleted
