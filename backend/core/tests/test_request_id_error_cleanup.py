"""Exception and response hooks may run on the same request."""

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from core.middleware.request_id import RequestIdMiddleware
from core.request_context import (
    TraceContext,
    get_request_id,
    get_trace_context,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)

TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


def _forbidden(request):
    raise PermissionDenied("No permission")


def _missing(request):
    raise Http404("Missing")


def _failed(request):
    raise RuntimeError("Expected view failure")


urlpatterns = [path("forbidden/", _forbidden), path("missing/", _missing), path("failed/", _failed)]


@pytest.mark.parametrize("traceparent", [None, TRACEPARENT])
def test_exception_then_response_consumes_each_context_token_once(traceparent):
    previous_id = get_request_id()
    previous_trace = get_trace_context()
    request = RequestFactory().get("/", HTTP_X_REQUEST_ID="error-path-2174")
    if traceparent:
        request.META["HTTP_TRACEPARENT"] = traceparent
    middleware = RequestIdMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    assert get_request_id() == "error-path-2174"
    if traceparent:
        assert get_trace_context().trace_id == "0123456789abcdef0123456789abcdef"
    assert middleware.process_exception(request, ValueError("failure")) is None
    response = middleware.process_response(request, HttpResponse(status=403))
    assert response.status_code == 403
    assert response["X-Request-Id"] == "error-path-2174"
    assert get_request_id() == previous_id
    assert get_trace_context() == previous_trace
    assert not hasattr(request, "_astrolift_request_id_token")
    assert not hasattr(request, "_astrolift_trace_token")


@pytest.mark.parametrize("route,status", [("forbidden", 403), ("missing", 404), ("failed", 500)])
@override_settings(
    ROOT_URLCONF=__name__,
    DEBUG=False,
    MIDDLEWARE=["core.middleware.request_id.RequestIdMiddleware"],
)
def test_http_error_preserves_original_status_header_and_outer_context(route, status):
    id_marker = set_request_id("outer-request")
    prior_trace = TraceContext(trace_id="f" * 32, span_id="f" * 16)
    trace_marker = set_trace_context(prior_trace)
    try:
        response = Client(raise_request_exception=False).get(
            f"/{route}/", HTTP_X_REQUEST_ID="failed-request", HTTP_TRACEPARENT=TRACEPARENT
        )
        assert response.status_code == status
        assert response["X-Request-Id"] == "failed-request"
        assert get_request_id() == "outer-request"
        assert get_trace_context() == prior_trace
    finally:
        reset_trace_context(trace_marker)
        reset_request_id(id_marker)
