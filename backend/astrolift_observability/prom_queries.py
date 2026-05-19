"""Pure PromQL builders for the App > Observability golden-signals
surface (#380).

Each builder takes the app slug + environment name + window width and
returns a :class:`QueryPlan` carrying:

* ``promql`` — the rendered PromQL expression. Dev-mode disclosure in
  the UI surfaces this verbatim so operators can paste into Grafana.
* ``labels`` — the dict of PromQL label matchers actually used. Kept
  separate from ``promql`` so the resolver / tests can assert label
  shape without re-parsing the string.

The builders are pure: no HTTP, no Django, no globals. They run the
same label-value sanitizer the existing operations Prometheus path
runs (``astrolift_operations.prometheus_client.sanitize_label_value``)
so a rogue caller can't break out of the label-match quoting.

PromQL contract (matches the operator-facing copy in the issue):

* request rate (traffic): ``sum(rate(http_requests_total{...}[<w>]))``
* error rate (errors):    ``sum(rate(http_requests_total{...,code=~"5.."}[<w>])) / clamp_min(sum(rate(http_requests_total{...}[<w>])), 1e-9)``
* latency p50/p90/p99:    ``histogram_quantile(q, sum by (le)(rate(http_request_duration_seconds_bucket{...}[<w>])))``
* cpu saturation:         ``sum(rate(container_cpu_usage_seconds_total{...}[<w>])) / sum(kube_pod_container_resource_limits{resource="cpu",...})``
* status-code breakdown:  ``sum by (code) (rate(http_requests_total{...}[<w>]))``

The rate window (``<w>``) widens with the time-range so short ranges
are snappy and long ranges stay stable. The rule lives in
:func:`pick_rate_window` so callers and tests share it.
"""

from __future__ import annotations

import dataclasses

from astrolift_operations.prometheus_client import sanitize_label_value

# ----------------------------------------------------------------------
# QueryPlan
# ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class QueryPlan:
    """Pure-data PromQL bundle.

    The resolver passes ``promql`` straight to the Prometheus HTTP
    API; tests assert against both the rendered string and the
    ``labels`` map so a refactor can change quoting / ordering without
    silently breaking the matcher contract.
    """

    promql: str
    labels: dict[str, str]
    rate_window: str
    """The rate-of-change window used inside the PromQL — exposed so
    range-query callers can pass the same width as the ``step`` lower
    bound and keep the series honest."""


# ----------------------------------------------------------------------
# rate-window selection
# ----------------------------------------------------------------------


def pick_rate_window(range_seconds: int) -> str:
    """Pick the ``rate(...[<w>])`` window for ``range_seconds``.

    The mapping is:

    * <= 1h   → ``1m``  (snappy on the live tab)
    * <= 6h   → ``5m``  (smooths sub-minute jitter)
    * <= 24h  → ``5m``
    * <= 7d   → ``15m`` (longer windows need wider smoothing)
    * else    → ``1h``

    Callers (resolver + range-query step calc) share this rule so the
    PromQL window and the request-range step always agree.
    """
    if range_seconds <= 60 * 60:
        return "1m"
    if range_seconds <= 24 * 60 * 60:
        return "5m"
    if range_seconds <= 7 * 24 * 60 * 60:
        return "15m"
    return "1h"


def pick_step_seconds(range_seconds: int, *, max_points: int = 360) -> int:
    """Pick a ``step`` for ``query_range`` so the returned matrix has
    at most ``max_points`` samples.

    Recharts struggles past a few hundred points on the wire; 360 is
    the standard ceiling we use across the metrics surface (one point
    per minute over six hours, one per ~four-minutes over a day, one
    per ~half-hour over a week).
    """
    if range_seconds <= 0:
        raise ValueError("range_seconds must be positive")
    step = range_seconds // max_points
    if step < 15:
        # Prometheus ``step`` < 15s is rarely useful and burns CPU; clamp.
        return 15
    return int(step)


# ----------------------------------------------------------------------
# label-matcher rendering
# ----------------------------------------------------------------------


def _build_labels(
    *,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the label-matcher dict for ``app_slug`` (+ env + workload).

    All inputs are sanitized via the same allow-list the operations
    Prometheus client uses — a caller can't smuggle in a quote or a
    backslash that would break out of the PromQL label match.

    ``workload_slug`` narrows the metric stream to a single workload
    (api / worker / scheduler / ...). When omitted the query rolls up
    every workload under the app — current pre-#422 behavior.
    """
    labels: dict[str, str] = {"app": sanitize_label_value(app_slug)}
    if environment_name:
        labels["environment"] = sanitize_label_value(environment_name)
    if workload_slug:
        labels["workload"] = sanitize_label_value(workload_slug)
    if extra:
        for k, v in extra.items():
            # The label *value* is sanitized; the *key* must be a
            # bare PromQL identifier — caller's responsibility.
            labels[k] = sanitize_label_value(v)
    return labels


def _render_label_match(labels: dict[str, str]) -> str:
    """Render ``{a="x",b="y"}`` with deterministic key order."""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def _render_label_match_with_extra(
    labels: dict[str, str],
    extra: str | None,
) -> str:
    """Render ``{a="x",<extra>}`` — used for inline regex matchers
    (e.g. ``code=~"5.."``) that don't fit the plain ``=`` map.

    ``extra`` is appended verbatim inside the braces; the caller is
    responsible for sanitizing anything user-controlled in it.
    """
    base = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    if extra:
        return "{" + base + "," + extra + "}"
    return "{" + base + "}"


# ----------------------------------------------------------------------
# builders
# ----------------------------------------------------------------------


def build_request_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Traffic — requests / second.

    ``sum(rate(http_requests_total{app=...}[<w>]))``
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    expr = f"sum(rate(http_requests_total{_render_label_match(labels)}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_error_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Errors — 5xx rate / total rate.

    ``sum(rate(http_requests_total{...,code=~"5.."}[<w>])) / clamp_min(sum(rate(http_requests_total{...}[<w>])), 1e-9)``

    ``clamp_min`` guards the zero-traffic case so the resolver gets
    ``0`` instead of a NaN (Prometheus serializes NaN as the string
    ``"NaN"`` which the existing client coerces to ``0.0`` but only
    after a parse failure — cleaner to clamp at the query layer).
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    base_match = _render_label_match(labels)
    err_match = _render_label_match_with_extra(labels, 'code=~"5.."')
    expr = (
        f"sum(rate(http_requests_total{err_match}[{rate_window}])) "
        f"/ clamp_min(sum(rate(http_requests_total{base_match}[{rate_window}])), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_latency_quantile_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    quantile: float,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Latency — histogram_quantile over the request-latency bucket
    histogram.

    ``histogram_quantile(q, sum by (le)(rate(http_request_duration_seconds_bucket{...}[<w>])))``
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be in (0, 1); got {quantile}")
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = (
        f"histogram_quantile({quantile:g}, "
        f"sum by (le)(rate(http_request_duration_seconds_bucket{match}[{rate_window}])))"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_cpu_saturation_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Saturation — actual CPU vs. requested limit.

    ``sum(rate(container_cpu_usage_seconds_total{app=...}[<w>])) / sum(kube_pod_container_resource_limits{app=...,resource="cpu"})``

    Series is a ratio in [0, 1+] (>1 = over-limit / throttling). The
    UI shells this into a percent. ``kube_pod_container_resource_limits``
    comes from kube-state-metrics which the bootstrap recipe already
    installs.
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    usage_match = _render_label_match(labels)
    limits_match = _render_label_match_with_extra(labels, 'resource="cpu"')
    expr = (
        f"sum(rate(container_cpu_usage_seconds_total{usage_match}[{rate_window}])) "
        f"/ clamp_min(sum(kube_pod_container_resource_limits{limits_match}), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_memory_saturation_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Saturation — working-set memory vs. requested limit (#642).

    ``sum(container_memory_working_set_bytes{app=...}) / sum(kube_pod_container_resource_limits{app=...,resource="memory"})``

    Series is a ratio in [0, 1+] (>1 = over-limit ⇒ OOMKill imminent).
    Working-set bytes is what the kubelet uses for OOM accounting; the
    request/limit gauge comes from kube-state-metrics same as CPU.

    No ``rate(...)`` wrapper — memory is a gauge, not a counter, so
    the value at the latest scrape is what we want. Prometheus's
    ``query_range`` still produces one sample per ``step`` (the
    last-scrape value within each window).
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    usage_match = _render_label_match(labels)
    limits_match = _render_label_match_with_extra(labels, 'resource="memory"')
    expr = (
        f"sum(container_memory_working_set_bytes{usage_match}) "
        f"/ clamp_min(sum(kube_pod_container_resource_limits{limits_match}), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_pod_cpu_usage_query(
    *,
    app_slug: str,
    environment_name: str | None,
    pod_name: str,
    range_seconds: int,
) -> QueryPlan:
    """Per-pod CPU usage in cores over time (#713).

    ``sum(rate(container_cpu_usage_seconds_total{app=...,pod="..."}[<w>]))``

    Narrowed by the pod's name (cAdvisor exposes ``pod`` as a label on
    every container metric). The result is a per-pod sparkline that
    sums across containers in the pod — same shape as the app-level
    saturation but with one extra label matcher.
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
    )
    labels["pod"] = sanitize_label_value(pod_name)
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = f"sum(rate(container_cpu_usage_seconds_total{match}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_pod_memory_usage_query(
    *,
    app_slug: str,
    environment_name: str | None,
    pod_name: str,
    range_seconds: int,
) -> QueryPlan:
    """Per-pod memory working-set bytes over time (#713).

    ``sum(container_memory_working_set_bytes{app=...,pod="..."})``

    Gauge (no rate). Same per-pod narrowing as the CPU query above.
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
    )
    labels["pod"] = sanitize_label_value(pod_name)
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = f"sum(container_memory_working_set_bytes{match})"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_status_code_breakdown_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Per-status-code stacked time-series.

    ``sum by (code) (rate(http_requests_total{...}[<w>]))``

    The resolver groups the returned matrix into 2xx / 3xx / 4xx / 5xx
    classes plus a top-5 individual-code list for tooltip use.
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = f"sum by (code) (rate(http_requests_total{match}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


# ----------------------------------------------------------------------
# Workload resource-usage builders (#430)
# ----------------------------------------------------------------------
#
# Instant (not range) queries used by the workload-detail gauges. The
# usage queries pick a 1m rate window so a spike at t-30s shows up
# without smoothing it out; the request/limit queries are bare sum() on
# kube-state-metrics gauges because requests and limits don't change
# minute-to-minute (only at deploy time).
#
# All four use the same ``app + workload + environment`` label
# matchers — narrower than the app-level golden signals because the
# gauges are per-workload-only by design.


def build_workload_cpu_usage_query(
    *,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str,
) -> QueryPlan:
    """CPU usage in cores (1m rate over CPU-seconds counter).

    ``sum(rate(container_cpu_usage_seconds_total{...}[1m]))``

    ``rate(... seconds[1m])`` already lands on a "cores currently in
    use" number — a series valued 0.25 means ~250m of CPU consumed
    over the last minute. The FE compares this directly against the
    request / limit cores returned by the matching gauge query.
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    match = _render_label_match(labels)
    expr = f"sum(rate(container_cpu_usage_seconds_total{match}[1m]))"
    return QueryPlan(promql=expr, labels=labels, rate_window="1m")


def build_workload_memory_usage_query(
    *,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str,
) -> QueryPlan:
    """Memory usage in bytes — ``container_memory_working_set_bytes``.

    Working-set bytes is what the kubelet uses for OOM accounting, so
    it's the right number to compare against the memory limit (the FE
    can't put "is this pod near OOM?" any plainer than 'usage/limit
    in percent').
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    match = _render_label_match(labels)
    expr = f"sum(container_memory_working_set_bytes{match})"
    return QueryPlan(promql=expr, labels=labels, rate_window="instant")


def build_workload_resource_request_query(
    *,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str,
    resource: str,
) -> QueryPlan:
    """Per-resource requests aggregate from kube-state-metrics.

    ``sum(kube_pod_container_resource_requests{app=...,resource="cpu|memory"})``

    The exported gauge is already in the same unit as the usage query
    (cores for CPU, bytes for memory), so the resolver can divide
    usage / request without conversion.
    """
    if resource not in ("cpu", "memory"):
        raise ValueError(f"resource must be cpu|memory; got {resource!r}")
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    match = _render_label_match_with_extra(labels, f'resource="{resource}"')
    expr = f"sum(kube_pod_container_resource_requests{match})"
    return QueryPlan(promql=expr, labels=labels, rate_window="instant")


# ----------------------------------------------------------------------
# Managed-service metric builders (#645 + #646)
# ----------------------------------------------------------------------
#
# Postgres metrics come from the postgres-exporter sidecar the
# postgres_cnpg / aurora / cloudsql variants all ship. The exporter
# exposes the metrics under the ``pg_*`` namespace with a
# ``managed_service`` label set to the service guid — same label the
# kube-state-metrics ``app`` / ``workload`` chain uses elsewhere.
#
# Object-store metrics come from the s3-exporter (per-cloud sidecar
# the platform's storage workflow installs alongside the bucket). The
# label shape mirrors the postgres path: one ``managed_service``
# matcher narrows to a single service.
#
# Both builders take ``service_guid`` instead of ``app_slug`` because
# they're indexed by the service, not the app — one app can have many
# managed services of the same kind, and the FE renders one panel per
# managed-service row.


def build_managed_service_postgres_query(
    *,
    service_guid: str,
    metric: str,
    range_seconds: int,
) -> QueryPlan:
    """One postgres-exporter metric scoped to one managed-service guid.

    ``metric`` is one of ``connections`` / ``cpu`` / ``iops`` /
    ``slow_queries`` / ``replica_lag``. Each maps to a PromQL series
    over the standard ``pg_*`` exporter names:

    * connections → ``sum(pg_stat_activity_count{managed_service="<g>"})``
    * cpu         → ``sum(rate(process_cpu_seconds_total{managed_service="<g>",job="postgres-exporter"}[<w>]))``
    * iops        → ``sum(rate(pg_stat_database_blks_read{managed_service="<g>"}[<w>])) + sum(rate(pg_stat_database_blks_hit{managed_service="<g>"}[<w>]))``
    * slow_queries→ ``sum(rate(pg_stat_statements_calls_above_1s{managed_service="<g>"}[<w>]))``
    * replica_lag → ``max(pg_replication_lag_seconds{managed_service="<g>"})``
    """
    labels = {"managed_service": sanitize_label_value(service_guid)}
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)

    if metric == "connections":
        expr = f"sum(pg_stat_activity_count{match})"
    elif metric == "cpu":
        labels_with_job = dict(labels)
        labels_with_job["job"] = "postgres-exporter"
        match_with_job = _render_label_match(labels_with_job)
        expr = f"sum(rate(process_cpu_seconds_total{match_with_job}[{rate_window}]))"
        labels = labels_with_job
    elif metric == "iops":
        expr = (
            f"sum(rate(pg_stat_database_blks_read{match}[{rate_window}])) "
            f"+ sum(rate(pg_stat_database_blks_hit{match}[{rate_window}]))"
        )
    elif metric == "slow_queries":
        expr = f"sum(rate(pg_stat_statements_calls_above_1s{match}[{rate_window}]))"
    elif metric == "replica_lag":
        expr = f"max(pg_replication_lag_seconds{match})"
    else:
        raise ValueError(f"unknown postgres metric: {metric!r}")

    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_managed_service_object_store_query(
    *,
    service_guid: str,
    metric: str,
    range_seconds: int,
) -> QueryPlan:
    """One object-store metric scoped to one managed-service guid.

    ``metric`` is one of ``bucket_size`` / ``request_count`` /
    ``errors_4xx`` / ``errors_5xx`` / ``egress_bytes``. Each maps to a
    PromQL series over the standard ``s3_exporter_*`` names:

    * bucket_size  → ``sum(s3_bucket_size_bytes{managed_service="<g>"})``
    * request_count→ ``sum(rate(s3_request_count_total{managed_service="<g>"}[<w>]))``
    * errors_4xx   → ``sum(rate(s3_request_count_total{managed_service="<g>",code=~"4.."}[<w>]))``
    * errors_5xx   → ``sum(rate(s3_request_count_total{managed_service="<g>",code=~"5.."}[<w>]))``
    * egress_bytes → ``sum(rate(s3_bytes_downloaded_total{managed_service="<g>"}[<w>]))``
    """
    labels = {"managed_service": sanitize_label_value(service_guid)}
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)

    if metric == "bucket_size":
        expr = f"sum(s3_bucket_size_bytes{match})"
    elif metric == "request_count":
        expr = f"sum(rate(s3_request_count_total{match}[{rate_window}]))"
    elif metric == "errors_4xx":
        err_match = _render_label_match_with_extra(labels, 'code=~"4.."')
        expr = f"sum(rate(s3_request_count_total{err_match}[{rate_window}]))"
    elif metric == "errors_5xx":
        err_match = _render_label_match_with_extra(labels, 'code=~"5.."')
        expr = f"sum(rate(s3_request_count_total{err_match}[{rate_window}]))"
    elif metric == "egress_bytes":
        expr = f"sum(rate(s3_bytes_downloaded_total{match}[{rate_window}]))"
    else:
        raise ValueError(f"unknown object_store metric: {metric!r}")

    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_workload_resource_limit_query(
    *,
    app_slug: str,
    environment_name: str | None,
    workload_slug: str,
    resource: str,
) -> QueryPlan:
    """Per-resource limits aggregate from kube-state-metrics.

    ``sum(kube_pod_container_resource_limits{app=...,resource="cpu|memory"})``

    Same shape as the request query, different metric name; cleaner
    than one builder with a kind kwarg because the call sites at the
    resolver layer pair them up explicitly.
    """
    if resource not in ("cpu", "memory"):
        raise ValueError(f"resource must be cpu|memory; got {resource!r}")
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    match = _render_label_match_with_extra(labels, f'resource="{resource}"')
    expr = f"sum(kube_pod_container_resource_limits{match})"
    return QueryPlan(promql=expr, labels=labels, rate_window="instant")


# ----------------------------------------------------------------------
# Per-endpoint (http_route) metric builders (#748)
# ----------------------------------------------------------------------
#
# These builders emit ``sum by (http_route)`` queries that fan out the
# golden-signal metrics by HTTP route label.  The ``http_route``
# label is the OpenTelemetry semantic convention for the matched URL
# template (e.g. ``GET /api/users/{id}``) — instrumentation libraries
# (OpenTelemetry SDK, otelhttp middleware, etc.) populate it
# automatically so no per-app config is needed.
#
# The queries use the same label-matcher sanitization and rate-window
# rules as the golden-signals builders to keep behaviour consistent.


def build_endpoint_request_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Per-route traffic — requests / second grouped by ``http_route``.

    ``sum by (http_route) (rate(http_requests_total{...}[<w>]))``
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = f"sum by (http_route) (rate(http_requests_total{match}[{rate_window}]))"
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_endpoint_error_rate_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Per-route error rate — ratio of 5xx to total, grouped by ``http_route``.

    ``sum by (http_route) (rate(http_requests_total{...,code=~"5.."}[<w>]))
    / clamp_min(sum by (http_route) (rate(http_requests_total{...}[<w>])), 1e-9)``
    """
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    base_match = _render_label_match(labels)
    err_match = _render_label_match_with_extra(labels, 'code=~"5.."')
    expr = (
        f"sum by (http_route) (rate(http_requests_total{err_match}[{rate_window}])) "
        f"/ clamp_min(sum by (http_route) (rate(http_requests_total{base_match}[{rate_window}])), 1e-9)"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)


def build_endpoint_latency_quantile_query(
    *,
    app_slug: str,
    environment_name: str | None,
    range_seconds: int,
    quantile: float,
    workload_slug: str | None = None,
) -> QueryPlan:
    """Per-route latency quantile, grouped by ``http_route``.

    ``histogram_quantile(q, sum by (http_route, le)
        (rate(http_request_duration_seconds_bucket{...}[<w>])))``

    Multiplied by 1000 at the resolver layer to produce milliseconds.
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be in (0, 1); got {quantile}")
    labels = _build_labels(
        app_slug=app_slug,
        environment_name=environment_name,
        workload_slug=workload_slug,
    )
    rate_window = pick_rate_window(range_seconds)
    match = _render_label_match(labels)
    expr = (
        f"histogram_quantile({quantile:g}, "
        f"sum by (http_route, le)(rate(http_request_duration_seconds_bucket{match}[{rate_window}])))"
    )
    return QueryPlan(promql=expr, labels=labels, rate_window=rate_window)
