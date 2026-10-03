"""Actual boto3 JSON transport and response admission; no AWS endpoint or saved credentials."""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _sdk.observability.cloudwatch_logs import CloudWatchLogsConfig, CloudWatchLogsQueryDriver

NS = "owned-ns"
APP = "same-app"
SELECTOR = '{namespace="owned-ns",app="same-app"}'
PATTERN = '{ $.kubernetes.namespace_name = "owned-ns" && $.kubernetes.labels.[\'astrolift.io/app\'] = "same-app" }'
WORKLOAD_PATTERN = (
    '{ $.kubernetes.namespace_name = "owned-ns" && '
    "$.kubernetes.labels.['astrolift.io/app'] = \"same-app\" && "
    "$.kubernetes.labels.['astrolift.io/workload'] = \"api\" }"
)
WINDOW = {"since": "2026-10-01T00:00:00+00:00", "until": "2026-10-01T01:00:00+00:00"}


@pytest.fixture
def transport():
    boto3 = pytest.importorskip("boto3")
    from botocore.config import Config

    state = {"requests": [], "events": [], "cursor": "opaque-next", "fail": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            # Only operation and JSON request body: never retain authentication headers.
            state["requests"].append((self.headers.get("X-Amz-Target"), payload))
            self.send_response(400 if state["fail"] else 200)
            self.send_header("Content-Type", "application/x-amz-json-1.1")
            self.end_headers()
            body = (
                {
                    "__type": "InvalidParameterException",
                    "message": "PRIVATE_AWS_BODY_MARKER /aws/private/group https://private.invalid/",
                }
                if state["fail"]
                else {"events": state["events"], "nextToken": state["cursor"]}
            )
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = boto3.client(
        "logs",
        region_name="us-west-2",
        endpoint_url=f"http://127.0.0.1:{server.server_port}",
        aws_access_key_id="local-fixture",
        aws_secret_access_key="local-fixture",
        config=Config(retries={"max_attempts": 0}, proxies={}),
    )
    state["client"] = client
    try:
        yield state
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def event(namespace=NS, app=APP, workload="api", *, message="OWNED request-ID", metadata=True):
    data = {
        "log": message + "\n",
        "kubernetes": {
            "namespace_name": namespace,
            "pod_name": "owned-pod",
            "container_name": "server",
            "labels": {"astrolift.io/app": app, "astrolift.io/workload": workload, "extra": "preserved"},
        },
    }
    return {
        "timestamp": 1_767_225_600_000,
        "logStreamName": "same-app-api-server",
        "message": json.dumps(data) if metadata else message,
    }


def driver(transport, **kwargs):
    return CloudWatchLogsQueryDriver(
        config=CloudWatchLogsConfig(
            log_group="/aws/private/group",
            region="us-west-2",
            log_stream_name_prefix="owned-stream-prefix",
            client=transport["client"],
            **kwargs,
        )
    )


def read(transport, query=SELECTOR, **kwargs):
    return driver(transport).query_logs(
        query, **WINDOW, limit=200, cursor="opaque-before", level=None, search=None, **kwargs
    )


def test_native_boto3_request_and_mixed_response_do_not_release_foreign_or_ambiguous_records(transport):
    duplicates = [
        '{"log":"DUPLICATE_PRIVATE","kubernetes":{"namespace_name":"foreign","namespace_name":"owned-ns","labels":{"astrolift.io/app":"same-app"}}}',
        '{"log":"DUPLICATE_PRIVATE","kubernetes":{"namespace_name":"owned-ns","labels":{"astrolift.io/app":"foreign","astrolift.io/app":"same-app"}}}',
        '{"log":"DUPLICATE_PRIVATE","kubernetes":{"namespace_name":"foreign","labels":{"astrolift.io/app":"same-app"}},"kubernetes":{"namespace_name":"owned-ns","labels":{"astrolift.io/app":"same-app"}}}',
    ]
    missing = event()
    parsed = json.loads(missing["message"])
    del parsed["kubernetes"]["labels"]
    missing["message"] = json.dumps(parsed)
    transport["events"] = [
        event(),
        event(namespace="other-ns", message="FOREIGN_NAMESPACE"),
        event(app="other-app", message="FOREIGN_APP"),
        missing,
        event(message="same-app owned-ns PLAINTEXT_PRIVATE", metadata=False),
        *[dict(event(), message=value) for value in duplicates],
    ]
    page = read(transport)
    assert [line.message for line in page.items] == ["OWNED request-ID"]
    line = page.items[0]
    assert (line.namespace, line.pod, line.container, line.timestamp) == (
        "owned-ns",
        "owned-pod",
        "server",
        "2026-01-01T00:00:00+00:00",
    )
    assert line.labels == {"astrolift.io/app": APP, "astrolift.io/workload": "api", "extra": "preserved"}
    assert (page.next_cursor, page.reached_retention, page.total_count) == ("opaque-next", False, -1)
    target, sent = transport["requests"][0]
    assert target == "Logs_20140328.FilterLogEvents"
    assert sent == {
        "logGroupName": "/aws/private/group",
        "startTime": int(dt.datetime.fromisoformat(WINDOW["since"]).timestamp() * 1000),
        "endTime": int(dt.datetime.fromisoformat(WINDOW["until"]).timestamp() * 1000),
        "limit": 200,
        "logStreamNamePrefix": "owned-stream-prefix",
        "nextToken": "opaque-before",
        "filterPattern": PATTERN,
    }


def test_workload_identity_and_literal_search_apply_only_after_admission(transport):
    transport["events"] = [
        event(message='literal "ID" * %'),
        event(workload="worker", message='literal "ID" * %'),
        event(message="different"),
        event(namespace="foreign", message='literal "ID" * %'),
    ]
    page = driver(transport).query_logs(
        SELECTOR[:-1] + ',workload="api"}', **WINDOW, limit=20, cursor="", level="error", search='"id" * %'
    )
    assert [line.message for line in page.items] == ['literal "ID" * %']
    assert transport["requests"][0][1]["filterPattern"] == WORKLOAD_PATTERN
    assert "nextToken" not in transport["requests"][0][1]


@pytest.mark.parametrize(
    "query",
    [
        "{}",
        '{namespace="owned-ns"}',
        '{app="same-app"}',
        '{namespace="owned-ns",app=""}',
        '{namespace="*",app="same-app"}',
        '{namespace="owned-ns",app=~"same.*"}',
        '{namespace="owned-ns",app="same-app",app="foreign"}',
        '{namespace="owned-ns",namespace="foreign",app="same-app"}',
        SELECTOR + ' |~ ".*"',
        SELECTOR[:-1] + ',unknown="ignored"}',
        SELECTOR[:-1] + ",}",
        SELECTOR[:-1] + ',workload="*"}',
        '{namespace="owned-ns",app="same?app"}',
        '{namespace="owned-ns",app="same%app"}',
        '{namespace="owned-ns",app="same-app"} OR {}',
    ],
)
def test_invalid_selector_refused_before_any_native_http_or_client_construction(transport, monkeypatch, query):
    monkeypatch.setattr(
        "_sdk.observability.cloudwatch_logs._build_logs_client",
        lambda **_kwargs: pytest.fail("invalid selector constructed ambient client"),
    )
    unconfigured = CloudWatchLogsQueryDriver(config=CloudWatchLogsConfig(log_group="private", region="us-west-2"))
    with pytest.raises(ValueError, match="exact namespace and app"):
        unconfigured.query_logs(query, **WINDOW, limit=10, cursor="", level=None, search=None)
    with pytest.raises(ValueError, match="exact namespace and app"):
        read(transport, query)
    assert transport["requests"] == []


def test_empty_admitted_page_preserves_server_cursor(transport):
    transport["events"] = [event(namespace="foreign")]
    page = read(transport)
    assert page.items == [] and page.next_cursor == "opaque-next"
    assert len(transport["requests"]) == 1


def test_provider_failure_diagnostics_are_sanitized_at_actual_boto3_boundary(transport):
    transport["fail"] = True
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger = logging.getLogger("cloudwatch-proof")
    logger.addHandler(handler)
    try:
        with pytest.raises(RuntimeError, match=r"^CloudWatch historical log read failed$") as failure:
            try:
                read(transport)
            except RuntimeError:
                logger.exception("historical read failed")
                raise
    finally:
        logger.removeHandler(handler)
    assert failure.value.__suppress_context__
    assert "CloudWatch historical log read failed" in output.getvalue()
    for marker in ["PRIVATE_AWS_BODY_MARKER", "/aws/private/group", "private.invalid"]:
        assert marker not in output.getvalue() and marker not in str(failure.value)


def test_lazy_client_constructed_once_only_after_exact_selector_admission(transport, monkeypatch):
    constructed = []

    def build(**kwargs):
        constructed.append(kwargs)
        return transport["client"]

    monkeypatch.setattr("_sdk.observability.cloudwatch_logs._build_logs_client", build)
    lazy = CloudWatchLogsQueryDriver(
        config=CloudWatchLogsConfig(log_group="owned-group", region="us-west-2", role_arn="owned-fixture-role")
    )
    assert constructed == []
    with pytest.raises(ValueError):
        lazy.query_logs("{}", **WINDOW, limit=10, cursor="", level=None, search=None)
    assert constructed == [] and transport["requests"] == []
    for cursor in ["", "opaque-next"]:
        page = lazy.query_logs(
            '{ namespace = "owned-ns", app = "same-app" }', **WINDOW, limit=10, cursor=cursor, level=None, search=None
        )
        assert page.next_cursor == "opaque-next"
    assert constructed == [{"region": "us-west-2", "role_arn": "owned-fixture-role"}]
    assert [request[1]["filterPattern"] for request in transport["requests"]] == [PATTERN, PATTERN]


@pytest.mark.parametrize("field", ["namespace_name", "labels", "app", "workload"])
def test_nonstring_or_missing_identity_metadata_never_authorizes_record(transport, field):
    record = event()
    parsed = json.loads(record["message"])
    if field == "namespace_name":
        parsed["kubernetes"][field] = [NS]
    elif field == "labels":
        parsed["kubernetes"][field] = APP
    elif field == "app":
        parsed["kubernetes"]["labels"]["astrolift.io/app"] = [APP]
    else:
        del parsed["kubernetes"]["labels"]["astrolift.io/workload"]
    record["message"] = json.dumps(parsed)
    transport["events"] = [record]
    result = read(transport, SELECTOR[:-1] + ',workload="api"}')
    assert result.items == [] and result.next_cursor == "opaque-next"


def test_malformed_admitted_timestamp_and_cursor_errors_never_emit_provider_values(transport):
    record = event()
    record["timestamp"] = float("inf")
    transport["events"] = [record]
    with pytest.raises(RuntimeError, match="CloudWatch historical log read failed") as error:
        read(transport)
    assert str(error.value) == "CloudWatch historical log read failed"
    assert error.value.__suppress_context__
    transport["events"] = []
    transport["cursor"] = {"private": "PRIVATE_CURSOR_MARKER"}
    with pytest.raises(RuntimeError, match="CloudWatch historical log read failed") as error:
        read(transport)
    assert str(error.value) == "CloudWatch historical log read failed" and error.value.__suppress_context__
