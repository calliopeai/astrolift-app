"""Real botocore parsing/signing over owned HTTP; no AWS requests or writes."""

import json
import logging
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
import pytest
from botocore.awsrequest import AWSPreparedRequest
from botocore.config import Config
from botocore.httpsession import URLLib3Session

from _sdk.domain_route_http_api import (
    HttpApiEffectHooks,
    HttpApiRouteTarget,
    HttpApiRouteUnavailable,
    HttpApiSubmission,
    http_api_request_sha256,
)
from aws.domain_route_api_gateway import HttpApiDomainPort

ACCOUNT = "123456789012"
REGION = "us-west-2"
FUNCTION = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:owned"
CERTIFICATE = f"arn:aws:acm:{REGION}:{ACCOUNT}:certificate/12345678-1234-1234-1234-123456789abc"
PRIVATE = "native-private-response-marker"


@pytest.fixture
def world(monkeypatch):
    target = HttpApiRouteTarget(
        str(uuid4()),
        "api.example.test",
        ACCOUNT,
        REGION,
        "aws",
        "abc123",
        str(uuid4()),
        str(uuid4()),
        FUNCTION,
        CERTIFICATE,
    )

    def tags(sid):
        return {"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": sid}

    state = {
        "requests": [],
        "allow": True,
        "writes": [],
        "sent": [],
        "acks": [],
        "domain": None,
        "mapping": None,
        "before_reply": None,
        "before_domain_reply": None,
        "deny": None,
        "lost": None,
        "bad_owner": False,
        "revision": "original",
        "extra_route": False,
        "account": ACCOUNT,
    }
    domain = {
        "domainName": target.hostname,
        "domainNameArn": f"arn:aws:apigateway:{REGION}::/domainnames/{target.hostname}",
        "domainNameConfigurations": [
            {
                "certificateArn": CERTIFICATE,
                "endpointType": "REGIONAL",
                "securityPolicy": "TLS_1_2",
                "apiGatewayDomainName": f"d-original.execute-api.{REGION}.amazonaws.com",
                "hostedZoneId": "ZOWNED",
                "domainNameStatus": "AVAILABLE",
            }
        ],
        "routingMode": "API_MAPPING_ONLY",
        "tags": tags(target.api_service_id),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, body, status=200, xml=False):
            hook, state["before_reply"] = state["before_reply"], None
            if hook:
                hook(self.path)
            raw = body.encode() if xml else json.dumps(body).encode()
            self.send_response(status)
            if status == 404:
                self.send_header("x-amzn-ErrorType", "NotFoundException")
            self.send_header("Content-Type", "text/xml" if xml else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state["requests"].append(("GET", self.path, self.headers.get("Authorization")))
            if state["deny"] and state["deny"] in self.path:
                self.reply({"message": PRIVATE}, 403)
                return
            path = urlsplit(self.path).path
            if path == "/v2/apis/abc123":
                self.reply(
                    {
                        "apiId": target.api_id,
                        "apiEndpoint": f"https://abc123.execute-api.{REGION}.amazonaws.com",
                        "protocolType": "HTTP",
                        "tags": tags(str(uuid4()) if state["bad_owner"] else target.api_service_id),
                    }
                )
            elif path.endswith("/routes"):
                rows = [
                    {
                        "routeId": "route1",
                        "routeKey": "$default",
                        "target": "integrations/int1",
                        "authorizationType": "NONE",
                    }
                ]
                if state["extra_route"]:
                    rows.append({"routeId": "route2", "routeKey": "GET /other"})
                self.reply({"items": rows})
            elif path.endswith("/integrations/int1"):
                self.reply(
                    {
                        "integrationId": "int1",
                        "integrationType": "AWS_PROXY",
                        "integrationUri": FUNCTION,
                        "payloadFormatVersion": "2.0",
                    }
                )
            elif "/stages/" in path:
                self.reply({"stageName": "$default", "autoDeploy": True})
            elif path.endswith("/policy"):
                self.reply(
                    {
                        "Policy": json.dumps(
                            {
                                "Version": "2012-10-17",
                                "Statement": [
                                    {
                                        "Sid": "AstroliftApiGatewayInvoke",
                                        "Effect": "Allow",
                                        "Action": "lambda:InvokeFunction",
                                        "Resource": FUNCTION,
                                        "Principal": {"Service": "apigateway.amazonaws.com"},
                                        "Condition": {
                                            "ArnLike": {
                                                "AWS:SourceArn": f"arn:aws:execute-api:{REGION}:{ACCOUNT}:abc123/*/*"
                                            }
                                        },
                                    }
                                ],
                            }
                        )
                    }
                )
            elif path.startswith("/2015-03-31/functions/"):
                self.reply(
                    {
                        "Configuration": {
                            "FunctionArn": FUNCTION,
                            "FunctionName": "owned",
                            "State": "Active",
                            "LastUpdateStatus": "Successful",
                            "RevisionId": state["revision"],
                            "CodeSha256": "YQ==",
                        },
                        "Tags": tags(target.faas_service_id),
                    }
                )
            elif path.endswith("/apimappings"):
                self.reply({"items": [] if state["mapping"] is None else [state["mapping"]]})
            elif path.startswith("/v2/domainnames/"):
                if state["domain"] is None:
                    self.reply({"message": "NotFound"}, 404)
                else:
                    self.reply(state["domain"])
            else:
                self.reply({"message": "unexpected fixture path"}, 400)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["requests"].append(("POST", self.path, self.headers.get("Authorization")))
            if self.path == "/":
                self.reply(
                    f'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult><Account>{state["account"]}</Account><Arn>arn:aws:iam::{state["account"]}:role/fixture</Arn><UserId>fixture</UserId></GetCallerIdentityResult></GetCallerIdentityResponse>',
                    xml=True,
                )
                return
            args = json.loads(body)
            state["writes"].append((self.path, args))
            if self.path == "/v2/domainnames":
                state["domain"] = domain
                reply = domain
            else:
                state["mapping"] = {
                    "apiId": args["apiId"],
                    "apiMappingId": "map1",
                    "stage": args["stage"],
                    "apiMappingKey": args["apiMappingKey"],
                }
                reply = state["mapping"]
            if state["lost"] == self.path:
                self.close_connection = True
                return
            if self.path == "/v2/domainnames" and state["before_domain_reply"]:
                state["before_domain_reply"]()
            self.reply(reply, 201)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = URLLib3Session.send

    def send(session, request):
        path = urlsplit(request.url)
        url = f"http://127.0.0.1:{server.server_port}" + path.path + ("?" + path.query if path.query else "")
        wire = AWSPreparedRequest(request.method, url, request.headers, request.body, request.stream_output)
        return original(session, wire)

    monkeypatch.setattr(URLLib3Session, "send", send)
    session = boto3.Session(
        aws_access_key_id="fixture-access", aws_secret_access_key="fixture-secret", region_name=REGION
    )
    config = Config(
        connect_timeout=1, read_timeout=1, retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True
    )
    clients = [session.client(service, config=config) for service in ("apigatewayv2", "lambda", "sts")]
    port = HttpApiDomainPort(api_client=clients[0], lambda_client=clients[1], sts_client=clients[2])

    def current():
        if not state["allow"]:
            raise HttpApiRouteUnavailable("CURRENT_WITHDRAWN")

    def sent(payload):
        receipt = HttpApiSubmission(target.operation_id, str(uuid4()), http_api_request_sha256(payload))
        state["sent"].append(receipt)
        return receipt

    def ack(submission, receipt):
        state["acks"].append((submission, receipt))

    try:
        yield target, port, state, current, HttpApiEffectHooks(sent, ack)
    finally:
        for client in clients:
            client.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def reviewed(world):
    target, port, state, current, hooks = world
    observation = port.observe_target(target, current=current)
    return replace(target, source_fingerprint=observation.source_fingerprint), port, state, current, hooks


def test_actual_native_create_read_and_exact_receipt_recovery(world):
    target, port, state, current, hooks = reviewed(world)
    domain = port.create_domain(target, hooks=hooks, current=current)
    assert domain.domain_name_arn.endswith("/domainnames/api.example.test")
    mapping = port.create_mapping(target, domain, hooks=hooks, current=current)
    assert port.observe_mapping(target, domain, mapping, current=current) == mapping
    assert len(state["writes"]) == len(state["sent"]) == len(state["acks"]) == 2
    assert [x[0] for x in state["writes"]] == ["/v2/domainnames", "/v2/domainnames/api.example.test/apimappings"]
    assert state["writes"][0][1]["domainNameConfigurations"] == [
        {"certificateArn": CERTIFICATE, "endpointType": "REGIONAL", "securityPolicy": "TLS_1_2"}
    ]
    assert all(auth and "AWS4-HMAC-SHA256" in auth for _, _, auth in state["requests"])
    with pytest.raises(HttpApiRouteUnavailable, match="ALREADY_EXISTS"):
        port.create_domain(target, hooks=hooks, current=current)
    with pytest.raises(HttpApiRouteUnavailable, match="ALREADY_EXISTS"):
        port.create_mapping(target, domain, hooks=hooks, current=current)
    assert len(state["writes"]) == 2


@pytest.mark.parametrize(
    "change,reason",
    [
        ("bad_owner", "OWNER_MISMATCH"),
        ("extra_route", "ROUTES_UNSUPPORTED"),
        ("account", "ACCOUNT_MISMATCH"),
        ("revision", "SOURCE_CHANGED"),
    ],
)
def test_current_native_identity_drift_refuses_before_sent(world, change, reason):
    target, port, state, current, hooks = reviewed(world)
    state[change] = "replacement" if change == "revision" else "999999999999" if change == "account" else True
    with pytest.raises(HttpApiRouteUnavailable, match=reason):
        port.create_domain(target, hooks=hooks, current=current)
    assert not state["sent"] and not state["writes"]


def test_pre_effect_current_refuses_before_native_request(world):
    target, port, state, current, _hooks = world
    state["allow"] = False
    with pytest.raises(HttpApiRouteUnavailable, match="CURRENT_WITHDRAWN"):
        port.observe_target(target, current=current)
    assert state["requests"] == []


def test_denied_native_read_does_not_log_raw_marker(world, caplog, capsys):
    target, port, state, current, _hooks = world
    state["deny"] = "/routes"
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HttpApiRouteUnavailable, match="NATIVE_READ_UNAVAILABLE") as failure:
        port.observe_target(target, current=current)
    captured = capsys.readouterr()
    assert PRIVATE not in str(failure.value) + caplog.text + captured.out + captured.err
    assert not state["writes"] and not state["sent"]


def test_sent_digest_mismatch_refuses_without_write(world):
    target, port, state, current, hooks = reviewed(world)
    bad = HttpApiEffectHooks(
        lambda payload: HttpApiSubmission(target.operation_id, str(uuid4()), "0" * 64), hooks.record_ack
    )
    with pytest.raises(HttpApiRouteUnavailable, match="SUBMISSION_MISMATCH"):
        port.create_domain(target, hooks=bad, current=current)
    assert not state["writes"] and not state["acks"]


@pytest.mark.parametrize("lost_stage", ["domain", "mapping"])
def test_lost_reply_is_unknown_one_attempt_and_no_ack(world, lost_stage):
    target, port, state, current, hooks = reviewed(world)
    if lost_stage == "mapping":
        domain = port.create_domain(target, hooks=hooks, current=current)
    state["lost"] = "/v2/domainnames" if lost_stage == "domain" else "/v2/domainnames/api.example.test/apimappings"
    before = len(state["writes"])
    with pytest.raises(HttpApiRouteUnavailable, match="WRITE_UNCONFIRMED"):
        if lost_stage == "domain":
            port.create_domain(target, hooks=hooks, current=current)
        else:
            port.create_mapping(target, domain, hooks=hooks, current=current)
    assert len(state["writes"]) == before + 1 and len(state["sent"]) == len(state["acks"]) + 1


def test_ack_is_retained_before_post_reply_withdrawal(world):
    target, port, state, current, hooks = reviewed(world)
    original_ack = hooks.record_ack

    def retain(submission, receipt):
        original_ack(submission, receipt)
        state["allow"] = False

    with pytest.raises(HttpApiRouteUnavailable, match="CURRENT_WITHDRAWN"):
        port.create_domain(target, hooks=HttpApiEffectHooks(hooks.commit_sent, retain), current=current)
    assert len(state["acks"]) == len(state["writes"]) == 1
    assert state["acks"][0][1].regional_target.startswith("d-original.")


def test_ack_persistence_failure_does_not_send_again(world):
    target, port, state, current, hooks = reviewed(world)

    def fail(_submission, _receipt):
        raise HttpApiRouteUnavailable("ACK_NOT_COMMITTED")

    with pytest.raises(HttpApiRouteUnavailable, match="ACK_NOT_COMMITTED"):
        port.create_domain(target, hooks=HttpApiEffectHooks(hooks.commit_sent, fail), current=current)
    assert len(state["sent"]) == len(state["writes"]) == 1 and not state["acks"]


def test_prepare_target_allows_empty_certificate_but_effect_requires_review(world):
    target, port, state, current, hooks = world
    assert port.observe_target(replace(target, certificate_arn=""), current=current).api_endpoint.startswith("https://")
    with pytest.raises(HttpApiRouteUnavailable, match="REVIEW_REQUIRED"):
        port.create_domain(target, hooks=hooks, current=current)
    assert not state["writes"]


def test_native_reply_withdrawal_retains_original_domain_ack_only(world):
    target, port, state, current, hooks = reviewed(world)
    state["before_domain_reply"] = lambda: state.update(allow=False)
    with pytest.raises(HttpApiRouteUnavailable, match="CURRENT_WITHDRAWN"):
        port.create_domain(target, hooks=hooks, current=current)
    assert len(state["acks"]) == len(state["writes"]) == 1
    assert state["acks"][0][1].certificate_arn == CERTIFICATE


def test_hook_cannot_replace_the_accepted_native_request(world):
    target, port, state, current, hooks = reviewed(world)

    def substitute(payload):
        payload["DomainName"] = "foreign.example.test"
        return HttpApiSubmission(target.operation_id, str(uuid4()), http_api_request_sha256(payload))

    with pytest.raises(HttpApiRouteUnavailable, match="SUBMISSION_MISMATCH"):
        port.create_domain(target, hooks=HttpApiEffectHooks(substitute, hooks.record_ack), current=current)
    assert not state["writes"]


def test_original_domain_and_mapping_identity_cannot_be_adopted_after_replacement(world):
    target, port, state, current, hooks = reviewed(world)
    domain = port.create_domain(target, hooks=hooks, current=current)
    mapping = port.create_mapping(target, domain, hooks=hooks, current=current)
    state["mapping"]["apiMappingId"] = "replacement"
    with pytest.raises(HttpApiRouteUnavailable, match="MAPPING_REPLACED"):
        port.observe_mapping(target, domain, mapping, current=current)
    state["domain"]["domainNameConfigurations"][0]["apiGatewayDomainName"] = (
        f"d-replaced.execute-api.{REGION}.amazonaws.com"
    )
    with pytest.raises(HttpApiRouteUnavailable, match="DOMAIN_REPLACED"):
        port.observe_domain(target, domain, current=current)
    assert len(state["writes"]) == 2


def test_native_review_fingerprint_is_stable_across_caller_request_fields(world):
    target, port, _state, current, _hooks = world
    first = port.observe_target(target, current=current)
    second = port.observe_target(
        replace(target, operation_id=str(uuid4()), hostname="other.example.test", certificate_arn=""), current=current
    )
    assert first.source_fingerprint == second.source_fingerprint


def test_authority_withdrawal_after_committed_sent_prevents_native_send(world):
    target, port, state, current, hooks = reviewed(world)

    def withdraw(payload):
        receipt = hooks.commit_sent(payload)
        state["allow"] = False
        return receipt

    with pytest.raises(HttpApiRouteUnavailable, match="CURRENT_WITHDRAWN"):
        port.create_domain(target, hooks=HttpApiEffectHooks(withdraw, hooks.record_ack), current=current)
    assert len(state["sent"]) == 1 and not state["writes"] and not state["acks"]


def test_mixed_client_credentials_refuse_before_any_native_request(world):
    from botocore.credentials import Credentials

    target, port, state, current, _hooks = world
    port.function._request_signer._credentials = Credentials("different", "fixture")
    with pytest.raises(HttpApiRouteUnavailable, match="CLIENT_IDENTITY_MISMATCH"):
        port.observe_target(target, current=current)
    assert not state["requests"]


def test_foreign_endpoint_and_disabled_tls_are_not_native_source_proof(world, monkeypatch):
    target, port, state, current, _hooks = world
    original = port.api.meta.endpoint_url
    monkeypatch.setattr(type(port.api.meta), "endpoint_url", property(lambda _self: "https://foreign.example.test"))
    with pytest.raises(HttpApiRouteUnavailable, match="ENDPOINT_UNSUPPORTED"):
        port.observe_target(target, current=current)
    monkeypatch.setattr(type(port.api.meta), "endpoint_url", property(lambda _self: original))
    port.api._endpoint.http_session._verify = False
    with pytest.raises(HttpApiRouteUnavailable, match="CLIENT_UNBOUNDED"):
        port.observe_target(target, current=current)
    assert not state["requests"]


@pytest.mark.parametrize("change,reason", [("revision", "SOURCE_CHANGED"), ("account", "ACCOUNT_MISMATCH")])
def test_native_source_drift_during_sent_commit_prevents_create(world, change, reason):
    target, port, state, current, hooks = reviewed(world)

    def change_source(payload):
        receipt = hooks.commit_sent(payload)
        state[change] = "replacement" if change == "revision" else "999999999999"
        return receipt

    with pytest.raises(HttpApiRouteUnavailable, match=reason):
        port.create_domain(target, hooks=HttpApiEffectHooks(change_source, hooks.record_ack), current=current)
    assert len(state["sent"]) == 1 and not state["writes"] and not state["acks"]


def test_original_domain_replaced_during_mapping_sent_commit_prevents_mapping(world):
    target, port, state, current, hooks = reviewed(world)
    domain = port.create_domain(target, hooks=hooks, current=current)

    def replace_domain(payload):
        receipt = hooks.commit_sent(payload)
        state["domain"]["domainNameConfigurations"][0]["apiGatewayDomainName"] = (
            f"d-replaced.execute-api.{REGION}.amazonaws.com"
        )
        return receipt

    with pytest.raises(HttpApiRouteUnavailable, match="DOMAIN_REPLACED"):
        port.create_mapping(target, domain, hooks=HttpApiEffectHooks(replace_domain, hooks.record_ack), current=current)
    assert len(state["sent"]) == 2 and len(state["writes"]) == len(state["acks"]) == 1


def test_inspect_allocation_confirms_absence_without_sent_or_writes(world):
    target, port, state, current, _hooks = world
    assert port.inspect_allocation(replace(target, certificate_arn=""), current=current) is True
    assert not state["writes"] and not state["sent"] and not state["acks"]


def test_inspect_allocation_refuses_existing_owned_domain_without_adoption(world):
    target, port, state, current, hooks = reviewed(world)
    port.create_domain(target, hooks=hooks, current=current)
    with pytest.raises(HttpApiRouteUnavailable, match="DOMAIN_ALREADY_EXISTS"):
        port.inspect_allocation(target, current=current)
    assert len(state["writes"]) == len(state["sent"]) == len(state["acks"]) == 1


def test_inspect_allocation_denied_is_not_absence(world):
    target, port, state, current, _hooks = world
    state["deny"] = "/v2/domainnames/"
    with pytest.raises(HttpApiRouteUnavailable, match="NATIVE_READ_UNAVAILABLE"):
        port.inspect_allocation(target, current=current)
    assert not state["writes"] and not state["sent"] and not state["acks"]


def test_inspect_allocation_rechecks_actual_account_after_absence(world):
    target, port, state, current, _hooks = world

    def on_reply(path):
        if "/domainnames/" in path:
            state["account"] = "999999999999"
        else:
            state["before_reply"] = on_reply

    state["before_reply"] = on_reply
    with pytest.raises(HttpApiRouteUnavailable, match="ACCOUNT_MISMATCH"):
        port.inspect_allocation(target, current=current)
    assert not state["writes"] and not state["sent"] and not state["acks"]
