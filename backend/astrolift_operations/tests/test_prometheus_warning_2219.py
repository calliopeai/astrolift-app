"""Real-wire completeness warnings cannot establish strict measurement evidence."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from astrolift_operations import prometheus_client as prom
from providers._sdk.workload_metrics import MetricContainer

ABSENT = object()
MARKER = "PRIVATE_COLLECTOR_WARNING_MARKER"


@pytest.fixture
def warning_wire():
    state = SimpleNamespace(
        warnings=ABSENT,
        value="1",
        warn_when=lambda query: True,
        requests=[],
        namespace="owned",
        member=MetricContainer("pod-1", "019eb737-0100-7000-8000-000000000001", "worker", "a" * 64, 900),
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(parse_qs(urlsplit(self.path).query))

        def do_POST(self):
            self.respond(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))

        def respond(self, parameters):
            query = parameters["query"][0]
            state.requests.append(query)
            is_limit = "kube_pod_container_resource_limits" in query
            is_usage = (
                "container_cpu_usage_seconds_total" in query or "container_memory_working_set_bytes" in query
            )
            labels = {}
            if is_limit or is_usage:
                labels = {
                    "namespace": state.namespace,
                    "pod": state.member.pod_name,
                    "container": state.member.container_name,
                }
                if is_limit:
                    cpu = 'resource="cpu"' in query
                    labels.update(
                        uid=state.member.pod_uid,
                        resource="cpu" if cpu else "memory",
                        unit="core" if cpu else "byte",
                    )
                else:
                    labels["id"] = "/docker/" + state.member.container_id
            payload = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": labels,
                            "values": [
                                [float(parameters["end"][0]) - 15, "100" if is_limit else state.value]
                            ],
                        }
                    ],
                },
            }
            if state.warnings is not ABSENT and state.warn_when(query):
                payload["warnings"] = state.warnings
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    prom.clear_cache_for_tests()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        prom.clear_cache_for_tests()


def arguments(wire, **kwargs):
    return {
        "endpoint": wire.endpoint,
        "query": "up",
        "start_unix": 1000,
        "end_unix": 1100,
        "step_seconds": 15,
        "strict": True,
    } | kwargs


@pytest.mark.parametrize("warnings", [[MARKER], None, MARKER, {}, 0, False, [None]])
def test_strict_warning_refusal_is_static_and_never_cached(warning_wire, warnings):
    warning_wire.warnings = warnings
    with pytest.raises(
        prom.PrometheusQueryError, match="^prometheus response completeness is uncertain$"
    ) as error:
        prom.query_range(**arguments(warning_wire))
    assert MARKER not in str(error.value)
    warning_wire.warnings = []
    result = prom.query_range(**arguments(warning_wire))
    assert result[0].values == ((1085, 1),)
    assert len(warning_wire.requests) == 2


@pytest.mark.parametrize("warnings", [ABSENT, []])
def test_strict_absent_and_empty_warnings_keep_real_zero_samples(warning_wire, warnings):
    warning_wire.warnings = warnings
    warning_wire.value = "0"
    result = prom.query_range(**arguments(warning_wire))
    assert result[0].values == ((1085, 0),)


def test_non_strict_warning_behavior_and_cache_remain_independent(warning_wire):
    warning_wire.warnings = [MARKER]
    result = prom.query_range(**arguments(warning_wire, strict=False))
    assert result[0].values == ((1085, 1),)
    assert prom.query_range(**arguments(warning_wire, strict=False)) == result
    assert len(warning_wire.requests) == 1
    with pytest.raises(prom.PrometheusQueryError):
        prom.query_range(**arguments(warning_wire))
    assert len(warning_wire.requests) == 2
