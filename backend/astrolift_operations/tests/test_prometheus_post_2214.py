"""Opt-in bounded form POST, real SigV4 body/header signing and cache isolation."""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from botocore.credentials import Credentials

from astrolift_operations import prometheus_client as prom


@pytest.fixture(autouse=True)
def clear_cache():
    prom.clear_cache_for_tests()
    yield
    prom.clear_cache_for_tests()


def payload(value="1"):
    return json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {"managed_service": "test"}, "values": [[100, value]]}],
            },
        }
    ).encode()


def arguments(**kwargs):
    return {
        "endpoint": "http://prom.invalid",
        "query": "up",
        "start_unix": 100,
        "end_unix": 200,
        "step_seconds": 30,
        "request_method": "POST",
        "strict": True,
        "max_request_bytes": 65536,
        "max_response_bytes": 524288,
        "max_series": 24,
        "max_samples": 120,
    } | kwargs


def test_post_form_body_content_type_and_get_default_cache_separation(monkeypatch):
    calls = []

    def open_request(req, timeout):
        calls.append(req)
        return io.BytesIO(payload())

    monkeypatch.setattr(prom.urllib.request, "urlopen", open_request)
    result = prom.query_range(**arguments(query='sum(metric{namespace="exact"})'))
    assert result[0].values == ((100, 1),)
    request = calls[0]
    assert request.get_method() == "POST"
    assert request.full_url == "http://prom.invalid/api/v1/query_range"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert parse_qs(request.data.decode()) == {
        "query": ['sum(metric{namespace="exact"})'],
        "start": ["100"],
        "end": ["200"],
        "step": ["30"],
    }
    prom.query_range(**arguments(query='sum(metric{namespace="exact"})'))
    assert len(calls) == 1
    prom.query_range(**arguments(query='sum(metric{namespace="exact"})', request_method="GET"))
    assert len(calls) == 2 and calls[-1].get_method() == "GET" and calls[-1].data is None
    assert "/api/v1/query_range?" in calls[-1].full_url


def test_request_byte_limit_applies_before_cache_or_http(monkeypatch):
    monkeypatch.setattr(prom.urllib.request, "urlopen", lambda *_a, **_kw: pytest.fail("over-limit HTTP"))
    with pytest.raises(prom.PrometheusQueryError, match="request exceeds"):
        prom.query_range(**arguments(query="x" * 65536))
    with pytest.raises(prom.PrometheusQueryError, match="GET or POST"):
        prom.query_range(**arguments(request_method="PUT"))


def test_strict_post_invalid_reading_is_not_cached_or_changed_to_zero(monkeypatch):
    responses = [payload("NaN"), payload("0")]
    calls = []

    def open_request(req, timeout):
        calls.append(req)
        return io.BytesIO(responses.pop(0))

    monkeypatch.setattr(prom.urllib.request, "urlopen", open_request)
    with pytest.raises(prom.PrometheusQueryError, match="invalid rate readings"):
        prom.query_range(**arguments())
    assert prom.query_range(**arguments())[0].values == ((100, 0),)
    assert len(calls) == 2


def test_actual_sigv4_signs_post_body_and_content_type_without_aws_calls(monkeypatch):
    monkeypatch.setattr(
        "botocore.session.Session.get_credentials",
        lambda *_: Credentials("LOCAL_TEST_ACCESS", "local-test-secret"),
    )
    import botocore.auth

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return cls(2026, 9, 30, 12)

    if hasattr(botocore.auth, "get_current_datetime"):
        monkeypatch.setattr(
            botocore.auth, "get_current_datetime", lambda: datetime(2026, 9, 30, 12, tzinfo=UTC)
        )
    else:
        monkeypatch.setattr(botocore.auth, "datetime", SimpleNamespace(datetime=FrozenDateTime))
    calls = []

    def open_request(req, timeout):
        calls.append(req)
        return io.BytesIO(payload())

    monkeypatch.setattr(prom.urllib.request, "urlopen", open_request)
    endpoint = "https://aps-workspaces.us-west-2.amazonaws.com/workspaces/local-test"
    prom.query_range(**arguments(endpoint=endpoint, query="first"))
    prom.query_range(**arguments(endpoint=endpoint, query="second"))
    first, second = calls
    assert first.get_method() == second.get_method() == "POST"
    first_authorization, second_authorization = (
        first.get_header("Authorization"),
        second.get_header("Authorization"),
    )
    assert first_authorization.startswith("AWS4-HMAC-SHA256")
    assert "/us-west-2/aps/aws4_request" in first_authorization
    assert "content-type" in first_authorization
    assert first.data != second.data and first_authorization != second_authorization
    assert first.get_header("Content-type") == "application/x-www-form-urlencoded"
