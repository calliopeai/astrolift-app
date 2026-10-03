"""Real pinned renderer plus registered STS/Logs and Kubernetes HTTP handoff.

The endpoints are owned local fixtures. They do not establish live ingestion,
durable public operation admission or activation of a cluster reader binding.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import threading
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import aws.session as session
import boto3
import pytest
from aws.cloudwatch_collector_execution import CollectorExecutor, ReaderBinding
from aws.cloudwatch_collector_render import render_collector
from botocore.config import Config

from core.cluster_log_query import resolve_log_query_driver
from providers.tests.aws import test_cloudwatch_collector_execution_http as native

STAGE = replace(
    native.STAGE,
    log_group=f"/astrolift/clusters/{native.GUID}/pods",
    log_group_arn=f"arn:aws:logs:us-west-2:123456789012:log-group:/astrolift/clusters/{native.GUID}/pods",
    irsa_role_arn=f"arn:aws:iam::123456789012:role/astrolift/astrolift-{native.GUID}-fluent-bit",
)
ROLE = "arn:aws:iam::123456789012:role/registered-cluster"
EXTERNAL_ID = "composed-fixture-external-id"


@pytest.fixture
def composed_wire(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "STAGE", STAGE)
    yield from native.wire.__wrapped__(tmp_path)


@pytest.fixture
def registered_reader(composed_wire, monkeypatch):
    session.clear_credential_cache()
    state = {"assumptions": [], "signed_reads": [], "clients": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            params = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            state["assumptions"].append({key: values[0] for key, values in params.items()})
            expiry = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(
                (
                    '<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                    "<AssumeRoleResult><Credentials><AccessKeyId>ASIACOMPOSEDFIXTURE</AccessKeyId>"
                    "<SecretAccessKey>local-fixture-only</SecretAccessKey>"
                    "<SessionToken>local-fixture-session</SessionToken>"
                    f"<Expiration>{expiry}</Expiration></Credentials></AssumeRoleResult></AssumeRoleResponse>"
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def build(service, **kwargs):
        kwargs.setdefault("aws_access_key_id", "ambient-fixture")
        kwargs.setdefault("aws_secret_access_key", "ambient-fixture")
        endpoint = (
            f"http://127.0.0.1:{server.server_port}"
            if service == "sts"
            else composed_wire["kube"]._api_client.configuration.host
        )
        sdk = boto3.client(
            service, endpoint_url=endpoint, config=Config(retries={"max_attempts": 0}, proxies={}), **kwargs
        )
        if service == "logs":

            def signed(request, **_kwargs):
                # Keep a boolean proof only, never authorization headers.
                state["signed_reads"].append(
                    b"Credential=ASIACOMPOSEDFIXTURE/" in request.headers["Authorization"]
                )

            sdk.meta.events.register("before-send.logs.FilterLogEvents", signed)
        state["clients"].append(sdk)
        return sdk

    monkeypatch.setattr(session, "_default_build", build)
    cluster = SimpleNamespace(
        slug="registered-cluster",
        provider_plugin=SimpleNamespace(slug="aws"),
        provider_config={
            "account_id": "123456789012",
            "credential": {"mode": "aws_assume_role", "role_arn": ROLE, "external_id": EXTERNAL_ID},
            "log_driver": "cloudwatch_logs",
            "log_config": {"log_group": STAGE.log_group, "region": STAGE.region},
        },
        auth_config={},
    )
    driver = resolve_log_query_driver(cluster)
    state["factory"] = lambda stage: ReaderBinding(
        driver, stage.cluster_guid, stage.log_group, stage.region, native.REQUEST.reader_source
    )
    try:
        yield state
    finally:
        for sdk in state["clients"]:
            sdk.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        session.clear_credential_cache()


def test_real_renderer_and_registered_reader_verify_fresh_read_after_exact_probe_deletion(
    composed_wire, registered_reader
):
    directory = Path(os.environ.get("COLLECTOR_RENDER_ARTIFACTS", ""))
    target = f"{platform.system().lower()}-" + {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(
        platform.machine(), "unsupported"
    )
    archive, binary = directory / "fluent-bit-0.58.2.tgz", directory / f"helm-v4.3.0-{target}"
    if not os.environ.get("COLLECTOR_RENDER_ARTIFACTS") or not archive.is_file() or not binary.is_file():
        pytest.skip("Set COLLECTOR_RENDER_ARTIFACTS to independently verified native archives")
    with CollectorExecutor(
        stage=STAGE,
        request=native.REQUEST,
        archive=archive.read_bytes(),
        renderer=partial(render_collector, helm_binary=str(binary)),
        kubernetes=composed_wire["kube"],
        reader_factory=registered_reader["factory"],
        checkpoints=composed_wire["port"],
        idle=lambda: None,
    ) as executor:
        result = executor.run()
    assert result.state == "POST_LOSS_READ_VERIFIED" and result.event_hash
    assert result.coverage == "LINUX_EC2" and not result.cleanup_pending
    assert not result.reader_activation_performed
    [assumption] = registered_reader["assumptions"]
    assert assumption["Action"] == "AssumeRole" and assumption["RoleArn"] == ROLE
    assert assumption["ExternalId"] == EXTERNAL_ID
    assert registered_reader["signed_reads"] == [True, True, True]
    [deleted] = [call for call in composed_wire["calls"] if call[0] == "DELETE"]
    assert deleted[2]["preconditions"] == {
        key: composed_wire["deleted_probe"]["metadata"][key] for key in ("uid", "resourceVersion")
    }
    reads = [call for call in composed_wire["calls"] if call[0] == "POST" and call[1] == "/"]
    assert all(call[2]["logGroupName"] == STAGE.log_group for call in reads)
    assert max(
        index for index, call in enumerate(composed_wire["calls"]) if call[0] == "POST" and call[1] == "/"
    ) > composed_wire["calls"].index(deleted)
    assert len([obj for obj in composed_wire["objects"].values() if obj["kind"] == "DaemonSet"]) == 1
