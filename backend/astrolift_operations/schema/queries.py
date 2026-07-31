"""Read-only queries for the operations app."""

from __future__ import annotations

import datetime as dt
import hashlib
from datetime import timedelta

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, PageType, clamp_limit, encode_cursor, keyset_page, search_q
from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    DeviceRegistration,
    Event,
    Notification,
    NotificationChannel,
    NotificationPreference,
    UserAlertSubscription,
    WebhookDelivery,
    WebhookSubscription,
    WorkflowRun,
    default_enabled,
)
from astrolift_operations.schema.types import (
    ActivityPageType,
    AggregatedEventType,
    AlertEventType,
    AlertRuleType,
    AppMetricsPointType,
    AppMetricsType,
    AuditEventPageType,
    AuditEventType,
    AuditRetentionType,
    DeviceRegistrationType,
    EventPageType,
    EventType,
    NotificationPreferenceType,
    NotificationType,
    UserAlertSubscriptionType,
    WebhookDeliveryType,
    WebhookSubscriptionType,
    WorkflowRunType,
    alert_event_to_type,
    alert_rule_to_type,
    audit_to_type,
    device_registration_to_type,
    event_to_type,
    notification_preference_to_type,
    notification_to_type,
    shape_activity_item,
    synthetic_preference_type,
    user_alert_subscription_to_type,
    webhook_delivery_to_type,
    webhook_to_type,
    workflow_run_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import get_current_tenant


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Read resolvers over org-owned rows MUST treat None as
    deny-by-default ("no rows"), never as "all rows" (#1042 / #1183).

    ``@tenant_scoped()`` only asserts a tenant context exists; it does
    NOT filter any queryset. Every resolver has to add the org
    constraint itself or it leaks cross-org.
    """
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


_TIME_RANGE_SECONDS = {
    "5m": 5 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}
_TIME_RANGE_BUCKETS = {
    "5m": 12,  # one point per 25s
    "1h": 12,  # one point per 5m
    "24h": 24,  # one point per hour
    "7d": 28,  # one point per 6h
    "30d": 30,  # one point per day
}


@strawberry.type
class AstroliftAppUptimePoint:
    checked_at: dt.datetime
    is_up: bool
    latency_ms: int
    status_code: int | None


@strawberry.type
class AstroliftAppUptime:
    """Synthetic-uptime rollup for one app (#uptime). ``is_up`` is the latest
    probe verdict (null until the first probe); ``uptime_pct`` + ``recent``
    drive the OBSERVE badge / graph."""

    is_up: bool | None
    last_checked_at: dt.datetime | None
    uptime_pct: float
    total_checks: int
    window_hours: int
    recent: list[AstroliftAppUptimePoint]


def _events_qs(
    *,
    event_type: str | None = None,
    severity: str | None = None,
    app_slug: str | None = None,
    search: str | None = None,
):
    """Filtered, unordered platform-event stream for the caller's org.

    Shared by the raw list field, the aggregator, and both paginated
    siblings so none of them can disagree about what an event row
    is. Ordering is deliberately not applied — ``keyset_page``
    imposes it from the seek key, and the two list callers apply
    ``-occurred_at`` themselves.

    Scopes the BASE queryset to the caller's org (#1183): without
    this, the unfiltered Event stream leaked every org's events —
    the app_slug path scoped its own JOIN but the default view did
    not. org_id None → deny-by-default (matches nothing).
    """
    org_id = _caller_org_id()
    if org_id is None:
        return Event.objects.none()
    qs = Event.objects.filter(organization_id=org_id)
    if event_type:
        qs = qs.filter(event_type=event_type)
    if severity:
        qs = qs.filter(severity=severity)
    if app_slug:
        qs = _filter_by_app_slug(qs, app_slug)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "event_type",
                "resource_kind",
                "resource_id",
                "registered_app__slug",
            )
        )
    return qs


def _webhook_subscriptions_qs(*, app_slug: str | None, search: str | None = None):
    """Filtered, unordered webhook subscriptions for the caller's org.

    Shared by the list field and its paginated sibling so the two
    can never disagree about which hooks are visible. Ordering is
    left to the caller / ``keyset_page``.

    Scope to the caller's org (#1183): WebhookSubscription owns an
    organization FK. Without it, the app_slug branch matched a
    same-slug app in any tenant and the org-wide branch listed
    every tenant's global hooks. org_id None → deny-by-default.
    """
    org_id = _caller_org_id()
    if org_id is None:
        return WebhookSubscription.objects.none()
    qs = WebhookSubscription.objects.filter(organization_id=org_id)
    if app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    else:
        qs = qs.filter(registered_app__isnull=True)
    if search:
        qs = qs.filter(search_q(search, "url"))
    return qs


def _webhook_deliveries_qs(*, subscription_id: GUID, search: str | None = None):
    """Filtered, unordered delivery attempts for one subscription.

    Tenant scoping rides on the subscription lookup — the org
    clause on that fetch is what makes a sibling-org
    ``subscription_id`` read as "no deliveries" rather than leaking
    another tenant's fan-out history. org_id None → deny-by-default.
    """
    org_id = _caller_org_id()
    if org_id is None:
        return WebhookDelivery.objects.none()
    sub = WebhookSubscription.objects.filter(
        guid=str(subscription_id),
        organization_id=org_id,
        deleted_at__isnull=True,
    ).first()
    if sub is None:
        return WebhookDelivery.objects.none()
    qs = WebhookDelivery.objects.filter(subscription=sub).select_related("subscription")
    if search:
        qs = qs.filter(search_q(search, "event_type", "delivery_id", "error"))
    return qs


def _alert_rules_qs(
    *,
    target: str | None,
    target_id: str | None,
    active_only: bool,
    search: str | None = None,
):
    """Filtered, unordered alert rules for the caller's org.

    Shared by the list field and its paginated sibling. Ordering is
    left to the caller / ``keyset_page``.

    Scope to the caller's org (#1183): AlertRule owns a non-null
    organization FK. Without it, every tenant's rules (predicates,
    notify channels) were listed to all. org_id None →
    deny-by-default.
    """
    org_id = _caller_org_id()
    if org_id is None:
        return AlertRule.objects.none()
    qs = AlertRule.objects.select_related("organization").filter(organization_id=org_id)
    if active_only:
        qs = qs.filter(is_active=True)
    if target:
        qs = qs.filter(target=target)
    if target_id:
        qs = qs.filter(target_id=target_id)
    if search:
        qs = qs.filter(search_q(search, "name", "target_id"))
    return qs


def _alert_events_qs(
    *,
    rule_id: GUID | None,
    unresolved_only: bool,
    search: str | None = None,
):
    """Filtered, unordered alert-firing history for the caller's org.

    Shared by the list field and its paginated sibling. Ordering is
    left to the caller / ``keyset_page``.

    Scope to the caller's org (#1183): without it, any tenant's
    firing history (summaries + detail payloads) was visible to
    all. Scope through the owning rule's org — consistent with the
    existing rule__guid filter — so an event surfaces only when its
    rule belongs to the caller. org_id None → deny-by-default.
    """
    org_id = _caller_org_id()
    if org_id is None:
        return AlertEvent.objects.none()
    qs = AlertEvent.objects.select_related("rule").filter(rule__organization_id=org_id)
    if rule_id is not None:
        qs = qs.filter(rule__guid=str(rule_id))
    if unresolved_only:
        qs = qs.filter(resolved_at__isnull=True)
    if search:
        qs = qs.filter(search_q(search, "summary", "rule__name"))
    return qs


@strawberry.type
class OperationsQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_uptime(
        self,
        info: Info,
        app_slug: str,
        window_hours: int = 24,
    ) -> AstroliftAppUptime | None:
        """Uptime state + recent history for one app in the caller's org.

        Returns null for an unknown/foreign-org slug. ``recent`` is oldest->
        newest (sparkline order), capped at 120 points."""
        tenant = get_current_tenant()
        if tenant is None:
            return None
        from astrolift_operations.models import AppUptimeResult
        from astrolift_registry.models import RegisteredApp

        app = RegisteredApp.objects.filter(
            slug=app_slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return None

        window = max(1, min(int(window_hours), 168))
        since = timezone.now() - timedelta(hours=window)
        base = AppUptimeResult.objects.filter(registered_app=app, deleted_at__isnull=True)
        window_qs = base.filter(checked_at__gte=since)
        total = window_qs.count()
        up = window_qs.filter(is_up=True).count()
        latest = base.order_by("-checked_at").first()
        recent = list(base.order_by("-checked_at")[:120])
        recent.reverse()
        return AstroliftAppUptime(
            is_up=(latest.is_up if latest else None),
            last_checked_at=(latest.checked_at if latest else None),
            uptime_pct=(round(100.0 * up / total, 2) if total else 0.0),
            total_checks=total,
            window_hours=window,
            recent=[
                AstroliftAppUptimePoint(
                    checked_at=r.checked_at,
                    is_up=r.is_up,
                    latency_ms=r.latency_ms,
                    status_code=r.status_code,
                )
                for r in recent
            ],
        )

    @strawberry.field(
        deprecation_reason=("Caps at 500 rows with no way to reach the 501st. Use astroliftEventsPage.")
    )
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events(
        self,
        info: Info,
        limit: int = 100,
        event_type: str | None = None,
        severity: str | None = None,
        app_slug: str | None = None,
    ) -> list[EventType]:
        qs = _events_qs(
            event_type=event_type,
            severity=severity,
            app_slug=app_slug,
        ).order_by("-occurred_at")
        return [event_to_type(e) for e in qs[: max(1, min(limit, 500))]]

    @strawberry.field(
        deprecation_reason=(
            "Caps at 500 buckets folded from a bounded 10k-row scan, so "
            "activity older than the scan window is unreachable. Use "
            "astroliftEventsAggregatedPage."
        )
    )
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events_aggregated(
        self,
        info: Info,
        limit: int = 100,
        event_type: str | None = None,
        severity: str | None = None,
        app_slug: str | None = None,
        aggregate_window_seconds: int = 300,
    ) -> list[AggregatedEventType]:
        """Roll up the raw event stream into ``(event_type,
        resource_kind, resource_id)`` buckets over a sliding window.

        Why this exists: a flapping pod can emit hundreds of identical
        ``workload.unhealthy`` events in a minute; the raw stream
        buries the genuinely new incidents underneath. The aggregator
        groups consecutive identical emissions so the operator sees
        ``workload.unhealthy ×42`` as a single row.

        ``aggregate_window_seconds`` is the *bucket break gap*: events
        within this many seconds of the previous member roll into the
        same bucket. Crossing the gap opens a new bucket. Default 300s
        (5 minutes) matches Prometheus' default alert grouping window.

        Bucket boundaries are tracked per ``(event_type, resource_kind,
        resource_id)`` so unrelated event streams never collide. Events
        with no ``resource_id`` group by ``event_type`` alone.
        """
        capped_limit = max(1, min(limit, 500))
        # Pull a generous source window so the aggregator has enough
        # raw rows to fill ``capped_limit`` buckets even when most events
        # collapse 10:1.
        scan_cap = min(capped_limit * 50, 10_000)
        qs = _events_qs(
            event_type=event_type,
            severity=severity,
            app_slug=app_slug,
        ).order_by("-occurred_at")
        rows = list(qs[:scan_cap])
        return _aggregate_events(
            rows,
            window_seconds=max(1, aggregate_window_seconds),
            limit=capped_limit,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events_aggregated_page(
        self,
        info: Info,
        limit: int = 50,
        after: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        app_slug: str | None = None,
        search: str | None = None,
        aggregate_window_seconds: int = 300,
    ) -> PageType[AggregatedEventType]:
        """Cursor-paginated rollup of the raw event stream (#1235).

        Replaces ``astroliftEventsAggregated``, which folded a bounded
        10k-row scan and then sliced: anything older than that scan was
        unreachable no matter how the operator filtered.

        The cursor is a position in the RAW stream — ``(occurred_at,
        guid)``, the same seek key and token format as
        ``astroliftEventsPage`` — because buckets are folds, not table
        rows, and have no column of their own to seek on. ``limit``
        still counts *buckets*: the walk consumes exactly the raw rows
        that fold into the first ``limit`` buckets and parks the cursor
        on the last one consumed, so every raw event is counted in
        exactly one bucket across the whole walk.

        The trade-off that buys that guarantee: a burst straddling a
        page boundary is reported as two adjacent buckets (``×30`` then
        ``×12``) instead of one ``×42``. Folding the whole stream first
        would avoid it and defeat the pagination.

        ``total_count`` is the number of matching RAW events, not
        buckets — the bucket count isn't knowable without folding
        everything, and "1,204 events" is the number the operator wants
        beside a grouped table anyway.
        """
        page_size = clamp_limit(limit)
        # Same 50:1 headroom the list field uses: enough raw rows to
        # open ``page_size`` buckets even when events collapse hard.
        scan_cap = min(page_size * 50, 10_000)
        window = max(1, aggregate_window_seconds)
        raw = keyset_page(
            _events_qs(
                event_type=event_type,
                severity=severity,
                app_slug=app_slug,
                search=search,
            ),
            cursor=after,
            limit=scan_cap,
            max_limit=scan_cap,
            sort_field="occurred_at",
        )

        rows = raw.rows
        folded = _fold_events(rows, window_seconds=window)
        if len(folded) > page_size:
            # Bucket ``page_size`` (0-indexed) is the first one that
            # doesn't fit. Every row before it belongs to a bucket we
            # ARE returning, so cut there and re-fold — the counts must
            # reflect only the rows this page consumed.
            consumed = folded[page_size]["open_index"]
            rows = rows[:consumed]
            folded = _fold_events(rows, window_seconds=window)
            last = rows[-1]
            next_cursor = encode_cursor(last.occurred_at, last.guid)
        else:
            next_cursor = raw.next_cursor

        return PageType(
            items=[_bucket_to_type(b) for b in folded],
            next_cursor=next_cursor,
            total_count=raw.total_count,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events_page(
        self,
        info: Info,
        limit: int = 100,
        after: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        app_slug: str | None = None,
        search: str | None = None,
    ) -> EventPageType:
        """Cursor-paginated event stream.

        ``after`` is the opaque cursor returned by the previous page;
        omit it to start from the newest event. Cursor is base64-JSON
        of ``[occurred_at_iso, guid_str]`` so the (occurred_at, guid)
        composite is the seek key — guid is a UUIDv7 so the secondary
        sort is also time-ordered, eliminating tie-break churn. The
        walk itself is the shared ``keyset_page`` helper (#1235), which
        mints byte-identical tokens, so cursors issued before the
        cutover keep decoding.

        ``search`` (#1235) is a free-text narrowing over event type,
        resource kind/id, and app slug — the same filter box the
        deprecated ``astroliftEvents`` list forced clients to apply
        client-side, over whatever fits under its 500-row cap.

        ``severity`` (``info`` | ``warn`` | ``error``) is an optional
        server-side filter (#540); it rides the composite
        (organization, occurred_at, severity) index so a narrow filter
        remains O(page) rather than scanning the full stream.

        ``app_slug`` (#539) scopes to events whose ``registered_app``
        FK points at the named app — the canonical per-app feed for
        the mobile + web app-detail event tabs. The slug-based filter
        replaces the previous client-side narrowing on
        ``resourceKind`` / ``payload.appSlug``; only events that
        carried a ``RegisteredApp`` link at emit time are returned, so
        narrowly-scoped emits (deploys, config syncs, secret rotations,
        scale ops) are visible end-to-end while infra-wide noise
        (cluster bootstrap, org-level audit) is excluded as intended.
        Tenancy is preserved by the existing ``@tenant_scoped``
        decorator on the resolver.
        """
        # Pre-clamp to this surface's own 500-row ceiling before the
        # shared helper sees it, so ``limit=0`` still collapses to a
        # single row rather than the helper's default page size.
        page_size = max(1, min(limit, 500))
        # Scope the base stream to the caller's org (#1183). Kept as an
        # explicit branch (not just ``_events_qs``'s empty queryset) so
        # the deny case reports NO_DATA_YET even on a continuation.
        if _caller_org_id() is None:
            return EventPageType(
                items=[],
                next_cursor=None,
                reason=ObservabilityPanelReason.NO_DATA_YET,
            )
        page = keyset_page(
            _events_qs(
                event_type=event_type,
                severity=severity,
                app_slug=app_slug,
                search=search,
            ),
            cursor=after,
            limit=page_size,
            max_limit=500,
            sort_field="occurred_at",
            with_total=False,
        )
        items = [event_to_type(e) for e in page.rows]
        if items:
            reason = ObservabilityPanelReason.OK
        elif after:
            # A continuation that ran off the end of the stream isn't
            # "no data ever" — the first page already had rows.
            reason = ObservabilityPanelReason.OK
        else:
            reason = ObservabilityPanelReason.NO_DATA_YET
        return EventPageType(
            items=items,
            next_cursor=page.next_cursor,
            reason=reason,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_recent_activity(
        self,
        info: Info,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ActivityPageType:
        """Cursor-paginated lifecycle activity feed for the dashboard
        landing card (#435).

        Filters the raw ``Event`` stream down to operator-relevant
        lifecycle facts — deploys, config syncs, scaling, cluster
        bring-in, secret rotations, and the like. The set of prefixes
        is intentionally permissive (``_LIFECYCLE_EVENT_PREFIXES``):
        new emit sites slot in for free as long as they follow the
        ``{resource}.{action}`` naming convention. The
        ``astroliftEventsPage`` query still exposes the unfiltered
        stream for the audit / events surface.

        Cursor shape matches ``astroliftEventsPage`` — base64-JSON of
        ``[occurred_at_iso, guid_str]`` over the
        ``(-occurred_at, -guid)`` seek key. Cursors round-trip across
        queries since the underlying table is the same, but callers
        shouldn't rely on that.

        The resolver pre-shapes each row into ``AstroliftActivityItem``
        — actor display, action verb, target label, and the
        canonical drill-down route — so the dashboard doesn't have to
        decode payloads. ``select_related`` on ``actor_user`` /
        ``registered_app`` keeps the per-row lookups off the per-page
        hot path.
        """
        # Pre-clamp to this feed's own 100-row ceiling before the shared
        # helper sees it, so ``limit=0`` still collapses to a single row
        # rather than the helper's default page size.
        page_size = max(1, min(limit, 100))
        # Scope to the caller's org (#1183) — the activity feed is a
        # filtered view over the same Event stream, so the org clause
        # rides alongside the lifecycle-prefix filter.
        org_id = _caller_org_id()
        if org_id is None:
            return ActivityPageType(items=[], next_cursor=None)
        qs = Event.objects.select_related("actor_user", "registered_app").filter(
            _lifecycle_event_filter(), organization_id=org_id
        )
        page = keyset_page(
            qs,
            cursor=cursor,
            limit=page_size,
            max_limit=100,
            sort_field="occurred_at",
            with_total=False,
        )
        return ActivityPageType(
            items=[shape_activity_item(e) for e in page.rows],
            next_cursor=page.next_cursor,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_audit_events(
        self,
        info: Info,
        limit: int = 100,
        action: str | None = None,
        decision: str | None = None,
        actor_id: str | None = None,
        created_at_gte: dt.datetime | None = None,
        created_at_lte: dt.datetime | None = None,
    ) -> list[AuditEventType]:
        """Legacy bounded audit query (#433): supports the existing
        polling list view while ``astroliftAuditEventsPage`` migrates
        the frontend onto cursor pagination + true date bounds.

        Filters: ``action`` (exact), ``decision`` (ALLOW/DENY/UNKNOWN),
        ``actor_id`` (exact), and inclusive ``occurred_at`` bounds.
        Caps at 500 rows per call regardless of ``limit``."""
        # Scope to the caller's org (#1183): AuditEvent rows carry PII
        # (actor, IP, user-agent, target). Without the org clause this
        # legacy list leaked every tenant's audit trail. org_id None →
        # deny-by-default (empty). NULL-org rows (platform/system events
        # written without a tenant context) are intentionally excluded
        # from every tenant's view.
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = AuditEvent.objects.filter(organization_id=org_id).order_by("-occurred_at")
        if action:
            qs = qs.filter(action=action)
        if decision:
            qs = qs.filter(decision=decision.upper())
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        if created_at_gte is not None:
            qs = qs.filter(occurred_at__gte=created_at_gte)
        if created_at_lte is not None:
            qs = qs.filter(occurred_at__lte=created_at_lte)
        return [audit_to_type(a) for a in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_audit_events_page(
        self,
        info: Info,
        limit: int = 100,
        after: str | None = None,
        action: str | None = None,
        decision: str | None = None,
        actor_id: str | None = None,
        created_at_gte: dt.datetime | None = None,
        created_at_lte: dt.datetime | None = None,
        include_total: bool = False,
    ) -> AuditEventPageType:
        """Cursor-paginated audit slice with date bounds (#433).

        Cursor encoding mirrors ``astrolift_events_page`` so the UI can
        reuse one paging helper. ``include_total`` returns the matching
        row count alongside the page — operators want the count for
        narrow filters but a full-range count is expensive, so the
        caller opts in."""
        # Pre-clamp to this surface's own 500-row ceiling before the
        # shared helper sees it, so ``limit=0`` still collapses to a
        # single row rather than the helper's default page size.
        page_size = max(1, min(limit, 500))
        # Scope to the caller's org (#1183) before the count + page so
        # both the returned rows and include_total reflect only the
        # caller's tenant. See astrolift_audit_events for the PII
        # rationale. org_id None → deny-by-default (empty page).
        org_id = _caller_org_id()
        if org_id is None:
            return AuditEventPageType(
                items=[],
                next_cursor=None,
                total_count=0 if include_total else None,
            )
        qs = AuditEvent.objects.filter(organization_id=org_id)
        if action:
            qs = qs.filter(action=action)
        if decision:
            qs = qs.filter(decision=decision.upper())
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        if created_at_gte is not None:
            qs = qs.filter(occurred_at__gte=created_at_gte)
        if created_at_lte is not None:
            qs = qs.filter(occurred_at__lte=created_at_lte)

        page = keyset_page(
            qs,
            cursor=after,
            limit=page_size,
            max_limit=500,
            sort_field="occurred_at",
            with_total=include_total,
        )
        return AuditEventPageType(
            items=[audit_to_type(a) for a in page.rows],
            next_cursor=page.next_cursor,
            total_count=page.total_count,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_audit_retention(self, info: Info) -> AuditRetentionType:
        """The caller org's audit-log retention window, in days.

        Sourced from ``Organization.audit_log_retention_days`` — the
        same per-org field the org-settings surface edits via
        ``updateOrganization`` — so /administration/audit and
        /administration/organization report (and change) one value
        rather than two divergent ones (#433). Previously this read the
        global ``AUDIT_RETENTION_DAYS`` Constance flag, which never
        agreed with the per-org column the settings page wrote.

        Scoped to the caller's org (#1183); org_id None → the model
        default (365). Always returns a value >= 1."""
        from astrolift_identity.models import Organization

        org_id = _caller_org_id()
        if org_id is None:
            return AuditRetentionType(days=365)
        org = Organization.objects.filter(pk=org_id).only("audit_log_retention_days").first()
        days = org.audit_log_retention_days if org is not None else 365
        return AuditRetentionType(days=max(1, int(days)))

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_runs(
        self,
        info: Info,
        limit: int = 50,
    ) -> list[WorkflowRunType]:
        # Scope to the caller's org (#1183). WorkflowRun carries a
        # nullable ``organization`` FK stamped from the tenant context
        # at reconcile time; org_id None → deny-by-default (empty), and
        # NULL-org platform runs stay out of every tenant's view. The
        # (organization, -started_at) index backs this directly.
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = WorkflowRun.objects.filter(organization_id=org_id).order_by("-started_at")[
            : max(1, min(limit, 200))
        ]
        return [workflow_run_to_type(w) for w in qs]

    @strawberry.field(
        deprecation_reason=(
            "Caps at 200 rows with no way to reach the 201st. " "Use astroliftWebhookSubscriptionsPage."
        )
    )
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def astrolift_webhook_subscriptions(
        self,
        info: Info,
        app_slug: str | None = None,
    ) -> list[WebhookSubscriptionType]:
        """List webhook subscriptions.

        Without ``app_slug``: org-wide subscriptions (those not bound
        to any app). Pass ``app_slug`` to list per-app subscriptions
        scoped to that app's UI page (#281)."""
        qs = _webhook_subscriptions_qs(app_slug=app_slug).order_by("-created_at")
        return [webhook_to_type(w) for w in qs[:200]]

    @strawberry.field
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def astrolift_webhook_subscriptions_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[WebhookSubscriptionType]:
        """Cursor-paginated webhook subscriptions (#1235).

        Replaces ``astroliftWebhookSubscriptions`` and its 200-row cap.
        Seek key is ``(-created_at, -guid)``; ``search`` matches the
        destination URL, which is the only operator-typed identifier a
        subscription carries.
        """
        page = keyset_page(
            _webhook_subscriptions_qs(app_slug=app_slug, search=search),
            cursor=after,
            limit=limit,
        )
        return page.map(webhook_to_type)

    @strawberry.field(
        deprecation_reason=(
            "Caps at 100 attempts with no way to reach the 101st. " "Use astroliftWebhookDeliveriesPage."
        )
    )
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def astrolift_webhook_deliveries(
        self,
        info: Info,
        subscription_id: GUID,
        limit: int = 10,
    ) -> list[WebhookDeliveryType]:
        """Last N delivery attempts for a subscription (#426).

        Surfaces real fan-out + operator test-fires together (test
        rows carry ``is_test=True`` so the UI can dim them). Tenant
        scoping rides on the subscription's organization — a query
        for a sibling-org subscription returns an empty list rather
        than leaking row counts."""
        capped = max(1, min(int(limit or 10), 100))
        qs = _webhook_deliveries_qs(subscription_id=subscription_id).order_by("-delivered_at")[:capped]
        return [webhook_delivery_to_type(d) for d in qs]

    @strawberry.field
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def astrolift_webhook_deliveries_page(
        self,
        info: Info,
        subscription_id: GUID,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[WebhookDeliveryType]:
        """Cursor-paginated delivery history for a subscription (#1235).

        Replaces ``astroliftWebhookDeliveries``, whose 100-attempt cap
        put the whole point of the surface out of reach: a hook that
        retries on every event burns through 100 rows in minutes, so
        "when did this integration start failing?" was unanswerable.

        Seek key is ``(-delivered_at, -guid)`` — ``delivered_at`` is
        the stamped attempt time and is NOT NULL, unlike the inherited
        ``created_at``-shaped ordering the rest of these pages use.
        ``search`` matches the event type, the echoed delivery id, and
        the captured error text.
        """
        page = keyset_page(
            _webhook_deliveries_qs(subscription_id=subscription_id, search=search),
            cursor=after,
            limit=limit,
            sort_field="delivered_at",
        )
        return page.map(webhook_delivery_to_type)

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_notifications(
        self,
        info: Info,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[NotificationType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []
        qs = Notification.objects.filter(user_id=tenant.actor_user_id).order_by("-created_at")
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        return [notification_to_type(n) for n in qs[: max(1, min(limit, 200))]]

    # ---- Push device registry (#476 §B) ----------------------------

    @strawberry.field
    def astrolift_my_mobile_devices(
        self,
        info: Info,
    ) -> list[DeviceRegistrationType]:
        """List the caller's live push registrations.

        Self-only — no permission gate (a user can only see their
        own devices). Returns live rows (stale + soft-deleted
        omitted) ordered most-recently-registered first.
        """
        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return []
        qs = DeviceRegistration.objects.filter(user_id=viewer.pk, stale_at__isnull=True).order_by(
            "-registered_at"
        )
        return [device_registration_to_type(d) for d in qs]

    # ---- Notification preferences (#476 §F, #499 §E) ---------------

    @strawberry.field
    def astrolift_my_notification_preferences(
        self,
        info: Info,
        channel: str | None = None,
    ) -> list[NotificationPreferenceType]:
        """Resolve the caller's effective preferences.

        Returns one row per (channel, event_kind) in the platform
        default catalog, overlaid with any explicit DB row the
        caller has set. Unknown event kinds (one a custom driver
        emits) are NOT enumerated here — they only surface after
        the caller has explicitly opted in via
        ``setNotificationPreference``.
        """
        from astrolift_operations.notification_dispatch import iter_template_event_types

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return []

        valid_channels = {c for c, _ in NotificationChannel.choices}
        if channel is not None:
            channel_lc = channel.lower()
            if channel_lc not in valid_channels:
                return []
            channels = (channel_lc,)
        else:
            channels = ("push",)  # only channel we render today

        # Pull every persisted row first so the synthesis loop can
        # overlay them in one pass without N+1.
        explicit: dict[tuple[str, str], NotificationPreference] = {}
        for row in NotificationPreference.objects.filter(
            user_id=viewer.pk,
            channel__in=channels,
        ):
            explicit[(row.channel, row.event_kind)] = row

        # Build the synthesised list from the dispatcher's catalog so
        # the UI gets the canonical event kinds + the session-kind
        # sub-keys (auth.session.created.{web,mobile,cli,...}).
        event_kinds: list[str] = []
        for et in iter_template_event_types():
            if et == "auth.session.created":
                event_kinds.extend(
                    [
                        "auth.session.created.web",
                        "auth.session.created.mobile",
                        "auth.session.created.cli",
                        "auth.session.created.api_token",
                        "auth.session.created.browser_extension",
                    ]
                )
            else:
                event_kinds.append(et)

        out: list[NotificationPreferenceType] = []
        for ch in channels:
            for kind in event_kinds:
                row = explicit.get((ch, kind))
                if row is not None:
                    out.append(notification_preference_to_type(row))
                else:
                    out.append(
                        synthetic_preference_type(
                            channel=ch,
                            event_kind=kind,
                            enabled=default_enabled(channel=ch, event_kind=kind),
                        )
                    )
        return out

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_devices(
        self,
        info: Info,
    ) -> list[DeviceRegistrationType]:
        """List the caller's registered push devices (#490).

        Auth via tenant binding: caller sees only their own
        DeviceRegistration rows, never another user's tokens.
        Order: most recently used first so the surface is useful
        for "which device did I last log in on" debugging."""
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []
        qs = DeviceRegistration.objects.filter(
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
        ).order_by("-last_used_at", "-created_at")
        return [device_registration_to_type(d) for d in qs]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_alert_subscriptions(
        self,
        info: Info,
        app_slug: str | None = None,
    ) -> list[UserAlertSubscriptionType]:
        """Caller's per-app alert notification subscriptions (#747).

        Filters to a single app when ``app_slug`` is provided.
        Returns only active (non-deleted) rows; absent rows mean the
        dispatcher uses its noisy-fallback default.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []
        qs = UserAlertSubscription.objects.filter(
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
        ).select_related("registered_app")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [user_alert_subscription_to_type(s) for s in qs]

    @strawberry.field(
        deprecation_reason=("Caps at 200 rows with no way to reach the 201st. Use astroliftAlertRulesPage.")
    )
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_rules(
        self,
        info: Info,
        target: str | None = None,
        target_id: str | None = None,
        active_only: bool = True,
    ) -> list[AlertRuleType]:
        """List alert rules.

        Without ``target``: every rule visible to the tenant. Pass
        ``target=app|env|workload|global`` (and optionally
        ``target_id``) to scope to one target."""
        qs = _alert_rules_qs(
            target=target,
            target_id=target_id,
            active_only=active_only,
        ).order_by("-created_at")[:200]
        return [alert_rule_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_rules_page(
        self,
        info: Info,
        target: str | None = None,
        target_id: str | None = None,
        active_only: bool = True,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[AlertRuleType]:
        """Cursor-paginated alert rules (#1235).

        Replaces ``astroliftAlertRules`` and its 200-row cap. Seek key
        is ``(-created_at, -guid)``; ``search`` matches the rule name
        and the target identifier (app slug / workload slug / guid) an
        operator types when hunting for "which rule covers this thing".

        Note ``activeOnly`` defaults to ``true``, same as the list
        field — an operator auditing muted/retired rules has to ask.
        """
        page = keyset_page(
            _alert_rules_qs(
                target=target,
                target_id=target_id,
                active_only=active_only,
                search=search,
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(alert_rule_to_type)

    @strawberry.field
    @require_permission(Permission.APP_READ_METRICS)
    @tenant_scoped()
    def astrolift_app_metrics(
        self,
        info: Info,
        app_slug: str,
        time_range: str = "1h",
    ) -> AppMetricsType | None:
        """Golden-signal time-series for one app over the requested
        window.

        Accepts ``time_range`` in ``5m | 1h | 24h | 7d | 30d``.
        Returns ``None`` for an unknown app.

        Source resolution (#297):
          1. The app's primary environment's ``tenant_cluster`` is
             read for an embedded Prometheus endpoint
             (``provider_config['prometheus_endpoint']``); when set,
             the resolver issues PromQL against it for rate /
             error-rate / latency percentiles.
          2. If the endpoint is unreachable, the PromQL is malformed,
             or no endpoint is configured, we fall back to the
             deterministic synthetic series (hash-seeded from the
             app's guid) so the UI never breaks on a misconfigured
             cluster.
          3. ``source`` flips to ``prometheus`` / ``otel`` on success
             so the UI can label the data accordingly.

        ``deploy_count`` is always aggregated from the real
        ``Deployment`` rows in the window — independent of the
        metrics backend.
        """
        from astrolift_lifecycle.models import Deployment
        from astrolift_operations import prometheus_client
        from astrolift_registry.models import RegisteredApp

        if time_range not in _TIME_RANGE_SECONDS:
            valid = ", ".join(sorted(_TIME_RANGE_SECONDS.keys()))
            raise ValueError(f"time_range must be one of {valid}")

        # Scope to the caller's org (#1183): slugs are unique per-org,
        # not global, so an unscoped slug lookup surfaced a same-slug
        # app (and its golden-signal metrics) from another tenant.
        # org_id None → deny-by-default (None / not-found).
        org_id = _caller_org_id()
        if org_id is None:
            return None
        app = (
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .only("id", "guid", "slug", "k8s_namespace")
            .first()
        )
        if app is None:
            return None

        window_seconds = _TIME_RANGE_SECONDS[time_range]
        bucket_count = _TIME_RANGE_BUCKETS[time_range]
        now = timezone.now()
        window_start = now - timedelta(seconds=window_seconds)

        deploy_count = Deployment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
            created_at__gte=window_start,
        ).count()

        prom_endpoint, prom_kind = _resolve_metrics_endpoint(app)
        if prom_endpoint:
            try:
                return _query_prometheus_for_app(
                    app_slug=app.slug,
                    namespace=app.k8s_namespace or "",
                    time_range=time_range,
                    window_seconds=window_seconds,
                    bucket_count=bucket_count,
                    now=now,
                    window_start=window_start,
                    deploy_count=deploy_count,
                    endpoint=prom_endpoint,
                    source=prom_kind,
                )
            except prometheus_client.PrometheusError:
                # Fall through to synthetic — never let observability
                # noise break the UI surface.
                pass

        return _synthetic_app_metrics(
            app_slug=app.slug,
            app_guid=str(app.guid),
            time_range=time_range,
            window_seconds=window_seconds,
            bucket_count=bucket_count,
            window_start=window_start,
            deploy_count=deploy_count,
        )

    @strawberry.field(
        deprecation_reason=("Caps at 500 rows with no way to reach the 501st. Use astroliftAlertEventsPage.")
    )
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_events(
        self,
        info: Info,
        rule_id: GUID | None = None,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> list[AlertEventType]:
        qs = _alert_events_qs(rule_id=rule_id, unresolved_only=unresolved_only).order_by("-fired_at")
        return [alert_event_to_type(e) for e in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_events_page(
        self,
        info: Info,
        rule_id: GUID | None = None,
        unresolved_only: bool = False,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[AlertEventType]:
        """Cursor-paginated alert-firing history (#1235).

        Replaces ``astroliftAlertEvents`` and its 500-row cap — a noisy
        rule burns that in a day, which put "when did this first fire?"
        out of reach.

        Seek key is ``(-fired_at, -guid)``: ``fired_at`` is the moment
        the predicate matched and is NOT NULL, whereas ``resolved_at``
        is null for everything still firing. ``search`` matches the
        event summary and the owning rule's name.
        """
        page = keyset_page(
            _alert_events_qs(
                rule_id=rule_id,
                unresolved_only=unresolved_only,
                search=search,
            ),
            cursor=after,
            limit=limit,
            sort_field="fired_at",
        )
        return page.map(alert_event_to_type)


# ---------------------------------------------------------------------------
# App-slug filter helper (#539)
# ---------------------------------------------------------------------------


def _filter_by_app_slug(qs, app_slug: str):
    """Narrow an Event queryset to a specific app within the caller's
    organization.

    Filters on ``registered_app__slug`` *and* the caller's
    ``organization_id`` so the slug collision case (two orgs holding
    the same app slug — slugs are unique per-org, not global) never
    leaks rows across tenants. The caller is already past the
    ``@tenant_scoped`` gate, so ``get_current_tenant()`` resolves; if
    it doesn't (unexpected), short-circuit to an empty queryset rather
    than fall back to the cross-tenant match.
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return qs.none()
    return qs.filter(registered_app__slug=app_slug, organization_id=org_id)


# ---------------------------------------------------------------------------
# Lifecycle event filter (dashboard activity feed, #435)
# ---------------------------------------------------------------------------

# Event-type prefixes the dashboard activity feed surfaces. The list
# is permissive on purpose — every operator-relevant lifecycle fact
# follows the ``{resource}.{action}`` naming convention so new emit
# sites are picked up without a backend change. Anything outside
# this set (audit, webhook test-fires, etc.) stays out of the
# dashboard but is still queryable via ``astroliftEventsPage``.
_LIFECYCLE_EVENT_PREFIXES: tuple[str, ...] = (
    "app.",
    "deploy.",
    "deployment.",
    "cluster.",
    "config.",
    "secret.",
    "scale.",
    "service.",
    "binding.",
    "managed_service.",
    "preview.",
    "promotion.",
    "rollback.",
    "drift.",
    "environment.",
)


def _lifecycle_event_filter():
    """Build the ``Q`` filter that matches any event whose
    ``event_type`` begins with one of the lifecycle prefixes."""
    from django.db.models import Q

    q = Q()
    for prefix in _LIFECYCLE_EVENT_PREFIXES:
        q |= Q(event_type__startswith=prefix)
    return q


# ---------------------------------------------------------------------------
# Event aggregation (#434 scope A)
# ---------------------------------------------------------------------------


def _fold_events(rows: list, *, window_seconds: int) -> list[dict]:
    """Walk newest→oldest, folding consecutive identical events into
    buckets. Returns the raw bucket dicts in newest-first *open* order.

    "Consecutive identical" means: same
    ``(event_type, resource_kind, resource_id)`` key AND the gap from
    the bucket's oldest member to the new candidate is less than
    ``window_seconds``. Crossing the gap opens a fresh bucket even
    when the key matches — that's how we model "the issue cleared and
    then came back."

    Each bucket carries ``open_index``: the position in ``rows`` of the
    member that opened it. ``astroliftEventsAggregatedPage`` cuts the
    scan at the open index of the first bucket that doesn't fit on the
    page — every earlier row belongs to a bucket being returned, so
    that index is exactly the row count this page consumed.

    Caller is responsible for tenant-scoping the input queryset; this
    function is pure aggregation.
    """
    buckets: list[dict] = []
    bucket_by_key: dict[tuple[str, str, str], int] = {}

    for index, row in enumerate(rows):
        key = (
            row.event_type or "",
            row.resource_kind or "",
            row.resource_id or "",
        )
        existing_idx = bucket_by_key.get(key)
        if existing_idx is not None:
            existing = buckets[existing_idx]
            # rows arrive newest-first; ``oldest`` shrinks as we add
            # older members. Gap is between the *current oldest in the
            # bucket* and the new (older) candidate.
            gap = (existing["oldest"] - row.occurred_at).total_seconds()
            if gap < window_seconds:
                existing["count"] += 1
                existing["oldest"] = row.occurred_at
                continue
        # Open a new bucket either because we've never seen the key,
        # or because the previous bucket's oldest member is too far
        # ahead of this candidate (it's a separate incident).
        bucket_by_key[key] = len(buckets)
        buckets.append(
            {
                "representative": row,
                "count": 1,
                "newest": row.occurred_at,
                "oldest": row.occurred_at,
                "event_type": key[0],
                "resource_kind": key[1],
                "resource_id": key[2],
                "open_index": index,
            }
        )
    return buckets


def _bucket_to_type(bucket: dict) -> AggregatedEventType:
    """Project one folded bucket onto its GraphQL type."""
    return AggregatedEventType(
        representative=event_to_type(bucket["representative"]),
        count=bucket["count"],
        first_at=bucket["oldest"],
        last_at=bucket["newest"],
        event_type=bucket["event_type"],
        resource_kind=bucket["resource_kind"],
        resource_id=bucket["resource_id"],
    )


def _aggregate_events(
    rows: list,
    *,
    window_seconds: int,
    limit: int,
) -> list[AggregatedEventType]:
    """Fold ``rows`` and return at most ``limit`` buckets, newest-first.

    Note the slice happens AFTER the whole input is folded, so a
    bucket's ``count`` includes members that appear beyond the cut —
    that is the (deprecated) list field's long-standing behaviour and
    is why ``astroliftEventsAggregatedPage`` re-folds its cut instead
    of reusing this.
    """
    return [_bucket_to_type(b) for b in _fold_events(rows, window_seconds=window_seconds)[:limit]]


# ---------------------------------------------------------------------------
# astroliftAppMetrics — Prometheus + synthetic helpers (#297)
# ---------------------------------------------------------------------------


def _resolve_metrics_endpoint(app) -> tuple[str | None, str]:
    """Find the metrics endpoint for ``app`` from its primary env's
    cluster. Returns ``(endpoint, source)`` where ``source`` is
    ``"prometheus"`` (the default), ``"otel"`` (when the cluster's
    config explicitly labels the endpoint as OTel), or ``""`` when
    no endpoint is configured.

    The endpoint and label live on ``TenantCluster.provider_config``
    under the ``prometheus_endpoint`` / ``observability_kind`` keys —
    operators set them via the cluster registration UI. Falls back
    to no endpoint when the cluster has no envs, no provider_config,
    or no endpoint key.
    """
    from astrolift_lifecycle.models import AppEnvironment

    env = (
        AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        )
        .select_related("tenant_cluster")
        .order_by("name")
        .first()
    )
    if env is None or env.tenant_cluster_id is None:
        return None, ""
    cluster = env.tenant_cluster
    cfg = cluster.provider_config or {}
    endpoint = (cfg.get("prometheus_endpoint") or "").strip()
    if not endpoint:
        return None, ""
    kind = (cfg.get("observability_kind") or "").strip().lower() or "prometheus"
    if kind not in {"prometheus", "otel"}:
        # Unknown labels degrade to prometheus rather than failing —
        # the wire protocol is the same.
        kind = "prometheus"
    return endpoint, kind


def _query_prometheus_for_app(
    *,
    app_slug: str,
    namespace: str,
    time_range: str,
    window_seconds: int,
    bucket_count: int,
    now,
    window_start,
    deploy_count: int,
    endpoint: str,
    source: str,
) -> AppMetricsType:
    """Run the four golden-signal PromQL queries for ``app_slug``
    against ``endpoint`` and shape the result into ``AppMetricsType``.

    PromQL contract (spec 08 §6):
      * request_rate: ``sum(rate(http_requests_total{app=...}[5m]))``
      * error_rate: ``... code=~"5.."`` over total
      * p50/p95/p99: ``histogram_quantile`` over the request-latency
        bucket histogram.

    On any PromQL or transport failure the caller swallows the
    exception and falls back to synthetic.
    """
    from astrolift_operations import prometheus_client

    safe_app = prometheus_client.sanitize_label_value(app_slug)
    # Rate window for short time_ranges (5m / 1h) is 1m for snappy
    # response; longer windows widen to 5m so the rate is stable.
    rate_window = "1m" if time_range in ("5m", "1h") else "5m"

    labels = f'app="{safe_app}"'
    if namespace:
        labels += f',namespace="{prometheus_client.sanitize_label_value(namespace)}"'

    rate_q = f"sum(rate(http_requests_total{{{labels}}}[{rate_window}]))"
    err_q = f'sum(rate(http_requests_total{{{labels},code=~"5.."}}[{rate_window}]))'
    p50_q = (
        f"histogram_quantile(0.50, "
        f"sum(rate(http_request_duration_seconds_bucket{{{labels}}}[{rate_window}])) by (le)"
        f")"
    )
    p95_q = (
        f"histogram_quantile(0.95, "
        f"sum(rate(http_request_duration_seconds_bucket{{{labels}}}[{rate_window}])) by (le)"
        f")"
    )
    p99_q = (
        f"histogram_quantile(0.99, "
        f"sum(rate(http_request_duration_seconds_bucket{{{labels}}}[{rate_window}])) by (le)"
        f")"
    )

    rate_value = prometheus_client.query_instant(endpoint=endpoint, query=rate_q)
    err_value = prometheus_client.query_instant(endpoint=endpoint, query=err_q)
    p50_value = prometheus_client.query_instant(endpoint=endpoint, query=p50_q)
    p95_value = prometheus_client.query_instant(endpoint=endpoint, query=p95_q)
    p99_value = prometheus_client.query_instant(endpoint=endpoint, query=p99_q)

    start_unix = int(window_start.timestamp())
    end_unix = int(now.timestamp())
    step_seconds = max(15, window_seconds // max(bucket_count, 1))

    rate_rows = prometheus_client.query_range(
        endpoint=endpoint,
        query=rate_q,
        start_unix=start_unix,
        end_unix=end_unix,
        step_seconds=step_seconds,
    )
    err_rows = prometheus_client.query_range(
        endpoint=endpoint,
        query=err_q,
        start_unix=start_unix,
        end_unix=end_unix,
        step_seconds=step_seconds,
    )
    p95_rows = prometheus_client.query_range(
        endpoint=endpoint,
        query=p95_q,
        start_unix=start_unix,
        end_unix=end_unix,
        step_seconds=step_seconds,
    )

    rate_buckets = prometheus_client.sum_range_to_buckets(
        rate_rows,
        bucket_count=bucket_count,
        start_unix=start_unix,
        end_unix=end_unix,
    )
    err_buckets = prometheus_client.sum_range_to_buckets(
        err_rows,
        bucket_count=bucket_count,
        start_unix=start_unix,
        end_unix=end_unix,
    )
    p95_buckets = prometheus_client.sum_range_to_buckets(
        p95_rows,
        bucket_count=bucket_count,
        start_unix=start_unix,
        end_unix=end_unix,
    )

    bucket_seconds = window_seconds / max(bucket_count, 1)
    series: list[AppMetricsPointType] = []
    for i in range(bucket_count):
        ts = window_start + timedelta(seconds=(i + 0.5) * bucket_seconds)
        series.append(
            AppMetricsPointType(
                timestamp=ts,
                request_rate=round(rate_buckets[i], 4),
                error_rate=round(err_buckets[i], 6),
                # p95 from PromQL is seconds; expose ms to match the
                # synthetic shape that the UI is already consuming.
                latency_p95=round(p95_buckets[i] * 1000.0, 1),
            )
        )

    return AppMetricsType(
        app_slug=app_slug,
        time_range=time_range,
        request_rate=round(rate_value, 4),
        # error_rate is dimensionless: errors / requests. Avoid div-by-0.
        error_rate=round((err_value / rate_value) if rate_value > 0 else 0.0, 6),
        p50_latency_ms=round(p50_value * 1000.0, 1),
        p95_latency_ms=round(p95_value * 1000.0, 1),
        p99_latency_ms=round(p99_value * 1000.0, 1),
        deploy_count=deploy_count,
        time_series=series,
        source=source,
    )


def _synthetic_app_metrics(
    *,
    app_slug: str,
    app_guid: str,
    time_range: str,
    window_seconds: int,
    bucket_count: int,
    window_start,
    deploy_count: int,
) -> AppMetricsType:
    """Deterministic synthetic data — hash(guid + bucket_index) → stable
    float in [0, 1). The UI sees identical numbers across reloads for
    a given (app, window), which keeps the metrics surface useful as
    a UI shape exercise when Prometheus is absent."""
    seed = hashlib.sha256(app_guid.encode("utf-8")).hexdigest()
    seed_int = int(seed[:16], 16)

    def _norm(i: int, lo: float, hi: float) -> float:
        r = (seed_int + i * 2654435761) & 0xFFFFFFFF
        unit = r / 0xFFFFFFFF
        return lo + (hi - lo) * unit

    bucket_seconds = window_seconds / bucket_count
    series: list[AppMetricsPointType] = []
    rates: list[float] = []
    errors: list[float] = []
    latencies: list[float] = []
    for i in range(bucket_count):
        ts = window_start + timedelta(seconds=(i + 0.5) * bucket_seconds)
        rate = round(_norm(i, 0.5, 80.0), 3)
        err = round(_norm(i + 1000, 0.0, 0.05), 4)
        p95 = round(_norm(i + 2000, 30.0, 750.0), 1)
        series.append(
            AppMetricsPointType(
                timestamp=ts,
                request_rate=rate,
                error_rate=err,
                latency_p95=p95,
            )
        )
        rates.append(rate)
        errors.append(err)
        latencies.append(p95)

    def _avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    sorted_lat = sorted(latencies)
    p50 = sorted_lat[len(sorted_lat) // 2] if sorted_lat else 0.0
    p95_idx = max(0, int(round(0.95 * (len(sorted_lat) - 1))))
    p99_idx = max(0, int(round(0.99 * (len(sorted_lat) - 1))))
    p95 = sorted_lat[p95_idx] if sorted_lat else 0.0
    p99 = sorted_lat[p99_idx] if sorted_lat else 0.0

    return AppMetricsType(
        app_slug=app_slug,
        time_range=time_range,
        request_rate=_avg(rates),
        error_rate=_avg(errors),
        p50_latency_ms=float(p50),
        p95_latency_ms=float(p95),
        p99_latency_ms=float(p99),
        deploy_count=deploy_count,
        time_series=series,
        source="synthetic",
    )
