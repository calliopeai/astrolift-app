"""
Tests for OpenTelemetry trace correlation in log records (P0.5d #181).

Acceptance:

- ``trace_id`` and ``span_id`` appear in JSON log lines when an OTel
  span is active.
- No errors when OTel is not configured (fields simply absent).
- Live span takes priority over the W3C traceparent contextvar so
  in-process spans (Temporal workflows, deferred tasks) correlate.
- Request-id and trace-id coexist in the same record.
"""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from core.logging import JsonFormatter
from core.request_context import (
    TraceContext,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)


def _capture(formatter: logging.Formatter, name: str = "test_trace") -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream


def _last_json(stream: io.StringIO) -> dict:
    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


def test_no_trace_fields_when_no_span_or_traceparent():
    logger, stream = _capture(JsonFormatter())
    logger.info("uncorrelated")
    payload = _last_json(stream)
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_traceparent_contextvar_surfaces_when_no_otel_span():
    ctx = TraceContext(
        trace_id="11111111111111111111111111111111",
        span_id="2222222222222222",
    )
    token = set_trace_context(ctx)
    try:
        logger, stream = _capture(JsonFormatter())
        logger.info("from inbound traceparent")
        payload = _last_json(stream)
        assert payload["trace_id"] == ctx.trace_id
        assert payload["span_id"] == ctx.span_id
    finally:
        reset_trace_context(token)


def test_live_otel_span_overrides_traceparent_contextvar():
    pytest.importorskip("opentelemetry")
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    cv = TraceContext(
        trace_id="ffffffffffffffffffffffffffffffff",
        span_id="ffffffffffffffff",
    )
    token = set_trace_context(cv)
    try:
        with tracer.start_as_current_span("workflow") as span:
            span_ctx = span.get_span_context()
            expected_trace = format(span_ctx.trace_id, "032x")
            expected_span = format(span_ctx.span_id, "016x")

            logger, stream = _capture(JsonFormatter())
            logger.info("inside live span")
            payload = _last_json(stream)

            assert payload["trace_id"] == expected_trace
            assert payload["span_id"] == expected_span
            # Sanity: it really is from the live span, not the
            # contextvar.
            assert payload["trace_id"] != cv.trace_id
    finally:
        reset_trace_context(token)


def test_request_id_and_trace_id_coexist_on_the_same_record():
    pytest.importorskip("opentelemetry")
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    rid = "01TRACEANDREQUESTCOEXISTRID00"
    rid_token = set_request_id(rid)
    try:
        with tracer.start_as_current_span("handle"):
            logger, stream = _capture(JsonFormatter())
            logger.info("correlated")
            payload = _last_json(stream)
            assert payload["request_id"] == rid
            assert "trace_id" in payload
            assert "span_id" in payload
    finally:
        reset_request_id(rid_token)


def test_log_call_does_not_crash_when_opentelemetry_is_absent(monkeypatch):
    # Simulate the SDK being unimportable: the formatter wraps the
    # OTel import in try/except, so absent fields, no crash.
    saved = sys.modules.pop("opentelemetry", None)
    saved_trace = sys.modules.pop("opentelemetry.trace", None)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    try:
        logger, stream = _capture(JsonFormatter())
        logger.info("with otel disabled")
        payload = _last_json(stream)
        assert "trace_id" not in payload
        assert "span_id" not in payload
    finally:
        if saved is not None:
            sys.modules["opentelemetry"] = saved
        if saved_trace is not None:
            sys.modules["opentelemetry.trace"] = saved_trace
