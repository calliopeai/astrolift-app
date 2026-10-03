"""Registered log-reader identity through actual local boto3 STS and Logs HTTP.

No live AWS endpoint, saved credential, database or control-plane driver override.
Only the SDK client endpoint factory is redirected to the owned local server.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from core.cluster_credentials import ClusterCredentialInvalid
from core.cluster_log_query import query_app_logs, resolve_log_query_driver

ROLE = "arn:aws:iam::210987654321:role/registered-reader"
LEGACY_ROLE = "arn:aws:iam::210987654321:role/legacy-reader"
EXTERNAL_ID = "fixture-org-external-id"
WINDOW = {"since_iso": "2026-10-01T00:00:00+00:00", "until_iso": "2026-10-01T01:00:00+00:00"}


def cluster(*, credential=None, legacy_role=None):
    config = {
        "account_id": "210987654321",
        "log_driver": "cloudwatch_logs",
        "log_config": {"log_group": "/owned/pod-logs", "region": "us-west-2"},
    }
    if credential is not None:
        config["credential"] = credential
    if legacy_role is not None:
        config["log_config"]["role_arn"] = legacy_role
    return SimpleNamespace(
        slug="registered-cluster",
        provider_plugin=SimpleNamespace(slug="aws"),
        provider_config=config,
        auth_config={},
    )


def explicit(**overrides):
    return {"mode": "aws_assume_role", "role_arn": ROLE, "external_id": EXTERNAL_ID, **overrides}


def read(row):
    return query_app_logs(
        cluster=row,
        namespace="owned-ns",
        app_slug="same-app",
        workload_slug="api",
        **WINDOW,
        limit=20,
        cursor="",
        level=None,
        search=None,
    )


@pytest.fixture
def wire(monkeypatch):
    import aws.session as session
    import boto3
    from botocore.config import Config

    session.clear_credential_cache()
    state = {"sts": [], "logs": [], "fail_sts": False, "clients": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("X-Amz-Target") == "Logs_20140328.FilterLogEvents":
                state["logs"].append(
                    {
                        "body": json.loads(body),
                        # Retain only a fixture identity observation, never headers.
                        "assumed": "Credential=ASIAFIXTUREKEY/" in self.headers.get("Authorization", ""),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/x-amz-json-1.1")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "events": [
                                {
                                    "timestamp": 1790812800000,
                                    "message": json.dumps(
                                        {
                                            "log": "owned fixture log",
                                            "kubernetes": {
                                                "namespace_name": "owned-ns",
                                                "labels": {
                                                    "astrolift.io/app": "same-app",
                                                    "astrolift.io/workload": "api",
                                                },
                                            },
                                        }
                                    ),
                                }
                            ]
                        }
                    ).encode()
                )
                return
            params = {key: values[0] for key, values in parse_qs(body.decode()).items()}
            assert params["Action"] == "AssumeRole"
            state["sts"].append(params)
            self.send_response(403 if state["fail_sts"] else 200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            if state["fail_sts"]:
                self.wfile.write(
                    b'<ErrorResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                    b"<Error><Type>Sender</Type><Code>AccessDenied</Code>"
                    b"<Message>PRIVATE_STS_BODY_MARKER</Message></Error></ErrorResponse>"
                )
                return
            expiry = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
            self.wfile.write(
                (
                    '<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                    "<AssumeRoleResult><Credentials>"
                    "<AccessKeyId>ASIAFIXTUREKEY</AccessKeyId>"
                    "<SecretAccessKey>local-fixture-only</SecretAccessKey>"
                    "<SessionToken>local-fixture-session</SessionToken>"
                    f"<Expiration>{expiry}</Expiration>"
                    "</Credentials></AssumeRoleResult></AssumeRoleResponse>"
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def build(service, **kwargs):
        kwargs.setdefault("aws_access_key_id", "ambient-fixture")
        kwargs.setdefault("aws_secret_access_key", "ambient-fixture")
        result = boto3.client(
            service,
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            config=Config(retries={"max_attempts": 0}, proxies={}),
            **kwargs,
        )
        state["clients"].append(result)
        return result

    monkeypatch.setattr(session, "_default_build", build)
    try:
        yield state
    finally:
        for client in state["clients"]:
            client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        session.clear_credential_cache()


def test_registered_role_and_external_id_reach_actual_sts_and_signed_logs_request(wire):
    row = cluster(credential=explicit())
    driver = resolve_log_query_driver(row)
    assert driver is not None and wire["sts"] == [] and wire["logs"] == []
    for _ in range(2):
        page = read(row)
        assert [line.message for line in page.items] == ["owned fixture log"]
    assert len(wire["sts"]) == 1  # Shared session factory caches the exact identity.
    assert wire["sts"][0]["RoleArn"] == ROLE
    assert wire["sts"][0]["ExternalId"] == EXTERNAL_ID
    assert "registered-cluster" in wire["sts"][0]["RoleSessionName"]
    assert len(wire["logs"]) == 2 and all(request["assumed"] for request in wire["logs"])
    assert all(request["body"]["logGroupName"] == "/owned/pod-logs" for request in wire["logs"])
    assert all("owned-ns" in request["body"]["filterPattern"] for request in wire["logs"])


def test_invalid_selector_cannot_assume_a_registered_role(wire):
    driver = resolve_log_query_driver(cluster(credential=explicit()))
    with pytest.raises(ValueError, match="exact namespace and app"):
        driver.query_logs(
            "{}", WINDOW["since_iso"], WINDOW["until_iso"], limit=20, cursor="", level=None, search=None
        )
    assert wire["clients"] == [] and wire["sts"] == [] and wire["logs"] == []


def test_changed_external_id_uses_a_distinct_assumed_session(wire):
    read(cluster(credential=explicit()))
    read(cluster(credential=explicit(external_id="rotated-fixture-id")))
    assert [request["ExternalId"] for request in wire["sts"]] == [EXTERNAL_ID, "rotated-fixture-id"]
    assert all(request["assumed"] for request in wire["logs"])


@pytest.mark.parametrize("legacy_role", [ROLE, LEGACY_ROLE])
def test_conflicting_legacy_role_never_replaces_the_registered_credential(wire, legacy_role):
    with pytest.raises(ValueError, match="remove the legacy log role override"):
        read(cluster(credential=explicit(), legacy_role=legacy_role))
    assert wire["clients"] == [] and wire["sts"] == [] and wire["logs"] == []


@pytest.mark.parametrize(
    "credential",
    [explicit(mode="unknown"), explicit(aws_secret_access_key="PRIVATE_CREDENTIAL_MARKER")],
)
def test_invalid_declaration_never_falls_back_to_ambient(wire, credential):
    with pytest.raises(ClusterCredentialInvalid):
        read(cluster(credential=credential))
    assert wire["clients"] == [] and wire["sts"] == [] and wire["logs"] == []


def test_legacy_log_role_still_works_without_a_registered_explicit_credential(wire):
    read(cluster(legacy_role=LEGACY_ROLE))
    assert wire["sts"][0]["RoleArn"] == LEGACY_ROLE
    assert "ExternalId" not in wire["sts"][0]
    assert wire["logs"][0]["assumed"]


def test_ambient_registration_keeps_ambient_reader_identity(wire):
    read(cluster())
    assert wire["sts"] == [] and len(wire["logs"]) == 1 and not wire["logs"][0]["assumed"]


def test_sts_refusal_does_not_attempt_an_ambient_read_or_expose_the_response(wire, caplog):
    wire["fail_sts"] = True
    with pytest.raises(RuntimeError, match="^CloudWatch historical log read failed$") as error:
        read(cluster(credential=explicit()))
    assert len(wire["sts"]) == 1 and wire["logs"] == []
    assert error.value.__suppress_context__
    assert "PRIVATE_STS_BODY_MARKER" not in caplog.text + str(error.value)
