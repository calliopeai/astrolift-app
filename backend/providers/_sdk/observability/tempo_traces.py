"""Tempo TraceDriver (#5).

Tempo is the matrix-default trace backend across all clouds. Pairs
naturally with Prometheus (metrics) + Loki (logs) for the
"PLG-stack" observability story; same Grafana dashboard set up.

Driver wraps Tempo's HTTP API: search for traces by service /
operation / duration / status, fetch a single trace by id.
"""

from __future__ import annotations

import json
import re
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
        try:
            return self._read_trace(trace_id)
        except Exception:
            # driver_op records exceptions. Never send provider payload-derived errors
            # into its logs/OTel exception events (for example malformed timestamps).
            raise RuntimeError("Tempo trace read failed") from None

    def _read_trace(self, trace_id: str) -> list[SpanRef]:
        url = f"{self._config.base_url.rstrip('/')}/api/traces/{trace_id}"
        response = self._http.get(url)
        body = response.json() if hasattr(response, "json") else response
        if body.get("spans"):
            if len(body["spans"]) > 2000:
                raise RuntimeError("Tempo trace exceeds the supported span bound")
            return [self._span_from_dict(span=span, trace_id=trace_id) for span in body["spans"]]
        out = []
        batches = body.get("batches", []) or body.get("resourceSpans", []) or []
        for batch in batches:
            resource = self._attributes((batch.get("resource") or {}).get("attributes", []))
            scopes = batch.get("scopeSpans", []) or batch.get("instrumentationLibrarySpans", []) or []
            for scope in scopes:
                for span in scope.get("spans", []) or []:
                    out.append(self._span_from_dict(span=span, trace_id=trace_id, resource=resource))
                    if len(out) > 2000:
                        raise RuntimeError("Tempo trace exceeds the supported span bound")
        return out

    def search_scoped(
        self,
        *,
        resource_attributes,
        since,
        until,
        limit=10,
        service=None,
        status=None,
        trace_id=None,
        operation=None,
        min_duration_ms=None,
    ):
        if not resource_attributes:
            raise ValueError("A trace search requires resource attribution")
        predicates = [
            f"resource.{json.dumps(key, ensure_ascii=False)} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in sorted(resource_attributes.items())
        ]
        if service:
            predicates.append(f"resource.service.name = {json.dumps(service, ensure_ascii=False)}")
        if operation:
            predicates.append(f"span:name = {json.dumps(operation, ensure_ascii=False)}")
        if min_duration_ms is not None:
            predicates.append(f"span:duration >= {int(min_duration_ms)}ms")
        if status in {"OK", "ERROR"}:
            predicates.append(f"span:status = {status.lower()}")
        if trace_id:
            if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
                raise ValueError("Invalid trace identity")
            predicates.append(f"trace:id = {json.dumps(trace_id)}")
        response = self._http.get(
            f"{self._config.base_url.rstrip('/')}/api/search",
            params={
                "q": "{ " + " && ".join(predicates) + " }",
                "start": str(since),
                "end": str(until),
                "limit": str(min(max(limit, 1), 21)),
            },
        )
        body = response.json() if hasattr(response, "json") else response
        ids = []
        for row in (body.get("traces") or [])[:21]:
            identity = row.get("traceID", "")
            if re.fullmatch(r"[0-9a-f]{32}", identity) and identity not in ids:
                ids.append(identity)
        return ids

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

    @staticmethod
    def _attributes(rows):
        attrs = {}
        for row in rows or []:
            key = row.get("key", "")
            value = row.get("value") or {}
            values = [value[k] for k in ("stringValue", "intValue", "boolValue", "doubleValue") if k in value]
            rendered = str(values[0]) if len(values) == 1 else ""
            if key in attrs and attrs[key] != rendered:
                attrs[key] = ""
            else:
                attrs[key] = rendered
        return attrs

    def _span_from_dict(self, *, span, trace_id, resource=None):
        attrs = self._attributes(span.get("attributes"))
        resource = dict(resource or {})
        status_code = {1: "OK", 2: "ERROR"}.get((span.get("status") or {}).get("code"), "UNSET")
        start_ns = int(span.get("startTimeUnixNano", 0) or 0)
        end_ns = int(span.get("endTimeUnixNano", 0) or 0)
        return SpanRef(
            trace_id=span.get("traceId") or trace_id,
            span_id=span.get("spanId", ""),
            parent_span_id=span.get("parentSpanId") or None,
            operation=span.get("name", ""),
            service=resource.get("service.name", attrs.get("service.name", "")),
            start_time=str(start_ns),
            duration_ms=max((end_ns - start_ns) / 1_000_000, 0.0),
            status_code=status_code,
            attributes=attrs,
            resource_attributes=resource,
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
        request = Request(full, headers={"Accept": "application/json"})
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        if self._org_id:
            request.add_header("X-Scope-OrgID", self._org_id)
        try:
            with urlopen(request, timeout=self._timeout) as raw:
                payload_bytes = raw.read(8 * 1024 * 1024 + 1)
                if len(payload_bytes) > 8 * 1024 * 1024:
                    raise RuntimeError("Tempo response exceeds the supported size bound")
                payload = payload_bytes.decode("utf-8")
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
