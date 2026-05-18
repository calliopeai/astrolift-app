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

import datetime as dt
from collections import defaultdict
from collections.abc import Callable

import strawberry
from strawberry.types import Info

from astrolift_observability import prom_client, prom_queries, url_probe, url_resolution
from astrolift_observability.schema.types import (
    AppGoldenSignal,
    AppUrlHealth,
    GoldenSignalKind,
    ManagedServiceMetrics,
    ManagedServiceMetricSeries,
    PodResourceUsage,
    PodResourceUsagePoint,
    StatusCodeBreakdown,
    StatusCodeSeries,
    TimeSeriesPoint,
    WorkloadResourceGauge,
    WorkloadResourceUsage,
)
from astrolift_services.models.managed_service import ManagedService
from astrolift_operations import prometheus_client
from astrolift_operations.prometheus_client import PrometheusError
from astrolift_registry.models import RegisteredApp
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission

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


def _classify_status_code(code: str) -> str:
    """Bucket an HTTP code string into one of 2xx / 3xx / 4xx / 5xx / other.

    Prometheus exposes the ``code`` label as a string; treat anything
    that doesn't parse to a 3-digit number as ``other``.
    """
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
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_golden_signals(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        range_seconds: int | None = None,
        workload_slug: str | None = None,
    ) -> list[AppGoldenSignal]:
        """Return the four golden signals (traffic / errors / latency /
        saturation) over ``range_seconds`` for ``app_slug`` (+ env +
        optional workload).

        Latency expands to three rows — p50, p90, p99 — so the FE can
        render them as a stacked line without a second round-trip.

        ``workload_slug`` narrows the Prometheus query to one workload
        (``api`` / ``worker`` / ``scheduler`` / ...). Omit to roll up
        every workload under the app (pre-#422 behavior).

        Empty list when the app doesn't exist for the current tenant,
        the cluster has no Prometheus endpoint, or Prometheus errors.
        """
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        app = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True).only("id", "slug").first()
        if app is None:
            return []

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return []

        now = _now_utc()
        end_unix = int(now.timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        builders: list[tuple[GoldenSignalKind, str, Callable[..., prom_queries.QueryPlan], dict]] = [
            (
                GoldenSignalKind.TRAFFIC,
                "rps",
                prom_queries.build_request_rate_query,
                {},
            ),
            (
                GoldenSignalKind.ERRORS,
                "ratio",
                prom_queries.build_error_rate_query,
                {},
            ),
            (
                GoldenSignalKind.LATENCY_P50,
                "seconds",
                prom_queries.build_latency_quantile_query,
                {"quantile": 0.50},
            ),
            (
                GoldenSignalKind.LATENCY_P90,
                "seconds",
                prom_queries.build_latency_quantile_query,
                {"quantile": 0.90},
            ),
            # #640 — p95 is the SLO-canonical default for the latency
            # tile. Keep p90 in the wire shape so legacy dashboards
            # don't break; the FE renders three at a time by default
            # (p50, p95, p99) and exposes p90 via the PromQL disclosure.
            (
                GoldenSignalKind.LATENCY_P95,
                "seconds",
                prom_queries.build_latency_quantile_query,
                {"quantile": 0.95},
            ),
            (
                GoldenSignalKind.LATENCY_P99,
                "seconds",
                prom_queries.build_latency_quantile_query,
                {"quantile": 0.99},
            ),
            (
                GoldenSignalKind.SATURATION_CPU,
                "ratio",
                prom_queries.build_cpu_saturation_query,
                {},
            ),
            # #642 — memory saturation completes the SATURATION pair.
            # Same PromQL shape as CPU but on working-set bytes vs the
            # memory limit. >100% means OOM is imminent.
            (
                GoldenSignalKind.SATURATION_MEMORY,
                "ratio",
                prom_queries.build_memory_saturation_query,
                {},
            ),
        ]

        out: list[AppGoldenSignal] = []
        for kind, unit, builder, extra in builders:
            plan = builder(
                app_slug=app.slug,
                environment_name=environment_name,
                workload_slug=workload_slug,
                range_seconds=seconds,
                **extra,
            )
            try:
                series = prom_client.query_range_series(
                    endpoint=endpoint,
                    promql=plan.promql,
                    start_unix=start_unix,
                    end_unix=end_unix,
                    step_seconds=step,
                )
            except PrometheusError:
                # Any single signal failing kills the panel — the FE
                # treats an empty list as "metrics not yet flowing".
                return []

            # Aggregate queries return at most one row. If Prometheus
            # returns zero rows the signal is empty but present so the
            # FE can render the card skeleton with the PromQL.
            pairs = series[0][1] if series else []
            out.append(
                AppGoldenSignal(
                    name=kind,
                    range_seconds=seconds,
                    samples=_samples_from_pairs(pairs),
                    promql=plan.promql,
                    unit=unit,
                )
            )
        return out

    @strawberry.field
    @require_permission(Permission.APP_READ)
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

        The FE distinguishes ``null`` ("HTTP metrics unavailable —
        omit the card") from a populated breakdown with zero series
        ("HTTP metrics available, but no traffic in this window")."""
        seconds = _clamp_range(range_seconds or _DEFAULT_RANGE_SECONDS)
        app = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True).only("id", "slug").first()
        if app is None:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(app=app, environment_name=environment_name)
        if endpoint is None:
            return None

        end_unix = int(_now_utc().timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        plan = prom_queries.build_status_code_breakdown_query(
            app_slug=app.slug,
            environment_name=environment_name,
            workload_slug=workload_slug,
            range_seconds=seconds,
        )
        try:
            rows = prom_client.query_range_series(
                endpoint=endpoint,
                promql=plan.promql,
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step,
                label_key="code",
            )
        except PrometheusError:
            return None

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

        return StatusCodeBreakdown(
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
    @require_permission(Permission.APP_READ)
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
        app = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True).only("id", "slug").first()
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
    @require_permission(Permission.APP_READ)
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
        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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
    @require_permission(Permission.APP_READ)
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
        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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

    @strawberry.field
    @require_permission(Permission.APP_READ)
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
        svc = (
            ManagedService.objects.select_related("registered_app", "app_environment")
            .filter(guid=str(managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return None

        kind = svc.kind
        if kind == ManagedService.Kind.POSTGRES:
            metric_set = self._POSTGRES_METRICS
            builder = prom_queries.build_managed_service_postgres_query
        elif kind == ManagedService.Kind.OBJECT_STORE:
            metric_set = self._OBJECT_STORE_METRICS
            builder = prom_queries.build_managed_service_object_store_query
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
    @require_permission(Permission.APP_READ)
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
        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
            .only("id", "slug")
            .first()
        )
        if app is None:
            return None

        endpoint = prom_client.resolve_prometheus_endpoint(
            app=app, environment_name=environment_name
        )
        if endpoint is None:
            return None

        end_unix = int(_now_utc().timestamp())
        start_unix = end_unix - seconds
        step = prom_queries.pick_step_seconds(seconds)

        cpu_plan = prom_queries.build_pod_cpu_usage_query(
            app_slug=app.slug,
            environment_name=environment_name,
            pod_name=pod_name,
            range_seconds=seconds,
        )
        mem_plan = prom_queries.build_pod_memory_usage_query(
            app_slug=app.slug,
            environment_name=environment_name,
            pod_name=pod_name,
            range_seconds=seconds,
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
                    namespace=cluster_observability.namespace_for_app(app),
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

        return PodResourceUsage(
            pod_name=pod_name,
            range_seconds=seconds,
            samples=samples,
            restart_count=restart_count,
            last_restart_at=last_restart_at,
        )
