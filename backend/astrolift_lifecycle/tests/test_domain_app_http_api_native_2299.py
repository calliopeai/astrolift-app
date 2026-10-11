"""Authenticated review/reservation + real SDK native HTTP execution, no cloud."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree import ElementTree as ET

import boto3
import pytest
from botocore.awsrequest import AWSPreparedRequest
from botocore.httpsession import URLLib3Session

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http
from astrolift_identity.models import ApiToken
from astrolift_lifecycle import domain_app_route_execution as execution
from astrolift_lifecycle.models import ManagedDomainAppBinding
from astrolift_services.models import ManagedService
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role
from providers.tests.aws.test_domain_route_api_gateway_http import (
    CERTIFICATE,
    FUNCTION,
    REGION,
)
from providers.tests.aws.test_domain_route_api_gateway_http import world as native_world_fixture

from .test_domain_app_route_store_2299 import admitted as admitted_fixture
from .test_domain_app_route_store_2299 import world  # noqa: F401

native_http_world = native_world_fixture
admitted = admitted_fixture

pytestmark = pytest.mark.django_db(transaction=True)
_ORIGINAL_SEND = URLLib3Session.send
REVIEW = """query($input: ManagedDomainAppRouteTargetInput!) { managedDomainAppRouteReview(input: $input) { kind appId appVersion reviewSha256 canAttach canPrepare allocationState apiId apiEndpoint records {name target targetZoneId} } }"""
CONNECT = """mutation($input: ManagedDomainAppRouteWriteInput!) { connectManagedDomainAppRoute(input: $input) {ok errors {code} data {id requestId status}} }"""


@contextmanager
def dns_certificate_wire(monkeypatch):
    state = {
        "records": [],
        "writes": [],
        "requests": [],
        "certificate_status": "ISSUED",
        "validation": [],
        "certificate_requests": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path))
            if self.headers.get("X-Amz-Target", "").endswith("DescribeCertificate"):
                data = json.dumps(
                    {
                        "Certificate": {
                            "CertificateArn": CERTIFICATE,
                            "Status": state["certificate_status"],
                            "DomainName": "example.test",
                            "SubjectAlternativeNames": ["example.test", "*.example.test"],
                            "NotBefore": 1700000000,
                            "NotAfter": 2000000000,
                            "DomainValidationOptions": state["validation"],
                        }
                    }
                )
            elif self.headers.get("X-Amz-Target", "").endswith("RequestCertificate"):
                state["certificate_requests"].append(json.loads(body))
                state["certificate_status"] = "PENDING_VALIDATION"
                state["validation"] = [
                    {
                        "DomainName": "example.test",
                        "ResourceRecord": {
                            "Name": "_abc.example.test.",
                            "Type": "CNAME",
                            "Value": "_def.acm-validations.aws.",
                        },
                    }
                ]
                data = json.dumps({"CertificateArn": CERTIFICATE})
            elif self.path.startswith("/2013-04-01/hostedzone/ZOWNED/rrset") and self.command == "GET":
                records = "".join(state["records"])
                data = f'<ListResourceRecordSetsResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ResourceRecordSets>{records}</ResourceRecordSets><IsTruncated>false</IsTruncated><MaxItems>16</MaxItems></ListResourceRecordSetsResponse>'
            elif self.path.startswith("/2013-04-01/hostedzone/ZOWNED/rrset") and self.command == "POST":
                root = ET.fromstring(body)
                record = next(n for n in root.iter() if n.tag.split("}")[-1] == "ResourceRecordSet")
                for node in record.iter():
                    node.tag = node.tag.split("}")[-1]
                state["records"].append(ET.tostring(record, encoding="unicode"))
                if next(n.text for n in record if n.tag == "Type") == "CNAME":
                    state["certificate_status"] = "ISSUED"
                state["writes"].append(body)
                data = '<ChangeResourceRecordSetsResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ChangeInfo><Id>/change/COWNED</Id><Status>PENDING</Status><SubmittedAt>2026-10-04T00:00:00Z</SubmittedAt></ChangeInfo></ChangeResourceRecordSetsResponse>'
            elif self.path.startswith("/2013-04-01/change/COWNED"):
                data = '<GetChangeResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ChangeInfo><Id>/change/COWNED</Id><Status>INSYNC</Status><SubmittedAt>2026-10-04T00:00:00Z</SubmittedAt></ChangeInfo></GetChangeResponse>'
            elif self.path.rstrip("/") == "/2013-04-01/hostedzone/ZOWNED":
                data = '<GetHostedZoneResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><HostedZone><Id>/hostedzone/ZOWNED</Id><Name>example.test.</Name><CallerReference>owned</CallerReference><Config><PrivateZone>false</PrivateZone></Config></HostedZone></GetHostedZoneResponse>'
            else:
                raise AssertionError((self.command, self.path))
            raw = data.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = URLLib3Session.send

    def send(session, request):
        parsed = urlsplit(request.url)
        if parsed.hostname == "route53.amazonaws.com" or parsed.hostname.startswith("acm."):
            url = (
                f"http://127.0.0.1:{server.server_port}"
                + parsed.path
                + ("?" + parsed.query if parsed.query else "")
            )
            return _ORIGINAL_SEND(
                session,
                AWSPreparedRequest(request.method, url, request.headers, request.body, request.stream_output),
            )
        return previous(session, request)

    monkeypatch.setattr(URLLib3Session, "send", send)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def connected_source(admitted, native_http_world, monkeypatch):
    w = admitted
    target, _port, state, _current, _hooks = native_http_world
    bind_role(w.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=w.org.pk, slug=uuid4().hex)
    w.cluster.region = REGION
    w.cluster.provider_config = {**w.cluster.provider_config, "region": REGION}
    w.cluster.save()
    w.domain.provision_cert_id = CERTIFICATE
    w.domain.save()
    w.medops_app.manifest_raw = """name = "owned"
[[workloads]]
name = "web"
kind = "faas"
is_public = true
faas_public = true
faas_package_type = "image"
"""
    w.medops_app.save()
    w.workload.kind = "faas"
    w.workload.save()
    for _kind, _variant, suffix, _handle, config, guid in (
        ("faas", "lambda", "fn", "faas/owned", {}, target.faas_service_id),
        (
            "api_gateway",
            "http_api",
            "api",
            "api_gateway/abc123",
            {"lambda_function_arn": FUNCTION},
            target.api_service_id,
        ),
    ):
        service = ManagedService.objects.get(
            registered_app=w.medops_app, app_environment=w.env, name=f"web-production-{suffix}"
        )
        service.guid, service.config, service.applied_config = guid, config, config
        service.save()
    from aws import session

    def build(service, **kwargs):
        return boto3.client(
            service, aws_access_key_id="fixture-access", aws_secret_access_key="fixture-secret", **kwargs
        )

    monkeypatch.setattr(session, "_default_build", build)
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow_once",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )
    w.route_input = {
        "domainId": str(w.domain.guid),
        "expectedDomainVersion": w.domain.version,
        "environmentId": str(w.env.guid),
        "expectedEnvironmentVersion": w.env.version,
        "workloadId": str(w.workload.guid),
        "hostname": target.hostname,
    }
    w.native = state
    w.starts = starts
    return w


def accept(client, w):
    reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
    assert not reply.get("errors"), reply
    review = reply["data"]["managedDomainAppRouteReview"]
    assert review["canAttach"] and review["kind"] == "FAAS_HTTP_API", review
    assert review["appId"] == str(w.medops_app.guid) and review["appVersion"] == w.medops_app.version
    assert review["allocationState"] == "PENDING_NATIVE_DOMAIN"
    assert review["records"][0]["target"] is None
    reply = graphql_http(
        client,
        w.headers,
        CONNECT,
        {"input": {**w.route_input, "reviewSha256": review["reviewSha256"], "requestId": str(uuid4())}},
    )
    assert not reply.get("errors"), reply
    result = reply["data"]["connectManagedDomainAppRoute"]
    assert result["ok"], result
    assert len(w.starts) == 1
    return ManagedDomainAppBinding.objects.get(guid=result["data"]["id"])


def test_actual_authenticated_review_connect_and_native_allocation_mapping_alias(
    connected_source, client, monkeypatch
):
    w = connected_source
    with dns_certificate_wire(monkeypatch) as dns:
        row = accept(client, w)
        assert w.native["writes"] == [] and dns["writes"] == []
        result = execution.reconcile(row.guid, checkpoint=lambda: None)
        assert result["status"] == "ready", result
        row.refresh_from_db()
        assert row.native_receipts["api-domain"]["phase"] == "ACK"
        assert row.native_receipts["api-mapping"]["phase"] == "ACK"
        assert row.native_receipts["alias"]["phase"] == "ACK"
        assert len(w.native["writes"]) == 2 and len(dns["writes"]) == 1
        assert b"d-original.execute-api" in dns["writes"][0]
        assert b"abc123.execute-api" not in dns["writes"][0]
        execution.reconcile(row.guid, checkpoint=lambda: None)
        assert len(w.native["writes"]) == 2 and len(dns["writes"]) == 1


def test_native_lost_domain_reply_retains_unknown_and_never_resends(connected_source, client, monkeypatch):
    w = connected_source
    with dns_certificate_wire(monkeypatch) as dns:
        row = accept(client, w)
        w.native["lost"] = "/v2/domainnames"
        with pytest.raises(ValueError):
            execution.reconcile(row.guid, checkpoint=lambda: None)
        row.refresh_from_db()
        assert row.native_receipts["api-domain"]["phase"] == "UNKNOWN"
        assert "ack" not in row.native_receipts["api-domain"]
        result = execution.reconcile(row.guid, checkpoint=lambda: None)
        assert result["status"] == "unknown"
        assert len(w.native["writes"]) == 1 and dns["writes"] == []


def test_native_returned_domain_ack_survives_withdrawal_without_mapping_or_alias(
    connected_source, client, monkeypatch
):
    w = connected_source
    with dns_certificate_wire(monkeypatch) as dns:
        row = accept(client, w)
        w.native["before_domain_reply"] = lambda: ApiToken.objects.filter(pk=w.token.pk).update(
            is_revoked=True
        )
        with pytest.raises(PermissionDenied):
            execution.reconcile(row.guid, checkpoint=lambda: None)
        row.refresh_from_db()
        assert row.native_receipts["api-domain"]["phase"] == "ACK"
        assert row.native_receipts["api-domain"]["ack"]["domain_name_arn"]
        assert len(w.native["writes"]) == 1 and dns["writes"] == []
        assert "api-mapping" not in row.native_receipts


OPERATION = """query($input: ManagedDomainAppRouteOperationInput!) { managedDomainAppRouteOperation(input:$input) { id requestId kind appId clusterId hostname purpose status canReconcile allocationState records {target targetZoneId} observedAt }}"""
PREPARE = """mutation($input:ManagedDomainAppRouteWriteInput!) { prepareManagedDomainAppRoute(input:$input) {ok errors {code} data {id requestId status}} }"""


def test_original_unknown_operation_recovers_without_review_or_new_allocation(
    connected_source, client, monkeypatch
):
    w = connected_source
    with dns_certificate_wire(monkeypatch) as dns:
        row = accept(client, w)
        w.native["lost"] = "/v2/domainnames"
        with pytest.raises(ValueError):
            execution.reconcile(row.guid, checkpoint=lambda: None)
        query = {"input": {"domainId": str(w.domain.guid), "requestId": str(row.request_id)}}
        result = graphql_http(client, w.headers, OPERATION, query)
        assert not result.get("errors"), result
        current = result["data"]["managedDomainAppRouteOperation"]
        assert current["requestId"] == str(row.request_id) and current["kind"] == "FAAS_HTTP_API"
        assert current["appId"] == str(w.medops_app.guid) and current["clusterId"] == str(w.cluster.guid)
        assert current["status"] == "unknown" and current["canReconcile"]
        assert current["records"][0]["target"] is None and current["observedAt"] is None
        assert len(w.native["writes"]) == 1 and dns["writes"] == []
        ApiToken.objects.filter(pk=w.token.pk).update(is_revoked=True)
        rejected = client.post(
            "/app/gql/config/",
            json.dumps({"query": OPERATION, "variables": query}),
            content_type="application/json",
            **w.headers,
        )
        assert rejected.status_code == 401
        assert len(w.native["writes"]) == 1


def test_missing_certificate_prepares_exact_existing_zone_then_connect_review(
    connected_source, client, monkeypatch
):
    w = connected_source
    w.domain.provision_cert_id = ""
    w.domain.dns_config = {}
    w.domain.save()
    w.route_input["expectedDomainVersion"] = w.domain.version
    with dns_certificate_wire(monkeypatch) as dns:
        reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
        review = reply["data"]["managedDomainAppRouteReview"]
        assert review["canPrepare"] and not review["canAttach"]
        reply = graphql_http(
            client,
            w.headers,
            PREPARE,
            {"input": {**w.route_input, "reviewSha256": review["reviewSha256"], "requestId": str(uuid4())}},
        )
        result = reply["data"]["prepareManagedDomainAppRoute"]
        assert result["ok"], reply
        row = ManagedDomainAppBinding.objects.get(guid=result["data"]["id"])
        assert execution.reconcile(row.guid, checkpoint=lambda: None)["status"] == "pending"
        assert execution.reconcile(row.guid, checkpoint=lambda: None)["status"] == "ready"
        w.domain.refresh_from_db()
        assert w.domain.provision_zone_id == "ZOWNED" and w.domain.provision_cert_id == CERTIFICATE
        assert len(dns["certificate_requests"]) == 1 and len(dns["writes"]) == 1
        assert len(w.native["writes"]) == 0
        assert dns["certificate_requests"][0]["IdempotencyToken"] == row.guid.hex
        w.route_input["expectedDomainVersion"] = w.domain.version
        reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
        assert not reply.get("errors"), reply
        assert reply["data"]["managedDomainAppRouteReview"]["canAttach"]
        assert len(dns["certificate_requests"]) == 1


@pytest.mark.parametrize("change", ["domain_version", "environment_version", "scope"])
def test_route_review_current_ceiling_and_versions_refuse_before_native(
    connected_source, client, monkeypatch, change
):
    w = connected_source
    if change == "scope":
        ApiToken.objects.filter(pk=w.token.pk).update(scopes=["write:apps"])
    else:
        key = "expectedDomainVersion" if change == "domain_version" else "expectedEnvironmentVersion"
        w.route_input[key] += 1
    with dns_certificate_wire(monkeypatch) as dns:
        reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
        assert reply.get("errors"), reply
        assert w.native["requests"] == [] and w.native["writes"] == [] and dns["requests"] == []
        assert not ManagedDomainAppBinding.objects.exists()


def test_native_allocation_access_denied_is_safe_unavailable_not_attachable(
    connected_source, client, monkeypatch
):
    w = connected_source
    w.native["deny"] = "/v2/domainnames"
    with dns_certificate_wire(monkeypatch) as dns:
        reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
        assert reply.get("errors"), reply
        assert "PRIVATE" not in json.dumps(reply) and w.native["writes"] == [] and dns["writes"] == []
        assert not ManagedDomainAppBinding.objects.exists()


def test_read_only_viewer_cannot_borrow_original_actors_reconcile_hint(connected_source, client, monkeypatch):
    from astrolift_clusters.tests.domain_http_helpers_2287 import http_token

    w = connected_source
    with dns_certificate_wire(monkeypatch):
        row = accept(client, w)
        _token, headers = http_token(w, scopes=("read:apps", "read:clusters"))
        reply = graphql_http(
            client,
            headers,
            OPERATION,
            {"input": {"domainId": str(w.domain.guid), "requestId": str(row.request_id)}},
        )
        assert not reply.get("errors"), reply
        assert reply["data"]["managedDomainAppRouteOperation"]["canReconcile"] is False
        assert w.native["writes"] == []
