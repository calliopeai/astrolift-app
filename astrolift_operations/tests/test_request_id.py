"""
Tests for the request-id middleware (P0.5b #179).

Drives a real Django view through the test client so we exercise the
full middleware stack — RequestIdMiddleware mints a ULID, stamps it
on the contextvar, echoes ``X-Request-Id`` on the response, and
clears the contextvar after the response is built.
"""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from django.http import JsonResponse
from django.test import RequestFactory, override_settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from core.logging import JsonFormatter, get_request_id
from core.middleware.request_id import RequestIdMiddleware
from core.request_context import (
    TraceContext,
    generate_ulid,
    get_request_id as ctx_get_request_id,
    get_trace_context,
)


# ---- ULID basic shape -------------------------------------------


_ULID_REGEX = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_generate_ulid_shape():
    rid = generate_ulid()
    assert _ULID_REGEX.match(rid), rid


def test_generate_ulid_time_sortable_when_called_in_sequence():
    # ULIDs minted milliseconds apart sort lexicographically.
    a = generate_ulid()
    # bump millisecond
    import time as _t

    _t.sleep(0.002)
    b = generate_ulid()
    assert a < b


# ---- Middleware unit (no view) ----------------------------------


def test_middleware_mints_ulid_and_round_trips_header():
    rf = RequestFactory()
    request = rf.get("/anything")
    mw = RequestIdMiddleware(get_response=lambda r: JsonResponse({"ok": True}))

    before = ctx_get_request_id()
    response = mw(request)
    rid = response["X-Request-Id"]
    assert _ULID_REGEX.match(rid), rid
    # Contextvar reverts to the prior value after process_response —
    # i.e. the in-request value did not leak.
    assert ctx_get_request_id() == before
    assert ctx_get_request_id() != rid


def test_middleware_honors_inbound_x_request_id():
    rf = RequestFactory()
    rid = "01HONORED_INBOUND_REQUEST_ID00"
    request = rf.get("/anything", HTTP_X_REQUEST_ID=rid)
    mw = RequestIdMiddleware(get_response=lambda r: JsonResponse({"ok": True}))

    before = ctx_get_request_id()
    response = mw(request)
    assert response["X-Request-Id"] == rid
    assert ctx_get_request_id() == before


def test_middleware_resets_contextvar_on_exception():
    rf = RequestFactory()
    request = rf.get("/anything")
    mw = RequestIdMiddleware(get_response=lambda r: JsonResponse({"ok": True}))

    before = ctx_get_request_id()
    mw.process_request(request)
    assert ctx_get_request_id() == request.request_id
    mw.process_exception(request, RuntimeError("boom"))
    assert ctx_get_request_id() == before


def test_middleware_parses_traceparent_into_context():
    rf = RequestFactory()
    traceparent = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    request = rf.get("/anything", HTTP_TRACEPARENT=traceparent)
    mw = RequestIdMiddleware(get_response=lambda r: JsonResponse({"ok": True}))

    mw.process_request(request)
    ctx = get_trace_context()
    assert ctx is not None
    assert ctx.trace_id == "1234567890abcdef1234567890abcdef"
    assert ctx.span_id == "1234567890abcdef"
    assert ctx.flags == 0x01

    mw.process_response(request, JsonResponse({"ok": True}))
    assert get_trace_context() is None


def test_middleware_ignores_malformed_traceparent():
    rf = RequestFactory()
    request = rf.get("/anything", HTTP_TRACEPARENT="not-a-traceparent")
    mw = RequestIdMiddleware(get_response=lambda r: JsonResponse({"ok": True}))
    mw.process_request(request)
    assert get_trace_context() is None
    mw.process_response(request, JsonResponse({"ok": True}))


# ---- Re-export contract -----------------------------------------


def test_get_request_id_reexported_from_core_logging():
    # The issue documents `from astrolift_core.logging import
    # get_request_id` as the import path; we ship the equivalent at
    # ``core.logging.get_request_id``.
    from core.logging import get_request_id as exported

    assert exported is ctx_get_request_id


# ---- End-to-end via the Django test client ---------------------


def _capture_logs(name: str = "astrolift_test_view"):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


_module_logger, _module_stream = _capture_logs()


@csrf_exempt
def _ping_view(request):
    # Log a line during the request — the JsonFormatter will pick up
    # the request_id from the contextvar that the middleware set.
    _module_logger.info(
        "ping handled",
        extra={"resolved_request_id": get_request_id()},
    )
    return JsonResponse({"request_id": getattr(request, "request_id", None)})


_test_urlpatterns = [
    path("__ping__/", _ping_view),
]


@pytest.fixture
def client_with_test_view(client):
    middleware = [
        "core.middleware.request_id.RequestIdMiddleware",
    ]
    with override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=middleware,
    ):
        # reset capture
        _module_stream.seek(0)
        _module_stream.truncate(0)
        yield client


# Module-level alias so ROOT_URLCONF=__name__ resolves.
urlpatterns = _test_urlpatterns


def test_view_response_carries_x_request_id_header(client_with_test_view):
    resp = client_with_test_view.get("/__ping__/")
    assert resp.status_code == 200
    rid = resp["X-Request-Id"]
    assert _ULID_REGEX.match(rid), rid
    assert resp.json()["request_id"] == rid


def test_view_logs_share_request_id_with_response_header(client_with_test_view):
    rid = "01ENDTOENDREQUESTID0000000"
    resp = client_with_test_view.get("/__ping__/", HTTP_X_REQUEST_ID=rid)
    assert resp.status_code == 200
    assert resp["X-Request-Id"] == rid

    line = _module_stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["request_id"] == rid
    assert payload["resolved_request_id"] == rid


def test_view_clears_request_id_contextvar_after_response(client_with_test_view):
    inbound = "01TESTNOLEAKREQUESTID00000"
    client_with_test_view.get("/__ping__/", HTTP_X_REQUEST_ID=inbound)
    # Once the response was built and process_response ran, the
    # contextvar belongs to the test process again — the in-request
    # ULID did not leak into the surrounding scope.
    assert ctx_get_request_id() != inbound


def test_view_with_traceparent_correlates_in_logs(client_with_test_view):
    traceparent = "00-aabbccddeeff00112233445566778899-aabbccddeeff0011-01"
    resp = client_with_test_view.get(
        "/__ping__/",
        HTTP_TRACEPARENT=traceparent,
    )
    assert resp.status_code == 200

    line = _module_stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["trace_id"] == "aabbccddeeff00112233445566778899"
    assert payload["span_id"] == "aabbccddeeff0011"
