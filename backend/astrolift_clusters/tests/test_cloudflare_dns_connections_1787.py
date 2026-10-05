"""Actual authenticated GraphQL and fixed-origin TLS protocol controls, no cloud."""

import datetime
import http.client
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.utils import timezone

from astrolift_clusters import cloudflare_dns_transport as transport
from astrolift_clusters.models import DnsProviderConnection, DnsProviderOAuthAttempt, ManagedDomain
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)
ZONE_ID = "a" * 32
TOKEN = "Cf_Private_Marker_1787_Only_Fixture"
SECRET = "Cf_OAuth_Private_Marker_1787"
ZONE = {
    "id": ZONE_ID,
    "name": "example.test",
    "account": {"id": "b" * 32},
    "status": "active",
    "name_servers": ["aa.ns.cloudflare.com", "bb.ns.cloudflare.com"],
}


def envelope(rows):
    return {
        "success": True,
        "errors": [],
        "result": rows,
        "result_info": {
            "page": 1,
            "per_page": 50,
            "count": len(rows),
            "total_count": len(rows),
            "total_pages": 1,
        },
    }


@pytest.fixture
def world():
    w = ScopeWorld("dns1787")
    w.user = make_user("dns1787")
    w.user.is_superuser = True
    w.user.save()
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        w.user,
        permissions=[Permission.PROVIDER_PLUGIN_READ, Permission.PROVIDER_PLUGIN_CONFIGURE],
        kind="ORG",
        scope_id=w.org.pk,
        slug="dns-owner1787",
    )
    minted = mint_token()
    w.bearer = minted.plaintext
    w.token = ApiToken.objects.create(
        user=w.user, organization=w.org, name="DNS fixture", token_hash=minted.token_hash, scopes=["admin"]
    )
    return w


@pytest.fixture
def wire(tmp_path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api.cloudflare.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("api.cloudflare.com"), x509.DNSName("dash.cloudflare.com")]
            ),
            False,
        )
        .sign(private, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    w = SimpleNamespace(calls=[], hook=None, status=200, override=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            w.calls.append((self.command, self.path, dict(self.headers), body))
            if w.hook:
                w.hook(self.path)
            path = urlsplit(self.path).path
            payload = w.override(self.path) if callable(w.override) else w.override
            if payload is None:
                if path == "/oauth2/revoke":
                    payload = b""
                elif path == "/oauth2/token":
                    payload = {
                        "access_token": TOKEN,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "scope": "zone.read dns.read",
                    }
                elif path.endswith("/dns_records"):
                    payload = envelope(
                        [
                            {
                                "id": "c" * 32,
                                "name": "_verification.example.test",
                                "type": "TXT",
                                "content": "bounded-record-value",
                                "ttl": 300,
                                "proxied": False,
                            }
                        ]
                    )
                elif path == "/client/v4/zones/" + ZONE_ID:
                    payload = {"success": True, "result": ZONE, "errors": []}
                else:
                    payload = envelope([ZONE])
            content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(w.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert_path, key_path)
    server.socket = server_ssl.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(cert_path))

    class Connection(http.client.HTTPSConnection):
        def connect(self):
            import socket

            sock = socket.create_connection(("127.0.0.1", server.server_port), self.timeout)
            self.sock = context.wrap_socket(sock, server_hostname=self.host)

    class Opener:
        def open(self, request, timeout):
            import urllib.error

            parsed = urlsplit(request.full_url)
            assert parsed.scheme == "https" and parsed.hostname in (
                "api.cloudflare.com",
                "dash.cloudflare.com",
            )
            c = Connection(parsed.hostname, timeout=timeout)
            c.request(
                request.get_method(),
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                body=request.data,
                headers=dict(request.header_items()),
            )
            response = c.getresponse()
            if response.status != 200:
                raise urllib.error.HTTPError(
                    request.full_url, response.status, TOKEN + SECRET, response.headers, response
                )
            return response

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *args: Opener())
    try:
        yield w
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def graphql(client, w, query, variables=None, *, browser=False):
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid), "HTTP_X_PLATFORM": "web"}
    if not browser:
        headers["HTTP_AUTHORIZATION"] = "Bearer " + w.bearer
    response = client.post(
        "/app/gql/config/",
        content_type="application/json",
        data=json.dumps({"query": query, "variables": variables or {}}),
        **headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()


CONNECT = "mutation($i:ConnectCloudflareDnsTokenInput!){connectCloudflareDnsToken(input:$i){ok errors{code message} data{id version name authMethod state dnsWritesSupported}}}"
ZONES = "query($i:CloudflareDnsConnectionInput!){cloudflareDnsZones(input:$i){complete reason items{id name accountId nameServers}}}"
RECORDS = "query($i:CloudflareDnsRecordsInput!){cloudflareDnsRecords(input:$i){complete reason items{id name type content ttl proxied priority}}}"


def connect(client, w):
    result = graphql(
        client, w, CONNECT, {"i": {"organizationId": str(w.org.guid), "name": "DNS", "token": TOKEN}}
    )
    assert not result.get("errors"), result
    reply = result["data"]["connectCloudflareDnsToken"]
    assert reply["ok"], reply
    return reply["data"]


def test_actual_http_encrypted_connection_inventory_attachment_disconnect(world, wire, client, caplog):
    row = connect(client, world)
    stored = DnsProviderConnection.objects.get(guid=row["id"])
    assert TOKEN.encode() not in bytes(stored.secret_ciphertext)
    assert row["dnsWritesSupported"] is False
    zones = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert zones["data"]["cloudflareDnsZones"]["complete"] is True
    records = graphql(
        client,
        world,
        RECORDS,
        {
            "i": {
                "connectionId": row["id"],
                "expectedVersion": row["version"],
                "zoneId": ZONE_ID,
                "zoneName": "example.test",
            }
        },
    )
    assert records["data"]["cloudflareDnsRecords"]["items"][0]["content"] == "bounded-record-value"
    domain = ManagedDomain.objects.create(
        organization=world.org,
        zone="example.test",
        dns_driver="legacy",
        dns_config={"preserve": True},
        verification_state="pending",
        verification_token="proof",
    )
    query = "mutation($i:AttachCloudflareDnsZoneInput!){attachCloudflareDnsZone(input:$i){ok errors{code} data{domainId domainVersion connectionId connectionVersion dnsWritesSupported zone{id name}}}}"
    attached = graphql(
        client,
        world,
        query,
        {
            "i": {
                "domainId": str(domain.guid),
                "expectedDomainVersion": domain.version,
                "connectionId": row["id"],
                "expectedConnectionVersion": row["version"],
                "zoneId": ZONE_ID,
                "zoneName": domain.zone,
            }
        },
    )
    assert attached["data"]["attachCloudflareDnsZone"]["ok"] is True, attached
    domain.refresh_from_db()
    assert (
        domain.dns_config == {"preserve": True}
        and domain.verification_state == "pending"
        and domain.verification_token == "proof"
        and domain.dns_driver == "legacy"
    )
    assert domain.dns_provider_zone_id == ZONE_ID and domain.dns_provider_connection_id == stored.pk
    result = graphql(
        client,
        world,
        "mutation($i:CloudflareDnsConnectionInput!){disconnectDnsProviderConnection(input:$i){ok data{state version}}}",
        {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}},
    )
    assert result["data"]["disconnectDnsProviderConnection"]["data"]["state"] == "DISCONNECTED"
    stored.refresh_from_db()
    assert not stored.secret_ciphertext
    from core.schema.audit import MutationAuditLog as AuditLog

    diagnostic = (
        caplog.text
        + str(list(AuditLog.objects.values()))
        + json.dumps([row, zones, records, attached, result])
    )
    assert TOKEN not in diagnostic and SECRET not in diagnostic
    assert all(call[0] == "GET" for call in wire.calls)


@pytest.mark.parametrize("ceiling", ["ordinary", "staff", "readonly", "team", "foreign"])
def test_denied_before_native_or_decrypt(world, wire, client, ceiling):
    if ceiling in ("ordinary", "staff"):
        world.user.is_superuser = False
        world.user.is_staff = ceiling == "staff"
        world.user.save()
    elif ceiling == "readonly":
        world.token.scopes = ["read:clusters"]
        world.token.save()
    elif ceiling == "team":
        world.token.team_id = world.medops.pk
        world.token.save()
    else:
        other = ScopeWorld("foreign1787")
        world.org = other.org
        response = client.post(
            "/app/gql/config/",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + world.bearer,
            HTTP_X_ASTROLIFT_ORGANIZATION=str(other.org.guid),
            data=json.dumps(
                {
                    "query": CONNECT,
                    "variables": {
                        "i": {"organizationId": str(other.org.guid), "name": "DNS", "token": TOKEN}
                    },
                }
            ),
        )
        assert response.status_code == 403 and not wire.calls and not DnsProviderConnection.objects.exists()
        return
    result = graphql(
        client, world, CONNECT, {"i": {"organizationId": str(world.org.guid), "name": "DNS", "token": TOKEN}}
    )
    assert result.get("errors") or result["data"]["connectCloudflareDnsToken"]["ok"] is False
    assert not wire.calls and not DnsProviderConnection.objects.exists()


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "UNAUTHENTICATED"),
        (403, "FORBIDDEN"),
        (429, "RATE_LIMITED"),
        (302, "UPSTREAM_UNAVAILABLE"),
        (500, "UPSTREAM_UNAVAILABLE"),
    ],
)
def test_native_errors_safe_distinct(world, wire, client, caplog, status, reason):
    wire.status = status
    wire.override = (TOKEN + SECRET).encode()
    result = graphql(
        client, world, CONNECT, {"i": {"organizationId": str(world.org.guid), "name": "DNS", "token": TOKEN}}
    )
    assert result["data"]["connectCloudflareDnsToken"]["errors"][0]["code"] == reason
    assert TOKEN not in caplog.text + json.dumps(result) and SECRET not in caplog.text + json.dumps(result)
    assert len(wire.calls) == 1 and not DnsProviderConnection.objects.exists()


@pytest.mark.parametrize("fault", ["operator", "token", "membership"])
def test_held_native_reply_withdrawal_no_connection(world, wire, client, fault):
    def withdraw(path):
        from django.db import connection

        try:
            if fault == "operator":
                type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
            elif fault == "token":
                ApiToken.objects.filter(pk=world.token.pk).update(scopes=["read:clusters"])
            else:
                Member.objects.filter(user=world.user).update(is_active=False)
        finally:
            connection.close()

    wire.hook = withdraw
    result = graphql(
        client, world, CONNECT, {"i": {"organizationId": str(world.org.guid), "name": "DNS", "token": TOKEN}}
    )
    assert result["data"]["connectCloudflareDnsToken"]["ok"] is False
    assert len(wire.calls) == 1 and not DnsProviderConnection.objects.exists()


def test_oauth_unconfigured_support_keeps_token_path(world, client):
    result = graphql(
        client,
        world,
        "{dnsProviderConnectionSupport{allowed apiTokenSupported oauthConfigured oauthSetupReason dnsWritesSupported}}",
    )
    assert result["data"]["dnsProviderConnectionSupport"] == {
        "allowed": True,
        "apiTokenSupported": True,
        "oauthConfigured": False,
        "oauthSetupReason": "OAUTH_CLIENT_NOT_CONFIGURED",
        "dnsWritesSupported": False,
    }


def test_oauth_actual_browser_pkce_single_use_and_secret_omission(world, wire, client, settings, caplog):
    settings.APP_BASE_URL = "https://astro.test"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "client-fixture"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET = SECRET
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES = ["zone.read", "dns.read"]
    client.force_login(world.user)
    result = graphql(
        client,
        world,
        "mutation($i:BeginCloudflareDnsOAuthInput!){beginCloudflareDnsOAuth(input:$i){ok errors{code} data{authorizationUrl}}}",
        {"i": {"organizationId": str(world.org.guid), "name": "OAuth DNS"}},
        browser=True,
    )
    assert not result.get("errors"), result
    reply = result["data"]["beginCloudflareDnsOAuth"]
    assert reply["ok"], reply
    params = parse_qs(urlsplit(reply["data"]["authorizationUrl"]).query)
    assert params["code_challenge_method"] == ["S256"]
    attempt = DnsProviderOAuthAttempt.objects.get()
    assert params["state"][0] not in attempt.state_sha256
    code = "Private_Code_Marker_1787"
    callback = client.get(
        "/api/clusters/dns/cloudflare/callback/",
        {"state": params["state"][0], "code": code},
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
    )
    assert callback.url == "/domains?connectionOutcome=saved"
    assert DnsProviderConnection.objects.get().auth_method == "OAUTH"
    attempt.refresh_from_db()
    assert attempt.consumed_at and not attempt.verifier_ciphertext
    requests = len(wire.calls)
    repeat = client.get(
        "/api/clusters/dns/cloudflare/callback/",
        {"state": params["state"][0], "code": code},
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
    )
    assert repeat.url.endswith("unconfirmed") and len(wire.calls) == requests
    from core.schema.audit import MutationAuditLog as AuditLog

    assert all(
        x not in caplog.text + str(list(AuditLog.objects.values())) + callback.content.decode()
        for x in (TOKEN, SECRET, code)
    )


@pytest.mark.parametrize("fault", ["overflow", "missing", "duplicate", "size", "invalid_json"])
def test_incomplete_inventory_never_claims_empty_complete(world, wire, client, fault):
    if fault == "overflow":
        wire.override = envelope([ZONE])
        wire.override["result_info"].update(total_count=401, total_pages=9)
    elif fault == "missing":
        wire.override = envelope([ZONE])
        wire.override["result_info"].pop("total_count")
    elif fault == "duplicate":
        wire.override = envelope([ZONE, ZONE])
    elif fault == "size":
        wire.override = b" " * 524289
    else:
        wire.override = b"not-json-" + TOKEN.encode()
    reply = graphql(
        client, world, CONNECT, {"i": {"organizationId": str(world.org.guid), "name": "DNS", "token": TOKEN}}
    )
    assert not reply["data"]["connectCloudflareDnsToken"]["ok"]
    assert not DnsProviderConnection.objects.exists() and len(wire.calls) == 1


@pytest.mark.parametrize(
    "fault", ["wrong_zone", "foreign_record", "stale_version", "retired", "foreign_connection"]
)
def test_inventory_source_identity_refusals(world, wire, client, fault):
    row = connect(client, world)
    wire.calls.clear()
    zone_id, zone_name, version = ZONE_ID, "example.test", row["version"]
    if fault == "wrong_zone":
        zone_name = "example.test.evil.test"
    elif fault == "foreign_record":
        wire.override = (
            lambda path: envelope(
                [{"id": "c" * 32, "name": "notexample.test", "type": "TXT", "content": TOKEN, "ttl": 300}]
            )
            if urlsplit(path).path.endswith("/dns_records")
            else {"success": True, "errors": [], "result": ZONE}
        )
    elif fault == "stale_version":
        version += 1
    elif fault == "retired":
        DnsProviderConnection.objects.filter(guid=row["id"]).update(state="DISCONNECTED")
    else:
        foreign = ScopeWorld("foreign-connection1787")
        DnsProviderConnection.objects.filter(guid=row["id"]).update(organization=foreign.org)
    result = graphql(
        client,
        world,
        RECORDS,
        {
            "i": {
                "connectionId": row["id"],
                "expectedVersion": version,
                "zoneId": zone_id,
                "zoneName": zone_name,
            }
        },
    )
    inventory = result["data"]["cloudflareDnsRecords"]
    assert inventory["complete"] is False and inventory["items"] == []
    assert len(wire.calls) <= (2 if fault == "foreign_record" else 1)


def test_readonly_registration_no_workflow_no_routing_and_no_native_delete(world, wire, client, monkeypatch):
    from astrolift_clusters.models import managed_domain_for_zone, resolve_managed_domain
    from core.tenancy import TenantContext, tenant_context

    monkeypatch.setattr(
        "astrolift_clusters.schema.mutations.start_workflow",
        lambda *a, **kw: pytest.fail("read-only DNS must not provision"),
    )
    row = connect(client, world)
    query = "mutation($i:RegisterCloudflareDnsZoneInput!){registerCloudflareDnsZone(input:$i){ok errors{code} data{domainId domainVersion verificationState verificationRecordName verificationRecordValue dnsWritesSupported zone{id name}}}}"
    result = graphql(
        client,
        world,
        query,
        {
            "i": {
                "connectionId": row["id"],
                "expectedConnectionVersion": row["version"],
                "zoneId": ZONE_ID,
                "zoneName": "example.test",
            }
        },
    )
    payload = result["data"]["registerCloudflareDnsZone"]
    assert payload["ok"], payload
    domain = ManagedDomain.objects.get(guid=payload["data"]["domainId"])
    assert (
        domain.default_for == "none"
        and domain.verification_state == "pending"
        and domain.provision_state == ""
    )
    monkeypatch.setattr("_sdk._dns_probe.lookup_txt", lambda _: [domain.verification_token])
    monkeypatch.setattr(
        "astrolift_clusters.schema.mutations._first_dns_cluster",
        lambda: pytest.fail("read-only DNS must not choose an unrelated writer"),
    )
    result = graphql(
        client, world, 'mutation{verifyManagedDomain(input:{zone:"example.test"}){ok data{verified message}}}'
    )
    assert result["data"]["verifyManagedDomain"]["data"]["verified"] is True
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        assert managed_domain_for_zone("example.test", world.org.pk) is None
        world.org.default_managed_domain = domain
        assert resolve_managed_domain(world.org) is None
    result = graphql(
        client,
        world,
        'mutation($id:GUID!){updateManagedDomain(input:{id:$id,defaultFor:"tenant_apps"}){ok errors{code}}}',
        {"id": str(domain.guid)},
    )
    assert not result["data"]["updateManagedDomain"]["ok"]
    result = graphql(
        client,
        world,
        "mutation($id:GUID!){softDeleteManagedDomain(input:{id:$id}){ok}}",
        {"id": str(domain.guid)},
    )
    assert result["data"]["softDeleteManagedDomain"]["ok"] is True
    assert DnsProviderConnection.objects.filter(guid=row["id"]).exists()


@pytest.mark.parametrize("fault", ["expiry", "other_session", "org", "withdrawn", "client_changed"])
def test_oauth_state_refuses_before_exchange(world, wire, client, settings, fault):
    from django.test import Client

    settings.APP_BASE_URL = "https://astro.test"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "client-fixture"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET = SECRET
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES = ["zone.read", "dns.read"]
    client.force_login(world.user)
    result = graphql(
        client,
        world,
        "mutation($i:BeginCloudflareDnsOAuthInput!){beginCloudflareDnsOAuth(input:$i){ok data{authorizationUrl}}}",
        {"i": {"organizationId": str(world.org.guid), "name": "OAuth DNS"}},
        browser=True,
    )
    payload = result["data"]["beginCloudflareDnsOAuth"]
    assert payload["ok"], result
    state = parse_qs(urlsplit(payload["data"]["authorizationUrl"]).query)["state"][0]
    if fault == "expiry":
        DnsProviderOAuthAttempt.objects.update(expires_at=timezone.now())
    elif fault == "other_session":
        client = Client()
        client.force_login(world.user)
    elif fault == "org":
        world.org = ScopeWorld("oauthforeign1787").org
    elif fault == "withdrawn":
        world.user.is_superuser = False
        world.user.save()
    else:
        settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET += "changed"
    response = client.get(
        "/api/clusters/dns/cloudflare/callback/",
        {"state": state, "code": "Private_Code_Marker_1787"},
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
    )
    assert (
        response.url.endswith("unconfirmed") and not wire.calls and not DnsProviderConnection.objects.exists()
    )


def test_transport_monotonic_deadline_before_next_send(wire):
    clock = [0.0]
    client = transport.CloudflareReadClient(TOKEN, lambda: None, clock=lambda: clock[0])
    client.zones()
    clock[0] = 31
    with pytest.raises(transport.DnsConnectionError, match="DEADLINE_EXCEEDED"):
        client.zones()
    assert len(wire.calls) == 1


def oauth_connection(client, world, settings):
    settings.APP_BASE_URL = "https://astro.test"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "client-fixture"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET = SECRET
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES = ["zone.read", "dns.read"]
    client.force_login(world.user)
    result = graphql(
        client,
        world,
        "mutation($i:BeginCloudflareDnsOAuthInput!){beginCloudflareDnsOAuth(input:$i){ok data{authorizationUrl}}}",
        {"i": {"organizationId": str(world.org.guid), "name": "OAuth DNS"}},
        browser=True,
    )
    state = parse_qs(urlsplit(result["data"]["beginCloudflareDnsOAuth"]["data"]["authorizationUrl"]).query)[
        "state"
    ][0]
    result = client.get(
        "/api/clusters/dns/cloudflare/callback/",
        {"state": state, "code": "Fixture_Code_1787"},
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
    )
    assert result.url.endswith("saved")
    return DnsProviderConnection.objects.get(auth_method="OAUTH")


@pytest.mark.parametrize("fault", ["none", "unknown", "changed_client", "withdrawn_reply"])
def test_oauth_disconnect_commits_local_first_and_never_resends(world, wire, client, settings, caplog, fault):
    row = oauth_connection(client, world, settings)
    before = len(wire.calls)
    if fault == "unknown":
        wire.status = 500
    elif fault == "changed_client":
        settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "other-client"
    elif fault == "withdrawn_reply":

        def withdrawn(path):
            if path == "/oauth2/revoke":
                world.token.deleted_at = timezone.now()
                world.token.save()

        wire.hook = withdrawn
    query = "mutation($i:CloudflareDnsConnectionInput!){disconnectDnsProviderConnection(input:$i){ok errors{code} data{id version state revocationState}}}"
    result = graphql(
        client, world, query, {"i": {"connectionId": str(row.guid), "expectedVersion": row.version}}
    )
    reply = result["data"]["disconnectDnsProviderConnection"]
    assert reply["ok"] and reply["data"]["state"] == "DISCONNECTED", result
    assert reply["data"]["revocationState"] == ("OAUTH_REVOKED" if fault == "none" else "OAUTH_UNCONFIRMED")
    row.refresh_from_db()
    assert not row.secret_ciphertext and row.state == "DISCONNECTED"
    assert len(wire.calls) == before + (0 if fault == "changed_client" else 1)
    assert TOKEN not in caplog.text and SECRET not in caplog.text
    if fault != "withdrawn_reply":
        repeat = graphql(
            client, world, query, {"i": {"connectionId": str(row.guid), "expectedVersion": row.version}}
        )
        assert not repeat["data"]["disconnectDnsProviderConnection"]["ok"]
        assert len(wire.calls) == before + (0 if fault == "changed_client" else 1)


def test_api_token_disconnect_is_local_only(world, wire, client):
    row = connect(client, world)
    before = len(wire.calls)
    result = graphql(
        client,
        world,
        "mutation($i:CloudflareDnsConnectionInput!){disconnectDnsProviderConnection(input:$i){ok data{revocationState}}}",
        {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}},
    )
    assert result["data"]["disconnectDnsProviderConnection"]["data"]["revocationState"] == "LOCAL_ONLY"
    assert len(wire.calls) == before


def test_binding_reload_retest_requires_explicit_fresh_rebind(world, wire, client):
    row = connect(client, world)
    result = graphql(
        client,
        world,
        "mutation($i:RegisterCloudflareDnsZoneInput!){registerCloudflareDnsZone(input:$i){ok data{domainId domainVersion}}}",
        {
            "i": {
                "connectionId": row["id"],
                "expectedConnectionVersion": row["version"],
                "zoneId": ZONE_ID,
                "zoneName": "example.test",
            }
        },
    )
    domain_id = result["data"]["registerCloudflareDnsZone"]["data"]["domainId"]
    query = "query($id:GUID!){dnsProviderDomainBinding(domainId:$id){domainId domainVersion state canVerify connectionId connectionVersion currentConnectionVersion zoneId zoneName dnsWritesSupported}}"
    binding = graphql(client, world, query, {"id": domain_id})["data"]["dnsProviderDomainBinding"]
    assert binding["state"] == "CURRENT" and binding["canVerify"]
    result = graphql(
        client,
        world,
        "mutation($i:CloudflareDnsConnectionInput!){retestDnsProviderConnection(input:$i){ok data{version}}}",
        {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}},
    )
    version = result["data"]["retestDnsProviderConnection"]["data"]["version"]
    binding = graphql(client, world, query, {"id": domain_id})["data"]["dnsProviderDomainBinding"]
    assert binding["state"] == "CONNECTION_CHANGED" and binding["connectionVersion"] == row["version"]
    assert binding["currentConnectionVersion"] == version
    result = graphql(
        client,
        world,
        "mutation($i:AttachCloudflareDnsZoneInput!){attachCloudflareDnsZone(input:$i){ok data{domainVersion connectionVersion}}}",
        {
            "i": {
                "domainId": domain_id,
                "expectedDomainVersion": binding["domainVersion"],
                "connectionId": row["id"],
                "expectedConnectionVersion": version,
                "zoneId": ZONE_ID,
                "zoneName": "example.test",
            }
        },
    )
    assert result["data"]["attachCloudflareDnsZone"]["ok"]
    assert (
        graphql(client, world, query, {"id": domain_id})["data"]["dnsProviderDomainBinding"]["state"]
        == "CURRENT"
    )


def test_readonly_txt_verification_withdrawal_after_dns_reply_cannot_commit(world, wire, client, monkeypatch):
    row = connect(client, world)
    domain = ManagedDomain.objects.create(
        organization=world.org,
        zone="example.test",
        dns_driver="cloudflare_read_only",
        verification_state="pending",
        verification_token="proof",
        dns_provider_connection_id=DnsProviderConnection.objects.get(guid=row["id"]).pk,
        dns_provider_zone_id=ZONE_ID,
        dns_provider_connection_version=row["version"],
    )

    def reply(_):
        world.user.is_superuser = False
        world.user.save()
        return ["proof"]

    monkeypatch.setattr("_sdk._dns_probe.lookup_txt", reply)
    result = graphql(
        client, world, 'mutation{verifyManagedDomain(input:{zone:"example.test"}){ok errors{code}}}'
    )
    assert not result["data"]["verifyManagedDomain"]["ok"]
    domain.refresh_from_db()
    assert domain.verification_state == "pending"


def test_actual_complete_two_page_inventory_and_changed_pagination_refusal(world, wire, client):
    def pages(path):
        page = int(parse_qs(urlsplit(path).query)["page"][0])
        rows = [{**ZONE, "id": f"{n:032x}"} for n in (range(1, 51) if page == 1 else range(51, 52))]
        payload = envelope(rows)
        payload["result_info"].update(page=page, total_count=51, total_pages=2)
        return payload

    wire.override = pages
    row = connect(client, world)
    result = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert (
        result["data"]["cloudflareDnsZones"]["complete"]
        and len(result["data"]["cloudflareDnsZones"]["items"]) == 51
    )

    def changed(path):
        result = pages(path)
        if result["result_info"]["page"] == 2:
            result["result_info"]["total_count"] = 52
        return result

    wire.override = changed
    result = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert result["data"]["cloudflareDnsZones"] == {
        "complete": False,
        "reason": "INVENTORY_CHANGED",
        "items": [],
    }


@pytest.mark.parametrize("fault", ["decrypt", "persist_revoke"])
def test_disconnect_local_receipt_survives_remote_recording_failure(
    world, wire, client, settings, monkeypatch, fault
):
    row = oauth_connection(client, world, settings)
    original_version = row.version
    if fault == "decrypt":
        DnsProviderConnection.objects.filter(pk=row.pk).update(secret_backend_kind="missing-fixture-key")
    else:
        save = DnsProviderConnection.save

        def failing_save(self, *args, **kwargs):
            if self.revocation_state == "OAUTH_REVOKED":
                raise ValueError(TOKEN + SECRET)
            return save(self, *args, **kwargs)

        monkeypatch.setattr(DnsProviderConnection, "save", failing_save)
    before = len(wire.calls)
    result = graphql(
        client,
        world,
        "mutation($i:CloudflareDnsConnectionInput!){disconnectDnsProviderConnection(input:$i){ok data{version state revocationState}}}",
        {"i": {"connectionId": str(row.guid), "expectedVersion": original_version}},
    )
    reply = result["data"]["disconnectDnsProviderConnection"]
    row.refresh_from_db()
    assert reply["ok"] and reply["data"] == {
        "version": row.version,
        "state": "DISCONNECTED",
        "revocationState": "OAUTH_UNCONFIRMED",
    }
    assert not row.secret_ciphertext and len(wire.calls) == before + (fault == "persist_revoke")


def test_real_migration_retained_secret_history_refuses_then_empty_roundtrip(world):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from core.secrets import encrypt_at_rest

    executor = MigrationExecutor(connection)
    original_targets = executor.loader.graph.leaf_nodes()
    original_applied = set(executor.loader.applied_migrations)
    secret = encrypt_at_rest(TOKEN.encode())
    row = DnsProviderConnection.objects.create(
        organization=world.org,
        name="retired fixture",
        auth_method="API_TOKEN",
        state="DISCONNECTED",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        deleted_at=timezone.now(),
    )
    try:
        with pytest.raises(RuntimeError, match="Retained DNS connection"):
            executor.migrate([("astrolift_clusters", "0022_reviewed_log_collector")])
        assert set(MigrationExecutor(connection).loader.applied_migrations) == original_applied
        assert bytes(DnsProviderConnection.all_objects.get(pk=row.pk).secret_ciphertext) == secret.backend_ref
        DnsProviderConnection.all_objects.filter(pk=row.pk).delete()
        executor = MigrationExecutor(connection)
        executor.migrate([("astrolift_clusters", "0022_reviewed_log_collector")])
        assert "astrolift_clusters_dnsproviderconnection" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(original_targets)
        assert set(MigrationExecutor(connection).loader.applied_migrations) == original_applied
        assert DnsProviderConnection.objects.count() == 0
        assert DnsProviderOAuthAttempt.objects.count() == 0
    finally:
        MigrationExecutor(connection).migrate(original_targets)


def test_actual_http_otel_spans_audit_and_formatted_logs_omit_credentials(
    world, wire, client, settings, caplog, monkeypatch
):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from core.schema.audit import MutationAuditLog

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("dns-1787-proof")
    # Attach to the actual installed Django SDK provider, preserving its other
    # processors; callback URL redaction must affect the real HTTP child spans.
    with tracer.start_as_current_span("actual-http-controls"):
        token_row = connect(client, world)
        oauth_row = oauth_connection(client, world, settings)
        wire.status = 403
        result = graphql(
            client,
            world,
            ZONES,
            {"i": {"connectionId": token_row["id"], "expectedVersion": token_row["version"]}},
        )
        assert result["data"]["cloudflareDnsZones"]["reason"] == "FORBIDDEN"
        result = graphql(
            client,
            world,
            "mutation($i:CloudflareDnsConnectionInput!){disconnectDnsProviderConnection(input:$i){ok data{revocationState}}}",
            {"i": {"connectionId": str(oauth_row.guid), "expectedVersion": oauth_row.version}},
        )
        assert (
            result["data"]["disconnectDnsProviderConnection"]["data"]["revocationState"]
            == "OAUTH_UNCONFIRMED"
        )
    evidence = repr(
        [(dict(span.attributes), span.events, span.status) for span in exporter.get_finished_spans()]
    )
    evidence += repr([record.__dict__ for record in caplog.records]) + caplog.text
    evidence += repr(list(MutationAuditLog.objects.values()))
    for marker in (TOKEN, SECRET, "Fixture_Code_1787"):
        assert marker not in evidence
    assert any(
        span.attributes.get("http.target", "").startswith("/api/clusters/dns/cloudflare/callback/")
        or span.attributes.get("http.url", "").endswith("/api/clusters/dns/cloudflare/callback/")
        for span in exporter.get_finished_spans()
    )
    processor.shutdown()


@pytest.mark.parametrize("fault", ["session", "password", "client_after_exchange"])
def test_oauth_post_response_original_session_and_client_freshness(world, wire, client, settings, fault):
    from django.contrib.sessions.models import Session

    settings.APP_BASE_URL = "https://astro.test"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "client-fixture"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET = SECRET
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES = ["zone.read", "dns.read"]
    client.force_login(world.user)
    result = graphql(
        client,
        world,
        "mutation($i:BeginCloudflareDnsOAuthInput!){beginCloudflareDnsOAuth(input:$i){ok data{authorizationUrl}}}",
        {"i": {"organizationId": str(world.org.guid), "name": "OAuth DNS"}},
        browser=True,
    )
    state = parse_qs(urlsplit(result["data"]["beginCloudflareDnsOAuth"]["data"]["authorizationUrl"]).query)[
        "state"
    ][0]
    key = client.session.session_key

    def withdrawal(path):
        if fault == "client_after_exchange" and path.startswith("/client/v4/zones"):
            settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET += "changed"
        elif path == "/oauth2/token" and fault == "session":
            Session.objects.filter(session_key=key).delete()
        elif path == "/oauth2/token" and fault == "password":
            world.user.set_unusable_password()
            world.user.save()

    wire.hook = withdrawal
    reply = client.get(
        "/api/clusters/dns/cloudflare/callback/",
        {"state": state, "code": "Fixture_Code_1787"},
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
    )
    assert reply.url.endswith("unconfirmed") and not DnsProviderConnection.objects.exists()
    assert len(wire.calls) == (2 if fault == "client_after_exchange" else 1)
    assert DnsProviderOAuthAttempt.objects.get().consumed_at is not None


def test_generic_registration_cannot_forge_read_only_driver(world, client):
    result = graphql(
        client,
        world,
        'mutation{createManagedDomain(input:{zone:"example.test",dnsDriver:"cloudflare_read_only",defaultFor:"none",organizationScoped:true}){ok errors{code}}}',
    )
    assert not result["data"]["createManagedDomain"]["ok"]
    assert not ManagedDomain.objects.exists()


def test_existing_connection_backend_retarget_after_native_reply_refuses(world, wire, client):
    row = connect(client, world)

    def change(_):
        DnsProviderConnection.objects.filter(guid=row["id"]).update(
            secret_backend_kind="foreign-fixture-backend"
        )

    wire.hook = change
    result = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert result["data"]["cloudflareDnsZones"] == {
        "complete": False,
        "reason": "CONNECTION_CHANGED",
        "items": [],
    }


@pytest.mark.parametrize(
    "fault", ["none", "other_session", "saved_foreign_org", "invalid_header", "expired_session"]
)
def test_actual_headerless_oauth_redirect_uses_original_bound_multi_org_session(
    world, wire, client, settings, fault
):
    other = ScopeWorld("dns-other-multi1787").org
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=other.pk)
    settings.APP_BASE_URL = "https://astro.test"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID = "client-fixture"
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET = SECRET
    settings.ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES = ["zone.read", "dns.read"]
    client.force_login(world.user)
    result = graphql(
        client,
        world,
        "mutation($i:BeginCloudflareDnsOAuthInput!){beginCloudflareDnsOAuth(input:$i){ok data{authorizationUrl}}}",
        {"i": {"organizationId": str(world.org.guid), "name": "OAuth DNS"}},
        browser=True,
    )
    state = parse_qs(urlsplit(result["data"]["beginCloudflareDnsOAuth"]["data"]["authorizationUrl"]).query)[
        "state"
    ][0]
    headers = {}
    if fault == "other_session":
        from django.test import Client

        client = Client()
        client.force_login(world.user)
    elif fault == "saved_foreign_org":
        session = client.session
        session["organization_id"] = other.pk
        session.save()
    elif fault == "invalid_header":
        from uuid import uuid4

        headers["HTTP_X_ASTROLIFT_ORGANIZATION"] = str(uuid4())
    elif fault == "expired_session":
        from django.contrib.sessions.models import Session

        Session.objects.filter(session_key=client.session.session_key).update(expire_date=timezone.now())
    # A real OAuth browser redirect has no custom org/platform headers.
    response = client.get(
        "/api/clusters/dns/cloudflare/callback/", {"state": state, "code": "Fixture_Code_1787"}, **headers
    )
    if fault == "none":
        assert response.url.endswith("saved")
        assert DnsProviderConnection.objects.get().organization_id == world.org.pk
        assert len(wire.calls) == 2
    else:
        assert response.url.endswith("unconfirmed")
        assert not DnsProviderConnection.objects.exists() and not wire.calls
        assert DnsProviderOAuthAttempt.objects.get().consumed_at is None


def test_private_domain_checkpoint_fences_actual_paged_http_inventory(world, wire, client, monkeypatch):
    from astrolift_clusters import dns_provider_connections as service

    row = connect(client, world)
    domain = ManagedDomain.objects.create(
        organization=world.org, zone="example.test", dns_driver="cloudflare_read_only"
    )
    expected_version = domain.version

    def current_domain():
        if ManagedDomain.objects.get(pk=domain.pk).version != expected_version:
            raise transport.DnsConnectionError("DOMAIN_CHANGED")

    original = service.observe_connection
    monkeypatch.setattr(
        service,
        "observe_connection",
        lambda *args, **kwargs: original(*args, **kwargs, checkpoint=current_domain),
    )

    def payload(path):
        page = int(parse_qs(urlsplit(path).query)["page"][0])
        result = envelope(
            [{**ZONE, "id": f"{n:032x}"} for n in (range(1, 51) if page == 1 else range(51, 52))]
        )
        result["result_info"].update(page=page, total_count=51, total_pages=2)
        return result

    wire.override = payload

    def withdrawal(path):
        if "page=2" in path:
            domain.save()

    wire.hook = withdrawal
    before = len(wire.calls)
    result = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert result["data"]["cloudflareDnsZones"] == {
        "complete": False,
        "reason": "DOMAIN_CHANGED",
        "items": [],
    }
    assert len(wire.calls) == before + 2
    before = len(wire.calls)
    result = graphql(
        client, world, ZONES, {"i": {"connectionId": row["id"], "expectedVersion": row["version"]}}
    )
    assert result["data"]["cloudflareDnsZones"]["reason"] == "DOMAIN_CHANGED" and len(wire.calls) == before
