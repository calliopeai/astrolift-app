"""
Persistent ``mutation_audit`` writer.

Replaces the log-only writer registered in ``core.mutations`` with one
that inserts a row into the ``AuditEvent`` table. Failures are
swallowed + logged so an audit-write hiccup never breaks the
underlying mutation.
"""

from __future__ import annotations

import hashlib
import logging

from django.db import transaction

from core.mutations import AuditEntry

log = logging.getLogger("astrolift_operations.audit")


def _fit(value: object | None, max_length: int) -> str:
    """Fit caller-controlled audit identifiers without silent collisions."""
    text = "" if value is None else str(value)
    if len(text) <= max_length:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{text[: max_length - len(digest) - 1]}~{digest}"


def write_audit_entry(entry: AuditEntry) -> None:
    from astrolift_operations.models import AuditEvent

    try:
        # The savepoint is required when a mutation runs inside an outer
        # transaction: catching an insert error alone leaves that transaction
        # unusable. Audit is explicitly best-effort and must never poison the
        # state change it records.
        with transaction.atomic():
            AuditEvent.objects.create(
                organization_id=entry.organization_id,
                actor_kind="user" if entry.actor_user_id else "system",
                actor_id=_fit(entry.actor_user_id, 64),
                action=_fit(entry.action, 128),
                decision=_fit(entry.decision, 8),
                target_kind=_fit(entry.target_kind, 64),
                target_id=_fit(entry.target_id, 64),
                data={
                    "duration_ms": entry.duration_ms,
                    "permissions": list(entry.permissions),
                    "error_code": entry.error_code,
                    "error_message": entry.error_message,
                    **(entry.extra or {}),
                },
            )
    except Exception:
        log.exception(
            "failed to persist audit entry",
            extra={"action": entry.action, "decision": entry.decision},
        )
        return
    # Zentinelle evidence rides on the persisted row: a mapped, allowed audit
    # action is re-emitted as an ``AUDIT.*`` event for webhook fan-out.
    from astrolift_operations.zentinelle_bridge import bridge_audit_entry

    bridge_audit_entry(entry)
