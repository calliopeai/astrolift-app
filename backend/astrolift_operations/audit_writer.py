"""
Persistent ``mutation_audit`` writer.

Replaces the log-only writer registered in ``core.mutations`` with one
that inserts a row into the ``AuditEvent`` table. Failures are
swallowed + logged so an audit-write hiccup never breaks the
underlying mutation.
"""

from __future__ import annotations

import logging

from core.mutations import AuditEntry

log = logging.getLogger("astrolift_operations.audit")


def write_audit_entry(entry: AuditEntry) -> None:
    from astrolift_operations.models import AuditEvent

    try:
        AuditEvent.objects.create(
            organization_id=entry.organization_id,
            actor_kind="user" if entry.actor_user_id else "system",
            actor_id=str(entry.actor_user_id) if entry.actor_user_id else "",
            action=entry.action,
            decision=entry.decision,
            target_kind=entry.target_kind or "",
            target_id=str(entry.target_id) if entry.target_id is not None else "",
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
