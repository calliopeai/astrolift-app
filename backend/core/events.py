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
import uuid
from collections.abc import Callable
from typing import Any

from core.request_context import get_request_id, get_trace_context
from core.tenancy import get_current_tenant

log = logging.getLogger("core.events")

# Resource kinds whose ``resource_id`` names a RegisteredApp — used to
# backfill ``registered_app_id`` when an app-scoped emit site didn't
# pass the FK explicitly (#1111). Kept narrow so a non-app resource
# with a colliding slug can't be mis-linked.
_APP_RESOURCE_KINDS = frozenset({"app", "registered_app"})


def _resolve_registered_app_id(
    resource_kind: str,
    resource_id: str,
    organization_id: int | None,
) -> int | None:
    """Best-effort lookup of the RegisteredApp PK an app-scoped event
    refers to, so the per-app events feed (which filters on the FK) is
    populated even when the emit site stamped only ``resource_kind`` +
    ``resource_id`` (#1111).

    ``resource_id`` is a slug at some emit sites and a guid at others,
    so both are tried; the org scope keeps a slug collision across
    tenants from mis-linking. Returns ``None`` (never raises) when the
    resource isn't an app or no row matches — the caller leaves the FK
    unset. Callers wrap this so an event write is never blocked on the
    lookup."""
    if organization_id is None or not resource_id:
        return None
    if (resource_kind or "").lower() not in _APP_RESOURCE_KINDS:
        return None

    from astrolift_registry.models import RegisteredApp

    qs = RegisteredApp.objects.filter(organization_id=organization_id, deleted_at__isnull=True)
    app_id = qs.filter(slug=resource_id).values_list("id", flat=True).first()
    if app_id is not None:
        return app_id
    # Only probe the guid column when the id parses as a UUID — the
    # field would otherwise raise on a slug string.
    try:
        uuid.UUID(str(resource_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return qs.filter(guid=resource_id).values_list("id", flat=True).first()


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
    severity: str = "info"
    """``info`` | ``warn`` | ``error``. Inferred at emit time by
    :func:`infer_event_severity` from ``payload`` + ``event_type``."""


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


# ---- severity inference --------------------------------------------
#
# Single source of truth for the severity heuristic used at emit time
# (Event.severity column, #540) and during the matching migration
# backfill. The mobile client applied the same rules at render time;
# moving the inference server-side lets the GraphQL query filter by
# severity without the client having to re-derive it.

_SEVERITY_VALUES = ("info", "warn", "error")


def infer_event_severity(
    event_type: str,
    payload: dict[str, Any] | None,
) -> str:
    """Return one of ``"info" | "warn" | "error"`` for ``(event_type, payload)``.

    Precedence (mirrors the mobile client's pre-#540 logic):

    1. ``payload.severity`` if it's one of the canonical values.
    2. ``payload.level`` if it's one of the canonical values.
    3. ``payload.status`` mapped: ``failed|error`` → ``error``,
       ``warned|degraded`` → ``warn``.
    4. ``event_type`` shape: ``.failed`` suffix or ``error`` substring
       (case-insensitive) → ``error``; ``.warned`` suffix or ``warn``
       substring → ``warn``.
    5. Default ``info``.
    """
    payload = payload or {}

    raw_sev = payload.get("severity")
    if isinstance(raw_sev, str) and raw_sev in _SEVERITY_VALUES:
        return raw_sev

    raw_level = payload.get("level")
    if isinstance(raw_level, str) and raw_level in _SEVERITY_VALUES:
        return raw_level

    raw_status = payload.get("status")
    if isinstance(raw_status, str):
        if raw_status in ("failed", "error"):
            return "error"
        if raw_status in ("warned", "degraded"):
            return "warn"

    et = (event_type or "").lower()
    if et.endswith(".failed") or "error" in et:
        return "error"
    if et.endswith(".warned") or "warn" in et:
        return "warn"

    return "info"


def _log_event(envelope: EventEnvelope) -> None:
    log.info(
        "event",
        extra={"event": dataclasses.asdict(envelope)},
    )


_writer: EventWriter = _log_event


def register_event_writer(writer: EventWriter) -> None:
    global _writer
    _writer = writer


# ---- subscriber registry -------------------------------------------
#
# Subscribers run *after* the writer persists the envelope. They are
# additive and side-effect-only: a subscriber that raises is logged
# and dropped, never propagated back to the emit caller. Used by the
# notification dispatcher (#476/#499) so push fan-out can be wired
# without bypassing the persistent event row write.
#
# Keep this list module-scoped (not per-class) so tests that swap a
# subscriber in can deterministically restore the previous list — the
# same shape as ``register_event_writer`` above.

EventSubscriber = Callable[[EventEnvelope], None]

_subscribers: list[EventSubscriber] = []


def register_event_subscriber(subscriber: EventSubscriber) -> None:
    """Add a subscriber called after every successful emit.

    Subscribers see the envelope *after* the writer has persisted it
    so a subscriber can safely look up the Event row by
    request_id/trace_id correlation. Subscribers that raise are
    logged at WARNING and dropped — never propagated back to the
    mutation that emitted the event.
    """
    if subscriber in _subscribers:
        # Idempotent — apps.ready may run twice in some test paths
        # (django.setup() + a per-app re-register in conftest).
        return
    _subscribers.append(subscriber)


def unregister_event_subscriber(subscriber: EventSubscriber) -> None:
    """Drop a previously-registered subscriber. No-op if not present."""
    if subscriber in _subscribers:
        _subscribers.remove(subscriber)


def _fanout_to_subscribers(envelope: EventEnvelope) -> None:
    for sub in list(_subscribers):
        try:
            sub(envelope)
        except Exception:  # noqa: BLE001 — fan-out must never fail emit
            log.warning(
                "event subscriber raised",
                exc_info=True,
                extra={"event_type": envelope.event_type},
            )


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

        org_id = organization_id or (tenant.organization_id if tenant else None)
        resource_kind = resource_kind or ""
        resource_id_str = str(resource_id) if resource_id is not None else ""

        # Backfill the app FK from the resource context when the emit
        # site didn't pass one (#1111). Without this an app-scoped event
        # (resource_kind=app, resource_id=<slug|guid>) never surfaces in
        # the per-app feed, which filters on ``registered_app_id``. The
        # lookup is best-effort — an event write must never fail on it.
        if registered_app_id is None:
            try:
                registered_app_id = _resolve_registered_app_id(resource_kind, resource_id_str, org_id)
            except Exception:  # noqa: BLE001 — never block an emit on the lookup
                log.warning("event app-fk backfill failed", exc_info=True)

        envelope = EventEnvelope(
            event_type=event_type,
            payload=payload or {},
            organization_id=org_id,
            team_id=team_id or (tenant.team_id if tenant else None),
            project_id=project_id or (tenant.project_id if tenant else None),
            registered_app_id=registered_app_id,
            actor_user_id=actor_user_id or (tenant.actor_user_id if tenant else None),
            resource_kind=resource_kind,
            resource_id=resource_id_str,
            request_id=request_id or "",
            trace_id=trace_id or "",
            severity=infer_event_severity(event_type, payload),
        )
        _writer(envelope)
        _fanout_to_subscribers(envelope)
        return envelope
