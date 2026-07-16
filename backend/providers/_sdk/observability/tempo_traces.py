"""Tempo TraceDriver (#5).

Tempo is the matrix-default trace backend across all clouds. Pairs
naturally with Prometheus (metrics) + Loki (logs) for the
"PLG-stack" observability story; same Grafana dashboard set up.

Driver wraps Tempo's HTTP API: search for traces by service /
operation / duration / status, fetch a single trace by id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.trace import SpanRef, TraceDriver, TraceSummary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class TempoConfig:
    base_url: str
    """Tempo HTTP API root, e.g. http://tempo:3200"""

    timeout_seconds: int = 30
    bearer_token: str | None = None
    org_id: str | None = None
    """X-Scope-OrgID header for multi-tenant Tempo / Grafana Cloud."""

    http_client: Any | None = None


class TempoTraceDriver(TraceDriver):
    def __init__(self, *, config: TempoConfig) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultHttp(
                timeout=config.timeout_seconds,
                bearer_token=config.bearer_token,
                org_id=config.org_id,
            )

    @driver_op(driver="tempo_traces")
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
    ) -> list[TraceSummary]:
        url = f"{self._config.base_url.rstrip('/')}/api/search"
        params: dict[str, str] = {
            "start": since,
            "end": until,
            "limit": str(limit),
        }
        tags: list[str] = []
        if service:
            tags.append(f"service.name={service}")
        if operation:
            tags.append(f"name={operation}")
        if status:
            tags.append(f"status={status}")
        if tags:
            params["tags"] = " ".join(tags)
        if min_duration_ms is not None:
            params["minDuration"] = f"{int(min_duration_ms)}ms"

        response = self._http.get(url, params=params)
        body = response.json() if hasattr(response, "json") else response
        traces = body.get("traces", [])
        out: list[TraceSummary] = []
        for trace in traces:
            out.append(
                TraceSummary(
                    trace_id=trace.get("traceID", ""),
                    root_service=trace.get("rootServiceName", ""),
                    root_operation=trace.get("rootTraceName", ""),
                    span_count=int(trace.get("spanCount", 0) or 0),
                    duration_ms=float(trace.get("durationMs", 0.0) or 0.0),
                    status_code=trace.get("status", "UNSET"),
                )
            )
        return out

    @driver_op(driver="tempo_traces")
    def get_trace(self, trace_id: str) -> list[SpanRef]:
        url = f"{self._config.base_url.rstrip('/')}/api/traces/{trace_id}"
        response = self._http.get(url)
        body = response.json() if hasattr(response, "json") else response
        spans = body.get("spans", []) or self._extract_spans(body)
        out: list[SpanRef] = []
        for span in spans:
            out.append(self._span_from_dict(span=span, trace_id=trace_id))
        return out

    async def stream_spans(
        self,
        *,
        service: str | None = None,
        since: str,
        until: str,
    ) -> AsyncIterator[SpanRef]:
        # Tempo doesn't expose a server-side stream; emulate by
        # listing traces + fetching each. Production wires
        # OTLP-side streaming separately.
        traces = self.list_traces(
            service=service,
            since=since,
            until=until,
            limit=100,
        )
        for trace in traces:
            for span in self.get_trace(trace.trace_id):
                yield span

    def _extract_spans(self, body: Any) -> list[Any]:
        # Newer Tempo returns OTLP-shaped batches → flatten.
        spans: list[Any] = []
        for batch in body.get("batches", []) or []:
            for ils in batch.get("instrumentationLibrarySpans", []) or []:
                spans.extend(ils.get("spans", []) or [])
        return spans

    def _span_from_dict(self, *, span: dict[str, Any], trace_id: str) -> SpanRef:
        attrs = {}
        for attr in span.get("attributes", []) or []:
            value = attr.get("value", {}) or {}
            v = value.get("stringValue") or value.get("intValue") or value.get("boolValue") or ""
            attrs[attr.get("key", "")] = str(v)
        status_code = "UNSET"
        status = span.get("status", {}) or {}
        if status.get("code") == 1:
            status_code = "OK"
        elif status.get("code") == 2:
            status_code = "ERROR"

        start_ns = int(span.get("startTimeUnixNano", 0) or 0)
        end_ns = int(span.get("endTimeUnixNano", 0) or 0)
        duration_ms = max((end_ns - start_ns) / 1_000_000, 0.0)
        return SpanRef(
            trace_id=trace_id,
            span_id=span.get("spanId", ""),
            parent_span_id=span.get("parentSpanId") or None,
            operation=span.get("name", ""),
            service=attrs.get("service.name", ""),
            start_time=str(start_ns),
            duration_ms=duration_ms,
            status_code=status_code,
            attributes=attrs,
        )


class _DefaultHttp:
    def __init__(
        self,
        *,
        timeout: int = 30,
        bearer_token: str | None = None,
        org_id: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._token = bearer_token
        self._org_id = org_id

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        full = url
        if params:
            full = f"{url}?{urlencode(params)}"
        request = Request(full)
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        if self._org_id:
            request.add_header("X-Scope-OrgID", self._org_id)
        try:
            with urlopen(request, timeout=self._timeout) as raw:
                payload = raw.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(
                f"tempo HTTP {exc.code}: {exc.reason}",
            ) from exc
        return _Response(body=payload)


@dataclass
class _Response:
    body: str

    def json(self) -> Any:
        import json

        return json.loads(self.body)
