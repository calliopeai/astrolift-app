"""Native botocore/SigV4 observes the exact immutable repository without writes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest

from aws.registry_ecr import ECRConfig, ECRDriver


@pytest.mark.parametrize(
    "mode,exclusions,immutable",
    [
        ("IMMUTABLE", [], True),
        ("MUTABLE", [], False),
        ("IMMUTABLE", [{"filterType": "WILDCARD", "filter": "intent-*"}], False),
    ],
)
def test_read_only_repository_observation_uses_actual_sdk_http(mode, exclusions, immutable):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            action = self.headers["X-Amz-Target"]
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((action, body))
            assert action.endswith(".DescribeRepositories")
            assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
            result = {
                "repositories": [
                    {
                        "repositoryName": "project-only",
                        "repositoryUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/project-only",
                        "imageTagMutability": mode,
                        "imageTagMutabilityExclusionFilters": exclusions,
                    }
                ]
            }
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-amz-json-1.1")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    native = boto3.client(
        "ecr",
        region_name="us-east-1",
        endpoint_url=f"http://127.0.0.1:{server.server_port}",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    try:
        observed = ECRDriver(
            config=ECRConfig(region="us-east-1", account_id="123456789012"), client=native
        ).observe_build_repository("project-only")
        assert observed.immutable_tags is immutable
        assert observed.name == "project-only"
        assert seen == [
            ("AmazonEC2ContainerRegistry_V20150921.DescribeRepositories", {"repositoryNames": ["project-only"]})
        ]
    finally:
        native.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
