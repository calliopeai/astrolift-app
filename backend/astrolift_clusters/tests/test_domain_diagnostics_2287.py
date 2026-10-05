"""Real authenticated GraphQL/PG and AWS SDK HTTP observations, no writes."""

import datetime as dt
import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from django.core.cache import cache
from django.utils import timezone

from astrolift_clusters import domain_diagnostics as service
from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.tests import test_domain_diagnostic_network_2287 as network_fixtures
from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.models import AstroliftSession, Member, Policy, RoleBinding
from astrolift_lifecycle.models import AppEnvironment
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db(transaction=True)

DIAGNOSTICS = """query($id:GUID!,$version:Int!){astroliftManagedDomainDiagnostics(domainId:$id,expectedVersion:$version){id version zone provisionClusterId checks{key state reason perspective expected observed} providerZone{state reason zoneId zoneName privateZone nameservers truncated bindingSource records{name type ttl values aliasTarget aliasZoneId evaluateTargetHealth}} routes{appId appSlug hostname recordedUrl clusterId observedState} routesTruncated actions{canCreate canDelete canRevalidate}}}"""
PROBE = """query($id:GUID!,$version:Int!,$hostname:String!,$tool:ManagedDomainProbeTool!){astroliftManagedDomainProbe(domainId:$id,expectedVersion:$version,hostname:$hostname,tool:$tool){state reason values tlsVerified publicAddress}}"""


@pytest.fixture
def dns_wire(monkeypatch):
    yield from network_fixtures.dns_wire.__wrapped__(monkeypatch)


@pytest.fixture
def world(monkeypatch):
    cache.clear()
    w = ScopeWorld("domain-" + uuid4().hex[:8])
    w.user = make_user(uuid4().hex[:8])
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        w.user,
        permissions=[
            Permission.PROVIDER_PLUGIN_READ,
            Permission.PROVIDER_PLUGIN_CONFIGURE,
            Permission.CLUSTER_MANAGE,
            Permission.APP_READ,
        ],
        kind="ORG",
        scope_id=w.org.pk,
        slug=uuid4().hex,
    )
    w.cluster = make_cluster(w, uuid4().hex[:8])
    w.domain = ManagedDomain.objects.create(
        organization=w.org,
        zone="example.test",
        dns_driver="route53",
        provision_zone_id="ZEXAMPLE",
        provision_nameservers=["ns.provider.test"],
        verification_state="pending",
        dns_config={"provision_cluster_id": w.cluster.pk},
    )
    w.token, w.headers = http_token(w, scopes=("admin",))
    return w


@pytest.fixture
def aws_wire(monkeypatch):
    state = {
        "requests": [],
        "zone": "example.test.",
        "private": False,
        "error": False,
        "before_reply": None,
        "pages": 1,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["requests"].append(self.path)
            if state["before_reply"]:
                callback, state["before_reply"] = state["before_reply"], None
                callback()
            if state["error"]:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(
                    b'<ErrorResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><Error><Type>Sender</Type><Code>AccessDenied</Code><Message>private-provider-body-marker</Message></Error><RequestId>fixture</RequestId></ErrorResponse>'
                )
                return
            if "rrset" in self.path:
                again = state["pages"] > 1 and "name=" not in self.path
                body = (
                    '<ListResourceRecordSetsResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ResourceRecordSets><ResourceRecordSet><Name>www.example.test.</Name><Type>A</Type><AliasTarget><HostedZoneId>ZALIAS</HostedZoneId><DNSName>lb.provider.test.</DNSName><EvaluateTargetHealth>false</EvaluateTargetHealth></AliasTarget></ResourceRecordSet></ResourceRecordSets><IsTruncated>'
                    + str(again).lower()
                    + "</IsTruncated>"
                    + (
                        "<NextRecordName>z.example.test.</NextRecordName><NextRecordType>A</NextRecordType>"
                        if again
                        else ""
                    )
                    + "<MaxItems>100</MaxItems></ListResourceRecordSetsResponse>"
                )
            else:
                body = f'<GetHostedZoneResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><HostedZone><Id>/hostedzone/ZEXAMPLE</Id><Name>{state["zone"]}</Name><CallerReference>fixture</CallerReference><Config><PrivateZone>{str(state["private"]).lower()}</PrivateZone></Config><ResourceRecordSetCount>2</ResourceRecordSetCount></HostedZone><DelegationSet><NameServers><NameServer>ns.provider.test</NameServer></NameServers></DelegationSet></GetHostedZoneResponse>'
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def client(current):
        value = boto3.client(
            "route53",
            region_name="us-east-1",
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            aws_access_key_id="synthetic",
            aws_secret_access_key="synthetic",
            config=Config(connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1}),
        )
        value.meta.events.register("before-send.*.*", lambda **_kwargs: current())
        return value

    monkeypatch.setattr(service, "_route53", client)
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


def ask(client, w, query=DIAGNOSTICS, **variables):
    return graphql_http(
        client, w.headers, query, {"id": str(w.domain.guid), "version": w.domain.version, **variables}
    )


def dns_answers(wire, *, match=False):
    wire["answers"] = [
        (
            "example.test",
            2,
            service.network._encode_name("ns.provider.test" if match else "other.provider.test"),
        )
    ]


@pytest.mark.parametrize("driver", ["route53", "cloudflare_read_only"])
def test_pending_owned_zone_has_public_mismatch_but_no_provider_inventory(
    world, client, dns_wire, aws_wire, driver
):
    world.domain.dns_driver = driver
    world.domain.save(update_fields=["dns_driver"])
    world.domain.refresh_from_db()
    dns_answers(dns_wire)
    reply = ask(client, world)
    assert not reply.get("errors"), reply.get("errors")
    data = reply["data"]["astroliftManagedDomainDiagnostics"]
    assert data["zone"] == "example.test"
    assert [row["state"] for row in data["checks"] if row["key"] == "delegation"] == ["MISMATCH", "MISMATCH"]
    assert data["providerZone"]["state"] == "UNSUPPORTED" and not aws_wire["requests"]
    assert data["providerZone"]["reason"] == "PLATFORM_OPERATOR_REQUIRED"
    assert data["providerZone"]["zoneId"] is None and data["providerZone"]["records"] == []
    assert data["provisionClusterId"] == str(world.cluster.guid) and not data["actions"]["canRevalidate"]
    world.domain.refresh_from_db()
    assert world.domain.verification_state == "pending"


def test_operator_exact_provider_alias_pages_private_zone_is_not_public_failure(
    world, client, dns_wire, aws_wire
):
    world.user.is_superuser = True
    world.user.save()
    aws_wire["pages"], aws_wire["private"] = 2, True
    dns_answers(dns_wire)
    reply = ask(client, world)
    assert not reply.get("errors"), reply.get("errors")
    data = reply["data"]["astroliftManagedDomainDiagnostics"]
    zone = data["providerZone"]
    assert zone["state"] == "OK" and zone["privateZone"] and not zone["truncated"]
    assert zone["bindingSource"] == "PLATFORM_WRITTEN"
    assert len(zone["records"]) == 2 and zone["records"][0]["aliasTarget"] == "lb.provider.test."
    assert zone["records"][0]["evaluateTargetHealth"] is False
    assert len(aws_wire["requests"]) == 3
    assert all(row["state"] == "UNSUPPORTED" for row in data["checks"] if row["key"] == "delegation")


def test_wrong_provider_zone_is_refused_before_records(world, client, dns_wire, aws_wire):
    world.user.is_superuser = True
    world.user.save()
    aws_wire["zone"] = "foreign.test."
    dns_answers(dns_wire, match=True)
    reply = ask(client, world)
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "ERROR" and zone["records"] == []
    assert len(aws_wire["requests"]) == 1


def test_provider_denial_is_error_not_empty_success_and_no_body_logging(
    world, client, dns_wire, aws_wire, caplog, capsys
):
    world.user.is_superuser = True
    world.user.save()
    aws_wire["error"] = True
    dns_answers(dns_wire)
    reply = ask(client, world)
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "ERROR" and zone["reason"] == "PROVIDER_ACCESS_DENIED"
    captured = capsys.readouterr()
    assert "private-provider-body-marker" not in str(reply) + caplog.text + captured.out + captured.err


@pytest.mark.parametrize("withdraw", ["token", "membership", "actor", "source", "scope"])
def test_post_provider_reply_withdrawal_releases_no_results_or_next_network(
    world, client, dns_wire, aws_wire, withdraw
):
    world.user.is_superuser = True
    world.user.save()

    def change():
        if withdraw == "token":
            type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
        elif withdraw == "membership":
            Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
                is_active=False
            )
        elif withdraw == "actor":
            type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
        elif withdraw == "scope":
            type(world.token).objects.filter(pk=world.token.pk).update(scopes=["read:apps"])
        else:
            ManagedDomain.objects.filter(pk=world.domain.pk).update(zone="replacement.test")

    aws_wire["before_reply"] = change
    reply = ask(client, world)
    assert reply.get("errors") and reply["data"]["astroliftManagedDomainDiagnostics"] is None
    assert len(aws_wire["requests"]) == 1 and not dns_wire["queries"]


@pytest.mark.parametrize(
    "hostname",
    [
        "foreign.test",
        "example.test.evil.test",
        "badexample.test",
        "https://example.test",
        "example..test",
        "example.test;id",
        "example.test\n",
        "127.0.0.1",
    ],
)
def test_unscoped_or_invalid_target_has_zero_network(world, client, dns_wire, aws_wire, hostname):
    reply = ask(client, world, PROBE, hostname=hostname, tool="HTTPS")
    assert reply.get("errors") and not dns_wire["queries"] and not aws_wire["requests"]


def test_foreign_domain_and_team_ceiling_have_zero_network(world, client, dns_wire, aws_wire):
    other = ScopeWorld("foreign-domain-" + uuid4().hex[:8])
    ManagedDomain.objects.filter(pk=world.domain.pk).update(organization=other.org)
    assert ask(client, world)["data"]["astroliftManagedDomainDiagnostics"] is None
    world.domain.organization = world.org
    world.domain.save()
    world.token.team = world.medops
    world.token.save()
    assert ask(client, world).get("errors")
    assert not dns_wire["queries"] and not aws_wire["requests"]


def test_shared_domain_requires_current_operator_admin_ceiling(world, client, dns_wire, aws_wire):
    world.domain.organization = None
    world.domain.save()
    assert ask(client, world).get("errors")
    world.user.is_superuser = True
    world.user.save()
    world.token.scopes = ["read:clusters"]
    world.token.save()
    assert ask(client, world).get("errors")
    assert not dns_wire["queries"] and not aws_wire["requests"]


def test_mixed_public_private_dns_refuses_https_transport(world, client, dns_wire, monkeypatch):
    dns_wire["answers"] = [
        ("example.test", 1, ipaddress.ip_address(value).packed) for value in ("8.8.4.4", "169.254.169.254")
    ]
    monkeypatch.setattr(service.network, "https_probe", lambda *_a, **_kw: pytest.fail("no transport"))
    reply = ask(client, world, PROBE, hostname="example.test", tool="HTTPS")
    assert reply["data"]["astroliftManagedDomainProbe"]["state"] == "ERROR"


def test_routes_are_own_live_suffix_scoped_and_recorded_not_native(world, client, dns_wire):
    dns_answers(dns_wire)
    AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster,
        name="production",
        url="https://app.example.test/?private-token=marker",
    )
    AppEnvironment.objects.create(
        registered_app=world.platform_app,
        tenant_cluster=world.cluster,
        name="production",
        url="https://otherexample.test/",
    )
    other = ScopeWorld("other-route-" + uuid4().hex[:8])
    AppEnvironment.objects.create(
        registered_app=other.medops_app,
        tenant_cluster=make_cluster(other, uuid4().hex[:8]),
        name="production",
        url="https://foreign.example.test/",
    )
    reply = ask(client, world)
    rows = reply["data"]["astroliftManagedDomainDiagnostics"]["routes"]
    assert len(rows) == 1 and rows[0]["appId"] == str(world.medops_app.guid)
    assert rows[0]["recordedUrl"] == "https://app.example.test/" and rows[0]["observedState"] == "UNKNOWN"


def test_actual_graphql_aliases_are_throttled_before_extra_dns(world, client, dns_wire):
    dns_wire["answers"] = []
    selection = " ".join(
        f'p{n}:astroliftManagedDomainProbe(domainId:"{world.domain.guid}",expectedVersion:{world.domain.version},hostname:"example.test",tool:LOOKUP){{state}}'
        for n in range(12)
    )
    reply = graphql_http(client, world.headers, "{" + selection + "}", {})
    assert len(dns_wire["queries"]) == 10
    assert len(reply["errors"]) == 2 and all(
        row["extensions"]["code"] == "RATE_LIMITED" for row in reply["errors"]
    )


def test_effective_default_names_older_own_zone_and_singular_exact_version(world, client, dns_wire):
    older = ManagedDomain.objects.create(
        organization=world.org, zone="old.test", dns_driver="route53", default_for="tenant_apps"
    )
    world.domain.default_for = "tenant_apps"
    world.domain.verification_state = "verified"
    world.domain.save()
    world.org.default_managed_domain = older
    world.org.save()
    dns_answers(dns_wire)
    reply = ask(client, world)
    row = next(
        row
        for row in reply["data"]["astroliftManagedDomainDiagnostics"]["checks"]
        if row["key"] == "effective_tenant_apps_domain"
    )
    assert row["state"] == "MISMATCH" and row["observed"] == ["old.test"]
    singular = graphql_http(
        client,
        world.headers,
        "query($id:GUID!){astroliftManagedDomain(domainId:$id){id version provisionClusterId}}",
        {"id": str(world.domain.guid)},
    )
    assert singular["data"]["astroliftManagedDomain"]["version"] == world.domain.version
    assert singular["data"]["astroliftManagedDomain"]["provisionClusterId"] == str(world.cluster.guid)


def test_domain_list_cluster_projection_is_one_query_and_refuses_foreign_rows(
    world, django_assert_num_queries
):
    other = ScopeWorld("foreign-cluster-" + uuid4().hex[:8])
    foreign = make_cluster(other, uuid4().hex[:8])
    domains = [world.domain]
    for index in range(25):
        domains.append(
            ManagedDomain.objects.create(
                organization=world.org,
                zone=f"zone{index}.test",
                dns_driver="route53",
                dns_config={"provision_cluster_id": str(world.cluster.guid) if index % 2 else foreign.pk},
            )
        )
    with django_assert_num_queries(1):
        clusters = service.provision_clusters(domains)
    assert clusters[world.domain.pk].guid == world.cluster.guid
    assert all(row.organization_id == world.org.pk for row in clusters.values())
    assert len(clusters) == 13


@pytest.mark.parametrize("withdraw", ["role", "policy"])
def test_fresh_current_authority_after_actual_dns_reply(world, client, dns_wire, monkeypatch, withdraw):
    original = service.network.dns_lookup
    dns_answers(dns_wire)

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if withdraw == "role":
            RoleBinding.objects.filter(user=world.user).update(
                expires_at=timezone.now() - dt.timedelta(seconds=1)
            )
        else:
            Policy.objects.create(
                organization=world.org,
                name="Withdraw",
                slug="withdraw",
                scope_level="ORG",
                effect="DENY",
                action_pattern=Permission.PROVIDER_PLUGIN_READ.value,
                conditions=[],
            )
        return result

    monkeypatch.setattr(service.network, "dns_lookup", observed)
    reply = ask(client, world, PROBE, hostname="example.test", tool="LOOKUP")
    assert reply.get("errors") and reply["data"]["astroliftManagedDomainProbe"] is None
    assert len(dns_wire["queries"]) == 1


@pytest.mark.parametrize("withdraw", ["revoked", "expired", "deleted", "foreign"])
def test_browser_sidecar_withdrawal_after_actual_provider_reply_is_not_revived(
    world, client, dns_wire, aws_wire, withdraw
):
    world.user.is_superuser = True
    world.user.save()
    client.force_login(world.user)
    world.headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    row = AstroliftSession.objects.create(user=world.user, session_key=client.session.session_key)

    def change():
        values = {
            "revoked": {"revoked_at": timezone.now()},
            "expired": {"expires_at": timezone.now() - dt.timedelta(seconds=1)},
            "deleted": {"deleted_at": timezone.now()},
            "foreign": {"user_id": make_user(uuid4().hex[:8]).pk},
        }[withdraw]
        AstroliftSession.all_objects.filter(pk=row.pk).update(**values)

    aws_wire["before_reply"] = change
    reply = ask(client, world)
    assert reply.get("errors") and reply["data"]["astroliftManagedDomainDiagnostics"] is None
    assert len(aws_wire["requests"]) == 1 and not dns_wire["queries"]
    row.refresh_from_db()
    assert (
        row.revoked_at
        if withdraw == "revoked"
        else row.expires_at
        if withdraw == "expired"
        else row.deleted_at
        if withdraw == "deleted"
        else row.user_id != world.user.pk
    )


def test_private_route53_client_ignores_process_endpoint_override(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:1/private-marker")
    monkeypatch.setenv("AWS_ENDPOINT_URL_ROUTE53", "http://127.0.0.1:2/private-marker")
    gates = []
    client = service._route53(lambda: gates.append(True))
    try:
        assert client.meta.endpoint_url == "https://route53.amazonaws.com"
        # Botocore consumes this routing option while constructing endpoint rules;
        # the final client Config does not retain it. The resolved native URL
        # above is the behavioral proof against both supplied overrides.
        assert client.meta.config.retries["total_max_attempts"] == 1
        assert client.meta.config.connect_timeout == client.meta.config.read_timeout == 3
        assert len(gates) >= 2
    finally:
        client.close()


def test_operator_unsupported_driver_remains_distinct_without_provider_transport(
    world, client, dns_wire, aws_wire
):
    world.user.is_superuser = True
    world.user.save(update_fields=["is_superuser"])
    world.domain.dns_driver = "unsupported_fixture"
    world.domain.save(update_fields=["dns_driver"])
    world.domain.refresh_from_db()
    dns_answers(dns_wire)
    reply = ask(client, world)
    assert not reply.get("errors"), reply.get("errors")
    zone = reply["data"]["astroliftManagedDomainDiagnostics"]["providerZone"]
    assert zone["state"] == "UNSUPPORTED" and zone["reason"] == "PROVIDER_INVENTORY_UNSUPPORTED"
    assert zone["zoneId"] is None and zone["records"] == [] and not aws_wire["requests"]


def test_empty_lookup_reports_no_data_without_provider_inventory(world, client, dns_wire, aws_wire):
    dns_wire["answers"] = []
    reply = ask(client, world, query=PROBE, hostname=world.domain.zone, tool="LOOKUP")
    assert not reply.get("errors"), reply.get("errors")
    data = reply["data"]["astroliftManagedDomainProbe"]
    assert data["state"] == "OK" and data["reason"] == "DNS_NO_DATA" and data["values"] == []
    assert not aws_wire["requests"]


@pytest.mark.parametrize("expected_configured", [True, False])
def test_ns_answer_is_separate_from_configured_delegation_and_cluster_probe(
    world, client, dns_wire, aws_wire, expected_configured
):
    if not expected_configured:
        world.domain.provision_nameservers = []
        world.domain.save(update_fields=["provision_nameservers"])
        world.domain.refresh_from_db()
    dns_answers(dns_wire, match=True)
    before = world.domain.version
    reply = ask(client, world)
    assert not reply.get("errors"), reply.get("errors")
    data = reply["data"]["astroliftManagedDomainDiagnostics"]
    lookup = next(row for row in data["checks"] if row["key"] == "lookup")
    assert lookup["state"] == "OK" and lookup["reason"] == "DNS_ANSWER"
    assert lookup["expected"] == [] and lookup["observed"] == ["ns.provider.test"]
    delegation = [row for row in data["checks"] if row["key"] == "delegation"]
    assert len(delegation) == 2
    for row in delegation:
        assert row["state"] == ("OK" if expected_configured else "UNKNOWN")
        assert row["reason"] == ("PUBLIC_DELEGATION_MATCH" if expected_configured else "DNS_ANSWER")
        assert row["expected"] == (["ns.provider.test"] if expected_configured else [])
    internal = next(row for row in data["checks"] if row["key"] == "internal_dns")
    assert internal["state"] == "UNSUPPORTED"
    assert internal["reason"] == "INTERNAL_DNS_PROBE_NOT_CONFIGURED"
    assert internal["expected"] == [] and internal["observed"] == []
    assert data["routes"] == []
    assert dns_wire["queries"] == [("example.test", 2)] * 3
    assert not aws_wire["requests"]
    world.domain.refresh_from_db()
    assert world.domain.version == before and world.domain.verification_state == "pending"
