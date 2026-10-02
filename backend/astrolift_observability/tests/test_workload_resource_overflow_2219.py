"""Finite exporter samples must not become nonfinite aggregate measurements."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from astrolift_observability.workload_resources import ResourceUnavailable, measure_resource
from providers._sdk.workload_metrics import MetricContainer


@pytest.mark.parametrize(
    "usage,limits",
    [
        ([1e308, 1e308], [100, 100]),  # usage sum overflows
        ([100, 100], [1e308, 1e308]),  # limit sum must not fabricate ratio zero
        ([1e308], [1e-308]),  # finite operands, nonfinite division
    ],
    ids=["usage-sum", "limit-sum", "ratio"],
)
def test_real_prometheus_wire_rejects_nonfinite_aggregate(usage, limits):
    members = tuple(
        MetricContainer(
            pod_name=f"pod-{index}",
            pod_uid=f"019eb737-0100-7000-8000-{index:012d}",
            container_name="worker",
            container_id=f"{index:064x}",
            started_at=900,
        )
        for index in range(len(usage))
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            query = parse_qs(body.decode())["query"][0]
            requests.append(query)
            is_limit = "kube_pod_container_resource_limits" in query
            rows = []
            for member, value in zip(members, limits if is_limit else usage, strict=True):
                labels = {
                    "namespace": "owned",
                    "pod": member.pod_name,
                    "container": member.container_name,
                }
                if is_limit:
                    labels.update(uid=member.pod_uid, resource="memory", unit="byte")
                else:
                    labels["id"] = "/docker/" + member.container_id
                rows.append({"metric": labels, "values": [[1000, str(value)]]})
            response = json.dumps(
                {
                    "status": "success",
                    "data": {"resultType": "matrix", "result": rows},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ResourceUnavailable, match="^INVALID_DATA$"):
            measure_resource(
                endpoint="http://127.0.0.1:" + str(server.server_port),
                namespace="owned",
                members=members,
                resource="memory",
                range_seconds=300,
                start_unix=1000,
                end_unix=1100,
                step_seconds=15,
            )
        assert len(requests) == 2  # both real strict reads passed before aggregation
        assert all(member.container_id in requests[0] for member in members)
        assert all(member.pod_uid in requests[1] for member in members)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
