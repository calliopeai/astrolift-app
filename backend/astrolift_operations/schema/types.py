"""GraphQL types for Event, AuditEvent, WorkflowRun, Notification."""

from __future__ import annotations

import datetime as dt
from typing import Any

import strawberry

from astrolift_graphql import GUID
from core.schema.enums import ObservabilityPanelReason

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
    resource_kind: str
    """The kind of resource this event is about — e.g. ``app``, ``cluster``,
    ``workload``, ``managed_service``. Empty string when the emit site
    didn't stamp one. The frontend turns ``(resource_kind, resource_id)``
    into a deep link (see #434 scope B)."""

    resource_id: str
    """The identifier (slug or guid) of the resource named by
    ``resource_kind``. Empty when unknown."""

    severity: str
    """``info`` | ``warn`` | ``error``. Stamped at emit time by the
    writer using the same heuristic the mobile client previously
    applied at render time (#540) — explicit ``payload.severity`` /
    ``payload.level`` win, then ``payload.status`` mapping, then the
    ``event_type`` shape (``*.failed`` / ``*error*`` → ``error``;
    ``*.warned`` / ``*warn*`` → ``warn``); ``info`` otherwise."""


@strawberry.type(name="AstroliftEventPage")
class EventPageType:
    """Cursor-paginated event slice. ``next_cursor`` is null when the
    caller has reached the end of the stream.

    ``reason`` (#1111) lets the per-app events panel distinguish "no
    activity for this app yet" (``NO_DATA_YET``) from a populated feed
    (``OK``). Platform events have no provider/config dimension, so
    only those two values are used; ``reason`` is set on the first
    page (``after`` unset) — cursor continuations report ``OK``."""

    items: list[EventType]
    next_cursor: str | None
    reason: ObservabilityPanelReason


@strawberry.type(name="AstroliftAggregatedEvent")
class AggregatedEventType:
    """One bucket of repeat-event de-duplication (#434 scope A).

    The aggregator groups raw ``Event`` rows by
    ``(event_type, resource_kind, resource_id)`` within a sliding
    window. ``representative`` is the newest event in the bucket and
    carries the payload the UI surfaces; ``count`` / ``first_at`` /
    ``last_at`` round out the row so the operator can tell at a glance
    "this thing fired N times between X and Y".

    A bucket size of 1 (no duplicates in the window) still rolls up so
    the client can render every row uniformly — the badge just shows
    ``1`` (or hides when count == 1)."""

    representative: EventType
    count: int
    first_at: dt.datetime
    last_at: dt.datetime
    event_type: str
    resource_kind: str
    resource_id: str


@strawberry.type(name="AstroliftActivityItem")
class ActivityItemType:
    """One pre-shaped row for the dashboard activity feed (#435).

    The raw ``Event`` row carries IDs and a payload blob; the dashboard
    needs presentation-ready fields so the client doesn't have to know
    the payload schema for every emit site. Shaped fields here are
    derived in the resolver — see ``shape_activity_item``:

    * ``actor_display`` — user-friendly actor label
      (``user_full_name`` / ``email`` / ``"system"``).
    * ``action`` — verb extracted from ``event_type`` (e.g.
      ``deploy.completed`` → ``"completed"``).
    * ``target_kind`` / ``target_label`` — what the event happened to
      (e.g. app slug, cluster name) so the row reads like a sentence.
    * ``target_href`` — the resolved drill-down route into the rest of
      the app, or ``None`` when no canonical route exists for that
      event kind.

    Raw ``event_type`` + ``payload`` are still exposed so the UI can
    pick its own icon and so future surfaces (filters, export) don't
    need a backend round-trip."""

    id: GUID
    event_type: str
    action: str
    actor_display: str
    target_kind: str
    target_label: str
    target_href: str | None
    occurred_at: dt.datetime
    payload: JSON


@strawberry.type(name="AstroliftActivityPage")
class ActivityPageType:
    """Cursor-paginated activity slice. Same cursor format as
    ``AstroliftEventPage`` — base64-JSON of
    ``[occurred_at_iso, guid_str]``. ``next_cursor`` is null when
    the caller has reached the end of the stream."""

    items: list[ActivityItemType]
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
    """Redacted mutation context. Sensitive values (``secret``,
    ``password``, ``token``, ``api_key``, ``authorization``,
    ``private_key``, ``credential``) are replaced with the literal
    ``***redacted***`` sentinel before crossing the GraphQL boundary
    (#433). Resolvers must call :func:`scrub_payload` rather than
    handing back the raw JSONField."""

    before: JSON | None
    """Pre-mutation snapshot, when the originating mutation persisted
    one to ``data.before``. Null for mutations that don't carry a
    before-state (e.g. permission denials, create-shaped actions)."""

    after: JSON | None
    """Post-mutation snapshot, when the originating mutation persisted
    one to ``data.after``. Null when the mutation didn't snapshot."""


@strawberry.type(name="AstroliftAuditEventPage")
class AuditEventPageType:
    """Cursor-paginated audit slice. ``next_cursor`` is null when the
    caller has reached the end of the stream within the date bounds.

    Pairs with ``astroliftAuditEvents`` — same cursor encoding as the
    Event stream so a UI can use a single helper for both."""

    items: list[AuditEventType]
    next_cursor: str | None
    total_count: int | None
    """Best-effort total of matching rows across the active filter set.
    Null when the query opted out of the count (large ranges) — the UI
    falls back to ``next_cursor != null`` to decide whether to show a
    'load more' control."""


@strawberry.type(name="AstroliftAuditExport")
class AuditExportType:
    """Result of an ``exportAuditEvents`` mutation. Carries the
    pre-signed download URL plus enough metadata (row count, byte
    count, sha256) for the UI to surface verification + progress."""

    id: GUID
    format: str
    """``csv`` | ``ndjson``."""

    row_count: int
    byte_count: int
    sha256: str
    download_url: str
    """Token-gated URL the client GETs to retrieve the bytes. The
    token is single-use-traceable but re-downloadable inside the TTL
    window so operators can recover from a fumbled save."""

    expires_at: dt.datetime
    created_at: dt.datetime


@strawberry.type(name="AstroliftAuditRetention")
class AuditRetentionType:
    """The org's audit-log retention policy in days. Surfaces in the
    UI subtitle as 'Audit events retained for N days per compliance
    policy' — required for SOC2 surface (#433 scope D).

    Value sourced from the per-org ``Organization.audit_log_retention_days``
    column — the same field the org-settings page edits — so the audit
    and organization surfaces stay in agreement."""

    days: int
    """The retention window, in days. Always >= 1. Defaults to 90."""


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
        resource_kind=e.resource_kind or "",
        resource_id=e.resource_id or "",
        severity=getattr(e, "severity", None) or "info",
    )


def shape_activity_item(e) -> ActivityItemType:
    """Pre-shape one ``Event`` row for the dashboard activity feed.

    Returns ``AstroliftActivityItem`` with derived display fields so
    the dashboard client doesn't have to know payload shapes:

    * ``actor_display`` — picks the best available user-readable label
      from ``actor_user`` (full name → email → username), falling back
      to ``"system"`` for unattributed emits.
    * ``action`` — last segment of ``event_type`` (``deploy.completed``
      → ``"completed"``); the resource segment goes into
      ``target_kind`` when no explicit ``resource_kind`` was stamped.
    * ``target_label`` / ``target_href`` — derived from
      ``(event_type, payload, registered_app)``. App-scoped events get
      a slug-based label and deep link; cluster events route by
      ``resource_id`` GUID. Returns ``None`` for ``target_href`` when
      no canonical route exists so the UI can render the row as
      non-clickable.

    Caller is expected to ``select_related("actor_user",
    "registered_app")`` so this stays cheap when called in a loop.
    """
    event_type = e.event_type or ""
    resource_segment, _, action_segment = event_type.partition(".")
    actor_display = _actor_display(getattr(e, "actor_user", None))
    target_kind = (e.resource_kind or resource_segment or "").strip()
    target_label, target_href = _resolve_activity_target(
        event_type=event_type,
        resource_kind=target_kind,
        resource_id=e.resource_id or "",
        payload=e.payload or {},
        registered_app=getattr(e, "registered_app", None),
    )
    return ActivityItemType(
        id=GUID(str(e.guid)),
        event_type=event_type,
        action=action_segment or event_type,
        actor_display=actor_display,
        target_kind=target_kind,
        target_label=target_label,
        target_href=target_href,
        occurred_at=e.occurred_at,
        payload=e.payload or {},
    )


def _actor_display(user) -> str:
    """Best-effort display label for an ``Event.actor_user``.

    Order of preference: full name (first + last) → email → username
    → ``"system"`` when no user is attached. Returning a non-empty
    string in all cases keeps the dashboard row layout stable."""
    if user is None:
        return "system"
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = (first + " " + last).strip()
    if full:
        return full
    email = (getattr(user, "email", "") or "").strip()
    if email:
        return email
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username
    return "system"


def _resolve_activity_target(
    *,
    event_type: str,
    resource_kind: str,
    resource_id: str,
    payload: dict,
    registered_app,
) -> tuple[str, str | None]:
    """Map an event to ``(label, href)`` for the activity row.

    Routing rules:

    * ``deploy.*`` / ``deployment.*`` → ``/apps/<slug>/deployments/<id>``
      when both slug and deployment id are recoverable; otherwise
      falls back to ``/apps/<slug>`` if a slug exists.
    * ``cluster.*`` → ``/clusters/<id>`` keyed off ``resource_id`` or
      ``payload['cluster_id']``.
    * ``secret.*`` → ``/apps/<slug>/secrets`` when scoped to an app.
    * ``app.*`` / ``config.*`` / ``scale.*`` → ``/apps/<slug>``.
    * ``binding.*`` / ``managed_service.*`` → ``/services``.
    * ``preview.*`` / ``promotion.*`` / ``rollback.*`` / ``drift.*``
      route through their app's overview when a slug is known.

    The label always falls back to the raw ``resource_id`` so the row
    never renders an empty target column."""
    payload = payload or {}
    app_slug = (
        (getattr(registered_app, "slug", None) if registered_app else None)
        or payload.get("app_slug")
        or payload.get("slug")
        or ""
    )
    app_slug = str(app_slug).strip()

    label = ""
    href: str | None = None

    if event_type.startswith(("deploy.", "deployment.")):
        deployment_id = (
            payload.get("deployment_guid")
            or payload.get("deployment_id")
            or (resource_id if resource_kind.lower() == "deployment" else "")
        )
        deployment_id = str(deployment_id or "").strip()
        if app_slug and deployment_id:
            label = f"{app_slug} · deploy {deployment_id[:8]}"
            href = f"/apps/{app_slug}/deployments/{deployment_id}"
        elif app_slug:
            label = app_slug
            href = f"/apps/{app_slug}"
    elif event_type.startswith("cluster."):
        cluster_id = payload.get("cluster_guid") or payload.get("cluster_id") or resource_id or ""
        cluster_id = str(cluster_id or "").strip()
        label = (
            payload.get("cluster_name")
            or payload.get("name")
            or (cluster_id[:8] if cluster_id else "cluster")
        )
        if cluster_id:
            href = f"/clusters/{cluster_id}"
    elif event_type.startswith("secret."):
        if app_slug:
            label = f"{app_slug} · secrets"
            href = f"/apps/{app_slug}/secrets"
        else:
            label = payload.get("secret_name") or resource_id or "secret"
    elif event_type.startswith(("binding.", "managed_service.")):
        label = payload.get("binding_name") or payload.get("service_name") or resource_id or "managed service"
        href = "/services"
    elif event_type.startswith(("app.", "config.", "scale.")):
        if app_slug:
            label = app_slug
            href = f"/apps/{app_slug}"
        else:
            label = resource_id or resource_kind or "app"
    elif event_type.startswith(("preview.", "promotion.", "rollback.", "drift.", "environment.")):
        if app_slug:
            label = app_slug
            href = f"/apps/{app_slug}"
        else:
            label = resource_id or resource_kind or "app"
    elif event_type.startswith("service."):
        label = payload.get("service_name") or resource_id or "service"
        href = "/services"
    else:
        label = resource_id or resource_kind or event_type

    if not label:
        label = resource_id or event_type
    return (str(label), href)


def audit_to_type(a) -> AuditEventType:
    from astrolift_operations.audit_redaction import scrub_payload

    raw = a.data or {}
    # Redact at the GraphQL boundary so callers never see plaintext
    # secrets even if a mutation accidentally stuffed one onto the
    # extras dict. Scrubbing is keyed off field name, not value
    # heuristics — see ``audit_redaction``.
    scrubbed = scrub_payload(raw)
    before = scrubbed.get("before") if isinstance(scrubbed, dict) else None
    after = scrubbed.get("after") if isinstance(scrubbed, dict) else None
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
        data=scrubbed,
        before=before,
        after=after,
    )


def audit_export_to_type(e, *, download_url: str) -> AuditExportType:
    return AuditExportType(
        id=GUID(str(e.guid)),
        format=e.format,
        row_count=int(e.row_count or 0),
        byte_count=int(e.byte_count or 0),
        sha256=e.sha256 or "",
        download_url=download_url,
        expires_at=e.expires_at,
        created_at=e.created_at,
    )


@strawberry.type(name="AstroliftAppLogExport")
class AppLogExportType:
    """Result of an ``exportAstroliftAppLogs`` mutation (#483).

    Carries the pre-signed download URL plus enough metadata
    (line count, byte count, sha256) for the UI to surface
    verification + progress. ``truncated`` flags when the line
    cap was hit before the upstream stream ended — the operator
    should narrow the time range / level / regex filter and retry."""

    id: GUID
    format: str
    """``csv`` | ``ndjson`` | ``txt``."""

    status: str
    """``ready`` | ``expired`` | ``failed`` — mirrors the row's
    ``status`` field. ``failed`` rows carry ``error_message``."""

    row_count: int
    """Number of log lines included in the export."""

    byte_count: int
    sha256: str
    truncated: bool
    """True when the line cap was hit before the source stream ended.
    Surface a 'narrow the filter and retry' hint in the UI when set."""

    download_url: str
    """Token-gated URL the client GETs to retrieve the bytes. Single
    use is logged on first read; subsequent reads inside the TTL
    window are still allowed so the operator can recover from a
    fumbled save."""

    expires_at: dt.datetime
    created_at: dt.datetime
    error_message: str
    """Captured failure detail when status == ``failed``. Empty
    string on success."""


def app_log_export_to_type(e, *, download_url: str) -> AppLogExportType:
    return AppLogExportType(
        id=GUID(str(e.guid)),
        format=e.format,
        status=e.status,
        row_count=int(e.row_count or 0),
        byte_count=int(e.byte_count or 0),
        sha256=e.sha256 or "",
        truncated=bool((e.filters_snapshot or {}).get("truncated", False)),
        download_url=download_url,
        expires_at=e.expires_at,
        created_at=e.created_at,
        error_message=e.error_message or "",
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
    version: int
    """Optimistic-concurrency version (#497). Pass back as
    ``ifMatchVersion`` on ``updateAstroliftWebhookSubscription`` to
    detect a concurrent edit."""


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
        version=int(w.version or 0),
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


@strawberry.type(name="AstroliftAlertMute")
class AlertMuteType:
    """An active TTL-bounded silence on an AlertRule (#434 scope C).

    ``ttl_until`` is the auto-unmute instant. ``created_by`` is the
    display label of the user who issued the mute (best-effort —
    name → email → ``"system"``)."""

    id: GUID
    ttl_until: dt.datetime
    reason: str
    created_by: str


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
    active_mute: AlertMuteType | None
    """The longest-lived non-expired mute on this rule, or ``null`` when
    not muted. The delivery worker suppresses channel fan-out when this
    is non-null; the alert still produces an ``AlertEvent`` row so the
    incident timeline stays intact."""

    managed_service_id: GUID | None
    """GUID of the bound ManagedService instance — non-null for
    per-service predicate kinds (e.g. ``ses_bounce_rate``,
    ``ses_complaint_rate``). ``null`` for app/env/global rules that
    fire against PromQL or org-wide signals."""


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
    from astrolift_operations.alert_mute import active_mute_for_rule

    mute = active_mute_for_rule(r)
    # ``managed_service`` is a nullable FK; avoid a join when it's null.
    managed_service_id: GUID | None
    if r.managed_service_id is None:
        managed_service_id = None
    else:
        managed_service_id = GUID(str(r.managed_service.guid))
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
        active_mute=alert_mute_to_type(mute) if mute is not None else None,
        managed_service_id=managed_service_id,
    )


def alert_mute_to_type(m) -> AlertMuteType:
    return AlertMuteType(
        id=GUID(str(m.guid)),
        ttl_until=m.ttl_until,
        reason=m.reason or "",
        created_by=_actor_display(m.muted_by),
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


@strawberry.type(name="AstroliftNotificationDelivery")
class NotificationDeliveryType:
    """One per-send audit row from the NotificationDispatcher."""

    id: GUID
    event_type: str
    driver_name: str
    target_kind: str
    """``push`` | ``email`` | ``sms`` | ``webhook``."""

    target_address: str
    status: str
    """``delivered`` | ``queued`` | ``failed`` | ``unsupported`` |
    ``rate_limited`` | ``invalid_token``."""

    provider_message_id: str
    error: str
    retriable: bool
    payload_excerpt: str
    delivered_at: dt.datetime


def notification_delivery_to_type(d) -> NotificationDeliveryType:
    return NotificationDeliveryType(
        id=GUID(str(d.guid)),
        event_type=d.event_type or "",
        driver_name=d.driver_name or "",
        target_kind=d.target_kind or "",
        target_address=d.target_address or "",
        status=d.status,
        provider_message_id=d.provider_message_id or "",
        error=d.error or "",
        retriable=bool(d.retriable),
        payload_excerpt=d.payload_excerpt or "",
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


# ---- Notification preference + device registration (#476 / #499) ----


@strawberry.type(name="AstroliftDeviceRegistration")
class DeviceRegistrationType:
    """One push-receivable endpoint owned by the current user.

    Surfaced under ``/settings/devices`` for revoke. ``token_last_4``
    is the only part of the token we expose — the platform never
    re-shows the full registration token to the user (it's already
    in their device's secure storage; surfacing it would just be a
    leak surface)."""

    id: GUID
    platform: str
    label: str
    token_last_4: str
    driver: str
    registered_at: dt.datetime
    last_seen_at: dt.datetime | None


def device_registration_to_type(d) -> DeviceRegistrationType:
    token = d.device_token or ""
    return DeviceRegistrationType(
        id=GUID(str(d.guid)),
        platform=d.platform,
        label=d.label or "",
        token_last_4=token[-4:] if token else "",
        driver=d.driver,
        registered_at=d.registered_at,
        last_seen_at=d.last_seen_at,
    )


@strawberry.type(name="AstroliftNotificationPreference")
class NotificationPreferenceType:
    """One user × channel × event-kind preference row.

    ``id`` is the GUID of the underlying preference row. ``enabled``
    reflects the effective value: when no row exists, the API
    returns a synthetic preference with ``id=""`` and the platform
    default for that (channel, event_kind)."""

    id: GUID | None
    channel: str
    event_kind: str
    enabled: bool


def notification_preference_to_type(p) -> NotificationPreferenceType:
    return NotificationPreferenceType(
        id=GUID(str(p.guid)) if getattr(p, "guid", None) else None,
        channel=p.channel,
        event_kind=p.event_kind,
        enabled=p.enabled,
    )


# ---- Notification profile (#490) ------------------------------------


@strawberry.type(name="AstroliftNotificationProfile")
class NotificationProfileType:
    """The install's active notification driver + retention window.

    ``config`` is deliberately not exposed: the dispatcher's
    ``_resolve_secret`` fallback reads a plain-text value stashed
    alongside a ``*_secret_ref``, so the blob can carry live
    credentials. Operators write it; nothing reads it back out
    over the API."""

    id: GUID
    driver: str
    retention_delivery_days: int
    is_active: bool


def notification_profile_to_type(p) -> NotificationProfileType:
    return NotificationProfileType(
        id=GUID(str(p.guid)),
        driver=p.driver,
        retention_delivery_days=p.retention_delivery_days,
        is_active=bool(p.is_active),
    )


def synthetic_preference_type(*, channel: str, event_kind: str, enabled: bool) -> NotificationPreferenceType:
    """Construct a preference type for a defaulted row (no DB entry)."""
    return NotificationPreferenceType(
        id=None,
        channel=channel,
        event_kind=event_kind,
        enabled=enabled,
    )


@strawberry.type(name="AstroliftUserAlertSubscription")
class UserAlertSubscriptionType:
    id: GUID
    app_slug: str
    alert_kind: str
    channel: str
    enabled: bool


def user_alert_subscription_to_type(s) -> UserAlertSubscriptionType:
    return UserAlertSubscriptionType(
        id=GUID(str(s.guid)),
        app_slug=s.registered_app.slug,
        alert_kind=s.alert_kind,
        channel=s.channel,
        enabled=s.enabled,
    )


@strawberry.type
class ObservabilityRetentionHoldType:
    """A placed hold on a window of observability data (#1602).

    GUID id, never the integer PK, per repo law. `stream` is one of the
    four streams or `"*"`.
    """

    id: GUID
    stream: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    resource_kind: str
    resource_id: str
    reason: str
    released: bool
    """True once released. The row is soft-deleted rather than removed, so
    a post-incident review can still see the hold that was in force."""


def observability_retention_hold_to_type(
    row, *, released: bool | None = None
) -> ObservabilityRetentionHoldType:
    """Project a hold row onto its GraphQL type.

    ``released`` overrides what the row says. Needed because the default
    manager excludes soft-deleted rows, so the release mutation cannot read
    its own write back -- `refresh_from_db()` raises `DoesNotExist` on the
    row it just released. Passing the fact explicitly is honest; querying
    through an unfiltered manager just to observe a soft delete would
    reintroduce the deleted rows this manager exists to hide.
    """
    return ObservabilityRetentionHoldType(
        id=GUID(str(row.guid)),
        stream=row.stream,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        resource_kind=row.resource_kind or "",
        resource_id=row.resource_id or "",
        reason=row.reason or "",
        released=(row.deleted_at is not None) if released is None else released,
    )


@strawberry.type
class ObservabilityRetentionType:
    """The effective retention for one observability stream (#1602).

    One row per stream, resolved through the policy module rather than read
    off the column, so the surface reports what the sweep will actually
    enforce. Reading the column directly would show an operator the number
    they typed even when it is above the platform ceiling -- and then the
    sweep would evict on a different one.
    """

    stream: str
    days: int
    source: str
    """``org_override`` or ``platform_default`` -- so the UI can say whether
    this org has actually set anything, rather than presenting an inherited
    default as a choice."""

    billable_window_days: int
    """What the usage dashboard bills against."""

    warn_threshold_days: int
    """When to warn an admin before retention closes on a row."""
