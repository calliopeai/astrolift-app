"""Tests for Prometheus MetricsDriver (#4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.observability.prometheus_metrics import (
    PrometheusConfig,
    PrometheusMetricsDriver,
)


@dataclass
class FakeResponse:
    body: dict[str, Any]

    def json(self) -> Any:
        return self.body


@dataclass
class FakeHttp:
    response: FakeResponse | None = None
    last_url: str | None = None
    last_params: dict[str, str] = field(default_factory=dict)

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        self.last_url = url
        self.last_params = dict(params or {})
        return self.response


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def driver(fake_http: FakeHttp) -> PrometheusMetricsDriver:
    return PrometheusMetricsDriver(
        config=PrometheusConfig(
            base_url="http://prometheus:9090",
            http_client=fake_http,
        ),
    )


def test_promql_label_matchers_serialized(
    driver: PrometheusMetricsDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "status": "success",
            "data": {"result": []},
        }
    )
    driver.query_metric(
        metric="http_requests_total",
        labels={"app": "api", "method": "GET"},
        range_start="2026-05-01T00:00:00Z",
        range_end="2026-05-01T01:00:00Z",
    )
    assert fake_http.last_params["query"] == ('http_requests_total{app="api",method="GET"}')


def test_query_returns_empty_series_on_no_results(
    driver: PrometheusMetricsDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "status": "success",
            "data": {"result": []},
        }
    )
    series = driver.query_metric(
        metric="up",
        labels={},
        range_start="0",
        range_end="1",
    )
    assert series.metric == "up"
    assert series.datapoints == []


def test_query_parses_datapoints(
    driver: PrometheusMetricsDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "up", "job": "api"},
                        "values": [
                            [1717200000, "1"],
                            [1717200060, "0"],
                        ],
                    }
                ],
            },
        }
    )
    series = driver.query_metric(
        metric="up",
        labels={"job": "api"},
        range_start="0",
        range_end="1",
    )
    assert len(series.datapoints) == 2
    assert series.datapoints[0].value == 1.0
    assert series.datapoints[1].value == 0.0
    # __name__ stripped, real labels preserved
    assert series.labels == {"job": "api"}


def test_query_failure_raises(
    driver: PrometheusMetricsDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "status": "error",
            "error": "bad query",
        }
    )
    with pytest.raises(RuntimeError, match="prometheus query failed"):
        driver.query_metric(
            metric="bad",
            labels={},
            range_start="0",
            range_end="1",
        )


def test_no_labels_emits_bare_metric(
    driver: PrometheusMetricsDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "status": "success",
            "data": {"result": []},
        }
    )
    driver.query_metric(
        metric="bare_metric",
        labels={},
        range_start="0",
        range_end="1",
    )
    assert fake_http.last_params["query"] == "bare_metric"
