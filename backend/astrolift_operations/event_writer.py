"""
Persistent ``Event.emit`` writer.

Replaces the log-only writer registered in ``core.events`` with one
that inserts a row into the ``Event`` table. Failures are swallowed
+ logged: an event-write hiccup must not take down a mutation.
"""

from __future__ import annotations

import logging

from core.events import EventEnvelope

log = logging.getLogger("astrolift_operations.events")


def write_event_envelope(envelope: EventEnvelope) -> None:
    if envelope.organization_id is None:
        # Events require a tenant; tenantless emits are dropped (with
        # a log line so we can find them).
        log.warning(
            "dropping tenantless event",
            extra={"event_type": envelope.event_type, "payload": envelope.payload},
        )
        return

    from astrolift_operations.models import Event

    try:
        Event.objects.create(
            organization_id=envelope.organization_id,
            team_id=envelope.team_id,
            project_id=envelope.project_id,
            registered_app_id=envelope.registered_app_id,
            event_type=envelope.event_type,
            payload=envelope.payload,
            actor_user_id=envelope.actor_user_id,
            resource_kind=envelope.resource_kind or "",
            resource_id=envelope.resource_id or "",
            request_id=envelope.request_id or "",
            trace_id=envelope.trace_id or "",
            severity=envelope.severity or "info",
        )
    except Exception:  # event writes must not break mutations
        log.exception(
            "failed to persist event",
            extra={"event_type": envelope.event_type},
        )
