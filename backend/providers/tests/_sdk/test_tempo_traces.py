"""Tests for Tempo TraceDriver (#5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.observability.tempo_traces import TempoConfig, TempoTraceDriver


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
    multi_responses: list[FakeResponse] = field(default_factory=list)
    call_count: int = 0

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        self.last_url = url
        self.last_params = dict(params or {})
        if self.multi_responses:
            r = self.multi_responses[min(self.call_count, len(self.multi_responses) - 1)]
            self.call_count += 1
            return r
        return self.response


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def driver(fake_http: FakeHttp) -> TempoTraceDriver:
    return TempoTraceDriver(
        config=TempoConfig(
            base_url="http://tempo:3200",
            http_client=fake_http,
        ),
    )


def test_list_traces_returns_summaries(
    driver: TempoTraceDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "traces": [
                {
                    "traceID": "abc",
                    "rootServiceName": "api",
                    "rootTraceName": "GET /v1/users",
                    "spanCount": 12,
                    "durationMs": 145.7,
                    "status": "OK",
                },
            ],
        }
    )
    summaries = driver.list_traces(
        service="api",
        since="0",
        until="1",
    )
    assert len(summaries) == 1
    assert summaries[0].trace_id == "abc"
    assert summaries[0].span_count == 12


def test_list_traces_translates_filters_to_tags(
    driver: TempoTraceDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"traces": []})
    driver.list_traces(
        service="api",
        operation="GET /v1",
        status="ERROR",
        min_duration_ms=100,
        since="0",
        until="1",
    )
    tags = fake_http.last_params["tags"]
    assert "service.name=api" in tags
    assert "name=GET /v1" in tags
    assert "status=ERROR" in tags
    assert fake_http.last_params["minDuration"] == "100ms"


def test_get_trace_extracts_otlp_batches(
    driver: TempoTraceDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "batches": [
                {
                    "instrumentationLibrarySpans": [
                        {
                            "spans": [
                                {
                                    "spanId": "s1",
                                    "parentSpanId": None,
                                    "name": "GET /v1/users",
                                    "startTimeUnixNano": 1717200000000000000,
                                    "endTimeUnixNano": 1717200000100000000,
                                    "status": {"code": 1},
                                    "attributes": [
                                        {
                                            "key": "service.name",
                                            "value": {"stringValue": "api"},
                                        },
                                        {
                                            "key": "http.status_code",
                                            "value": {"intValue": 200},
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    spans = driver.get_trace("abc")
    assert len(spans) == 1
    assert spans[0].operation == "GET /v1/users"
    assert spans[0].service == "api"
    assert spans[0].status_code == "OK"
    assert spans[0].attributes["http.status_code"] == "200"
    assert spans[0].duration_ms == 100.0


def test_get_trace_status_error(
    driver: TempoTraceDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "spans": [
                {
                    "spanId": "s2",
                    "name": "x",
                    "startTimeUnixNano": 0,
                    "endTimeUnixNano": 0,
                    "status": {"code": 2},
                }
            ],
        }
    )
    spans = driver.get_trace("trace2")
    assert spans[0].status_code == "ERROR"


@pytest.mark.asyncio
async def test_stream_spans_pages_through_traces(
    driver: TempoTraceDriver,
    fake_http: FakeHttp,
) -> None:
    fake_http.multi_responses = [
        FakeResponse(
            body={
                "traces": [
                    {
                        "traceID": "t1",
                        "rootServiceName": "api",
                        "rootTraceName": "x",
                        "spanCount": 1,
                        "durationMs": 1,
                        "status": "OK",
                    },
                    {
                        "traceID": "t2",
                        "rootServiceName": "api",
                        "rootTraceName": "y",
                        "spanCount": 1,
                        "durationMs": 2,
                        "status": "OK",
                    },
                ],
            }
        ),
        FakeResponse(
            body={
                "spans": [
                    {
                        "spanId": "s1",
                        "name": "x",
                        "startTimeUnixNano": 0,
                        "endTimeUnixNano": 0,
                    },
                ]
            }
        ),
        FakeResponse(
            body={
                "spans": [
                    {
                        "spanId": "s2",
                        "name": "y",
                        "startTimeUnixNano": 0,
                        "endTimeUnixNano": 0,
                    },
                ]
            }
        ),
    ]
    spans = []
    async for span in driver.stream_spans(
        service="api",
        since="0",
        until="1",
    ):
        spans.append(span)
    assert {s.span_id for s in spans} == {"s1", "s2"}
