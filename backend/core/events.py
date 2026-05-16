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
import datetime as dt
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


# Supply-chain gate payload shape (#313). Emitted as the ``payload``
# dict of a ``deploy.supply_chain.blocked`` Event by the
# PromoteDeploymentWorkflow when the policy resolved off
# ``RegisteredApp.security_policy_resolved`` short-circuits a promote.
#
# Defined here (alongside ``EventEnvelope``) so the workflow code and
# any future writers reference the same shape. Use ``as_payload()`` to
# obtain the JSON-serialisable dict that goes into the envelope's
# ``payload`` slot — datetimes serialise as ISO-8601 UTC strings so
# downstream consumers (webhook fan-out, activity feed) can render
# them without per-consumer parsing.
_SUPPLY_CHAIN_BLOCK_REASONS = frozenset(
    {
        "critical_cves",
        "missing_signature",
        "high_cve_threshold_exceeded",
    }
)


@dataclasses.dataclass(slots=True, frozen=True)
class SupplyChainBlockedPayload:
    """Frozen record of a supply-chain gate decision against a promote.

    All identifiers are external-stable (slug / GUID / image digest),
    never integer PKs — the payload is part of the public event
    contract and feeds outbound webhooks.

    ``cve_summary`` is None when the block path didn't run a scan
    (e.g. a missing-signature block can fire before scanning); when
    present it carries severity-bucketed counts as
    ``{"critical": N, "high": N, "medium": N, "low": N}``.
    """

    app_slug: str
    deployment_guid: str
    image_digest: str
    reason: str
    cve_summary: dict[str, int] | None
    signature_required: bool
    blocked_at: dt.datetime

    def __post_init__(self) -> None:
        if self.reason not in _SUPPLY_CHAIN_BLOCK_REASONS:
            raise ValueError(
                f"SupplyChainBlockedPayload.reason must be one of "
                f"{sorted(_SUPPLY_CHAIN_BLOCK_REASONS)!r}; got {self.reason!r}"
            )

    def as_payload(self) -> dict[str, Any]:
        """Return the JSON-serialisable dict for ``Event.emit(payload=...)``.

        Datetimes go out as ISO-8601 UTC strings ('Z' suffix when the
        input is timezone-aware UTC) so webhook consumers don't need
        Python-side parsers.
        """
        blocked_at_iso = self.blocked_at.isoformat()
        if self.blocked_at.tzinfo is not None and self.blocked_at.utcoffset() == dt.timedelta(0):
            # Normalise the UTC offset to the canonical 'Z' suffix for
            # interop with consumers that don't accept '+00:00'.
            blocked_at_iso = blocked_at_iso.replace("+00:00", "Z")
        return {
            "app_slug": self.app_slug,
            "deployment_guid": self.deployment_guid,
            "image_digest": self.image_digest,
            "reason": self.reason,
            "cve_summary": dict(self.cve_summary) if self.cve_summary is not None else None,
            "signature_required": bool(self.signature_required),
            "blocked_at": blocked_at_iso,
        }


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
