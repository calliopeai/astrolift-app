"""Read-only queries for the operations app."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
from datetime import timedelta

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    Event,
    Notification,
    WebhookDelivery,
    WebhookSubscription,
    WorkflowRun,
)
from astrolift_operations.schema.types import (
    ActivityPageType,
    AlertEventType,
    AlertRuleType,
    AppMetricsPointType,
    AppMetricsType,
    AuditEventPageType,
    AuditEventType,
    AuditRetentionType,
    EventPageType,
    EventType,
    NotificationType,
    WebhookDeliveryType,
    WebhookSubscriptionType,
    WorkflowRunType,
    alert_event_to_type,
    alert_rule_to_type,
    audit_to_type,
    event_to_type,
    notification_to_type,
    shape_activity_item,
    webhook_delivery_to_type,
    webhook_to_type,
    workflow_run_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

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
class OperationsQuery:
    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events(
        self,
        info: Info,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[EventType]:
        qs = Event.objects.order_by("-occurred_at")
        if event_type:
            qs = qs.filter(event_type=event_type)
        return [event_to_type(e) for e in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events_page(
        self,
        info: Info,
        limit: int = 100,
        after: str | None = None,
        event_type: str | None = None,
    ) -> EventPageType:
        """Cursor-paginated event stream.

        ``after`` is the opaque cursor returned by the previous page;
        omit it to start from the newest event. Cursor is base64-JSON
        of ``[occurred_at_iso, guid_str]`` so the (occurred_at, guid)
        composite is the seek key — guid is a UUIDv7 so the secondary
        sort is also time-ordered, eliminating tie-break churn.
        """
        page_size = max(1, min(limit, 500))
        qs = Event.objects.order_by("-occurred_at", "-guid")
        if event_type:
            qs = qs.filter(event_type=event_type)
        if after:
            decoded = _decode_event_cursor(after)
            if decoded is not None:
                from django.db.models import Q

                cursor_at, cursor_guid = decoded
                qs = qs.filter(
                    Q(occurred_at__lt=cursor_at) | (Q(occurred_at=cursor_at) & Q(guid__lt=cursor_guid))
                )
        # Fetch one extra to detect end-of-stream cheaply.
        rows = list(qs[: page_size + 1])
        items = rows[:page_size]
        next_cursor = (
            _encode_event_cursor(items[-1].occurred_at, str(items[-1].guid))
            if len(rows) > page_size and items
            else None
        )
        return EventPageType(
            items=[event_to_type(e) for e in items],
            next_cursor=next_cursor,
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
        page_size = max(1, min(limit, 100))
        qs = (
            Event.objects.select_related("actor_user", "registered_app")
            .filter(_lifecycle_event_filter())
            .order_by("-occurred_at", "-guid")
        )
        if cursor:
            decoded = _decode_event_cursor(cursor)
            if decoded is not None:
                from django.db.models import Q

                cursor_at, cursor_guid = decoded
                qs = qs.filter(
                    Q(occurred_at__lt=cursor_at) | (Q(occurred_at=cursor_at) & Q(guid__lt=cursor_guid))
                )
        rows = list(qs[: page_size + 1])
        items = rows[:page_size]
        next_cursor = (
            _encode_event_cursor(items[-1].occurred_at, str(items[-1].guid))
            if len(rows) > page_size and items
            else None
        )
        return ActivityPageType(
            items=[shape_activity_item(e) for e in items],
            next_cursor=next_cursor,
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
        qs = AuditEvent.objects.order_by("-occurred_at")
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
        page_size = max(1, min(limit, 500))
        qs = AuditEvent.objects.order_by("-occurred_at", "-guid")
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

        total_count: int | None = None
        if include_total:
            total_count = qs.count()

        if after:
            decoded = _decode_event_cursor(after)
            if decoded is not None:
                from django.db.models import Q

                cursor_at, cursor_guid = decoded
                qs = qs.filter(
                    Q(occurred_at__lt=cursor_at) | (Q(occurred_at=cursor_at) & Q(guid__lt=cursor_guid))
                )

        rows = list(qs[: page_size + 1])
        items = rows[:page_size]
        next_cursor = (
            _encode_event_cursor(items[-1].occurred_at, str(items[-1].guid))
            if len(rows) > page_size and items
            else None
        )
        return AuditEventPageType(
            items=[audit_to_type(a) for a in items],
            next_cursor=next_cursor,
            total_count=total_count,
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_audit_retention(self, info: Info) -> AuditRetentionType:
        """Org-visible retention policy. Sourced from the
        ``AUDIT_RETENTION_DAYS`` Constance flag so operators can tune
        the visible window at runtime. Always returns a value >= 1."""
        from constance import config as constance_config

        raw = getattr(constance_config, "AUDIT_RETENTION_DAYS", 90)
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = 90
        return AuditRetentionType(days=max(1, days))

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_runs(
        self,
        info: Info,
        limit: int = 50,
    ) -> list[WorkflowRunType]:
        qs = WorkflowRun.objects.order_by("-started_at")[: max(1, min(limit, 200))]
        return [workflow_run_to_type(w) for w in qs]

    @strawberry.field
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
        qs = WebhookSubscription.objects.order_by("-created_at")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        else:
            qs = qs.filter(registered_app__isnull=True)
        return [webhook_to_type(w) for w in qs[:200]]

    @strawberry.field
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
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []
        sub = WebhookSubscription.objects.filter(
            guid=str(subscription_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if sub is None:
            return []
        capped = max(1, min(int(limit or 10), 100))
        qs = (
            WebhookDelivery.objects.filter(subscription=sub)
            .select_related("subscription")
            .order_by("-delivered_at")[:capped]
        )
        return [webhook_delivery_to_type(d) for d in qs]

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

    @strawberry.field
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
        qs = AlertRule.objects.select_related("organization")
        if active_only:
            qs = qs.filter(is_active=True)
        if target:
            qs = qs.filter(target=target)
        if target_id:
            qs = qs.filter(target_id=target_id)
        qs = qs.order_by("-created_at")[:200]
        return [alert_rule_to_type(r) for r in qs]

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

        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_events(
        self,
        info: Info,
        rule_id: GUID | None = None,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> list[AlertEventType]:
        qs = AlertEvent.objects.select_related("rule")
        if rule_id is not None:
            qs = qs.filter(rule__guid=str(rule_id))
        if unresolved_only:
            qs = qs.filter(resolved_at__isnull=True)
        qs = qs.order_by("-fired_at")[: max(1, min(limit, 500))]
        return [alert_event_to_type(e) for e in qs]


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
# Cursor helpers (Event)
# ---------------------------------------------------------------------------


def _encode_event_cursor(occurred_at, guid: str) -> str:
    payload = json.dumps([occurred_at.isoformat(), guid], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def _decode_event_cursor(token: str):
    """Return ``(occurred_at_dt, guid_str)`` or ``None`` if the token
    is malformed. We swallow garbage so a bogus cursor restarts from
    the top instead of erroring — UX over strictness."""
    import datetime as dt

    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        ts, guid = json.loads(raw)
        return dt.datetime.fromisoformat(ts), guid
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError):
        return None


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
