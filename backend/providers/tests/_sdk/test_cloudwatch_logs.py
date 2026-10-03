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
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.observability.cloudwatch_logs import (
    CloudWatchLogsConfig,
    CloudWatchLogsQueryDriver,
    CloudWatchLogsRetentionDriver,
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
                                "labels": {"astrolift.io/app": "hello-obs"},
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


def test_plain_events_are_not_attributed_by_stream_or_request_namespace() -> None:
    client = FakeLogsClient(
        {
            "events": [
                {
                    "timestamp": 1_767_225_600_000,
                    "logStreamName": "hello-obs-worker-7",
                    "message": "hello-obs plain text line",
                }
            ]
        }
    )
    since, until = _query_window()
    page = _driver(client).query_logs(
        '{namespace="acme-hello",app="hello-obs"}', since, until, limit=100, cursor="", level=None, search=None
    )
    assert page.items == []


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
    # Exact metadata identity predicates; search is not ownership authority.
    assert kw["filterPattern"] == (
        '{ $.kubernetes.namespace_name = "acme-hello" && '
        "$.kubernetes.labels.['astrolift.io/app'] = \"hello-obs\" && "
        "$.kubernetes.labels.['astrolift.io/workload'] = \"api\" }"
    )
    # No-data page still carries the backend cursor forward.
    assert page.items == []
    assert page.next_cursor == "page-2"


def test_unscoped_selector_is_refused_before_request() -> None:
    client = FakeLogsClient({"events": []})
    since, until = _query_window()
    with pytest.raises(ValueError, match="exact namespace and app"):
        _driver(client).query_logs("{}", since, until, limit=10, cursor="", level=None, search=None)
    assert client.last_kwargs is None


def test_query_error_propagates() -> None:
    class Boom:
        def filter_log_events(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("throttled")

    since, until = _query_window()
    with pytest.raises(RuntimeError, match="CloudWatch historical log read failed"):
        _driver(Boom()).query_logs(
            '{namespace="acme-hello",app="hello-obs"}', since, until, limit=10, cursor="", level=None, search=None
        )


# ---- moto end-to-end -------------------------------------------------


@pytest.fixture
def _aws_creds(monkeypatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")


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
                {
                    "timestamp": t0,
                    "message": json.dumps(
                        {
                            "log": "hello-obs started",
                            "kubernetes": {
                                "namespace_name": "acme-hello",
                                "pod_name": stream,
                                "labels": {"astrolift.io/app": "hello-obs"},
                            },
                        }
                    ),
                },
                {
                    "timestamp": t0 + 1000,
                    "message": json.dumps(
                        {
                            "message": "hello-obs ready",
                            "kubernetes": {
                                "namespace_name": "acme-hello",
                                "pod_name": stream,
                                "labels": {"astrolift.io/app": "hello-obs"},
                            },
                        }
                    ),
                },
            ],
        )

        driver = CloudWatchLogsQueryDriver(
            config=CloudWatchLogsConfig(log_group=group, region="us-west-2", client=client)
        )
        since = (now - dt.timedelta(hours=1)).isoformat()
        until = (now + dt.timedelta(hours=1)).isoformat()
        # Keep exact recorded namespace/app metadata through the real SDK request.
        page = driver.query_logs(
            '{namespace="acme-hello",app="hello-obs"}',
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


def test_moto_native_retention_remains_idempotent_and_rounds_up(_aws_creds: None) -> None:
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        client = boto3.client("logs", region_name="us-west-2")
        group = "/aws/containerinsights/owned-retention/application"
        client.create_log_group(logGroupName=group)
        retention = CloudWatchLogsRetentionDriver(
            config=CloudWatchLogsConfig(log_group=group, region="us-west-2", client=client)
        )
        assert retention.current_retention_days() is None
        assert retention.apply_retention(8) == {"changed": True, "days": 14, "previous": None}
        assert retention.current_retention_days() == 14
        assert retention.apply_retention(14) == {"changed": False, "days": 14}
        assert client.describe_log_groups(logGroupNamePrefix=group)["logGroups"][0]["retentionInDays"] == 14
