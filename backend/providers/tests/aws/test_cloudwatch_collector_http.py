"""Actual shared-session boto3 HTTP requests to an owned local API fixture."""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

import boto3
import pytest
from botocore.config import Config

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws.cloudwatch_collector import CollectorPreparationError, CollectorSpec, prepare_collector
from aws.session import aws_client, clear_credential_cache

ACCOUNT = "123456789012"
REGION = "us-west-2"
NAME = "fixture-cluster"
GUID = "8b2e4a0c-b67b-4f98-b086-cf09cbb21f9c"
ROLE = f"astrolift-{GUID}-fluent-bit"
ISSUER = f"oidc.eks.{REGION}.amazonaws.com/id/FIXTURE"


@pytest.fixture
def wire():
    calls = []
    state = {"account": ACCOUNT, "issuer": ISSUER, "error": False, "status": "ACTIVE", "partition": "aws"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, payload, status=200, content_type="application/json"):
            raw = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def xml(self, action, body):
            self.respond(
                f"<{action}Response><{action}Result>{body}</{action}Result><ResponseMetadata><RequestId>fixture</RequestId></ResponseMetadata></{action}Response>",
                content_type="text/xml",
            )

        def do_GET(self):
            calls.append(("DescribeCluster", self.path))
            self.respond(
                {
                    "cluster": {
                        "name": NAME,
                        "arn": f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{NAME}",
                        "status": state["status"],
                        "identity": {"oidc": {"issuer": "https://" + state["issuer"]}},
                    }
                }
            )

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            target = self.headers.get("X-Amz-Target", "")
            if target:
                action = target.split(".")[-1]
                kwargs = json.loads(raw)
            else:
                kwargs = {key: values[0] for key, values in parse_qs(raw).items()}
                action = kwargs.pop("Action")
                kwargs.pop("Version", None)
            calls.append((action, kwargs))
            if state["error"] and action == "GetOpenIDConnectProvider":
                self.respond(
                    "<ErrorResponse><Error><Code>AccessDenied</Code><Message>foreign-provider-body-marker</Message></Error></ErrorResponse>",
                    403,
                    "text/xml",
                )
            elif action == "AssumeRole":
                self.xml(
                    action,
                    "<Credentials><AccessKeyId>ASIAFIXTURE0000000000</AccessKeyId><SecretAccessKey>synthetic-secret</SecretAccessKey><SessionToken>synthetic-session</SessionToken><Expiration>2099-01-01T00:00:00Z</Expiration></Credentials>",
                )
            elif action == "GetCallerIdentity":
                self.xml(
                    action,
                    f"<Account>{state['account']}</Account><Arn>arn:{state['partition']}:sts::{state['account']}:assumed-role/fixture/session</Arn><UserId>fixture</UserId>",
                )
            elif action == "GetOpenIDConnectProvider":
                self.xml(action, f"<Url>{ISSUER}</Url><ClientIDList><member>sts.amazonaws.com</member></ClientIDList>")
            elif action == "GetRole":
                self.respond(
                    "<ErrorResponse><Error><Code>NoSuchEntity</Code><Message>absent</Message></Error></ErrorResponse>",
                    404,
                    "text/xml",
                )
            elif action == "CreateRole":
                self.xml(
                    action,
                    f"<Role><Path>/astrolift/</Path><RoleName>{ROLE}</RoleName><RoleId>fixture-role</RoleId><Arn>arn:aws:iam::{ACCOUNT}:role/astrolift/{ROLE}</Arn><CreateDate>2026-01-01T00:00:00Z</CreateDate></Role>",
                )
            elif action == "PutRolePolicy":
                self.xml(action, "")
            elif action == "DescribeLogGroups":
                self.respond({"logGroups": []})
            elif action in {"CreateLogGroup", "PutRetentionPolicy"}:
                self.respond({})
            else:
                self.respond({"unexpected": escape(action)}, 500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clients = []
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"

    def build(name, **kwargs):
        kwargs.setdefault("aws_access_key_id", "fixture-access")
        kwargs.setdefault("aws_secret_access_key", "fixture-secret")
        client = boto3.client(name, endpoint_url=endpoint, config=Config(retries={"max_attempts": 0}), **kwargs)
        clients.append(client)
        return client

    clear_credential_cache()
    ambient_sts = build("sts", region_name=REGION)
    factory = partial(aws_client, build=build, sts=ambient_sts)
    try:
        yield calls, state, factory
    finally:
        for client in clients:
            client.close()
        clear_credential_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def spec():
    return CollectorSpec(
        GUID,
        NAME,
        REGION,
        CloudCredential(
            cloud="aws",
            mode=CredentialMode.AWS_ASSUME_ROLE,
            declared_account=ACCOUNT,
            role_arn=f"arn:aws:iam::{ACCOUNT}:role/connection",
            external_id="fixture-external-id",
        ),
    )


def test_native_http_uses_shared_assumed_identity_and_exact_oidc_policies(wire):
    calls, _, factory = wire
    stage = prepare_collector(spec(), client_factory=factory)
    assume = [kwargs for action, kwargs in calls if action == "AssumeRole"]
    assert len(assume) == 1
    assert assume[0]["ExternalId"] == "fixture-external-id"
    assert assume[0]["RoleArn"] == spec().credential.role_arn
    [oidc] = [kwargs for action, kwargs in calls if action == "GetOpenIDConnectProvider"]
    assert oidc == {"OpenIDConnectProviderArn": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{ISSUER}"}
    [role] = [kwargs for action, kwargs in calls if action == "CreateRole"]
    trust = json.loads(role["AssumeRolePolicyDocument"])
    assert (
        trust["Statement"][0]["Condition"]["StringEquals"][ISSUER + ":sub"]
        == "system:serviceaccount:astrolift-system:fluent-bit"
    )
    [policy] = [kwargs for action, kwargs in calls if action == "PutRolePolicy"]
    statements = json.loads(policy["PolicyDocument"])["Statement"]
    assert statements[0]["Resource"] == stage.log_group_arn
    assert statements[1]["Resource"] == stage.log_group_arn + ":log-stream:*"
    assert not stage.collector_installed and not stage.ingestion_verified
    assert stage.candidate_reader_config()["log_config"]["log_group"] == f"/astrolift/clusters/{GUID}/pods"


@pytest.mark.parametrize("fault", ["account", "issuer", "error", "status", "partition"])
def test_native_http_identity_or_provider_failure_refuses_without_resource_writes(wire, fault, caplog):
    calls, state, factory = wire
    state[fault] = {
        "account": "999999999999",
        "issuer": "foreign.example/id/OTHER",
        "error": True,
        "status": "DELETING",
        "partition": "aws-cn",
    }[fault]
    with pytest.raises(CollectorPreparationError) as caught:
        prepare_collector(spec(), client_factory=factory)
    assert not any(action.startswith(("Create", "Put", "Update", "Delete")) for action, _ in calls)
    assert "foreign-provider-body-marker" not in str(caught.value) + caplog.text
    assert "foreign.example" not in str(caught.value) + caplog.text
