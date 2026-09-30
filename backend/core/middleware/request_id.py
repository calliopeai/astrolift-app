"""
Request-ID + OTel trace correlation middleware.

For every incoming request we:

1. Take an incoming ``X-Request-Id`` header if present (so callers can
   thread their own trace ID through), else mint a ULID.
2. Pull ``traceparent`` (W3C) into a :class:`TraceContext` so downstream
   logs can correlate without depending on the OTel SDK directly.
3. Stamp both onto ``request`` and into the contextvars used by the
   JSON formatter.
4. Echo ``X-Request-Id`` back to the client so they can grep our logs.
"""

from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from core.request_context import (
    TraceContext,
    generate_ulid,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
TRACEPARENT_HEADER = "HTTP_TRACEPARENT"


class RequestIdMiddleware(MiddlewareMixin):
    def process_request(self, request) -> None:
        rid = request.META.get(REQUEST_ID_HEADER) or generate_ulid()
        request.request_id = rid
        request._astrolift_request_id_token = set_request_id(rid)

        traceparent = request.META.get(TRACEPARENT_HEADER)
        if traceparent:
            ctx = _parse_traceparent(traceparent)
            if ctx is not None:
                request._astrolift_trace_token = set_trace_context(ctx)
                request.trace_context = ctx

    def process_response(self, request, response):
        rid = getattr(request, "request_id", None)
        if rid is not None:
            response["X-Request-Id"] = rid

        self._reset_context(request)
        return response

    def process_exception(self, request, exception):
        self._reset_context(request)
        return None

    @staticmethod
    def _reset_context(request):
        for name, reset in (
            ("_astrolift_request_id_token", reset_request_id),
            ("_astrolift_trace_token", reset_trace_context),
        ):
            token = getattr(request, name, None)
            if token is not None:
                delattr(request, name)
                reset(token)


def _parse_traceparent(value: str) -> TraceContext | None:
    # W3C traceparent: ``<version>-<trace-id>-<parent-id>-<flags>``
    parts = value.strip().split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_id = parts[1]
    span_id = parts[2]
    try:
        flags = int(parts[3], 16)
    except ValueError:
        return None
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    return TraceContext(trace_id=trace_id, span_id=span_id, flags=flags)
