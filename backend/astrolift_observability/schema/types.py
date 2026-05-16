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
