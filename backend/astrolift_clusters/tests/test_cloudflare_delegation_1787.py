# ruff: noqa: F811  (pytest fixtures imported from the read-connection tests)
"""Delegating a managed child zone from a Cloudflare parent (#1787), over the
same fixed-origin TLS fixture as the read-only connection tests."""

import json

import pytest
from django.test import Client

from astrolift_clusters import cloudflare_delegation as delegation
from astrolift_clusters import cloudflare_dns_transport as transport
from astrolift_clusters.models import DnsProviderConnection, ManagedDomain
from astrolift_clusters.tests.test_cloudflare_dns_connections_1787 import (  # noqa: F401  (fixtures)
    TOKEN,
    ZONE,
    ZONE_ID,
    connect,
    envelope,
    graphql,
    wire,
    world,
)

pytestmark = pytest.mark.django_db(transaction=True)
WRITE_TOKEN = "Cf_Delegation_Marker_1787_Write_Only"
CHILD = "astro.example.test"
ROUTE53 = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk", "ns-3.awsdns-03.com", "ns-4.awsdns-04.net"]
GRANT = (
    "mutation($i:GrantCloudflareDelegationInput!){grantCloudflareDelegation(input:$i)"
    "{ok errors{code} data{id version dnsWritesSupported delegationZoneName}}}"
)
BIND = (
    "mutation($i:BindManagedDomainDelegationInput!){bindManagedDomainDelegation(input:$i)"
    "{ok errors{code} data{id}}}"
)


@pytest.fixture
def client():
    return Client()


def _record(name, kind, content, rid):
    return {"id": rid * 32, "name": name, "type": kind, "content": content, "ttl": 3600, "proxied": False}


def _serve(wire, records):
    """GET dns_records pages the given rows; a POST echoes the created NS."""

    def override(path):
        if path.endswith("/user/tokens/verify"):
            return {"success": True, "errors": [], "result": {"id": "t" * 32, "status": "active"}}
        if "/dns_records" in path and "?" in path:
            return envelope(records)
        if path.endswith("/dns_records"):
            body = json.loads(wire.calls[-1][3])
            return {
                "success": True,
                "errors": [],
                "result": _record(body["name"], "NS", body["content"], "d"),
            }
        return None

    wire.override = override


def _grant(client, world, wire, version):
    _serve(wire, [])
    reply = graphql(
        client,
        world,
        GRANT,
        {
            "i": {
                "connectionId": world.connection_id,
                "expectedVersion": version,
                "zoneId": ZONE_ID,
                "zoneName": ZONE["name"],
                "token": WRITE_TOKEN,
            }
        },
    )["data"]["grantCloudflareDelegation"]
    return reply


def _connected(client, world, wire):
    row = connect(client, world)
    world.connection_id = row["id"]
    return row


def _domain(world, zone=CHILD):
    return ManagedDomain.objects.create(
        organization=world.org, zone=zone, dns_driver="route53", provision_nameservers=ROUTE53
    )


def test_plan_creates_only_into_an_empty_name_and_leaves_anything_else():
    assert delegation.plan([], CHILD, ROUTE53) == {"action": "create", "nameservers": sorted(ROUTE53)}
    same = [_record(CHILD, "NS", ns, "e") for ns in ROUTE53]
    assert delegation.plan(same, CHILD, ROUTE53) == {"action": "in_place"}
    other = [_record(CHILD, "NS", "ns.elsewhere.test", "e")]
    assert delegation.plan(other, CHILD, ROUTE53)["action"] == "conflict"
    cname = [_record(CHILD, "CNAME", "x.example.test", "e")]
    assert delegation.plan(cname, CHILD, ROUTE53)["action"] == "conflict"
    # Records at other names do not matter.
    assert (
        delegation.plan([_record("www.example.test", "A", "1.2.3.4", "e")], CHILD, ROUTE53)["action"]
        == "create"
    )


def test_a_read_client_never_writes_and_a_write_client_writes_only_ns():
    read = transport.CloudflareReadClient.__new__(transport.CloudflareReadClient)
    read._writes, read._token, read._requests = False, TOKEN, 0
    read._checkpoint, read._clock, read._deadline = (lambda: None), (lambda: 0), 30
    with pytest.raises(transport.DnsConnectionError) as refused:
        read.json(f"/zones/{ZONE_ID}/dns_records", record={"type": "NS", "name": CHILD, "content": "x.test"})
    assert refused.value.reason == "WRITE_NOT_GRANTED"

    write = transport.CloudflareReadClient.__new__(transport.CloudflareReadClient)
    write._writes, write._token, write._requests = True, WRITE_TOKEN, 0
    write._checkpoint, write._clock, write._deadline = (lambda: None), (lambda: 0), 30
    for path, record in (
        (f"/zones/{ZONE_ID}/dns_records", {"type": "A", "name": CHILD, "content": "1.2.3.4"}),
        (f"/zones/{ZONE_ID}", {"type": "NS", "name": CHILD, "content": "x.test"}),
    ):
        with pytest.raises(transport.DnsConnectionError) as refused:
            write.json(path, record=record)
        assert refused.value.reason == "INVALID_NATIVE_PATH"


def test_grant_verifies_the_token_and_seals_it_beside_the_read_token(world, wire, client):
    row = _connected(client, world, wire)
    reply = _grant(client, world, wire, row["version"])

    assert reply["ok"], reply
    assert reply["data"]["dnsWritesSupported"] is True
    assert reply["data"]["delegationZoneName"] == ZONE["name"]
    stored = DnsProviderConnection.objects.get(guid=row["id"])
    assert WRITE_TOKEN.encode() not in bytes(stored.delegation_ciphertext)
    assert stored.delegation_zone_id == ZONE_ID
    assert any(call[1].endswith("/user/tokens/verify") for call in wire.calls)


def test_bind_needs_a_grant_and_a_true_parent(world, wire, client):
    row = _connected(client, world, wire)
    domain = _domain(world)
    stranger = _domain(world, zone="astro.other.test")

    unbound = graphql(
        client,
        world,
        BIND,
        {
            "i": {
                "domainId": str(domain.guid),
                "expectedDomainVersion": domain.version,
                "connectionId": row["id"],
                "expectedConnectionVersion": row["version"],
            }
        },
    )["data"]["bindManagedDomainDelegation"]
    assert unbound["errors"][0]["code"] == "WRITE_NOT_GRANTED"

    granted = _grant(client, world, wire, row["version"])["data"]
    for target, expect in ((stranger, "NOT_A_PARENT_ZONE"), (domain, None)):
        target.refresh_from_db()
        reply = graphql(
            client,
            world,
            BIND,
            {
                "i": {
                    "domainId": str(target.guid),
                    "expectedDomainVersion": target.version,
                    "connectionId": granted["id"],
                    "expectedConnectionVersion": granted["version"],
                }
            },
        )["data"]["bindManagedDomainDelegation"]
        if expect:
            assert reply["errors"][0]["code"] == expect
        else:
            assert reply["ok"], reply


def _bound(client, world, wire):
    row = _connected(client, world, wire)
    granted = _grant(client, world, wire, row["version"])["data"]
    domain = _domain(world)
    reply = graphql(
        client,
        world,
        BIND,
        {
            "i": {
                "domainId": str(domain.guid),
                "expectedDomainVersion": domain.version,
                "connectionId": granted["id"],
                "expectedConnectionVersion": granted["version"],
            }
        },
    )["data"]["bindManagedDomainDelegation"]
    assert reply["ok"], reply
    return domain


def _posts(wire):
    return [json.loads(body) for method, path, _h, body in wire.calls if method == "POST"]


def test_delegate_writes_the_ns_set_once_then_is_a_no_op(world, wire, client):
    domain = _bound(client, world, wire)
    wire.calls.clear()
    _serve(wire, [])

    outcome = delegation.delegate(domain.pk, ROUTE53)

    assert outcome == {"action": "created", "nameservers": sorted(ROUTE53)}
    assert sorted(p["content"] for p in _posts(wire)) == sorted(ROUTE53)
    assert {(p["type"], p["name"]) for p in _posts(wire)} == {("NS", CHILD)}
    assert all(WRITE_TOKEN in dict(h).get("Authorization", "") for m, _p, h, _b in wire.calls if m == "POST")

    wire.calls.clear()
    _serve(wire, [_record(CHILD, "NS", ns, "e6789"[i]) for i, ns in enumerate(ROUTE53)])
    assert delegation.delegate(domain.pk, ROUTE53) == {"action": "in_place"}
    assert _posts(wire) == []
    domain.refresh_from_db()
    assert domain.dns_config["delegation_write"]["action"] == "in_place"


def test_delegate_leaves_someone_elses_records_alone(world, wire, client):
    domain = _bound(client, world, wire)
    wire.calls.clear()
    _serve(wire, [_record(CHILD, "CNAME", "elsewhere.example.test", "e")])

    outcome = delegation.delegate(domain.pk, ROUTE53)

    assert outcome["action"] == "conflict" and "CNAME" in outcome["reason"]
    assert _posts(wire) == []


def test_an_unbound_domain_is_left_to_the_operator(world):
    domain = _domain(world)
    assert delegation.delegate(domain.pk, ROUTE53) == {"action": "unbound"}
