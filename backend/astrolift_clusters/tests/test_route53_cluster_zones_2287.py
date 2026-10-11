"""Route 53 zones listed and bound through a cluster's declared credential (#2287)."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_clusters.tests import test_cloudflare_dns_connections_1787 as cloudflare
from astrolift_clusters.tests import test_domain_diagnostics_2287 as diagnostics
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db(transaction=True)

ACCOUNT = "123456789012"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/astrolift-dns"
CLUSTERS = "{route53DnsClusters{complete reason items{id name region shared}}}"
ZONES = "query($i:Route53DnsZonesInput!){route53DnsZones(input:$i){complete reason truncated items{id name privateZone recordCount}}}"
ATTACH = "mutation($i:AttachRoute53DnsZoneInput!){attachRoute53DnsZone(input:$i){ok errors{code} data{domainId domainVersion clusterId accountId verificationState zone{id name privateZone}}}}"
ZONE = {"Id": "/hostedzone/ZCLUSTER1", "Name": "example.test.", "Config": {"PrivateZone": False}}


@pytest.fixture
def world(monkeypatch):
    w = diagnostics.world.__wrapped__(monkeypatch)
    w.bearer = w.headers["HTTP_AUTHORIZATION"].removeprefix("Bearer ")
    # DNS connections are platform-operator only, as for Cloudflare.
    w.user.is_superuser = True
    w.user.save()
    return w


@pytest.fixture
def dns_wire(monkeypatch):
    yield from diagnostics.dns_wire.__wrapped__(monkeypatch)


def aws_cluster(org, *, credential=True, verified=True, account=ACCOUNT):
    if not ProviderPlugin.objects.filter(slug="aws").exists():
        ProviderPlugin.objects.bulk_create(
            [
                ProviderPlugin(
                    name="AWS", slug="aws", plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
                )
            ]
        )
    suffix = uuid4().hex[:8]
    return TenantCluster.objects.create(
        organization=org,
        name=f"aws-{suffix}",
        slug=f"aws-{suffix}",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        provider_config={"credential": {"mode": "aws_assume_role", "role_arn": ROLE}} if credential else {},
        region="us-west-2",
        cloud_account_id=account,
        cloud_account_verified_at=timezone.now() if verified else None,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )


@pytest.fixture
def aws(monkeypatch):
    """The reviewed client factory, recording which identity each call used."""
    state = {
        "opened": [],
        "calls": [],
        "zones": [ZONE, {**ZONE, "Id": "/hostedzone/ZAPEX2", "Name": "apex.test."}],
    }

    class Route53:
        def list_hosted_zones(self, **kwargs):
            state["calls"].append(("list_hosted_zones", kwargs))
            return {"HostedZones": state["zones"], "IsTruncated": False}

        def get_hosted_zone(self, Id):
            state["calls"].append(("get_hosted_zone", Id))
            zone = next(z for z in state["zones"] if z["Id"].endswith("/" + Id))
            return {"HostedZone": zone, "DelegationSet": {"NameServers": ["ns-1.awsdns.test"]}}

        def list_resource_record_sets(self, **kwargs):
            state["calls"].append(("list_resource_record_sets", kwargs))
            return {
                "ResourceRecordSets": [
                    {
                        "Name": "example.test.",
                        "Type": "A",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": "192.0.2.1"}],
                    }
                ],
                "IsTruncated": False,
            }

    @contextmanager
    def clients(*, region, credential, checkpoint, expected_account_id="", services=()):
        state["opened"].append(
            (region, credential.role_arn, credential.is_ambient, expected_account_id, services)
        )
        checkpoint()
        yield {"route53": Route53()}, expected_account_id

    monkeypatch.setattr("aws.domain_route_clients.clients", clients)
    monkeypatch.setattr(
        diagnostics.service, "_route53", lambda current: pytest.fail("AMBIENT_ROUTE53_FALLBACK")
    )
    return state


def ask(client, w, query, variables=None):
    return cloudflare.graphql(client, w, query, variables)


def attach(client, w, cluster, **overrides):
    values = {
        "domainId": str(w.domain.guid),
        "expectedDomainVersion": w.domain.version,
        "clusterId": str(cluster.guid),
        "zoneId": "ZCLUSTER1",
        "zoneName": "example.test",
        **overrides,
    }
    return ask(client, w, ATTACH, {"i": values})["data"]["attachRoute53DnsZone"]


def test_an_operator_lists_the_orgs_and_shared_aws_clusters_only(world, client):
    own = aws_cluster(world.org)
    shared = aws_cluster(None, credential=False)
    aws_cluster(world.org, verified=False)
    aws_cluster(ScopeWorld("other-" + uuid4().hex[:8]).org)

    reply = ask(client, world, CLUSTERS)["data"]["route53DnsClusters"]

    assert reply["complete"] is True, reply
    assert {row["id"]: row["shared"] for row in reply["items"]} == {
        str(own.guid): False,
        str(shared.guid): True,
    }


def test_a_non_operator_is_refused_before_aws(world, client, aws):
    cluster = aws_cluster(world.org)
    world.user.is_superuser = False
    world.user.save()

    clusters = ask(client, world, CLUSTERS)["data"]["route53DnsClusters"]
    zones = ask(client, world, ZONES, {"i": {"clusterId": str(cluster.guid)}})["data"]["route53DnsZones"]
    bound = attach(client, world, cluster)

    assert clusters["reason"] == zones["reason"] == "PERMISSION_DENIED"
    assert bound["ok"] is False and bound["errors"][0]["code"] == "PERMISSION_DENIED"
    assert aws["opened"] == []


def test_zones_are_listed_with_the_clusters_credential_pinned_to_its_account(world, client, aws):
    cluster = aws_cluster(world.org)

    reply = ask(client, world, ZONES, {"i": {"clusterId": str(cluster.guid)}})["data"]["route53DnsZones"]

    assert reply["complete"] is True and reply["truncated"] is False
    assert [(z["id"], z["name"]) for z in reply["items"]] == [
        ("ZAPEX2", "apex.test"),
        ("ZCLUSTER1", "example.test"),
    ]
    assert aws["opened"] == [("us-west-2", ROLE, False, ACCOUNT, ("sts", "route53"))]


@pytest.mark.parametrize(
    "make, reason",
    [
        (lambda w: aws_cluster(ScopeWorld("other-" + uuid4().hex[:8]).org), "NOT_FOUND"),
        (lambda w: aws_cluster(w.org, verified=False), "CLUSTER_ACCOUNT_UNVERIFIED"),
    ],
)
def test_a_cluster_the_caller_may_not_list_through_is_refused_before_aws(world, client, aws, make, reason):
    cluster = make(world)

    reply = ask(client, world, ZONES, {"i": {"clusterId": str(cluster.guid)}})["data"]["route53DnsZones"]

    assert reply == {"complete": False, "reason": reason, "truncated": False, "items": []}
    assert aws["opened"] == []


def test_the_zone_binds_only_to_the_reviewed_domain(world, client, aws):
    cluster = aws_cluster(world.org)

    data = attach(client, world, cluster)

    assert data["ok"] is True, data
    world.domain.refresh_from_db()
    assert (
        world.domain.dns_cluster_id,
        world.domain.dns_cluster_zone_id,
        world.domain.dns_cluster_account_id,
    ) == (
        cluster.pk,
        "ZCLUSTER1",
        ACCOUNT,
    )
    assert data["data"]["zone"] == {"id": "ZCLUSTER1", "name": "example.test", "privateZone": False}
    assert ("get_hosted_zone", "ZCLUSTER1") in aws["calls"]


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"zoneName": "apex.test", "zoneId": "ZAPEX2"}, "DOMAIN_CHANGED"),
        ({"zoneName": "example.test", "zoneId": "ZAPEX2"}, "PROVIDER_ZONE_IDENTITY_MISMATCH"),
        ({"zoneId": "not-a-zone"}, "INVALID_ID"),
        ({"expectedDomainVersion": 999}, "DOMAIN_CHANGED"),
    ],
)
def test_a_mismatched_zone_or_stale_domain_is_refused_without_a_write(world, client, aws, overrides, code):
    cluster = aws_cluster(world.org)

    data = attach(client, world, cluster, **overrides)

    assert data["ok"] is False and data["errors"][0]["code"] == code
    world.domain.refresh_from_db()
    assert world.domain.dns_cluster_id is None and world.domain.dns_cluster_zone_id == ""


def test_diagnostics_read_a_bound_zone_through_the_cluster_credential(world, client, aws, dns_wire):
    cluster = aws_cluster(world.org)
    assert attach(client, world, cluster)["ok"]
    world.domain.refresh_from_db()
    aws["opened"].clear()
    diagnostics.dns_answers(dns_wire)

    reply = diagnostics.ask(client, world)

    assert not reply.get("errors"), reply.get("errors")
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "OK" and zone["bindingSource"] == "CLUSTER_CREDENTIAL"
    assert zone["zoneId"] == "ZCLUSTER1" and zone["records"][0]["values"] == ["192.0.2.1"]
    assert aws["opened"] == [("us-west-2", ROLE, False, ACCOUNT, ("sts", "route53"))]


@pytest.mark.parametrize("change", ["account", "foreign", "inactive"])
def test_a_withdrawn_cluster_binding_never_falls_back_to_ambient(world, client, aws, dns_wire, change):
    cluster = aws_cluster(world.org)
    assert attach(client, world, cluster)["ok"]
    if change == "account":
        TenantCluster.objects.filter(pk=cluster.pk).update(cloud_account_id="210987654321")
    elif change == "foreign":
        TenantCluster.objects.filter(pk=cluster.pk).update(
            organization=ScopeWorld("o-" + uuid4().hex[:8]).org
        )
    else:
        TenantCluster.objects.filter(pk=cluster.pk).update(is_active=False)
    world.domain.refresh_from_db()
    aws["opened"].clear()
    diagnostics.dns_answers(dns_wire)

    zone = diagnostics.ask(client, world)["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]

    assert zone["state"] == "ERROR" and zone["reason"] == "PROVIDER_CLUSTER_UNAVAILABLE"
    assert zone["records"] == [] and aws["opened"] == []
    assert ManagedDomain.objects.get(pk=world.domain.pk).dns_cluster_zone_id == "ZCLUSTER1"
