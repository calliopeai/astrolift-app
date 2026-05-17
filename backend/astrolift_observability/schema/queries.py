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

from astrolift_observability import prom_client, prom_queries
from astrolift_observability.schema.types import (
    AppGoldenSignal,
    GoldenSignalKind,
    StatusCodeBreakdown,
    StatusCodeSeries,
    TimeSeriesPoint,
)
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
