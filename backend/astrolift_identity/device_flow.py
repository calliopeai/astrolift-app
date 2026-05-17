"""Device-flow lifecycle primitives (#475).

Pure-ish business logic: secret minting, hashing, state transitions,
token issuance + rotation. The view layer in
:mod:`astrolift_identity.device_flow_views` is a thin HTTP shell over
this.

Wire contract (CLI consumer in
``astrolift-cli/internal/auth/auth.go``):

* ``POST /api/cli/v1/auth/start`` →
  ``{session_id, login_url, poll_interval_seconds, expires_in_seconds}``
* ``POST /api/cli/v1/auth/complete`` body ``{session_id}`` →
  - ``200`` + ``{access_token, refresh_token, expires_at}`` on success
  - ``202`` (empty body) while pending
  - ``410`` / ``404`` on expired / unknown / denied
* ``POST /api/cli/v1/auth/refresh`` body ``{refresh_token}`` →
  - ``200`` + new pair on success
  - ``401`` / ``410`` on replay / expired

Token shape:

* Access bearer is an ``alft_at_…`` token (the existing API-token
  scheme). The middleware authorizing it is the one already in
  ``astrolift_identity.middleware``, so device-flow-issued tokens
  authenticate every GraphQL + REST endpoint without a second
  bearer scheme.
* Refresh bearer is an opaque ``alft_rt_…`` secret. Single-use:
  rotated on every refresh, hash stored on
  :class:`DeviceFlowSession`. Replay (presenting an old hash after
  rotation) returns ``410`` and invalidates the session.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import secrets
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from astrolift_identity.api_tokens import (
    DEFAULT_SCOPES,
)
from astrolift_identity.api_tokens import (
    mint_token as mint_api_token,
)

if TYPE_CHECKING:  # pragma: no cover
    from astrolift_identity.models import DeviceFlowSession


# Default lifetimes. Kept here (not on the model) so tests can monkey-
# patch a shorter clock without DB migration churn.
SESSION_TTL = dt.timedelta(minutes=10)
"""How long a pending device-flow row stays approvable."""

ACCESS_TOKEN_TTL = dt.timedelta(hours=1)
"""How long the issued ``alft_at_`` bearer is valid before the CLI
must refresh."""

REFRESH_TOKEN_TTL = dt.timedelta(days=30)
"""How long the rotating refresh-token chain stays valid."""

POLL_INTERVAL_SECONDS = 2
"""Default poll cadence advertised to the CLI on /start."""

MIN_POLL_INTERVAL = dt.timedelta(seconds=2)
"""Per-session floor on /complete cadence. Polls landing inside this
window return ``slow_down`` (HTTP 429)."""

REFRESH_TOKEN_PREFIX = "alft_rt_"
"""Plaintext prefix on the rotating refresh secret. Pairs with
``alft_at_`` (access) and lets accidental-leak detectors flag the
right shape."""


# ---- secret minting --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    plaintext: str
    token_hash: str
    last_4: str


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def mint_refresh_token() -> IssuedRefreshToken:
    """Mint a fresh ``alft_rt_…`` opaque secret."""
    body = secrets.token_urlsafe(32)
    plaintext = REFRESH_TOKEN_PREFIX + body
    return IssuedRefreshToken(
        plaintext=plaintext,
        token_hash=_hash(plaintext),
        last_4=plaintext[-4:],
    )


def _mint_session_guid() -> str:
    """Mint the public ``session_id`` returned to the CLI.

    We don't reuse the row's ``guid`` because the value is logged
    by the CLI and surfaced in the browser ``login_url`` — keeping
    it distinct from the internal guid keeps the public identifier
    rotateable without renaming the primary public key.
    """
    return secrets.token_urlsafe(32)


# ---- helpers for the view layer -------------------------------------


def normalize_client_label(label: str | None, *, fallback: str = "") -> str:
    """Trim + length-cap operator-provided labels.

    ``fallback`` lets the view default to the User-Agent or
    ``cli`` when the caller didn't send a label.
    """
    if label is None:
        return (fallback or "")[:128]
    cleaned = label.strip()
    if not cleaned:
        return (fallback or "")[:128]
    return cleaned[:128]


def normalize_client_kind(kind: str | None) -> str:
    """Coerce ``client_kind`` to the known vocabulary.

    Unknown kinds default to ``cli`` — they're a forensic field, not
    a security boundary, so we accept-and-normalize rather than
    reject.
    """
    if not kind:
        return "cli"
    k = kind.strip().lower()
    if k in {"cli", "mobile", "browser", "ide"}:
        return k
    return "cli"


# ---- lifecycle transitions ------------------------------------------


def _is_expired(session: DeviceFlowSession, *, now: dt.datetime) -> bool:
    return session.expires_at <= now


def mark_expired_if_needed(session: DeviceFlowSession, *, now: dt.datetime | None = None) -> bool:
    """Flip a pending row to expired when its deadline has passed.

    Returns True when the transition fired. Idempotent: a row already
    in ``expired`` / ``consumed`` / ``denied`` is left alone.
    """
    if session.state != session.STATE_PENDING:
        return False
    now = now or timezone.now()
    if not _is_expired(session, now=now):
        return False
    session.state = session.STATE_EXPIRED
    session.save(update_fields=["state", "updated_at", "version"])
    return True


def approve_session(
    session: DeviceFlowSession,
    *,
    user,
    organization=None,
    now: dt.datetime | None = None,
) -> str | None:
    """Move a pending row to approved.

    Returns ``None`` on success; an error code on rejection
    (``"expired"`` / ``"already_terminal"``).
    """
    now = now or timezone.now()
    if mark_expired_if_needed(session, now=now):
        return "expired"
    if session.state != session.STATE_PENDING:
        return "already_terminal"
    session.state = session.STATE_APPROVED
    session.approved_user = user
    session.organization = organization
    session.approved_at = now
    session.save(
        update_fields=[
            "state",
            "approved_user",
            "organization",
            "approved_at",
            "updated_at",
            "version",
        ]
    )
    return None


def deny_session(session: DeviceFlowSession, *, now: dt.datetime | None = None) -> str | None:
    """Move a pending row to denied. Idempotent on ``denied``;
    rejects any other terminal state with ``"already_terminal"``."""
    now = now or timezone.now()
    if mark_expired_if_needed(session, now=now):
        return "expired"
    if session.state == session.STATE_DENIED:
        return None
    if session.state != session.STATE_PENDING:
        return "already_terminal"
    session.state = session.STATE_DENIED
    session.denied_at = now
    session.save(update_fields=["state", "denied_at", "updated_at", "version"])
    return None


# ---- issuance + refresh ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedCredentials:
    """Plaintext credentials returned to the CLI exactly once."""

    access_token: str
    refresh_token: str
    access_token_expires_at: dt.datetime
    refresh_token_expires_at: dt.datetime


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionResult:
    """Result of a single ``/complete`` poll.

    ``status`` mirrors the wire shape:

    * ``"pending"`` → HTTP 202
    * ``"issued"`` → HTTP 200 with ``credentials``
    * ``"expired"`` / ``"denied"`` → HTTP 410
    * ``"unknown"`` → HTTP 404
    * ``"slow_down"`` → HTTP 429
    """

    status: str
    credentials: IssuedCredentials | None = None


def _consume_session_locked(
    session: DeviceFlowSession,
    *,
    now: dt.datetime,
) -> IssuedCredentials:
    """Mint the access + refresh pair and flip state to consumed.

    Caller MUST hold a row-level lock on ``session`` (the view does
    this via ``select_for_update``). Single-use: a second call would
    short-circuit at the state check upstream.
    """
    from astrolift_identity.models import ApiToken

    minted_access = mint_api_token()
    minted_refresh = mint_refresh_token()
    access_expires = now + ACCESS_TOKEN_TTL
    refresh_expires = now + REFRESH_TOKEN_TTL

    api_token_row = ApiToken.objects.create(
        user=session.approved_user,
        organization=session.organization,
        name=session.client_label or session.client_kind or "device-flow",
        token_hash=minted_access.token_hash,
        token_last_4=minted_access.last4,
        scopes=list(DEFAULT_SCOPES),
        expires_at=access_expires,
    )

    session.api_token = api_token_row
    session.state = session.STATE_CONSUMED
    session.consumed_at = now
    session.refresh_token_hash = minted_refresh.token_hash
    session.refresh_token_last_4 = minted_refresh.last_4
    session.refresh_token_expires_at = refresh_expires
    session.access_token_expires_at = access_expires
    session.save(
        update_fields=[
            "api_token",
            "state",
            "consumed_at",
            "refresh_token_hash",
            "refresh_token_last_4",
            "refresh_token_expires_at",
            "access_token_expires_at",
            "updated_at",
            "version",
        ]
    )

    return IssuedCredentials(
        access_token=minted_access.plaintext,
        refresh_token=minted_refresh.plaintext,
        access_token_expires_at=access_expires,
        refresh_token_expires_at=refresh_expires,
    )


def _rotate_refresh_locked(
    session: DeviceFlowSession,
    *,
    now: dt.datetime,
) -> IssuedCredentials:
    """Mint a fresh access + refresh pair on a consumed session.

    Used by ``/refresh``: the prior refresh secret is invalidated by
    the very act of replacing ``refresh_token_hash``. A request
    presenting the prior plaintext after rotation will hash-miss on
    the next ``/refresh`` and return 410. The prior ``ApiToken`` is
    revoked so a stolen access bearer doesn't outlive its refresh.
    """
    from astrolift_identity.models import ApiToken

    # Revoke the prior access bearer atomically — chain-of-trust:
    # holding the previous refresh DOES NOT entitle you to keep the
    # previous access token beyond the refresh moment.
    if session.api_token_id:
        ApiToken.objects.filter(pk=session.api_token_id).update(is_revoked=True)

    minted_access = mint_api_token()
    minted_refresh = mint_refresh_token()
    access_expires = now + ACCESS_TOKEN_TTL
    refresh_expires = now + REFRESH_TOKEN_TTL

    api_token_row = ApiToken.objects.create(
        user=session.approved_user,
        organization=session.organization,
        name=session.client_label or session.client_kind or "device-flow",
        token_hash=minted_access.token_hash,
        token_last_4=minted_access.last4,
        scopes=list(DEFAULT_SCOPES),
        expires_at=access_expires,
    )

    session.api_token = api_token_row
    session.refresh_token_hash = minted_refresh.token_hash
    session.refresh_token_last_4 = minted_refresh.last_4
    session.refresh_token_expires_at = refresh_expires
    session.access_token_expires_at = access_expires
    session.save(
        update_fields=[
            "api_token",
            "refresh_token_hash",
            "refresh_token_last_4",
            "refresh_token_expires_at",
            "access_token_expires_at",
            "updated_at",
            "version",
        ]
    )

    return IssuedCredentials(
        access_token=minted_access.plaintext,
        refresh_token=minted_refresh.plaintext,
        access_token_expires_at=access_expires,
        refresh_token_expires_at=refresh_expires,
    )


# ---- create + poll + refresh entry points (used by the view) --------


def create_session(
    *,
    client_label: str = "",
    client_kind: str = "cli",
    user_agent: str = "",
    client_ip: str | None = None,
    now: dt.datetime | None = None,
):
    """Create a fresh ``DeviceFlowSession`` row and return the public
    ``session_id`` alongside it.

    The view turns this into the ``POST /api/cli/v1/auth/start``
    response by adding the ``login_url``.
    """
    from astrolift_identity.models import DeviceFlowSession

    now = now or timezone.now()
    session_id = _mint_session_guid()
    # Normalize ``client_kind`` first so the label-fallback below
    # picks up the canonical (lowercased + validated) value rather
    # than whatever the caller sent verbatim.
    kind = normalize_client_kind(client_kind)
    label = normalize_client_label(client_label, fallback=kind)

    row = DeviceFlowSession.objects.create(
        client_label=label,
        client_kind=kind,
        user_agent=(user_agent or "")[:512],
        client_ip=client_ip,
        state=DeviceFlowSession.STATE_PENDING,
        expires_at=now + SESSION_TTL,
        session_guid=session_id,
    )
    return row, session_id


def lookup_session_for_complete(session_id: str):
    """Resolve a polling client's ``session_id`` to its row.

    Returns ``None`` when the id is empty or doesn't match any row.
    Caller is expected to ``select_for_update`` when transitioning.
    """
    from astrolift_identity.models import DeviceFlowSession

    if not session_id or not isinstance(session_id, str):
        return None
    return DeviceFlowSession.all_objects.filter(session_guid=session_id).first()


def poll_complete(
    session_id: str,
    *,
    now: dt.datetime | None = None,
) -> CompletionResult:
    """Resolve a polling client's ``session_id`` to a status.

    Wraps the row lookup + state transition in a single transaction
    so two simultaneous polls can't both observe ``approved`` and
    both mint credentials.
    """
    from astrolift_identity.models import DeviceFlowSession

    now = now or timezone.now()
    with transaction.atomic():
        row = DeviceFlowSession.all_objects.select_for_update().filter(session_guid=session_id).first()
        if row is None:
            return CompletionResult(status="unknown")

        # Per-session poll rate limit. A repeat poll inside the
        # interval returns ``slow_down`` (HTTP 429) so the CLI's
        # ``PollLoginUntil`` backs off. Skip the floor on the very
        # first poll (no prior ``polled_at``).
        if row.polled_at is not None and now - row.polled_at < MIN_POLL_INTERVAL:
            # Don't update polled_at on a rate-limited poll —
            # otherwise a tight loop would keep extending the
            # window forever and never observe the real state.
            return CompletionResult(status="slow_down")

        # Lazy expiry: anyone arriving at an expired-but-still-
        # pending row flips it to ``expired`` here.
        mark_expired_if_needed(row, now=now)

        # Stamp the poll *before* we branch — the timestamp is
        # what the rate limiter reads next time, and we want to
        # record the attempt regardless of outcome.
        row.polled_at = now
        row.save(update_fields=["polled_at", "updated_at", "version"])

        if row.state == DeviceFlowSession.STATE_PENDING:
            return CompletionResult(status="pending")
        if row.state == DeviceFlowSession.STATE_DENIED:
            return CompletionResult(status="denied")
        if row.state == DeviceFlowSession.STATE_EXPIRED:
            return CompletionResult(status="expired")
        if row.state == DeviceFlowSession.STATE_CONSUMED:
            # The CLI saw 200 already; a second /complete after
            # the first consume should NOT re-issue. Returning
            # ``expired`` is the right hint for "this session is
            # done" without leaking 'someone already polled'.
            return CompletionResult(status="expired")
        if row.state == DeviceFlowSession.STATE_APPROVED:
            # Approved + not yet consumed → mint and flip to
            # consumed under the row lock. No second caller can
            # race in here because they're queued on
            # select_for_update.
            creds = _consume_session_locked(row, now=now)
            return CompletionResult(status="issued", credentials=creds)

        # Unreachable in practice — guard against schema drift.
        return CompletionResult(status="unknown")


def refresh_credentials(
    refresh_plaintext: str,
    *,
    now: dt.datetime | None = None,
) -> CompletionResult:
    """Rotate the refresh secret + reissue the access token.

    Status codes:

    * ``"issued"`` (200) → new pair returned in ``credentials``
    * ``"unknown"`` (401) → hash didn't match any session, or
      prefix is wrong
    * ``"expired"`` (410) → session is past its refresh TTL or
      not in a consumable state
    """
    from astrolift_identity.models import DeviceFlowSession

    now = now or timezone.now()
    if not refresh_plaintext or not refresh_plaintext.startswith(REFRESH_TOKEN_PREFIX):
        return CompletionResult(status="unknown")

    digest = _hash(refresh_plaintext)

    with transaction.atomic():
        row = DeviceFlowSession.all_objects.select_for_update().filter(refresh_token_hash=digest).first()
        if row is None:
            # Hash miss → either the token never existed or it's
            # already been rotated away. We treat both as
            # ``unknown`` so the CLI re-runs ``astro auth login``
            # rather than guessing about replay vs. theft.
            return CompletionResult(status="unknown")

        # Refresh works only on consumed sessions (the one place
        # where a refresh hash should live). Soft-deleted rows
        # short-circuit.
        if row.deleted_at is not None:
            return CompletionResult(status="expired")
        if row.state != DeviceFlowSession.STATE_CONSUMED:
            return CompletionResult(status="expired")
        if row.refresh_token_expires_at is not None and row.refresh_token_expires_at <= now:
            # The refresh chain hit its TTL — invalidate the
            # session entirely so a future ``/refresh`` with the
            # same plaintext doesn't keep returning 410-but-still-
            # alive. Clearing the hash also makes the chain dead
            # to any caller who somehow held onto the plaintext.
            from astrolift_identity.models import ApiToken

            if row.api_token_id:
                ApiToken.objects.filter(pk=row.api_token_id).update(is_revoked=True)
            row.refresh_token_hash = ""
            row.refresh_token_last_4 = ""
            row.save(
                update_fields=[
                    "refresh_token_hash",
                    "refresh_token_last_4",
                    "updated_at",
                    "version",
                ]
            )
            return CompletionResult(status="expired")

        creds = _rotate_refresh_locked(row, now=now)
        return CompletionResult(status="issued", credentials=creds)


# ---- URL building ----------------------------------------------------


def build_login_url(*, session_id: str, request) -> str:
    """Construct the browser ``login_url`` returned by /start.

    Honours ``settings.APP_BASE_URL`` when set (production) and
    falls back to the request's absolute URI in dev. The path is
    served by :func:`device_flow_approval_view` and includes
    enough context (the session id) for the user to verify they're
    approving the right flow.
    """
    from django.conf import settings as dj_settings

    path = f"/{dj_settings.BASE_URL}cli/auth/device/{session_id}/"
    base = (dj_settings.APP_BASE_URL or "").rstrip("/")
    if base:
        return base + path
    return request.build_absolute_uri(path)


__all__ = [
    "ACCESS_TOKEN_TTL",
    "CompletionResult",
    "IssuedCredentials",
    "MIN_POLL_INTERVAL",
    "POLL_INTERVAL_SECONDS",
    "REFRESH_TOKEN_PREFIX",
    "REFRESH_TOKEN_TTL",
    "SESSION_TTL",
    "approve_session",
    "build_login_url",
    "create_session",
    "deny_session",
    "lookup_session_for_complete",
    "mark_expired_if_needed",
    "mint_refresh_token",
    "poll_complete",
    "refresh_credentials",
]
