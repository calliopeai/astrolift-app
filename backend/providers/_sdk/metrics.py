"""MetricsDriver protocol -- query metrics for an app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MetricDatapoint:
    timestamp: str
    value: float


@dataclass(frozen=True)
class MetricSeries:
    metric: str
    labels: dict[str, str]
    datapoints: list[MetricDatapoint] = field(default_factory=list)


class MetricsDriver(Protocol):
    """Protocol for querying metrics (CPU, memory, request rate, error rate, latency).

    Backends: Prometheus, CloudWatch, Stackdriver, Azure Monitor, Datadog,
    Honeycomb, New Relic, Grafana Cloud, OTLP-compatible.
    """

    def query_metric(
        self,
        metric: str,
        labels: dict[str, str],
        range_start: str,
        range_end: str,
    ) -> MetricSeries: ...
