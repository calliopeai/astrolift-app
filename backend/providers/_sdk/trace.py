"""TraceDriver protocol (#66) -- query distributed traces.

Backends: Tempo, Jaeger, X-Ray, Stackdriver Trace, Azure Monitor
Application Insights, Datadog APM, Honeycomb, Lightstep,
OTLP-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class SpanRef:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str
    service: str
    start_time: str
    duration_ms: float
    status_code: str
    """OK | ERROR | UNSET — OpenTelemetry status."""

    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    root_service: str
    root_operation: str
    span_count: int
    duration_ms: float
    status_code: str


class TraceDriver(Protocol):
    """Protocol for querying distributed traces."""

    def list_traces(
        self,
        *,
        service: str | None = None,
        operation: str | None = None,
        min_duration_ms: float | None = None,
        status: str | None = None,
        since: str,
        until: str,
        limit: int = 50,
    ) -> list[TraceSummary]: ...

    def get_trace(self, trace_id: str) -> list[SpanRef]: ...

    def stream_spans(
        self,
        *,
        service: str | None = None,
        since: str,
        until: str,
    ) -> AsyncIterator[SpanRef]: ...
