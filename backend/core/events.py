"""
Platform event emission helper.

The Astrolift control plane keeps an append-only event log
(``Event`` model in ``astrolift_operations``). All non-trivial state
changes — workflow phase transitions, deploy lifecycle steps, member
changes — produce one event row per fact. The event log feeds:

* the in-app activity feeds;
* the outbound webhook fan-out (``WebhookSubscription``);
* the event-driven Temporal listeners.

This module is the *only* sanctioned writer. Models do not insert
``Event`` rows directly — :meth:`Event.emit` is the entry point so we
can:

* funnel everything through a single shape;
* auto-capture request/trace context for correlation with logs;
* swap the persistence path under test.

The persistent writer registered by ``astrolift_operations.apps.ready``
inserts an ``Event`` row; if no writer is registered the envelope is
logged via the structured JSON logger and dropped.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from core.request_context import get_request_id, get_trace_context
from core.tenancy import get_current_tenant

log = logging.getLogger("core.events")


@dataclasses.dataclass(slots=True, frozen=True)
class EventEnvelope:
    event_type: str
    payload: dict[str, Any]
    organization_id: int | None
    team_id: int | None
    project_id: int | None
    registered_app_id: int | None
    actor_user_id: int | None = None
    resource_kind: str = ""
    resource_id: str = ""
    request_id: str = ""
    trace_id: str = ""


EventWriter = Callable[[EventEnvelope], None]


def _log_event(envelope: EventEnvelope) -> None:
    log.info(
        "event",
        extra={"event": dataclasses.asdict(envelope)},
    )


_writer: EventWriter = _log_event


def register_event_writer(writer: EventWriter) -> None:
    global _writer
    _writer = writer


class Event:
    """Namespace holder so call sites read ``Event.emit(...)``.

    Mirrors the shape of the persistent ``astrolift_operations.Event``
    model and auto-populates request/trace context from the
    request-scoped contextvars.
    """

    @staticmethod
    def emit(
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        resource_kind: str | None = None,
        resource_id: str | int | None = None,
        actor_user_id: int | None = None,
        organization_id: int | None = None,
        team_id: int | None = None,
        project_id: int | None = None,
        registered_app_id: int | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> EventEnvelope:
        tenant = get_current_tenant()

        if request_id is None:
            request_id = get_request_id() or ""
        if trace_id is None:
            trace = get_trace_context()
            trace_id = trace.trace_id if trace is not None else ""

        envelope = EventEnvelope(
            event_type=event_type,
            payload=payload or {},
            organization_id=organization_id or (tenant.organization_id if tenant else None),
            team_id=team_id or (tenant.team_id if tenant else None),
            project_id=project_id or (tenant.project_id if tenant else None),
            registered_app_id=registered_app_id,
            actor_user_id=actor_user_id or (tenant.actor_user_id if tenant else None),
            resource_kind=resource_kind or "",
            resource_id=str(resource_id) if resource_id is not None else "",
            request_id=request_id or "",
            trace_id=trace_id or "",
        )
        _writer(envelope)
        return envelope
