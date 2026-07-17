"""Tests for the CloudWatch Logs LogQueryDriver (#1111).

Two layers:

* a hand fake ``logs`` client pins the event→LogLine mapping + the
  FilterLogEvents request shape (deterministic, no AWS);
* a moto-backed client proves the driver works end-to-end against the
  real CloudWatch Logs API surface;
* a wiring test pins ``resolve_log_query_driver`` selecting this driver
  from a cluster's ``provider_config``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.observability.cloudwatch_logs import (
    CloudWatchLogsConfig,
    CloudWatchLogsQueryDriver,
)


class FakeLogsClient:
    """Records the FilterLogEvents kwargs and returns a scripted page."""

    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.last_kwargs: dict[str, Any] | None = None

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        return self.response


def _driver(client: Any, **overrides: Any) -> CloudWatchLogsQueryDriver:
    cfg = dict(
        log_group="/aws/containerinsights/prod/application",
        region="us-west-2",
        client=client,
    )
    cfg.update(overrides)
    return CloudWatchLogsQueryDriver(config=CloudWatchLogsConfig(**cfg))


def _query_window() -> tuple[str, str]:
    since = dt.datetime(2026, 7, 1, 0, 0, tzinfo=dt.UTC).isoformat()
    until = dt.datetime(2026, 7, 1, 1, 0, tzinfo=dt.UTC).isoformat()
    return since, until


def test_maps_container_insights_json_events() -> None:
    """Container Insights ``application`` logs are JSON envelopes with
    kubernetes metadata + the raw line under ``log`` — the driver
    unwraps them into LogLine fields."""
    client = FakeLogsClient(
        {
            "events": [
                {
                    "timestamp": 1_767_225_600_000,  # 2026-01-01T00:00:00Z
                    "logStreamName": "hello-obs-api-0",
                    "message": json.dumps(
                        {
                            "log": "request handled\n",
                            "kubernetes": {
                                "namespace_name": "acme-hello",
                                "pod_name": "hello-obs-api-0",
                                "container_name": "api",
                            },
                        }
                    ),
                },
            ],
            "nextToken": "",
        }
    )
    since, until = _query_window()
    page = _driver(client).query_logs(
        '{namespace="acme-hello",app="hello-obs"}',
        since,
        until,
        limit=100,
        cursor="",
        level=None,
        search=None,
    )
    assert len(page.items) == 1
    line = page.items[0]
    assert line.namespace == "acme-hello"
    assert line.pod == "hello-obs-api-0"
    assert line.container == "api"
    assert line.message == "request handled"  # trailing newline stripped
    # Epoch-ms → ISO-8601 (not reinterpreted as Loki nanoseconds).
    assert line.timestamp == "2026-01-01T00:00:00+00:00"


def test_maps_plain_events_via_stream_name() -> None:
    """A plain (non-JSON) log group carries the message verbatim; pod
    identity falls back to the stream name."""
    client = FakeLogsClient(
        {
            "events": [
                {
                    "timestamp": 1_767_225_600_000,
                    "logStreamName": "worker-7",
                    "message": "plain text line",
                },
            ],
        }
    )
    since, until = _query_window()
    page = _driver(client).query_logs(
        '{namespace="acme-hello",app="hello-obs"}',
        since,
        until,
        limit=100,
        cursor="",
        level=None,
        search=None,
    )
    assert page.items[0].pod == "worker-7"
    assert page.items[0].message == "plain text line"
    # No JSON kube metadata ⇒ namespace falls back to the selector's.
    assert page.items[0].namespace == "acme-hello"


def test_request_shape_time_window_filter_and_prefix() -> None:
    """The driver translates the window to epoch-ms, scopes the filter
    pattern to app + workload + search (AND), and forwards the stream
    prefix + cursor."""
    client = FakeLogsClient({"events": [], "nextToken": "page-2"})
    since, until = _query_window()
    page = _driver(client, log_stream_name_prefix="acme-hello_").query_logs(
        '{namespace="acme-hello",app="hello-obs",workload="api"}',
        since,
        until,
        limit=250,
        cursor="page-1",
        level="error",
        search="timeout",
    )
    kw = client.last_kwargs
    assert kw["logGroupName"] == "/aws/containerinsights/prod/application"
    # Window carried through as epoch-ms (computed from the same ISO
    # strings so the assertion can't drift from the driver's clock).
    assert kw["startTime"] == int(dt.datetime.fromisoformat(since).timestamp() * 1000)
    assert kw["endTime"] == int(dt.datetime.fromisoformat(until).timestamp() * 1000)
    assert kw["limit"] == 250
    assert kw["logStreamNamePrefix"] == "acme-hello_"
    assert kw["nextToken"] == "page-1"
    # app + workload + search as AND-ed quoted terms.
    assert kw["filterPattern"] == '"hello-obs" "api" "timeout"'
    # No-data page still carries the backend cursor forward.
    assert page.items == []
    assert page.next_cursor == "page-2"


def test_no_filter_pattern_when_nothing_to_scope() -> None:
    client = FakeLogsClient({"events": []})
    since, until = _query_window()
    _driver(client).query_logs("{}", since, until, limit=10, cursor="", level=None, search=None)
    assert "filterPattern" not in client.last_kwargs


def test_query_error_propagates() -> None:
    class Boom:
        def filter_log_events(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("throttled")

    since, until = _query_window()
    with pytest.raises(RuntimeError, match="throttled"):
        _driver(Boom()).query_logs("{}", since, until, limit=10, cursor="", level=None, search=None)


# ---- moto end-to-end -------------------------------------------------


@pytest.fixture
def _aws_creds() -> None:
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")


def test_end_to_end_against_moto(_aws_creds: None) -> None:
    """Put real events into a moto CloudWatch Logs group + stream, then
    query them through the driver and assert the mapped result shape."""
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")

    with moto.mock_aws():
        client = boto3.client("logs", region_name="us-west-2")
        group = "/aws/containerinsights/prod/application"
        stream = "hello-obs-api-0"
        client.create_log_group(logGroupName=group)
        client.create_log_stream(logGroupName=group, logStreamName=stream)
        # CloudWatch (and moto) reject PutLogEvents older than 14 days,
        # so anchor the fixture events to "now".
        now = dt.datetime.now(dt.UTC)
        t0 = int(now.timestamp() * 1000)
        client.put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[
                {"timestamp": t0, "message": "hello-obs started"},
                {"timestamp": t0 + 1000, "message": "hello-obs ready"},
            ],
        )

        driver = CloudWatchLogsQueryDriver(
            config=CloudWatchLogsConfig(log_group=group, region="us-west-2", client=client)
        )
        since = (now - dt.timedelta(hours=1)).isoformat()
        until = (now + dt.timedelta(hours=1)).isoformat()
        # Namespace-only selector ⇒ no filterPattern, so this exercises
        # the window + event→LogLine mapping end-to-end without leaning
        # on moto's filter-pattern parser (the pattern shape is pinned
        # by the fake-client test above).
        page = driver.query_logs(
            '{namespace="acme-hello"}',
            since,
            until,
            limit=100,
            cursor="",
            level=None,
            search=None,
        )

    messages = [line.message for line in page.items]
    assert messages == ["hello-obs started", "hello-obs ready"]
    assert page.items[0].pod == "hello-obs-api-0"


# ---- resolver wiring -------------------------------------------------


def test_resolve_log_query_driver_selects_cloudwatch() -> None:
    from core import cluster_log_query

    cluster = SimpleNamespace(
        slug="prod",
        provider_config={
            "log_driver": "cloudwatch_logs",
            "log_config": {
                "log_group": "/aws/containerinsights/prod/application",
                "region": "us-west-2",
            },
        },
    )
    driver = cluster_log_query.resolve_log_query_driver(cluster)
    assert isinstance(driver, CloudWatchLogsQueryDriver)


def test_resolve_log_query_driver_cloudwatch_requires_group_and_region() -> None:
    from core import cluster_log_query

    cluster = SimpleNamespace(
        slug="prod",
        provider_config={
            "log_driver": "cloudwatch_logs",
            "log_config": {"region": "us-west-2"},  # no log_group
        },
    )
    assert cluster_log_query.resolve_log_query_driver(cluster) is None
