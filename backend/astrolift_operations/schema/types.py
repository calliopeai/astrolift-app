"""GraphQL types for Event, AuditEvent, WorkflowRun, Notification."""

from __future__ import annotations

import datetime as dt
from typing import Any

import strawberry

from astrolift_graphql import GUID

# Strawberry needs a concrete scalar for arbitrary JSON; we re-use
# the standard JSONScalar shipped with strawberry.
JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftEvent")
class EventType:
    id: GUID
    event_type: str
    payload: JSON
    organization_id: str | None
    team_id: str | None
    project_id: str | None
    registered_app_id: str | None
    occurred_at: dt.datetime


@strawberry.type(name="AstroliftEventPage")
class EventPageType:
    """Cursor-paginated event slice. ``next_cursor`` is null when the
    caller has reached the end of the stream."""

    items: list[EventType]
    next_cursor: str | None


@strawberry.type(name="AstroliftAuditEvent")
class AuditEventType:
    id: GUID
    organization_id: str | None
    occurred_at: dt.datetime
    actor_kind: str
    actor_id: str
    actor_display: str
    action: str
    decision: str
    target_kind: str
    target_id: str
    target_slug: str
    request_id: str
    data: JSON


@strawberry.type(name="AstroliftWorkflowRun")
class WorkflowRunType:
    id: GUID
    workflow_kind: str
    workflow_id: str
    run_id: str
    status: str
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    organization_id: str | None
    registered_app_id: str | None
    failure: JSON


def _maybe_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def event_to_type(e) -> EventType:
    return EventType(
        id=GUID(str(e.guid)),
        event_type=e.event_type,
        payload=e.payload or {},
        organization_id=_maybe_str(e.organization_id),
        team_id=_maybe_str(e.team_id),
        project_id=_maybe_str(e.project_id),
        registered_app_id=_maybe_str(e.registered_app_id),
        occurred_at=e.occurred_at,
    )


def audit_to_type(a) -> AuditEventType:
    return AuditEventType(
        id=GUID(str(a.guid)),
        organization_id=_maybe_str(a.organization_id),
        occurred_at=a.occurred_at,
        actor_kind=a.actor_kind,
        actor_id=a.actor_id or "",
        actor_display=a.actor_display or "",
        action=a.action,
        decision=a.decision,
        target_kind=a.target_kind or "",
        target_id=a.target_id or "",
        target_slug=a.target_slug or "",
        request_id=a.request_id or "",
        data=a.data or {},
    )


def workflow_run_to_type(w) -> WorkflowRunType:
    return WorkflowRunType(
        id=GUID(str(w.guid)),
        workflow_kind=w.workflow_kind,
        workflow_id=w.workflow_id,
        run_id=w.run_id,
        status=w.status,
        started_at=w.started_at,
        ended_at=w.ended_at,
        organization_id=_maybe_str(w.organization_id),
        registered_app_id=_maybe_str(w.registered_app_id),
        failure=w.failure or {},
    )


@strawberry.type(name="AstroliftWebhookSubscription")
class WebhookSubscriptionType:
    id: GUID
    url: str
    events: list[str]
    is_active: bool
    format: str
    """Outbound payload shape: ``generic`` | ``slack`` | ``discord``."""

    last_delivery_at: dt.datetime | None
    last_response_status: int | None
    failure_count: int
    secret_rotated_at: dt.datetime | None
    """When the secret was last rotated; null if never. UI surfaces
    this beside the rotate button."""

    created_at: dt.datetime


@strawberry.type(name="AstroliftNotification")
class NotificationType:
    id: GUID
    user_id: str
    kind: str
    title: str
    body: str
    link: str
    read_at: dt.datetime | None
    created_at: dt.datetime


def webhook_to_type(w) -> WebhookSubscriptionType:
    return WebhookSubscriptionType(
        id=GUID(str(w.guid)),
        url=w.url,
        events=list(w.events or []),
        is_active=w.is_active,
        format=w.format or "generic",
        last_delivery_at=w.last_delivery_at,
        last_response_status=w.last_response_status,
        failure_count=w.failure_count,
        secret_rotated_at=w.secret_rotated_at,
        created_at=w.created_at,
    )


def notification_to_type(n) -> NotificationType:
    return NotificationType(
        id=GUID(str(n.guid)),
        user_id=str(n.user_id) if n.user_id else "",
        kind=n.kind,
        title=n.title,
        body=n.body or "",
        link=n.link or "",
        read_at=n.read_at,
        created_at=n.created_at,
    )


@strawberry.type(name="AstroliftAlertRule")
class AlertRuleType:
    id: GUID
    name: str
    target: str
    """app | env | workload | global"""

    target_id: str
    severity: str
    """info | warn | critical"""

    predicate: JSON
    notify_channels: JSON
    is_active: bool
    organization_slug: str
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftAlertEvent")
class AlertEventType:
    id: GUID
    rule_id: GUID
    severity: str
    fired_at: dt.datetime
    resolved_at: dt.datetime | None
    acknowledged_at: dt.datetime | None
    summary: str
    detail: JSON


def alert_rule_to_type(r) -> AlertRuleType:
    return AlertRuleType(
        id=GUID(str(r.guid)),
        name=r.name,
        target=r.target,
        target_id=r.target_id or "",
        severity=r.severity,
        predicate=r.predicate or {},
        notify_channels=list(r.notify_channels or []),
        is_active=r.is_active,
        organization_slug=r.organization.slug,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def alert_event_to_type(e) -> AlertEventType:
    return AlertEventType(
        id=GUID(str(e.guid)),
        rule_id=GUID(str(e.rule.guid)),
        severity=e.severity,
        fired_at=e.fired_at,
        resolved_at=e.resolved_at,
        acknowledged_at=e.acknowledged_at,
        summary=e.summary or "",
        detail=e.detail or {},
    )


@strawberry.type(name="AstroliftAppMetricsPoint")
class AppMetricsPointType:
    """One row of the time-series body."""

    timestamp: dt.datetime
    request_rate: float
    error_rate: float
    latency_p95: float


@strawberry.type(name="AstroliftAppMetrics")
class AppMetricsType:
    """Golden-signal rollup for one app over the chosen window.

    ``time_range`` accepts: ``5m``, ``1h``, ``24h``, ``7d``, ``30d``.
    Aggregates derive from the cluster's
    Prometheus / OTel endpoint when wired; today the resolver
    falls back to deterministic synthetic data so the UI surface
    exists end-to-end. ``source`` reflects which path produced the
    row: ``prometheus`` | ``synthetic``.
    """

    app_slug: str
    time_range: str
    request_rate: float
    """Requests per second, averaged over the window."""

    error_rate: float
    """Fraction of requests that returned 5xx (0.0–1.0)."""

    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    deploy_count: int
    """Deployments started against the app in the window."""

    time_series: list[AppMetricsPointType]
    source: str
    """``prometheus`` | ``synthetic``."""


@strawberry.type(name="AstroliftWebhookDelivery")
class WebhookDeliveryType:
    """One persisted delivery attempt against a subscription (#426).

    Surfaced by ``astroliftWebhookDeliveries`` so the operator UI's
    expand-row can show last-N attempts with status + payload
    snippet. ``is_test`` separates synthetic probes from real fan-out
    so the UI can dim test rows."""

    id: GUID
    subscription_id: GUID
    event_type: str
    retry_attempt: int
    status_code: int | None
    latency_ms: int
    success: bool
    is_test: bool
    request_payload_excerpt: str
    response_body_excerpt: str
    error: str
    delivery_id: str
    delivered_at: dt.datetime


def webhook_delivery_to_type(d) -> WebhookDeliveryType:
    return WebhookDeliveryType(
        id=GUID(str(d.guid)),
        subscription_id=GUID(str(d.subscription.guid)),
        event_type=d.event_type or "",
        retry_attempt=int(d.retry_attempt or 1),
        status_code=d.status_code,
        latency_ms=int(d.latency_ms or 0),
        success=bool(d.success),
        is_test=bool(d.is_test),
        request_payload_excerpt=d.request_payload_excerpt or "",
        response_body_excerpt=d.response_body_excerpt or "",
        error=d.error or "",
        delivery_id=d.delivery_id or "",
        delivered_at=d.delivered_at,
    )


@strawberry.type(name="AstroliftWebhookTestResult")
class WebhookTestResultType:
    """One ad-hoc test delivery against a webhook subscription.

    Operators trigger this from the UI's 'Send test event' button.
    The platform synthesizes a ``webhook.test`` payload, signs it
    with the subscription's secret derivation key, and posts it
    synchronously so the operator gets an immediate response code
    + latency reading — distinct from the async delivery worker
    that handles real events.

    ``status_code`` is null when the request failed at the transport
    layer (DNS, TCP, TLS) before any HTTP exchange happened. The
    ``error`` field carries that diagnostic; ``duration_ms`` still
    reflects how long we waited before giving up."""

    subscription_id: GUID
    url: str
    delivered: bool
    status_code: int | None
    duration_ms: int
    response_body_excerpt: str
    error: str
    delivery_id: str
    timestamp: dt.datetime
