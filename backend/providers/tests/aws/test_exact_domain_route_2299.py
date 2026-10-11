"""Actual botocore HTTP serialization for exact apex aliases and ACM intent."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from _sdk.dns import ExactAliasRoute
from aws.domain_route import DomainRouteError, create_alias, observe_alias
from aws.domain_route_certificate import request_certificate

ZONE = "example.test"
ROUTE = ExactAliasRoute("ZOWNED", ZONE, ZONE, "custom-123.us-east-1.elb.amazonaws.com", "ZALB")
ARN = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"


class Wire:
    def __init__(self):
        self.calls = []
        self.records = []
        self.lost = False
        self.on_response = None

    def response(self, method, path, body):
        self.calls.append((method, path, body))
        if path.startswith("/2013-04-01/hostedzone/ZOWNED/rrset") and method == "GET":
            records = "".join(self.records)
            data = f'<ListResourceRecordSetsResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ResourceRecordSets>{records}</ResourceRecordSets><IsTruncated>false</IsTruncated><MaxItems>16</MaxItems></ListResourceRecordSetsResponse>'
        elif path in ("/2013-04-01/hostedzone/ZOWNED", "/2013-04-01/hostedzone/ZOWNED/"):
            data = f'<GetHostedZoneResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><HostedZone><Id>/hostedzone/ZOWNED</Id><Name>{ZONE}.</Name><CallerReference>astrolift-owned</CallerReference><Config><PrivateZone>false</PrivateZone></Config></HostedZone></GetHostedZoneResponse>'
        elif method == "POST" and path in (
            "/2013-04-01/hostedzone/ZOWNED/rrset/",
            "/2013-04-01/hostedzone/ZOWNED/rrset",
        ):
            data = '<ChangeResourceRecordSetsResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ChangeInfo><Id>/change/COWNED</Id><Status>PENDING</Status><SubmittedAt>2026-10-04T00:00:00Z</SubmittedAt></ChangeInfo></ChangeResourceRecordSetsResponse>'
        elif method == "POST" and path == "/":
            data = json.dumps({"CertificateArn": ARN})
        else:
            raise AssertionError((method, path))
        if self.on_response:
            self.on_response(method, path)
        return data.encode()


@contextmanager
def native():
    wire = Wire()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            data = wire.response(self.command, self.path, body)
            if wire.lost and self.command == "POST":
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    clients = {
        name: boto3.client(
            name,
            region_name="us-east-1",
            endpoint_url=endpoint,
            aws_access_key_id="fixture",
            aws_secret_access_key="fixture",
            config=Config(retries={"total_max_attempts": 1}, connect_timeout=1, read_timeout=2),
        )
        for name in ("route53", "acm")
    }
    try:
        yield wire, clients
    finally:
        for client in clients.values():
            client.close()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_apex_is_native_create_alias_not_cname():
    with native() as (wire, clients):
        ack = []
        outcome = create_alias(clients["route53"], ROUTE, checkpoint=lambda: None, record_ack=ack.append)
        assert outcome.change_id == "/change/COWNED" and ack == [outcome]
        posts = [body for method, _path, body in wire.calls if method == "POST"]
        assert len(posts) == 1
        assert b"<Action>CREATE</Action>" in posts[0]
        assert b"<Type>A</Type>" in posts[0] and b"<AliasTarget>" in posts[0]
        assert b"<HostedZoneId>ZALB</HostedZoneId>" in posts[0]
        assert b"CNAME" not in posts[0] and b"UPSERT" not in posts[0]


@pytest.mark.parametrize("kind", ["A", "AAAA", "CNAME"])
def test_existing_address_records_refuse_without_write(kind):
    with native() as (wire, clients):
        wire.records = [
            f"<ResourceRecordSet><Name>{ZONE}.</Name><Type>{kind}</Type><TTL>300</TTL><ResourceRecords><ResourceRecord><Value>192.0.2.1</Value></ResourceRecord></ResourceRecords></ResourceRecordSet>"
        ]
        with pytest.raises(DomainRouteError, match="NATIVE_HOST_ALREADY_ROUTED"):
            create_alias(clients["route53"], ROUTE, checkpoint=lambda: None, record_ack=lambda _: None)
        assert not any(method == "POST" for method, _path, _body in wire.calls)


def test_lost_native_reply_is_unknown_with_one_send():
    with native() as (wire, clients):
        wire.lost = True
        acks = []
        with pytest.raises(DomainRouteError, match="NATIVE_DNS_ACK_UNKNOWN"):
            create_alias(clients["route53"], ROUTE, checkpoint=lambda: None, record_ack=acks.append)
        assert acks == []
        assert sum(method == "POST" for method, _path, _body in wire.calls) == 1


def test_ack_retained_before_post_reply_authority_withdrawal():
    with native() as (wire, clients):
        withdrawn = False
        acks = []

        def checkpoint():
            if withdrawn:
                raise PermissionError("withdrawn")

        def after(method, _path):
            nonlocal withdrawn
            if method == "POST":
                withdrawn = True

        wire.on_response = after
        with pytest.raises(PermissionError):
            create_alias(clients["route53"], ROUTE, checkpoint=checkpoint, record_ack=acks.append)
        assert len(acks) == 1 and acks[0].change_id == "/change/COWNED"
        assert sum(method == "POST" for method, _path, _body in wire.calls) == 1


def test_absent_observation_does_not_write():
    with native() as (wire, clients):
        result = observe_alias(clients["route53"], ROUTE, checkpoint=lambda: None)
        assert not result.present and not result.matches
        assert not any(method == "POST" for method, _path, _body in wire.calls)


def test_native_certificate_uses_original_operation_and_retains_ack():
    with native() as (wire, clients):
        operation = str(uuid4())
        acks = []
        arn = request_certificate(
            clients["acm"],
            zone=ZONE,
            region="us-east-1",
            account="123456789012",
            operation_id=operation,
            checkpoint=lambda: None,
            record_ack=acks.append,
        )
        assert arn == ARN and acks == [ARN]
        request = json.loads(wire.calls[0][2])
        assert request["DomainName"] == ZONE and request["SubjectAlternativeNames"] == ["*." + ZONE]
        assert request["IdempotencyToken"] == operation.replace("-", "")
        assert request["Tags"] == [{"Key": "astrolift.io/domain-route-operation", "Value": operation}]


def test_legacy_driver_resolution_cannot_assume_role_before_reviewed_port(monkeypatch):
    from aws.dns_route53 import Route53Driver

    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy client must remain lazy")

    monkeypatch.setattr("aws.dns_route53.aws_client", forbidden)
    assert Route53Driver()._r53_native is None


def test_signing_refresh_withdrawal_refuses_before_using_refreshed_identity():
    from aws.domain_route_clients import _RouteSigningCredentials

    calls = []
    live = True

    class NativeCredentials:
        def get_frozen_credentials(self):
            nonlocal live
            calls.append("refresh")
            live = False
            return object()

    def current():
        calls.append("current")
        if not live:
            raise PermissionError("withdrawn")

    with pytest.raises(PermissionError):
        _RouteSigningCredentials(NativeCredentials(), current).get_frozen_credentials()
    assert calls == ["current", "refresh", "current"]


def test_deep_subdomain_certificate_declares_exact_name_not_wildcard_guess():
    with native() as (wire, clients):
        request_certificate(
            clients["acm"],
            zone=ZONE,
            hostname="api.deep." + ZONE,
            region="us-east-1",
            account="123456789012",
            operation_id=str(uuid4()),
            checkpoint=lambda: None,
            record_ack=lambda _: None,
        )
        request = json.loads(wire.calls[0][2])
        assert request["SubjectAlternativeNames"] == ["*." + ZONE, "api.deep." + ZONE]


def test_zero_operation_certificate_refuses_before_native():
    with native() as (wire, clients):
        with pytest.raises(DomainRouteError, match="INVALID_CERTIFICATE_OPERATION"):
            request_certificate(
                clients["acm"],
                zone=ZONE,
                region="us-east-1",
                account="123456789012",
                operation_id="00000000-0000-0000-0000-000000000000",
                checkpoint=lambda: None,
                record_ack=lambda _: None,
            )
        assert wire.calls == []
