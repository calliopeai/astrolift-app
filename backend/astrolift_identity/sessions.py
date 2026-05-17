"""
Session lifecycle helpers and middleware.

Single source of truth for the (create-or-update) AstroliftSession
write path. Every authenticated request flows through
:func:`record_session`, which:

1. Finds-or-creates the sidecar row for the request's session key.
2. Stamps ``last_seen_at`` / ``last_seen_ip`` / ``last_seen_agent``
   (rate-limited per
   :data:`astrolift_identity.models.LAST_SEEN_WRITE_THROTTLE_SECONDS`).
3. Enforces ``MAX_SESSIONS_PER_CLIENT_KIND`` — when the new row would
   push the user above the per-kind quota, the oldest live session of
   that kind is auto-revoked + audit-logged.

The Constance entry for the quota is JSON-shaped so operators can
tune one knob per kind without redeploying. Default values live on
``DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND``.

The session_key → ClientKind binding is set at login time (the
auth1 / device-flow path stores it in ``request.session["client_kind"]``);
this module reads it back, falling back to ``WEB`` for legacy rows
that pre-date the field landing. That backfill-on-touch keeps the
migration simple — no data migration is needed for pre-existing
django_session rows.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from astrolift_identity.models import (
    DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND,
    LAST_SEEN_WRITE_THROTTLE_SECONDS,
    AstroliftSession,
    ClientKind,
    RevocationReason,
)

log = logging.getLogger(__name__)


SESSION_CLIENT_KIND_KEY = "client_kind"
"""Django-session key the login path writes into when issuing a
session for a non-browser client. The session-tracking middleware
reads it on the next authed request to backfill
``AstroliftSession.client_kind``. Browser logins don't need to set
it — the middleware defaults to ``WEB`` when the key is missing.
"""

SESSION_LABEL_KEY = "client_label"
"""Optional display label the client supplies at session-issue time
(``alice@laptop``, ``iPhone 17 Pro``). Surfaced verbatim in the
operator-facing sessions list."""


def _normalize_client_kind(raw: Any) -> str:
    """Coerce a session value to a valid ClientKind, defaulting to
    WEB when unknown/missing so a malformed legacy row never crashes
    the middleware."""
    if not isinstance(raw, str):
        return ClientKind.WEB.value
    lowered = raw.strip().lower()
    if lowered not in ClientKind.values:
        return ClientKind.WEB.value
    return lowered


def _client_ip(request: HttpRequest) -> str | None:
    """Best-effort IP — honours X-Forwarded-For if present, falls
    back to REMOTE_ADDR. Returns None on a malformed value so the
    column stays NULL rather than receiving garbage."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        candidate = xff.split(",")[0].strip()
        if candidate:
            return candidate
    return request.META.get("REMOTE_ADDR") or None


def _client_ua(request: HttpRequest) -> str:
    return (request.META.get("HTTP_USER_AGENT") or "")[:512]


def _quota_for(client_kind: str) -> int:
    """Per-kind max-sessions quota. Reads Constance first, falls back
    to the in-code default. A missing entry / parse error logs and
    falls back rather than raising — middleware must never fail the
    request because Constance is misconfigured."""
    try:
        from constance import config as constance_config

        raw = getattr(constance_config, "MAX_SESSIONS_PER_CLIENT_KIND", None)
    except Exception:  # noqa: BLE001 — constance is best-effort
        raw = None

    quotas: dict[str, int] = dict(DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, int) and v >= 0:
                quotas[k.strip().lower()] = v
    elif isinstance(raw, str):
        # Constance stores JSON-ish strings transparently; tolerate a
        # JSON payload as a convenience for operators who paste from a
        # config doc.
        import json

        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            for k, v in decoded.items():
                if isinstance(k, str) and isinstance(v, int) and v >= 0:
                    quotas[k.strip().lower()] = v

    return quotas.get(client_kind, DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND.get(client_kind, 5))


def _emit_audit(*, action: str, user_id: int | None, target_id: str | None, extra: dict[str, Any]) -> None:
    """Thin wrapper around the platform audit writer.

    Imported lazily so this module doesn't tie its import order to
    the audit subsystem (which itself imports tenant context).
    """
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=user_id,
            organization_id=None,
            action=action,
            decision="ALLOW",
            target_kind="astrolift_session",
            target_id=target_id,
            duration_ms=0,
            permissions=(),
            extra=extra or None,
        )
    )


def _enforce_quota(*, user_id: int, client_kind: str, current_session_id: int) -> int:
    """Evict the oldest live sessions of ``client_kind`` for ``user_id``
    so the surviving set fits inside the per-kind quota.

    Returns the count of evicted rows. Never evicts the row identified
    by ``current_session_id`` — the caller just touched/created it,
    and evicting it would race the request that's holding it.
    """
    quota = _quota_for(client_kind)
    if quota <= 0:
        return 0
    live_qs = (
        AstroliftSession.objects.filter(
            user_id=user_id,
            client_kind=client_kind,
            revoked_at__isnull=True,
            deleted_at__isnull=True,
        )
        .exclude(pk=current_session_id)
        .order_by("created_at")
    )
    live_ids = list(live_qs.values_list("pk", flat=True))
    # ``live_ids`` excludes the current row; quota counts INCLUDE it.
    overflow = max(0, len(live_ids) + 1 - quota)
    if overflow == 0:
        return 0
    evict_pks = live_ids[:overflow]
    now = timezone.now()
    evicted = 0
    for pk in evict_pks:
        row = AstroliftSession.all_objects.filter(pk=pk).first()
        if row is None:
            continue
        row.revoked_at = now
        row.revocation_reason = RevocationReason.QUOTA_EVICTION
        row.deleted_at = now
        row.save(
            update_fields=[
                "revoked_at",
                "revocation_reason",
                "deleted_at",
                "updated_at",
                "version",
            ]
        )
        _delete_underlying_django_session(row.session_key)
        _emit_audit(
            action="session.quota_evicted",
            user_id=user_id,
            target_id=str(row.guid),
            extra={
                "client_kind": client_kind,
                "quota": quota,
            },
        )
        evicted += 1
    return evicted


def _delete_underlying_django_session(session_key: str) -> None:
    """Hard-delete the ``django_session`` row so the cookie fails auth
    on the next request. Safe to call with an empty key (API-token
    sessions don't have one)."""
    if not session_key:
        return
    from django.contrib.sessions.models import Session

    Session.objects.filter(session_key=session_key).delete()


def record_session(request: HttpRequest) -> AstroliftSession | None:
    """Find-or-create the sidecar row for ``request``'s session.

    Returns the row (so callers in tests can assert on it). Returns
    ``None`` when the request isn't authed or doesn't have a session
    key — there's nothing to track in either case.

    Idempotent and rate-limited: a chatty client (the FE issues a
    query every few seconds) only writes ``last_seen_at`` once per
    :data:`LAST_SEEN_WRITE_THROTTLE_SECONDS`.
    """
    user = getattr(request, "user", None)
    if user is None or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return None
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None) if session else None
    if not session_key:
        return None

    now = timezone.now()
    client_kind = _normalize_client_kind(session.get(SESSION_CLIENT_KIND_KEY) if session else None)
    label = (session.get(SESSION_LABEL_KEY, "") if session else "") or ""
    if not isinstance(label, str):
        label = ""
    label = label[:200]

    expires_at = getattr(session, "get_expiry_date", lambda: None)()

    with transaction.atomic():
        row = AstroliftSession.all_objects.select_for_update().filter(session_key=session_key).first()
        created = False
        if row is None:
            row = AstroliftSession(
                user=user,
                session_key=session_key,
                organization=None,
                client_kind=client_kind,
                label=label,
                last_seen_at=now,
                last_seen_ip=_client_ip(request),
                last_seen_agent=_client_ua(request),
                expires_at=expires_at,
            )
            row.save()
            created = True
        else:
            # Re-bind to the current user if the cookie was reused for a
            # different account (a fresh login on the same browser cycles
            # the key, so this is defensive — covers admin impersonation
            # where the key persists across user swaps).
            updates: list[str] = []
            if row.user_id != user.pk:
                row.user = user
                updates.append("user")
            if row.deleted_at is not None or row.revoked_at is not None:
                # Re-issued after a soft-revoke — clear the revocation
                # state so listing surfaces this row as live again.
                row.deleted_at = None
                row.revoked_at = None
                row.revocation_reason = ""
                row.revoked_by = None
                updates += ["deleted_at", "revoked_at", "revocation_reason", "revoked_by"]
            if expires_at and row.expires_at != expires_at:
                row.expires_at = expires_at
                updates.append("expires_at")
            should_touch_last_seen = (
                row.last_seen_at is None
                or (now - row.last_seen_at).total_seconds() >= LAST_SEEN_WRITE_THROTTLE_SECONDS
            )
            if should_touch_last_seen:
                row.last_seen_at = now
                row.last_seen_ip = _client_ip(request)
                row.last_seen_agent = _client_ua(request)
                updates += ["last_seen_at", "last_seen_ip", "last_seen_agent"]
            if updates:
                row.save(update_fields=updates + ["updated_at", "version"])

    if created:
        _enforce_quota(
            user_id=user.pk,
            client_kind=row.client_kind,
            current_session_id=row.pk,
        )
    return row


def touch_last_seen(row: AstroliftSession) -> AstroliftSession:
    """Explicit-heartbeat path used by the ``heartbeatSession`` mutation.

    Bypasses the throttle — the client asked for a write, so we honor
    it. Still cheap (one update on a single row).
    """
    row.last_seen_at = timezone.now()
    row.save(update_fields=["last_seen_at", "updated_at", "version"])
    return row


def revoke_session(
    *,
    row: AstroliftSession,
    actor_user_id: int | None,
    reason: str = RevocationReason.USER_REVOKE,
) -> AstroliftSession:
    """Soft-delete a session row + drop the underlying Django session.

    Idempotent: revoking an already-revoked row is a no-op that still
    returns the row.
    """
    if row.revoked_at is not None or row.deleted_at is not None:
        return row
    now = timezone.now()
    row.revoked_at = now
    row.revocation_reason = reason
    row.deleted_at = now
    if actor_user_id is not None:
        from django.contrib.auth import get_user_model

        revoker = get_user_model().objects.filter(pk=actor_user_id).first()
        if revoker is not None:
            row.revoked_by = revoker
    row.save(
        update_fields=[
            "revoked_at",
            "revocation_reason",
            "deleted_at",
            "revoked_by",
            "updated_at",
            "version",
        ]
    )
    _delete_underlying_django_session(row.session_key)
    return row


# ---------------------------------------------------------------- middleware


class SessionTrackingMiddleware:
    """Stamp ``AstroliftSession`` rows on every authed request.

    Wraps the response cycle so the write happens *after* the view —
    that way a view that lazily resolves the session (the GraphQL
    endpoint does) populates ``request.session.session_key`` before we
    read it. Writing pre-view would miss any session created during
    the request itself (login mutations).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        try:
            record_session(request)
        except Exception:  # noqa: BLE001 — tracking must never fail the request
            log.warning("SessionTrackingMiddleware: record_session failed", exc_info=True)
        return response
