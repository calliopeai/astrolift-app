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
    without depending on enum ordinals."""

    TRAFFIC = "traffic"
    ERRORS = "errors"
    LATENCY_P50 = "latency_p50"
    LATENCY_P90 = "latency_p90"
    LATENCY_P99 = "latency_p99"
    SATURATION_CPU = "saturation_cpu"


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
