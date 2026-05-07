"""
Platform event emission helper.

The Astrolift control plane keeps an append-only event log
(``Event`` model in P1.T5). All non-trivial state changes — workflow
phase transitions, deploy lifecycle steps, member changes — produce one
event row per fact. The event log feeds:

* the in-app activity feeds;
* the outbound webhook fan-out (``WebhookSubscription``);
* the event-driven Temporal listeners.

This module is the *only* sanctioned writer. Models do not insert
``Event`` rows directly — ``Event.emit()`` is the entry point so we
can:

* funnel everything through a single shape;
* add structured logging + tracing;
* swap implementations under test.

Until the ``Event`` model lands the writer logs to the JSON logger and
returns; once the model is wired in, ``register_event_writer`` swaps
in the persistent implementation.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

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

    Mirrors the shape of the persistent model that ships in P1.T5.
    """

    @staticmethod
    def emit(
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        organization_id: int | None = None,
        team_id: int | None = None,
        project_id: int | None = None,
        registered_app_id: int | None = None,
    ) -> EventEnvelope:
        tenant = get_current_tenant()
        envelope = EventEnvelope(
            event_type=event_type,
            payload=payload or {},
            organization_id=organization_id
            or (tenant.organization_id if tenant else None),
            team_id=team_id or (tenant.team_id if tenant else None),
            project_id=project_id or (tenant.project_id if tenant else None),
            registered_app_id=registered_app_id,
        )
        _writer(envelope)
        return envelope
