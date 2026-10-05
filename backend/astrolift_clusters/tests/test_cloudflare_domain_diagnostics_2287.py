"""Actual HTTP/PG domain binding and fixed-origin owned TLS provider reads."""

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from astrolift_clusters.models import DnsProviderConnection, ManagedDomain
from astrolift_clusters.tests import test_cloudflare_dns_connections_1787 as cloudflare
from astrolift_clusters.tests import test_domain_diagnostics_2287 as diagnostics
from astrolift_identity.models import Member
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db(transaction=True)

QUERY = diagnostics.DIAGNOSTICS.replace("evaluateTargetHealth", "evaluateTargetHealth proxied priority")
ATTACH = """mutation($i:AttachCloudflareDnsZoneInput!){attachCloudflareDnsZone(input:$i){ok errors{code} data{domainId domainVersion connectionId connectionVersion dnsWritesSupported}}}"""


@pytest.fixture
def world(monkeypatch):
    return diagnostics.world.__wrapped__(monkeypatch)


@pytest.fixture
def dns_wire(monkeypatch):
    yield from diagnostics.dns_wire.__wrapped__(monkeypatch)


@pytest.fixture
def wire(tmp_path, monkeypatch):
    yield from cloudflare.wire.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def attached(world, wire, client):
    world.user.is_superuser = True
    world.user.save()
    world.bearer = world.headers["HTTP_AUTHORIZATION"].removeprefix("Bearer ")
    result = cloudflare.connect(client, world)
    world.connection = DnsProviderConnection.objects.get(guid=result["id"])
    reply = cloudflare.graphql(
        client,
        world,
        ATTACH,
        {
            "i": {
                "domainId": str(world.domain.guid),
                "expectedDomainVersion": world.domain.version,
                "connectionId": result["id"],
                "expectedConnectionVersion": result["version"],
                "zoneId": cloudflare.ZONE_ID,
                "zoneName": world.domain.zone,
            }
        },
    )
    assert reply["data"]["attachCloudflareDnsZone"]["ok"]
    world.domain.refresh_from_db()
    wire.calls.clear()
    return world


def read(client, w, dns_wire):
    dns_wire["answers"] = [
        ("example.test", 2, diagnostics.service.network._encode_name("aa.ns.cloudflare.com")),
        ("example.test", 2, diagnostics.service.network._encode_name("bb.ns.cloudflare.com")),
    ]
    return diagnostics.ask(client, w, QUERY)


def test_exact_protected_cloudflare_attachment_supersedes_ambient_route53_read_only(
    attached, wire, dns_wire, client, monkeypatch
):
    def forbidden(_current):
        pytest.fail("AMBIENT_ROUTE53_FALLBACK")

    monkeypatch.setattr(diagnostics.service, "_route53", forbidden)
    before = (attached.domain.dns_driver, attached.domain.provision_state, attached.domain.verification_state)
    reply = read(client, attached, dns_wire)
    assert not reply.get("errors"), reply.get("errors")
    data = reply["data"]["astroliftManagedDomainDiagnostics"]
    zone = data["providerZone"]
    assert zone["state"] == "OK" and zone["reason"] == "PROVIDER_ZONE_OBSERVED_READ_ONLY"
    assert zone["bindingSource"] == "PROTECTED_CLOUDFLARE_CONNECTION"
    assert zone["zoneId"] == cloudflare.ZONE_ID and zone["zoneName"] == attached.domain.zone
    assert zone["privateZone"] is False and zone["truncated"] is False
    assert zone["nameservers"] == cloudflare.ZONE["name_servers"]
    assert zone["records"][0]["values"] == ["bounded-record-value"]
    assert zone["records"][0]["proxied"] is False and zone["records"][0]["priority"] is None
    assert all(row["state"] == "OK" for row in data["checks"] if row["key"] == "delegation")
    assert len(wire.calls) == 5 and all(row[0] == "GET" for row in wire.calls)
    attached.domain.refresh_from_db()
    assert before == (
        attached.domain.dns_driver,
        attached.domain.provision_state,
        attached.domain.verification_state,
    )


def test_tenant_public_diagnostics_never_read_provider_credentials(attached, wire, dns_wire, client):
    attached.user.is_superuser = False
    attached.user.save()
    reply = read(client, attached, dns_wire)
    assert not reply.get("errors")
    data = reply["data"]["astroliftManagedDomainDiagnostics"]
    assert data["providerZone"]["state"] == "UNSUPPORTED" and data["providerZone"]["records"] == []
    assert len([row for row in data["checks"] if row["key"] == "delegation"]) == 2
    assert wire.calls == []


@pytest.mark.parametrize(
    "change", ["stale", "disconnected", "expired", "foreign", "missing_zone", "foreign_zone"]
)
def test_unavailable_protected_binding_never_falls_back_to_ambient_or_empty_success(
    attached, wire, dns_wire, client, monkeypatch, change
):
    from django.utils import timezone

    if change == "stale":
        DnsProviderConnection.objects.filter(pk=attached.connection.pk).update(
            version=attached.connection.version + 1
        )
    elif change == "disconnected":
        DnsProviderConnection.objects.filter(pk=attached.connection.pk).update(state="DISCONNECTED")
    elif change == "expired":
        DnsProviderConnection.objects.filter(pk=attached.connection.pk).update(expires_at=timezone.now())
    elif change == "foreign":
        other = ScopeWorld("foreign-" + uuid4().hex[:8])
        DnsProviderConnection.objects.filter(pk=attached.connection.pk).update(organization=other.org)
    elif change == "missing_zone":
        ManagedDomain.objects.filter(pk=attached.domain.pk).update(dns_provider_zone_id="")
    else:
        zone = {**cloudflare.ZONE, "id": "d" * 32, "name": "foreign.test"}
        wire.override = {"success": True, "errors": [], "result": zone}
    monkeypatch.setattr(diagnostics.service, "_route53", lambda current: pytest.fail("AMBIENT_FALLBACK"))
    reply = read(client, attached, dns_wire)
    assert not reply.get("errors"), reply.get("errors")
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "ERROR" and zone["records"] == [] and zone["nameservers"] == []
    assert len(wire.calls) == (1 if change == "foreign_zone" else 0)


@pytest.mark.parametrize("change", ["binding", "zone", "version", "actor", "membership", "token", "scope"])
def test_after_native_response_source_or_authority_withdrawal_refuses_before_next_read(
    attached, wire, dns_wire, client, change
):
    def withdraw(_path):
        if change == "binding":
            ManagedDomain.objects.filter(pk=attached.domain.pk).update(dns_provider_zone_id="d" * 32)
        elif change == "zone":
            ManagedDomain.objects.filter(pk=attached.domain.pk).update(zone="different.test")
        elif change == "version":
            ManagedDomain.objects.filter(pk=attached.domain.pk).update(version=attached.domain.version + 1)
        elif change == "actor":
            type(attached.user).objects.filter(pk=attached.user.pk).update(is_active=False)
        elif change == "membership":
            Member.objects.filter(user=attached.user, scope_kind="ORG", scope_id=attached.org.pk).update(
                is_active=False
            )
        elif change == "token":
            type(attached.token).objects.filter(pk=attached.token.pk).update(is_revoked=True)
        else:
            type(attached.token).objects.filter(pk=attached.token.pk).update(scopes=["read:apps"])

    wire.hook = withdraw
    reply = read(client, attached, dns_wire)
    assert reply.get("errors") and reply["data"]["astroliftManagedDomainDiagnostics"] is None
    assert len(wire.calls) == 1


def test_provider_denial_is_sanitized_error_not_success(attached, wire, dns_wire, client, caplog, capsys):
    wire.status = 403
    reply = read(client, attached, dns_wire)
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "ERROR" and zone["reason"] == "PROVIDER_ACCESS_DENIED"
    assert zone["records"] == [] and len(wire.calls) == 1
    captured = capsys.readouterr()
    assert cloudflare.TOKEN not in str(reply) + caplog.text + captured.out + captured.err
    assert cloudflare.SECRET not in str(reply) + caplog.text + captured.out + captured.err


def record(index):
    return {
        "id": f"{index:032x}",
        "name": f"record{index}.example.test",
        "type": "MX",
        "content": "mail.example.test",
        "ttl": 300,
        "proxied": False,
        "priority": 10,
    }


def pages(path):
    if "/dns_records" not in path:
        return {"success": True, "errors": [], "result": cloudflare.ZONE}
    page = int(parse_qs(urlsplit(path).query)["page"][0])
    rows = [record(index) for index in range((page - 1) * 50 + 1, page * 50 + 1)]
    result = cloudflare.envelope(rows)
    result["result_info"].update(page=page, total_count=150, total_pages=3)
    return result


def test_complete_provider_pages_have_explicit_diagnostic_cap_and_priority(attached, wire, dns_wire, client):
    wire.override = pages
    reply = read(client, attached, dns_wire)
    assert not reply.get("errors")
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "OK" and zone["truncated"] and len(zone["records"]) == 100
    assert zone["records"][0]["priority"] == 10
    assert len(wire.calls) == 7


def test_binding_change_during_first_record_page_prevents_second_page(attached, wire, dns_wire, client):
    wire.override = pages

    def change(path):
        if "/dns_records" in path:
            ManagedDomain.objects.filter(pk=attached.domain.pk).update(dns_provider_connection_version=999)

    wire.hook = change
    reply = read(client, attached, dns_wire)
    assert reply.get("errors") and reply["data"]["astroliftManagedDomainDiagnostics"] is None
    assert len([row for row in wire.calls if "/dns_records" in row[1]]) == 1


def test_zone_metadata_drift_between_record_observations_is_unavailable(attached, wire, dns_wire, client):
    reads = []

    def change(path):
        reads.append(path)
        if len(reads) >= 5:
            return {"success": True, "errors": [], "result": {**cloudflare.ZONE, "account": {"id": "e" * 32}}}
        if "/dns_records" in path:
            return cloudflare.envelope([record(1)])
        return {"success": True, "errors": [], "result": cloudflare.ZONE}

    wire.override = change
    reply = read(client, attached, dns_wire)
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "ERROR" and zone["records"] == [] and len(wire.calls) == 5


def test_foreign_organization_context_has_zero_provider_or_dns_work(attached, wire, dns_wire, client):
    import json

    other = ScopeWorld("foreign-context-" + uuid4().hex[:8])
    headers = {**attached.headers, "HTTP_X_ASTROLIFT_ORGANIZATION": str(other.org.guid)}
    reply = client.post(
        "/app/gql/config/",
        content_type="application/json",
        data=json.dumps(
            {
                "query": QUERY,
                "variables": {"id": str(attached.domain.guid), "version": attached.domain.version},
            }
        ),
        **headers,
    )
    assert reply.status_code in (200, 403)
    if reply.status_code == 200:
        payload = reply.json()
        assert payload.get("errors") or payload["data"]["astroliftManagedDomainDiagnostics"] is None
    assert wire.calls == [] and dns_wire["queries"] == []
