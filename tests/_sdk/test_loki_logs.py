"""Tests for Loki LogStreamDriver (#2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.observability.loki_logs import LokiConfig, LokiLogStreamDriver


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
        self, url: str, *, params: dict[str, str] | None = None,
    ) -> Any:
        self.last_url = url
        self.last_params = dict(params or {})
        return self.response


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def driver(fake_http: FakeHttp) -> LokiLogStreamDriver:
    return LokiLogStreamDriver(
        config=LokiConfig(
            base_url="http://loki:3100",
            http_client=fake_http,
        ),
    )


@pytest.mark.asyncio
async def test_stream_logs_yields_per_value(
    driver: LokiLogStreamDriver, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={
        "status": "success",
        "data": {
            "result": [{
                "stream": {
                    "namespace": "acme-api",
                    "pod": "api-0",
                    "container": "api",
                    "level": "info",
                },
                "values": [
                    ["1717200000000000000", "request received"],
                    ["1717200000100000000", "request handled"],
                ],
            }],
        },
    })
    lines = []
    async for line in driver.stream_logs(
        query='{namespace="acme-api"}',
        since="0", until="1",
    ):
        lines.append(line)
    assert len(lines) == 2
    assert lines[0].pod == "api-0"
    assert lines[0].container == "api"
    assert lines[0].message == "request received"
    assert lines[0].level == "info"


@pytest.mark.asyncio
async def test_kubernetes_label_aliases_recognized(
    driver: LokiLogStreamDriver, fake_http: FakeHttp,
) -> None:
    """Loki tenants normalize label keys differently — accept the
    common alternate forms (kubernetes_pod_name, container_name)."""
    fake_http.response = FakeResponse(body={
        "status": "success",
        "data": {
            "result": [{
                "stream": {
                    "namespace": "ns",
                    "kubernetes_pod_name": "p1",
                    "kubernetes_container_name": "c1",
                },
                "values": [["1", "msg"]],
            }],
        },
    })
    lines = []
    async for line in driver.stream_logs(
        query='{}', since="0", until="1",
    ):
        lines.append(line)
    assert lines[0].pod == "p1"
    assert lines[0].container == "c1"


@pytest.mark.asyncio
async def test_query_failure_raises(
    driver: LokiLogStreamDriver, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={
        "status": "error", "error": "parse failed",
    })
    with pytest.raises(RuntimeError, match="loki query failed"):
        async for _ in driver.stream_logs(
            query='{}', since="0", until="1",
        ):
            pass


@pytest.mark.asyncio
async def test_query_url_is_loki_query_range(
    driver: LokiLogStreamDriver, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={
        "status": "success", "data": {"result": []},
    })
    async for _ in driver.stream_logs(
        query='{namespace="x"}', since="0", until="1",
    ):
        pass
    assert fake_http.last_url == "http://loki:3100/loki/api/v1/query_range"
    assert fake_http.last_params["query"] == '{namespace="x"}'
    assert fake_http.last_params["direction"] == "forward"
