"""Tests for the Prometheus PromQL client (#297).

Network is patched out; we assert the URL shape, response parsing,
cache behavior, and error mapping.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from astrolift_operations import prometheus_client


@pytest.fixture(autouse=True)
def _clear_cache():
    prometheus_client.clear_cache_for_tests()
    yield
    prometheus_client.clear_cache_for_tests()


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---- query_instant --------------------------------------------------


def test_query_instant_vector_sums_series(monkeypatch):
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"app": "x"}, "value": [1234, "1.5"]},
                {"metric": {"app": "x", "code": "500"}, "value": [1234, "0.5"]},
            ],
        },
    }
    captured: dict = {}

    def fake_urlopen(req, timeout=5.0):
        captured["url"] = req.full_url
        return _FakeResp(payload)

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    value = prometheus_client.query_instant(
        endpoint="http://prom.local",
        query='sum(rate(http_requests_total{app="x"}[1m]))',
    )
    assert value == 2.0
    assert "/api/v1/query?" in captured["url"]


def test_query_instant_scalar(monkeypatch):
    payload = {
        "status": "success",
        "data": {"resultType": "scalar", "result": [1234, "0.042"]},
    }

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        lambda req, timeout=5.0: _FakeResp(payload),
    )
    assert prometheus_client.query_instant(endpoint="http://p", query="x") == 0.042


def test_query_instant_empty_vector_is_zero(monkeypatch):
    payload = {"status": "success", "data": {"resultType": "vector", "result": []}}
    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        lambda req, timeout=5.0: _FakeResp(payload),
    )
    assert prometheus_client.query_instant(endpoint="http://p", query="x") == 0.0


def test_query_instant_caches_by_endpoint_and_query(monkeypatch):
    calls = {"n": 0}
    payload = {
        "status": "success",
        "data": {"resultType": "scalar", "result": [1, "7"]},
    }

    def fake_urlopen(req, timeout=5.0):
        calls["n"] += 1
        return _FakeResp(payload)

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    prometheus_client.query_instant(endpoint="http://p", query="q")
    prometheus_client.query_instant(endpoint="http://p", query="q")
    # Second call should hit the TTL cache, not the network.
    assert calls["n"] == 1


def test_query_instant_5xx_is_unavailable(monkeypatch):
    def fake_urlopen(req, timeout=5.0):
        raise HTTPError(req.full_url, 503, "down", {}, None)

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with pytest.raises(prometheus_client.PrometheusUnavailable):
        prometheus_client.query_instant(endpoint="http://p", query="q")


def test_query_instant_4xx_is_query_error(monkeypatch):
    def fake_urlopen(req, timeout=5.0):
        raise HTTPError(req.full_url, 422, "bad", {}, None)

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with pytest.raises(prometheus_client.PrometheusQueryError):
        prometheus_client.query_instant(endpoint="http://p", query="q")


def test_query_instant_network_failure_is_unavailable(monkeypatch):
    def fake_urlopen(req, timeout=5.0):
        raise URLError("no route to host")

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with pytest.raises(prometheus_client.PrometheusUnavailable):
        prometheus_client.query_instant(endpoint="http://p", query="q")


def test_query_instant_promql_error_body(monkeypatch):
    """Prometheus returns 200 + status=error on PromQL parse errors —
    must surface as a query error, not an unavailable error."""
    payload = {"status": "error", "error": "parse error: bad token"}
    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        lambda req, timeout=5.0: _FakeResp(payload),
    )
    with pytest.raises(prometheus_client.PrometheusQueryError, match="parse error"):
        prometheus_client.query_instant(endpoint="http://p", query="bogus")


# ---- query_range ---------------------------------------------------


def test_query_range_passes_window(monkeypatch):
    payload = {
        "status": "success",
        "data": {"resultType": "matrix", "result": []},
    }
    captured: dict = {}

    def fake_urlopen(req, timeout=5.0):
        captured["url"] = req.full_url
        return _FakeResp(payload)

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    out = prometheus_client.query_range(
        endpoint="http://p",
        query='sum(rate(x{app="y"}[1m]))',
        start_unix=1_700_000_000,
        end_unix=1_700_003_600,
        step_seconds=300,
    )
    assert out == ()
    assert "query_range" in captured["url"]
    assert "start=1700000000" in captured["url"]
    assert "end=1700003600" in captured["url"]
    assert "step=300" in captured["url"]


def test_query_range_parses_matrix(monkeypatch):
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"app": "x"},
                    "values": [
                        [1700000000, "0.1"],
                        [1700000060, "0.2"],
                    ],
                },
            ],
        },
    }
    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        lambda req, timeout=5.0: _FakeResp(payload),
    )
    out = prometheus_client.query_range(
        endpoint="http://p",
        query="q",
        start_unix=1_700_000_000,
        end_unix=1_700_000_120,
        step_seconds=60,
    )
    assert len(out) == 1
    assert out[0].metric_labels == {"app": "x"}
    assert out[0].values == ((1700000000.0, 0.1), (1700000060.0, 0.2))


# ---- sum_range_to_buckets ------------------------------------------


def test_sum_range_to_buckets_distributes_samples():
    rows = [
        prometheus_client.RangeQueryResult(
            metric_labels={},
            values=(
                (0.0, 1.0),
                (10.0, 2.0),
                (20.0, 3.0),
                (90.0, 4.0),
            ),
        ),
    ]
    buckets = prometheus_client.sum_range_to_buckets(
        rows,
        bucket_count=10,
        start_unix=0,
        end_unix=100,
    )
    assert len(buckets) == 10
    assert buckets[0] == 1.0  # ts=0 falls in bucket 0
    assert buckets[1] == 2.0  # ts=10
    assert buckets[2] == 3.0  # ts=20
    assert buckets[9] == 4.0  # ts=90


# ---- sanitize_label_value -------------------------------------------


def test_sanitize_rejects_quote():
    with pytest.raises(prometheus_client.PrometheusQueryError):
        prometheus_client.sanitize_label_value('app"; drop_table')


def test_sanitize_allows_normal_slugs():
    assert prometheus_client.sanitize_label_value("hello-app") == "hello-app"
    assert prometheus_client.sanitize_label_value("ns_v1.2.3") == "ns_v1.2.3"
