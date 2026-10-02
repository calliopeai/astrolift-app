"""Read-only resolvers for the App > Observability golden-signals
surface (#380).

Two queries:

* ``astroliftAppGoldenSignals`` — returns a list of six
  ``AppGoldenSignal`` rows (traffic, errors, latency p50/p90/p99,
  cpu saturation) over ``range_seconds``.
* ``astroliftAppStatusCodeBreakdown`` — returns a single
  ``StatusCodeBreakdown`` grouping the per-code series into
  2xx/3xx/4xx/5xx/other classes plus a per-class top-5 code list for
  the FE tooltip.

Both are gated on :data:`Permission.APP_READ` + ``@tenant_scoped()``
and degrade to ``[]`` / ``None`` when:

* the app doesn't exist for the current tenant
* the env has no cluster or the cluster has no
  ``prometheus_endpoint`` configured
* Prometheus is unreachable or returns a PromQL error

The empty-state surface is intentionally indistinguishable in those
last three cases: the UI shows the "metrics not yet flowing" callout
with a doc link.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections import defaultdict
from typing import Any

import strawberry
from strawberry.types import Info

from astrolift_observability import prom_client, prom_queries, url_probe, url_resolution
from astrolift_observability.schema.types import (
    AppEndpointMetric,
    AppGoldenSignal,
    AppGoldenSignalsResult,
    AppMetricNamesResult,
    AppTrace,
    AppUrlHealth,
    ExecutePromqlResult,
    GoldenSignalKind,
    ManagedServiceMetrics,
    ManagedServiceMetricSeries,
    PodResourceUsage,
    PodResourceUsagePoint,
    PromqlSeries,
    StatusCodeBreakdown,
    StatusCodeSeries,
    TimeSeriesPoint,
    TraceSpan,
    WorkloadResourceGauge,
    WorkloadResourceUsage,
)
from astrolift_observability.scopes import managed_service_metrics_scope
from astrolift_operations import prometheus_client
from astrolift_operations.prometheus_client import PrometheusError
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_services.models.managed_service import ManagedService
from core.cluster_observability import namespace_for_app_environment
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

# Internal metric catalogs must not become Strawberry root fields (#2111).
_POSTGRES_METRICS: tuple[tuple[str, str], ...] = (
    ("connections", "count"),
    ("cpu", "seconds"),
    ("iops", "rps"),
    ("slow_queries", "rps"),
    ("replica_lag", "seconds"),
)
_OBJECT_STORE_METRICS: tuple[tuple[str, str], ...] = (
    ("bucket_size", "bytes"),
    ("request_count", "rps"),
    ("errors_4xx", "rps"),
    ("errors_5xx", "rps"),
    ("egress_bytes", "rps"),
)

_MODEL_ENDPOINT_METRICS: tuple[tuple[str, str], ...] = (
    ("tokens_per_second", "rps"),
    ("requests_running", "count"),
    ("requests_waiting", "count"),
    ("ttft_p95", "seconds"),
    ("kv_cache_usage", "ratio"),
)

# Default window when the FE doesn't pass one — 1h matches the time-
# range picker's "1h" default.
_DEFAULT_RANGE_SECONDS = 60 * 60

# Allow-list for ``range_seconds`` — matches the FE picker (15m / 1h /
# 6h / 24h / 7d). We allow any value the FE submits but clamp to a
# sane ceiling so a malicious caller can't ask Prometheus for a year
# of high-resolution data.
_MIN_RANGE_SECONDS = 60  # 1 minute floor
_MAX_RANGE_SECONDS = 31 * 86400  # 31 days ceiling


def _clamp_range(seconds: int) -> int:
    if seconds < _MIN_RANGE_SECONDS:
        return _MIN_RANGE_SECONDS
    if seconds > _MAX_RANGE_SECONDS:
        return _MAX_RANGE_SECONDS
    return seconds


def _now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _samples_from_pairs(pairs: list[tuple[float, float]]) -> list[TimeSeriesPoint]:
    """Convert ``[(unix, value), ...]`` to ``[TimeSeriesPoint, ...]``."""
    return [
        TimeSeriesPoint(
            ts=dt.datetime.fromtimestamp(ts, tz=dt.UTC),
            value=value,
        )
        for ts, value in pairs
    ]


# The RED half of the golden-signals group. Saturation is excluded: it
# comes from cAdvisor / kube-state-metrics, which every cluster scrapes.
_HTTP_SIGNAL_KINDS = frozenset(
    {
        GoldenSignalKind.TRAFFIC,
        GoldenSignalKind.ERRORS,
        GoldenSignalKind.LATENCY_P50,
        GoldenSignalKind.LATENCY_P90,
        GoldenSignalKind.LATENCY_P95,
        GoldenSignalKind.LATENCY_P99,
    }
)


def _app_declares_metrics(app: Any) -> bool:
    """Whether any workload in the app's manifest exposes Prometheus metrics.

    ``metrics.enabled`` (#1226) is what puts ``http_requests_total`` in
    front of a scraper; with it unset on every workload the app-metric
    RED queries select a series that cannot exist. Read from
    ``RegisteredApp.manifest_normalized`` — the persisted normalization
    of the last applied manifest — so this costs no extra query.

    Deliberately app-wide rather than per-workload: over-reporting
    "instrumented" only leaves the pre-#1708 empty panel, while
    under-reporting would tell an operator a configured signal is
    unconfigured.
    """
    workloads = (app.manifest_normalized or {}).get("workloads") or []
    return any(bool(w.get("metrics_enabled")) for w in workloads if isinstance(w, dict))


def _signal_reason(sig: AppGoldenSignal, *, red_source: bool) -> ObservabilityPanelReason:
    """Label one golden signal's empty state (#1708)."""
    if sig.samples:
        return ObservabilityPanelReason.OK
    if sig.name in _HTTP_SIGNAL_KINDS and not red_source:
        return ObservabilityPanelReason.NOT_CONFIGURED
    return ObservabilityPanelReason.NO_DATA_YET


def _red_source_configured(*, app: Any, edge: Any, cloudwatch: bool = False) -> bool:
    """Whether *any* source can answer the RED queries for this app.

    Three exist: the cluster's ingress variant when it has an
    edge-metrics mapping (#1224), the app's own instrumentation, and —
    for golden signals only — the ALB/CloudWatch fallback on AWS
    clusters. None of the three means an empty panel says "no source",
    not "no traffic" (#1708).
    """
    return edge is not None or cloudwatch or _app_declares_metrics(app)


def _backfill_from_cloudwatch(
    *,
    out: list[AppGoldenSignal],
    app: Any,
    environment_name: str | None,
    app_namespace: str,
    start_unix: int,
    end_unix: int,
    step: int,
    seconds: int,
) -> tuple[list[AppGoldenSignal], bool]:
    """Replace empty HTTP-kind signals with CloudWatch ALB data.

    Called only when Prometheus has no HTTP series (uninstrumented apps).
    Falls back silently on any error or non-AWS cluster — the original
    empty-series list is returned unchanged in those cases.

    Returns the (possibly patched) signals and whether CloudWatch is a
    *source* for this app — the dispatch reached the ALB — which is
    true even when it reported no traffic in the window, and is what
    separates NO_DATA_YET from NOT_CONFIGURED for the caller (#1708).
    """
    from astrolift_lifecycle.models.app_environment import AppEnvironment

    env = (
        AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
            **({"name": environment_name} if environment_name else {}),
        )
        .select_related("tenant_cluster__provider_plugin")
        .order_by("name")
        .first()
    )
    if env is None or env.tenant_cluster_id is None:
        log.warning("cloudwatch fallback: no env/cluster for app=%s", app.slug)
        return out, False

    cluster = env.tenant_cluster
    plugin_slug = (
        cluster.provider_plugin.slug if cluster.provider_plugin_id and cluster.provider_plugin else ""
    ).lower()
    log.warning(
        "cloudwatch fallback: app=%s cluster=%s plugin=%s",
        app.slug,
        cluster.slug,
        plugin_slug or "(none)",
    )
    if plugin_slug != "aws":
        return out, False

    try:
        from core.cluster_management import (  # noqa: PLC0415
            cluster_alb_http_metrics_dispatch,
        )

        cw_data = cluster_alb_http_metrics_dispatch(
            cluster=cluster,
            app_namespace=app_namespace,
            start_unix=start_unix,
            end_unix=end_unix,
            step_seconds=step,
        )
    except Exception as exc:
        log.warning("cloudwatch fallback: dispatch failed app=%s err=%s", app.slug, exc)
        from astrolift_observability.schema.types import (
            GoldenSignalSource,
            GoldenSignalUnavailableReason,
        )

        return [
            dataclasses.replace(
                signal,
                reason=ObservabilityPanelReason.ERROR,
                measurement=dataclasses.replace(
                    signal.measurement,
                    source=GoldenSignalSource.CLOUDWATCH_ALB,
                    available=False,
                    unavailable_reason=GoldenSignalUnavailableReason.PROVIDER_ERROR,
                ),
            )
            if signal.measurement is not None
            and signal.name.value.startswith(("traffic", "errors", "latency"))
            else signal
            for signal in out
        ], True

    has_data = any(bool(v) for v in cw_data.values())
    log.warning(
        "cloudwatch fallback: app=%s has_data=%s keys=%s",
        app.slug,
        has_data,
        {k: len(v) for k, v in cw_data.items()},
    )
    if not has_data:
        return out, True

    # Map CloudWatch key → GoldenSignalKind enum (for sig.name comparison)
    _CW_KIND_MAP: dict[str, GoldenSignalKind] = {
        "rps": GoldenSignalKind.TRAFFIC,
        "error_rate": GoldenSignalKind.ERRORS,
        "latency_p50": GoldenSignalKind.LATENCY_P50,
        "latency_p95": GoldenSignalKind.LATENCY_P95,
        "latency_p99": GoldenSignalKind.LATENCY_P99,
    }
    _CW_UNIT_MAP = {
        "rps": "rps",
        "error_rate": "ratio",
        "latency_p50": "seconds",
        "latency_p95": "seconds",
        "latency_p99": "seconds",
    }
    # Rebuild the list: swap in CloudWatch data for any HTTP signal
    patched: list[AppGoldenSignal] = []
    for sig in out:
        # sig.name is a GoldenSignalKind enum instance
        cw_key = next((k for k, kind_enum in _CW_KIND_MAP.items() if kind_enum == sig.name), None)
        cw_pairs = cw_data.get(cw_key, []) if cw_key else []
        if cw_pairs:
            pairs = cw_pairs
            patched.append(
                dataclasses.replace(
                    sig,
                    samples=_samples_from_pairs(pairs),
                    promql=f"# CloudWatch ALB — {sig.name.value if hasattr(sig.name, 'value') else sig.name}",
                    unit=_CW_UNIT_MAP.get(cw_key or "", sig.unit),
                )
            )
        else:
            patched.append(sig)
    return patched, True


def _classify_status_code(code: str) -> str:
    """Bucket an HTTP code string into one of 2xx / 3xx / 4xx / 5xx / other.

    Prometheus exposes the ``code`` label as a string; treat anything
    that doesn't parse to a 3-digit number as ``other``. Edge variants
    whose metrics are class-granular (CloudWatch ALB, #1225) emit the
    class itself ("2xx" … "5xx") as the series key — pass those through.
    """
    if code in ("2xx", "3xx", "4xx", "5xx"):
        return code
    if not code or not code.isdigit() or len(code) != 3:
        return "other"
    bucket = code[0]
    if bucket in ("2", "3", "4", "5"):
        return f"{bucket}xx"
    return "other"


def _instant_resource_gauge(
    *,
    endpoint: str,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str,
    resource: str,
    unit: str,
) -> WorkloadResourceGauge | None:
    """Run usage/request/limit instant queries for one resource and
    fold into a gauge.

    Returns ``None`` when **every** of the three queries errored —
    that's the "Prometheus is dark" signal the caller uses to pick
    the empty-state. A single-query error is swallowed and surfaced
    as 0.0 on that field; the FE is robust to a missing denominator.
    """

    builders = {
        "usage": (
            prom_queries.build_workload_cpu_usage_query
            if resource == "cpu"
            else prom_queries.build_workload_memory_usage_query
        ),
        "request": prom_queries.build_workload_resource_request_query,
        "limit": prom_queries.build_workload_resource_limit_query,
    }
    plans = {
        "usage": builders["usage"](
            app_slug=app_slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
        ),
        "request": builders["request"](
            app_slug=app_slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            resource=resource,
        ),
        "limit": builders["limit"](
            app_slug=app_slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            resource=resource,
        ),
    }
    values: dict[str, float] = {}
    error_count = 0
    for key, plan in plans.items():
        try:
            values[key] = float(
                prometheus_client.query_instant(
                    endpoint=endpoint,
                    query=plan.promql,
                )
            )
        except PrometheusError:
            values[key] = 0.0
            error_count += 1
    if error_count == len(plans):
        return None

    current = values["usage"]
    request = values["request"]
    limit = values["limit"]
    return WorkloadResourceGauge(
        unit=unit,
        current=current,
        request=request,
        limit=limit,
        percent_of_request=(current / request * 100.0) if request > 0 else 0.0,
        percent_of_limit=(current / limit * 100.0) if limit > 0 else 0.0,
    )


@strawberry.type
class GoldenSignalsQuery:
    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_golden_signals(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        range_seconds: int | None = None,
        workload_slug: str | None = None,
    ) -> AppGoldenSignalsResult:
        """Return the four golden signals (traffic / errors / latency /
        saturation) over ``range_seconds`` for ``app_slug`` (+ env +
        optional workload).

        Latency expands to four rows — p50, p90, p95, p99 — so the FE can
        render them as a stacked line without a second round-trip.

        ``workload_slug`` requires verified runtime ownership and canonical
        instrumentation IDs. Resource series bind physical container IDs
        and pod UIDs. Omit for explicitly labeled app-environment totals.

        Returns a reason-discriminated envelope (#1111):
        NOT_CONFIGURED when the app/cluster has no Prometheus endpoint,
        ERROR when no signal is available and a read failed, NO_DATA_YET
        when every signal came back empty, OK when any signal is available.
        Each signal retains its independent source, scope and failure reason.
        """
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .select_related("organization")
            .only(
                "id",
                "guid",
                "slug",
                "k8s_namespace",
                "manifest_normalized",
                "organization__slug",
                "organization__guid",
            )
            .first()
        )
        if app is None:
            return AppGoldenSignalsResult(reason=ObservabilityPanelReason.NOT_CONFIGURED, signals=[])

        from astrolift_observability.golden_signals import golden_signals_for_app

        return golden_signals_for_app(
            app=app,
            environment_name=environment_name,
            workload_slug=workload_slug,
            seconds=seconds,
            now=_now_utc(),
            cloudwatch_fallback=_backfill_from_cloudwatch,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_status_code_breakdown(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        range_seconds: int | None = None,
        workload_slug: str | None = None,
    ) -> StatusCodeBreakdown | None:
        """Per-status-code stacked time-series. ``None`` when the app
        can't be resolved, has no Prometheus endpoint, or Prometheus
        errors.

        ``workload_slug`` narrows the breakdown to a single workload
        (mirrors :func:`astrolift_app_golden_signals`); omit to roll
        every workload up.

        ``reason`` (#1111) makes the empty state explicit: NOT_CONFIGURED
        (no Prometheus endpoint, or no HTTP metric source for the app —
        #1708), NO_DATA_YET (a source exists, no traffic in window),
        ERROR (Prometheus errored), or OK. ``null`` is reserved for
        "no such app for this tenant"."""
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug", "k8s_namespace", "manifest_normalized", "organization__slug")
            .select_related("organization")
            .first()
        )
        if app is None:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return StatusCodeBreakdown(
                reason=ObservabilityPanelReason.NOT_CONFIGURED,
                range_seconds=seconds,
                series=[],
                promql="",
            )

        end_unix = int(_now_utc().timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        # Edge-sourced when the cluster's ingress variant is mapped (#1224):
        # grouped by the variant's status label, surfaced on
        # ``plan.group_label`` so the series keys resolve either way.
        edge = prom_client.resolve_edge_metrics(app=app, environment_name=environment_name)
        plan = prom_queries.build_status_code_breakdown_query(
            app_slug=app.slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            range_seconds=seconds,
            edge=edge,
            namespace=namespace_for_app_environment(app, environment_name),
        )
        try:
            rows = prom_client.query_range_series(
                endpoint=endpoint,
                promql=plan.promql,
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step,
                label_key=plan.group_label,
            )
        except PrometheusError:
            log.warning("status_code_breakdown: prometheus query failed for app %s", app.slug)
            return StatusCodeBreakdown(
                reason=ObservabilityPanelReason.ERROR,
                range_seconds=seconds,
                series=[],
                promql=plan.promql,
            )

        # Aggregate per-code samples into 2xx/3xx/4xx/5xx/other buckets
        # and track per-class total volume so we can pick the top-5
        # individual codes for the tooltip.
        class_buckets: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
        class_top: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for code, samples in rows:
            cls = _classify_status_code(code)
            class_top[cls][code] += sum(v for _, v in samples)
            for ts, value in samples:
                class_buckets[cls][ts] += value

        # Preserve a stable display order even when a class is absent.
        display_order = ["2xx", "3xx", "4xx", "5xx", "other"]
        series_out: list[StatusCodeSeries] = []
        for cls in display_order:
            if cls not in class_buckets:
                continue
            bucket = class_buckets[cls]
            samples = [
                TimeSeriesPoint(
                    ts=dt.datetime.fromtimestamp(ts, tz=dt.UTC),
                    value=value,
                )
                for ts, value in sorted(bucket.items())
            ]
            top_codes = sorted(class_top[cls].items(), key=lambda kv: kv[1], reverse=True)[:5]
            series_out.append(
                StatusCodeSeries(
                    code_class=cls,
                    samples=samples,
                    top_codes=[code for code, _ in top_codes],
                )
            )

        # Same two-cause empty state as the RED signals (#1708): with
        # no edge mapping and no app instrumentation this query selects
        # a series that cannot exist, which is NOT_CONFIGURED rather
        # than "no traffic in the window". There is no CloudWatch
        # fallback on this panel.
        if series_out:
            reason = ObservabilityPanelReason.OK
        elif _red_source_configured(app=app, edge=edge):
            reason = ObservabilityPanelReason.NO_DATA_YET
        else:
            reason = ObservabilityPanelReason.NOT_CONFIGURED
        return StatusCodeBreakdown(
            reason=reason,
            range_seconds=seconds,
            series=series_out,
            promql=plan.promql,
        )

    # -- Per-workload live resource usage (#430) ---------------------
    #
    # Drives the two gauges (CPU + memory) at the top of the workload
    # detail page. Returns ``None`` (rather than an empty list) when
    # Prometheus is unreachable so the FE can render the "metrics not
    # yet flowing" callout instead of a stale-looking gauge at 0%.
    #
    # The resolver issues four instant queries per call (usage +
    # request + limit for each of CPU and memory). They share the
    # same TTL cache the operations client uses, so the 5s FE poll
    # mostly hits cache and Prometheus sees a roughly 30s effective
    # cadence under sustained load.

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_workload_resource_usage(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
    ) -> WorkloadResourceUsage | None:
        """Live CPU + memory usage vs. request/limit for one workload.

        Sources four instant PromQL queries per resource against the
        environment's Prometheus endpoint:

        * usage — ``sum(rate(container_cpu_usage_seconds_total[1m]))``
          (cores) or ``sum(container_memory_working_set_bytes)`` (bytes)
        * request — ``sum(kube_pod_container_resource_requests{resource=...})``
        * limit — ``sum(kube_pod_container_resource_limits{resource=...})``

        Returns ``None`` when the app is unknown to the tenant, the
        environment's cluster has no Prometheus endpoint configured,
        or every query errored — the FE treats null as the empty-state
        signal.
        """
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return None

        cpu_gauge = _instant_resource_gauge(
            endpoint=endpoint,
            app_slug=app.slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            resource="cpu",
            unit="cores",
        )
        memory_gauge = _instant_resource_gauge(
            endpoint=endpoint,
            app_slug=app.slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            resource="memory",
            unit="bytes",
        )
        # Both gauges failing = Prometheus is down / mis-scoped; treat
        # the same as no endpoint so the FE shows one consistent empty
        # state instead of two flavours.
        if cpu_gauge is None and memory_gauge is None:
            return None
        # Mixed-state: one source dark, the other live. Synthesize a
        # zero gauge for the dark side so the FE can still render the
        # half that works (rather than dropping the whole card).
        if cpu_gauge is None:
            cpu_gauge = WorkloadResourceGauge(
                unit="cores",
                current=0.0,
                request=0.0,
                limit=0.0,
                percent_of_request=0.0,
                percent_of_limit=0.0,
            )
        if memory_gauge is None:
            memory_gauge = WorkloadResourceGauge(
                unit="bytes",
                current=0.0,
                request=0.0,
                limit=0.0,
                percent_of_request=0.0,
                percent_of_limit=0.0,
            )

        return WorkloadResourceUsage(
            cpu=cpu_gauge,
            memory=memory_gauge,
            sourced_at=_now_utc(),
        )

    # -- URL health probe (#406) -------------------------------------
    #
    # Live HTTP health for an app's public URLs. Separate resolver
    # from the Prometheus-derived signals because this hits the app
    # directly from the control plane rather than scraping the
    # cluster — different failure modes, different latency budget,
    # different empty-state semantics.
    #
    # Both resolvers validate the URL belongs to the app (env URLs +
    # public-workload subdomain hosts) before issuing a request, so
    # a caller with APP_READ can't turn the platform into an
    # arbitrary HTTP fetcher.

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_url_health(
        self,
        info: Info,
        app_slug: str,
        url: str,
        force_refresh: bool = False,
    ) -> AppUrlHealth | None:
        """Probe ``url`` for ``app_slug`` and return the current health.

        ``force_refresh`` skips the 30s result cache (the FE passes
        ``true`` when an operator clicks the pill to re-run the
        probe immediately).

        Returns ``None`` when:

        * the app doesn't exist in the current tenant
        * ``url`` isn't a known URL for the app (env URLs +
          ``<workload.slug>.<app.subdomain>`` for public workloads)

        The ``unknown`` status value in :class:`AppUrlHealth` is
        reserved for the FE's pre-first-probe pill; a live probe
        always lands on ``ok`` / ``degraded`` / ``down``.
        """
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "guid", "slug", "subdomain")
            .first()
        )
        if app is None:
            return None

        normalized = url_resolution.normalize_url(url)
        if normalized is None:
            return None
        if normalized not in url_resolution.app_urls(app):
            return None

        result = url_probe.probe_url(
            app_guid=str(app.guid),
            url=normalized,
            use_cache=not force_refresh,
        )
        return AppUrlHealth(
            url=result.url,
            status=result.status,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            last_checked=result.last_checked,
            message=result.message,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_url_probe_history(
        self,
        info: Info,
        app_slug: str,
        url: str,
        limit: int = 5,
    ) -> list[AppUrlHealth]:
        """Recent probe results for (``app_slug``, ``url``).

        Newest entries come first. Bounded server-side to
        :data:`url_probe.HISTORY_MAX_ENTRIES` regardless of the
        requested ``limit``. Empty list when the app/url is unknown
        or no probes have run yet.

        Backed by a cache ring buffer rather than a DB table — this
        widget is a glanceable tooltip, not a persistent timeline.
        Persistence is its own ticket if/when operators ask for
        cross-restart probe history.
        """
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "guid", "slug", "subdomain")
            .first()
        )
        if app is None:
            return []

        normalized = url_resolution.normalize_url(url)
        if normalized is None:
            return []
        if normalized not in url_resolution.app_urls(app):
            return []

        results = url_probe.history_for(
            app_guid=str(app.guid),
            url=normalized,
            limit=limit,
        )
        return [
            AppUrlHealth(
                url=r.url,
                status=r.status,
                status_code=r.status_code,
                latency_ms=r.latency_ms,
                last_checked=r.last_checked,
                message=r.message,
            )
            for r in results
        ]

    # -- Managed-service metrics (#645 + #646) -----------------------
    #
    # One resolver covers both RDS-style (postgres) and S3-style
    # (object_store) panels. Switches on the managed-service ``kind``
    # to pick the metric set + PromQL builder. Returns ``None`` when
    # the kind isn't supported today (anything other than postgres /
    # object_store) so the FE doesn't render an empty panel; returns
    # an envelope with an empty ``series`` list when Prometheus is
    # reachable but has no data (the FE shows the "metrics not yet
    # flowing" callout).
    #
    # Data path: Prometheus over the same ``prometheus_endpoint`` the
    # golden-signals resolver uses, scraping the per-kind exporter
    # sidecars (postgres-exporter / s3-exporter). The exporters tag
    # series with the managed-service guid so we can scope per-row.

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=managed_service_metrics_scope("managed_service_id"))
    @tenant_scoped()
    def astrolift_app_managed_service_metrics(
        self,
        info: Info,
        managed_service_id: strawberry.ID,
        range_seconds: int | None = None,
    ) -> ManagedServiceMetrics | None:
        """Time-series metrics for one managed-service row (#645 + #646).

        Postgres kind → connections / cpu / iops / slow_queries /
        replica_lag. Object-store kind → bucket_size / request_count /
        errors_4xx / errors_5xx / egress_bytes.

        Returns ``None`` for unsupported kinds (anything other than
        postgres / object_store today, or a missing service). Returns
        an envelope with empty ``series`` when Prometheus is dark —
        same shape as the golden-signals empty state.
        """
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        tenant = get_current_tenant()
        svc = (
            ManagedService.objects.select_related("registered_app", "app_environment")
            .filter(
                guid=str(managed_service_id),
                registered_app__organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return None

        kind = svc.kind
        if kind == ManagedService.Kind.POSTGRES:
            metric_set = _POSTGRES_METRICS
            builder = prom_queries.build_managed_service_postgres_query
        elif kind == ManagedService.Kind.OBJECT_STORE:
            metric_set = _OBJECT_STORE_METRICS
            builder = prom_queries.build_managed_service_object_store_query
        elif kind == ManagedService.Kind.MODEL_ENDPOINT and svc.variant == "vllm":
            metric_set = _MODEL_ENDPOINT_METRICS
            builder = prom_queries.build_managed_service_model_endpoint_query
        else:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(
            app=svc.registered_app,
            environment_name=svc.app_environment.name,
        )
        if endpoint is None:
            # Same envelope shape as the populated case, with an empty
            # series list — the FE renders the "metrics not yet
            # flowing" callout inside the panel.
            return ManagedServiceMetrics(
                managed_service_id=managed_service_id,
                kind=kind,
                name=svc.name,
                range_seconds=seconds,
                series=[],
            )

        end_unix = int(_now_utc().timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)
        # ``source`` field on each series is hardcoded to
        # ``prometheus`` for now — once the cost-driver per-cloud
        # metrics integration is wired we'll branch on cluster.kind
        # and label CloudWatch / Cloud Monitoring / Azure Monitor
        # appropriately. The wire shape is forward-compatible.
        source = "prometheus"

        series_out: list[ManagedServiceMetricSeries] = []
        for metric, unit in metric_set:
            plan = builder(
                service_guid=str(svc.guid),
                metric=metric,
                range_seconds=seconds,
            )
            try:
                rows = prom_client.query_range_series(
                    endpoint=endpoint,
                    promql=plan.promql,
                    start_unix=start_unix,
                    end_unix=end_unix,
                    step_seconds=step,
                )
            except PrometheusError:
                # Per-metric error degrades to empty samples — keep
                # the rest of the panel renderable. The FE shows the
                # empty-state callout inside the affected card only.
                rows = []
            pairs = rows[0][1] if rows else []
            series_out.append(
                ManagedServiceMetricSeries(
                    name=metric,
                    unit=unit,
                    samples=_samples_from_pairs(pairs),
                    source=source,
                )
            )

        return ManagedServiceMetrics(
            managed_service_id=managed_service_id,
            kind=kind,
            name=svc.name,
            range_seconds=seconds,
            series=series_out,
        )

    # -- Per-pod resource usage (#713) -------------------------------
    #
    # Powers the pod-row expander on the Observability tab. Returns a
    # CPU + memory time-series narrowed by the pod's name plus the
    # kubelet's restart counter for the pod (so the operator sees both
    # "how often it restarts" and the live resource consumption).

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_pod_resource_usage(
        self,
        info: Info,
        app_slug: str,
        pod_name: str,
        environment_name: str | None = None,
        range_seconds: int | None = None,
    ) -> PodResourceUsage | None:
        """Per-pod CPU + memory sparkline + restart history (#713).

        Returns ``None`` when the app doesn't resolve for the tenant
        or the cluster has no Prometheus endpoint. Returns an envelope
        with empty samples when Prometheus is reachable but has
        nothing for the pod (e.g. the pod terminated minutes ago and
        cAdvisor dropped its series).
        """
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return PodResourceUsage(
                reason=ObservabilityPanelReason.NOT_CONFIGURED,
                pod_name=pod_name,
                range_seconds=seconds,
                samples=[],
                restart_count=0,
                last_restart_at=None,
            )

        end_unix = int(_now_utc().timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        # cAdvisor carries no app label, so these have to be namespace-scoped
        # or they match nothing (#1703).
        pod_namespace = namespace_for_app_environment(app, environment_name)
        cpu_plan = prom_queries.build_pod_cpu_usage_query(
            app_slug=app.slug,
            environment_name=environment_name,
            pod_name=pod_name,
            range_seconds=seconds,
            namespace=pod_namespace,
        )
        mem_plan = prom_queries.build_pod_memory_usage_query(
            app_slug=app.slug,
            environment_name=environment_name,
            pod_name=pod_name,
            range_seconds=seconds,
            namespace=pod_namespace,
        )

        try:
            cpu_rows = prom_client.query_range_series(
                endpoint=endpoint,
                promql=cpu_plan.promql,
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step,
            )
        except PrometheusError:
            cpu_rows = []
        try:
            mem_rows = prom_client.query_range_series(
                endpoint=endpoint,
                promql=mem_plan.promql,
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step,
            )
        except PrometheusError:
            mem_rows = []

        cpu_pairs = cpu_rows[0][1] if cpu_rows else []
        mem_pairs = mem_rows[0][1] if mem_rows else []

        # The two queries share start/end/step so their sample arrays
        # line up by index. Take the longest as the baseline and
        # fill in missing values with 0.0 — the FE renders gaps as
        # the y-axis floor rather than a discontinuity.
        baseline = cpu_pairs if len(cpu_pairs) >= len(mem_pairs) else mem_pairs
        samples: list[PodResourceUsagePoint] = []
        for i, (ts_unix, _) in enumerate(baseline):
            cpu_value = cpu_pairs[i][1] if i < len(cpu_pairs) else 0.0
            mem_value = mem_pairs[i][1] if i < len(mem_pairs) else 0.0
            samples.append(
                PodResourceUsagePoint(
                    ts=dt.datetime.fromtimestamp(ts_unix, tz=dt.UTC),
                    cpu_cores=cpu_value,
                    memory_bytes=mem_value,
                )
            )

        # Restart count comes from the existing cluster-side pod-list
        # path so the expander stays consistent with the row above it.
        # ``last_restart_at`` isn't on the driver SDK's PodInfo yet —
        # leave it null and file a follow-up to wire the kubelet
        # ``ContainerStateTerminated.finishedAt`` through every driver.
        # Lazy import keeps schema-export from pulling providers in.
        from core import cluster_observability

        restart_count = 0
        last_restart_at: dt.datetime | None = None
        try:
            # Pick the env-scoped cluster the same way list_app_pods
            # does — we go via the AppEnvironment row to get the
            # namespace + cluster for this env.
            from astrolift_lifecycle.models import AppEnvironment

            env_qs = AppEnvironment.objects.filter(
                registered_app=app, deleted_at__isnull=True
            ).select_related("tenant_cluster")
            if environment_name:
                env_qs = env_qs.filter(name=environment_name)
            env = env_qs.order_by("name").first()
            if env is not None and env.tenant_cluster is not None:
                pods = cluster_observability.list_app_pods(
                    cluster=env.tenant_cluster,
                    namespace=cluster_observability.namespace_for_environment(env),
                    app_slug=app.slug,
                )
                for p in pods:
                    if p.name == pod_name:
                        restart_count = p.restarts
                        # ``last_restart_at`` stays None — see comment
                        # above; mirrored on the FE empty-state copy.
                        break
        except cluster_observability.ClusterObservabilityError:
            pass
        except Exception:
            # Cluster-side read is best-effort — never block the
            # metric sparkline on a cluster outage.
            pass

        reason = ObservabilityPanelReason.OK if samples else ObservabilityPanelReason.NO_DATA_YET
        return PodResourceUsage(
            reason=reason,
            pod_name=pod_name,
            range_seconds=seconds,
            samples=samples,
            restart_count=restart_count,
            last_restart_at=last_restart_at,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_metric_names(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        lookback_seconds: int = 3600,
        limit: int = 200,
    ) -> AppMetricNamesResult:
        """The metric names this app's own workloads expose (#1226).

        Reads ``/api/v1/label/__name__/values`` restricted to the app's
        namespace, then drops the families the platform emits into that
        namespace — cAdvisor, kube-state-metrics, node-exporter, the
        ingress controller — because those already have dedicated panels
        and are not what "my app's metrics" means.

        Scoped by namespace at the Prometheus end rather than filtered
        here: an unrestricted name enumeration on a busy cluster returns
        tens of thousands of entries.

        ``lookback_seconds`` bounds the window, so a metric an app stopped
        emitting a month ago does not linger in the picker forever.

        Permission: ``APP_READ`` — the same gate as the golden signals and
        ``astroliftExecutePromql``, and strictly narrower than the latter,
        which takes arbitrary PromQL.
        """
        limit = max(1, min(int(limit), 1000))
        lookback = max(60, min(int(lookback_seconds), 7 * 24 * 3600))

        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug", "k8s_namespace", "organization__slug")
            .first()
        )
        if app is None:
            return AppMetricNamesResult(
                ok=False, names=[], truncated=False, limit=limit, error="app not found"
            )

        namespace = namespace_for_app_environment(app, environment_name)
        if not namespace:
            return AppMetricNamesResult(
                ok=False,
                names=[],
                truncated=False,
                limit=limit,
                error="app has no namespace to read metrics from",
            )

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return AppMetricNamesResult(
                ok=False,
                names=[],
                truncated=False,
                limit=limit,
                error="no Prometheus endpoint configured for this app's cluster",
            )

        end_unix = int(_now_utc().timestamp())
        try:
            found, truncated = prometheus_client.metric_names(
                endpoint=endpoint,
                match=prom_queries.build_namespace_series_match(namespace),
                start_unix=end_unix - lookback,
                end_unix=end_unix,
                # Asked for more than we return, so the platform families
                # dropped below do not eat into the operator's budget.
                limit=limit * 4,
            )
        except prometheus_client.PrometheusError as exc:
            return AppMetricNamesResult(ok=False, names=[], truncated=False, limit=limit, error=str(exc))

        owned = [n for n in found if prom_queries.is_app_owned_metric(n)]
        return AppMetricNamesResult(
            ok=True,
            names=owned[:limit],
            truncated=truncated or len(owned) > limit,
            limit=limit,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_execute_promql(
        self,
        info: Info,
        app_slug: str,
        query: str,
        start_unix: int,
        end_unix: int,
        step_seconds: int,
        environment_name: str | None = None,
    ) -> ExecutePromqlResult:
        """Execute a raw PromQL expression against the cluster Prometheus (#750).

        Proxies ``query_range`` to the Prometheus endpoint configured on the
        app's cluster (``TenantCluster.provider_config['prometheus_endpoint']``).
        Returns ``ok=False`` when no endpoint is configured or when Prometheus
        rejects the expression — the FE shows a "metrics unavailable" state and
        never surfaces the raw error to end-users.

        ``start_unix`` / ``end_unix`` are UNIX seconds; ``step_seconds`` is the
        resolution step. The caller is responsible for keeping the step value
        sane — ``pick_step_seconds`` in ``prom_queries`` provides a standard
        helper.

        Permission: ``APP_READ`` — same gate as the golden-signals queries.
        No PromQL allow-list: the query is passed verbatim. Operators with
        ``APP_READ`` already have full read access to the cluster's metric stream
        via the golden-signals surface; this query adds free-form expression
        capability without widening the permission boundary.
        """
        if not query or not query.strip():
            return ExecutePromqlResult(ok=False, error="query is required", series=[])
        if step_seconds <= 0:
            return ExecutePromqlResult(ok=False, error="step_seconds must be positive", series=[])
        if end_unix <= start_unix:
            return ExecutePromqlResult(ok=False, error="end_unix must be greater than start_unix", series=[])

        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return ExecutePromqlResult(ok=False, error="app not found", series=[])

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return ExecutePromqlResult(
                ok=False,
                error="no Prometheus endpoint configured for this app's cluster",
                series=[],
            )

        try:
            rows = prometheus_client.query_range(
                endpoint=endpoint,
                query=query.strip(),
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step_seconds,
            )
        except PrometheusError as exc:
            return ExecutePromqlResult(ok=False, error=str(exc), series=[])

        series: list[PromqlSeries] = []
        for row in rows:
            points = [
                TimeSeriesPoint(
                    ts=dt.datetime.fromtimestamp(ts_unix, tz=dt.UTC),
                    value=value,
                )
                for ts_unix, value in row.values
            ]
            series.append(
                PromqlSeries(
                    metric_labels=dict(row.metric_labels),
                    values=points,
                )
            )
        return ExecutePromqlResult(ok=True, error="", series=series)

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_traces(
        self,
        info: Info,
        app_slug: str,
        since: str,
        until: str,
        environment_name: str | None = None,
        service: str | None = None,
        operation: str | None = None,
        min_duration_ms: float | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[AppTrace]:
        """List distributed traces for an app (#749).

        Queries the trace backend configured on the app's cluster
        (``TenantCluster.provider_config['trace_driver']`` + ``trace_config``).
        Currently supports Tempo; the protocol allows for Jaeger, X-Ray, etc.

        ``since`` / ``until`` are ISO-8601 timestamps or UNIX seconds as
        strings — the format accepted by Tempo's HTTP API.

        Returns ``[]`` when the cluster has no trace backend configured or
        the backend is unreachable — same empty-state convention as the other
        observability resolvers.
        """
        from astrolift_observability import trace_client

        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return []

        driver = trace_client.resolve_trace_driver(app=app, environment_name=environment_name)
        if driver is None:
            return []

        try:
            summaries = driver.list_traces(
                service=service,
                operation=operation,
                min_duration_ms=min_duration_ms,
                status=status,
                since=since,
                until=until,
                limit=limit or 50,
            )
        except Exception:
            return []

        return [
            AppTrace(
                trace_id=s.trace_id,
                root_service=s.root_service,
                root_operation=s.root_operation,
                span_count=s.span_count,
                duration_ms=s.duration_ms,
                status_code=s.status_code,
            )
            for s in summaries
        ]

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_trace_spans(
        self,
        info: Info,
        app_slug: str,
        trace_id: str,
        environment_name: str | None = None,
    ) -> list[TraceSpan]:
        """Fetch all spans for a single trace (#749).

        ``trace_id`` is the trace identifier returned by ``astroliftAppTraces``.

        Returns ``[]`` when the cluster has no trace backend configured, the
        trace doesn't exist, or the backend is unreachable.
        """
        from astrolift_observability import trace_client

        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return []

        driver = trace_client.resolve_trace_driver(app=app, environment_name=environment_name)
        if driver is None:
            return []

        try:
            spans = driver.get_trace(trace_id)
        except Exception:
            return []

        return [
            TraceSpan(
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                operation=span.operation,
                service=span.service,
                start_time=span.start_time,
                duration_ms=span.duration_ms,
                status_code=span.status_code,
                attributes=dict(span.attributes),
            )
            for span in spans
        ]

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_endpoint_metrics(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        range_seconds: int | None = None,
        workload_slug: str | None = None,
    ) -> list[AppEndpointMetric]:
        """Per-HTTP-route golden-signal snapshot (#748).

        Queries Prometheus for the ``http_route`` dimension of the standard
        OTEL metrics (``http_requests_total``,
        ``http_request_duration_seconds_bucket``) and returns one row per
        unique route label.

        Latency values are in milliseconds. Routes with no histogram samples
        carry ``None`` latency fields. Routes with zero total traffic are
        excluded (they appear when the counter series is stale but not yet
        expired from Prometheus; the FE doesn't need them).

        Degrades to ``[]`` when the cluster has no Prometheus endpoint or the
        backend is unreachable — same empty-state convention as the other
        observability resolvers.
        """
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        tenant = get_current_tenant()
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .only("id", "slug")
            .first()
        )
        if app is None:
            return []

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return []

        now = _now_utc()
        end_unix = int(now.timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        rate_plan = prom_queries.build_endpoint_request_rate_query(
            app_slug=app.slug,
            environment_name=environment_name,
            range_seconds=seconds,
            workload_slug=workload_slug,
        )
        err_plan = prom_queries.build_endpoint_error_rate_query(
            app_slug=app.slug,
            environment_name=environment_name,
            range_seconds=seconds,
            workload_slug=workload_slug,
        )
        lat_plans = {
            q: prom_queries.build_endpoint_latency_quantile_query(
                app_slug=app.slug,
                environment_name=environment_name,
                range_seconds=seconds,
                quantile=q,
                workload_slug=workload_slug,
            )
            for q in (0.5, 0.9, 0.99)
        }

        def _query_by_route(plan) -> dict[str, float]:
            """Run query, return {http_route: last_sample_value}."""
            try:
                rows = prom_client.query_range_series(
                    endpoint=endpoint,
                    promql=plan.promql,
                    start_unix=start_unix,
                    end_unix=end_unix,
                    step_seconds=step,
                    label_key="http_route",
                )
            except PrometheusError:
                return {}
            out: dict[str, float] = {}
            for route, values in rows:
                if values:
                    out[route] = values[-1][1]  # last sample in window
            return out

        rates = _query_by_route(rate_plan)
        errors = _query_by_route(err_plan)
        lat_by_q: dict[float, dict[str, float]] = {}
        for q, plan in lat_plans.items():
            lat_by_q[q] = _query_by_route(plan)

        all_routes = set(rates) | set(errors) | set(lat_by_q.get(0.5, {}))
        out: list[AppEndpointMetric] = []
        for route in sorted(all_routes):
            rps = rates.get(route, 0.0)
            if rps <= 0.0:
                continue
            err_ratio = errors.get(route, 0.0)
            p50 = lat_by_q[0.5].get(route)
            p90 = lat_by_q[0.9].get(route)
            p99 = lat_by_q[0.99].get(route)
            out.append(
                AppEndpointMetric(
                    route=route,
                    request_rate=rps,
                    error_rate_ratio=err_ratio,
                    p50_ms=p50 * 1000 if p50 is not None else None,
                    p90_ms=p90 * 1000 if p90 is not None else None,
                    p99_ms=p99 * 1000 if p99 is not None else None,
                )
            )
        return out
