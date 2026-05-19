"""GraphQL types for the App > Observability golden-signals surface
(#380)."""

from __future__ import annotations

import datetime as dt
import enum

import strawberry


@strawberry.type(name="AstroliftTimeSeriesPoint")
class TimeSeriesPoint:
    """One sample on a Prometheus-derived time-series.

    ``ts`` is the sample's UNIX wall-clock; ``value`` is the numeric
    sample. ``value`` may be 0 for a missing / NaN sample (the upstream
    Prometheus client coerces ``"NaN"`` payloads to 0 so the wire shape
    is uniform).
    """

    ts: dt.datetime
    value: float


@strawberry.enum
class GoldenSignalKind(enum.Enum):
    """The four SRE golden signals plus their conventional names.

    Mirrored as a string-valued enum so the FE can switch on the name
    without depending on enum ordinals.

    Latency carries four quantiles — p50 (typical user), p90 (legacy),
    p95 (SLO-canonical, added in #640), and p99 (tail outliers). The
    FE renders three at a time by default; the fourth is reachable via
    the PromQL disclosure so operators can paste into Grafana.

    Saturation carries two resources — CPU (in cores) and memory (in
    bytes); both come from kube-state-metrics + cAdvisor.
    """

    TRAFFIC = "traffic"
    ERRORS = "errors"
    LATENCY_P50 = "latency_p50"
    LATENCY_P90 = "latency_p90"
    LATENCY_P95 = "latency_p95"
    LATENCY_P99 = "latency_p99"
    SATURATION_CPU = "saturation_cpu"
    SATURATION_MEMORY = "saturation_memory"


@strawberry.type(name="AstroliftAppGoldenSignal")
class AppGoldenSignal:
    """One golden-signal time-series for one app/env over one window."""

    name: GoldenSignalKind
    range_seconds: int
    samples: list[TimeSeriesPoint]
    promql: str
    """The exact PromQL the resolver evaluated. Surfaced verbatim in
    the dev-mode disclosure so operators can paste into Grafana."""
    unit: str
    """Display unit hint for the FE: ``"rps"``, ``"ratio"``,
    ``"seconds"``, ``"percent"``."""


@strawberry.type(name="AstroliftStatusCodeSeries")
class StatusCodeSeries:
    """One row of the status-code breakdown matrix.

    ``code_class`` is one of ``"2xx" | "3xx" | "4xx" | "5xx" | "other"``;
    ``samples`` is the per-bucket request-rate. ``top_codes`` carries
    the up-to-five individual HTTP codes that made up the class over
    the window (descending by total volume) — used by the FE tooltip.
    """

    code_class: str
    samples: list[TimeSeriesPoint]
    top_codes: list[str]


@strawberry.type(name="AstroliftStatusCodeBreakdown")
class StatusCodeBreakdown:
    """Per-status-code stacked time-series for one app/env."""

    range_seconds: int
    series: list[StatusCodeSeries]
    promql: str


@strawberry.type(name="AstroliftWorkloadResourceGauge")
class WorkloadResourceGauge:
    """One resource (CPU or memory) gauge for the workload-detail page (#430).

    ``current`` / ``request`` / ``limit`` are all in the same unit (cores
    for CPU, bytes for memory) so the FE doesn't have to coerce. Either
    ``request`` or ``limit`` may be 0.0 when the workload's manifest
    omitted the bound — the FE picks the available denominator (limit
    when set, request when not) for the percentage gauge.

    ``percent_of_request`` and ``percent_of_limit`` are pre-computed
    server-side so two clients on the same gauge agree on the colour
    band even if they round differently. Both are 0.0..>100.0 (over-100
    means usage exceeded the bound — surfaced as a red gauge).
    """

    unit: str
    current: float
    request: float
    limit: float
    percent_of_request: float
    percent_of_limit: float


@strawberry.type(name="AstroliftWorkloadResourceUsage")
class WorkloadResourceUsage:
    """Per-workload live resource usage vs. requests/limits (#430).

    Powers the two gauges on the workload-detail page. ``None`` is
    returned at the resolver layer when the cluster has no Prometheus
    endpoint or every PromQL call errored — the FE renders the
    empty-state callout in either case.

    ``sourced_at`` is the wall-clock when the resolver queried
    Prometheus. The FE uses it to show "x seconds ago" so an operator
    can tell at-a-glance whether the poll loop is alive.
    """

    cpu: WorkloadResourceGauge
    memory: WorkloadResourceGauge
    sourced_at: dt.datetime


# ---- #482 — historical / aggregated log access -------------------
#
# Two surfaces:
#   * The plural live subscription reuses ``AstroliftAppLogLine`` from
#     the lifecycle schema (one shape for every log line on the wire,
#     live or historical).
#   * The historical query returns a ``AstroliftAppLogPage`` — items +
#     cursor + retention flag + the "this cluster has no aggregator
#     wired" flag (``historicalAvailable``) so the FE can render the
#     right empty state.


@strawberry.type(name="AstroliftAppLogQueryLine")
class AppLogLine:
    """One log line from the historical query (#482).

    Distinct from ``AstroliftAppLogLine`` (the live subscription type)
    because the historical query also carries a heuristic ``level``
    derived from the line text (or the structured-log ``level`` label
    when the backend reports it) so the FE doesn't have to re-classify
    server-side filtered results.
    """

    pod_name: str
    container: str
    timestamp: str
    """ISO-8601 timestamp string. Strings rather than ``datetime`` so
    nanosecond-precision values from Loki survive the round-trip."""

    message: str
    level: str
    """Heuristic level: ``error`` | ``warn`` | ``info`` | ``debug`` |
    ``other``. ``other`` when no structured label and no heuristic
    keyword matched."""

    stream: str
    """``stdout`` / ``stderr`` (best-effort — most aggregators report
    everything as ``stdout``)."""


@strawberry.type(name="AstroliftAppLogPage")
class AppLogPage:
    """One page of historical log lines for an app/env (#482).

    ``items`` is the page contents (oldest-first). ``next_cursor`` is
    opaque to the FE — pass it back unchanged to fetch the next page;
    empty string means end-of-window.

    ``reached_retention`` is true when the requested ``since`` predates
    the backend's configured retention — the page may still carry items
    (the visible slice) but the FE badges it with "earlier lines were
    discarded by the log backend."

    ``historical_available`` is false when the cluster has no
    log-aggregator driver configured. The FE distinguishes that case
    from "no lines in window" by switching the empty state to
    "live tail only on this cluster."

    ``total_count`` is best-effort — backends that can't compute it
    return -1; the FE treats negative as "unknown" and shows the
    page-size count instead. Loki returns -1 today; CloudWatch /
    Stackdriver can hand back exact counts when their query APIs do.
    """

    items: list[AppLogLine]
    next_cursor: str
    reached_retention: bool
    historical_available: bool
    total_count: int


@strawberry.type(name="AstroliftManagedServiceMetricSeries")
class ManagedServiceMetricSeries:
    """One metric time-series scoped to a managed service (#645 / #646).

    ``name`` identifies the metric on the wire (``connections`` /
    ``cpu`` / ``iops`` / ``slow_queries`` / ``replica_lag`` for
    relational DBs; ``bucket_size`` / ``request_count`` /
    ``errors_4xx`` / ``errors_5xx`` / ``egress_bytes`` for object
    stores). The FE switches on the name to pick a card title /
    formatter — keep the set deterministic so the panel layout stays
    stable.

    ``unit`` is a hint for the FE formatter (``count`` / ``bytes`` /
    ``percent`` / ``rps`` / ``seconds`` / ``ms``).

    ``source`` records which provider-side store the resolver queried
    (``cloudwatch`` / ``cloud_monitoring`` / ``azure_monitor`` /
    ``prometheus`` / ``unknown``) so an operator can verify in the UI
    *where* the data came from without re-reading the cluster row.
    """

    name: str
    unit: str
    samples: list[TimeSeriesPoint]
    source: str


@strawberry.type(name="AstroliftManagedServiceMetrics")
class ManagedServiceMetrics:
    """Per-managed-service metrics envelope (#645 + #646).

    Returns the family of time-series the FE renders as a side-by-side
    card grid below the golden-signals panel — driven by the
    managed-service ``kind`` (``postgres`` ⇒ RDS-style metrics;
    ``object_store`` ⇒ S3-style metrics).

    Empty ``series`` list means "the kind is supported but the cloud's
    metrics store had nothing for this resource over the window" — the
    FE shows the same "metrics not yet flowing" empty state the golden
    signals use.

    ``None`` from the resolver means "the kind isn't currently
    supported by the metrics resolver" (anything other than postgres /
    object_store today); the FE doesn't render a panel in that case.
    """

    managed_service_id: strawberry.ID
    kind: str
    name: str
    range_seconds: int
    series: list[ManagedServiceMetricSeries]


@strawberry.type(name="AstroliftPodResourceUsagePoint")
class PodResourceUsagePoint:
    """One sample on the per-pod CPU+memory sparkline (#713).

    Both values are filled in the same query so the FE can render a
    twin sparkline without two requests. CPU is in cores (rate over
    the cpu-seconds counter), memory is in bytes (working-set).
    """

    ts: dt.datetime
    cpu_cores: float
    memory_bytes: float


@strawberry.type(name="AstroliftPodResourceUsage")
class PodResourceUsage:
    """Per-pod CPU + memory time-series + restart history (#713).

    Powers the per-pod expander on the Observability tab. ``None`` from
    the resolver means the pod isn't visible to the platform (deleted,
    wrong tenant, wrong cluster) or the cluster has no Prometheus —
    the FE renders the "metrics not flowing" empty state in either
    case.

    ``restart_count`` is the kubelet's per-pod restart counter; the
    expander surfaces it alongside the last-restart timestamp so an
    operator scanning a CrashLoopBackOff row sees both "how often" and
    "when" at a glance.
    """

    pod_name: str
    range_seconds: int
    samples: list[PodResourceUsagePoint]
    restart_count: int
    last_restart_at: dt.datetime | None


@strawberry.type(name="AstroliftAppUrlHealth")
class AppUrlHealth:
    """One probe result for an app URL (#406).

    ``status`` is one of ``"ok" | "degraded" | "down" | "unknown"``;
    the FE uses it to pick a pill color (green / amber / red / grey)
    without coupling to status-code numeric ranges. ``unknown`` is
    reserved for the no-probe-yet state — the live-probe resolver
    never returns it (a synchronous probe always produces ``ok`` /
    ``degraded`` / ``down``), but it's part of the wire enum so the
    FE can render an "unchecked" pill before the first round trip
    lands.

    ``status_code`` is the HTTP status from the response (null when
    the request never produced one — connect refused, DNS failure,
    timeout). ``latency_ms`` is the wall-clock from request issue to
    response received, including TLS + redirects. ``message`` is a
    short operator-facing reason and is empty on the happy path.
    """

    url: str
    status: str
    status_code: int | None
    latency_ms: int | None
    last_checked: dt.datetime
    message: str


@strawberry.type(name="AstroliftPromqlSeries")
class PromqlSeries:
    """One series (matrix row) from a raw PromQL execution (#750).

    ``metric_labels`` is the label dict the Prometheus engine attached
    to the series — for aggregated expressions it will be empty; for
    ``sum by (code) (...)`` it will carry ``{"code": "200"}`` etc.
    It is serialised as a JSON scalar so the FE doesn't need a fixed
    schema per-query.

    ``values`` is a list of ``[ts_unix, value]`` pairs — the same
    shape Prometheus's ``query_range`` returns.  ``ts`` is UNIX seconds
    as a float; ``value`` is the numeric sample (NaN is coerced to 0).
    """

    metric_labels: strawberry.scalars.JSON
    """Label dict from the Prometheus engine — serialised as JSON."""

    values: list[TimeSeriesPoint]
    """Ordered list of (timestamp, value) samples."""


@strawberry.type(name="AstroliftExecutePromqlResult")
class ExecutePromqlResult:
    """Envelope returned by ``astroliftExecutePromql`` (#750).

    ``ok`` is ``False`` when the cluster has no Prometheus endpoint
    configured or the PromQL expression was rejected by Prometheus.
    ``error`` carries the operator-facing reason; ``series`` is empty
    in the error case.

    The caller is expected to show a "metrics unavailable" state when
    ``ok`` is ``False`` rather than bubbling the error through a
    GraphQL error field.
    """

    ok: bool
    error: str
    series: list[PromqlSeries]


@strawberry.type(name="AstroliftTraceSpan")
class TraceSpan:
    """One span inside a distributed trace (#749).

    Mirrors the ``SpanRef`` data class from the provider SDK's
    ``TraceDriver`` protocol. ``attributes`` is the span's OTEL
    attribute map serialised as JSON.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str
    service: str
    start_time: str
    """UNIX nanoseconds as a string (OTEL convention)."""
    duration_ms: float
    status_code: str
    """OK | ERROR | UNSET"""
    attributes: strawberry.scalars.JSON


@strawberry.type(name="AstroliftAppTrace")
class AppTrace:
    """A summary of one distributed trace (#749).

    Returned by ``astroliftAppTraces``. The ``traceId`` is passed to
    ``astroliftTraceSpans`` to retrieve the full span set.
    """

    trace_id: str
    root_service: str
    root_operation: str
    span_count: int
    duration_ms: float
    status_code: str
    """OK | ERROR | UNSET"""


@strawberry.type(name="AstroliftAppEndpointMetric")
class AppEndpointMetric:
    """Per-HTTP-route golden-signal snapshot (#748).

    Returned by ``astroliftAppEndpointMetrics`` — one row per unique
    ``http_route`` label seen in Prometheus over the requested window.

    ``http_route`` is the OpenTelemetry semantic-convention label for
    the matched URL template (e.g. ``GET /api/users/{id}``).
    Instrumentation libraries populate it automatically.

    Latency values are in milliseconds; error rate is a ratio in
    [0.0, 1.0+] (>1 = pathological; the FE clips the bar at 100%).
    A ``None`` latency value means no histogram data arrived yet (the
    app hasn't sent a histogram metric for that route).
    """

    route: str
    request_rate: float
    """Requests / second over the queried window."""
    error_rate_ratio: float
    """5xx / total rate ratio. 0.0 when no errors."""
    p50_ms: float | None
    p90_ms: float | None
    p99_ms: float | None
