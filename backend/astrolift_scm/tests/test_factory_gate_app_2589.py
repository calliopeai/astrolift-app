"""The factory gate GitHub App (#2589).

Registration runs the real manifest views (start, callback, setup); the
GraphQL read and the key rotation run through the served schema; the token
mint through its REST route. GitHub is a local HTTP server: it answers the
manifest conversion, GET /app, the installation read and the token mint,
checks every App JWT against the App's public key, and records each request.
Real Postgres rows throughout.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from astrolift_identity.api_tokens import mint_token as mint_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_operations.models import AuditEvent
from astrolift_scm.models import SourceConnection
from astrolift_scm.services import factory_gate
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

START = "/app/auth1/scm/github/app-manifest/start"
CALLBACK = "/app/auth1/scm/github/app-manifest/callback"
SETUP = "/app/auth1/scm/github/app-manifest/setup"
TOKEN = "/api/scm/v1/factory-gate/token/"


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _public(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None).public_key()


def _key_body(pem: str) -> str:
    """A run of the key's base64 body: present in any copy of the key."""
    return "".join(pem.strip().splitlines()[1:-1])[40:120]


class _GitHub:
    """GitHub's REST API for one App, at the HTTP layer."""

    def __init__(self):
        self.app_id = 4242
        self.client_id = "Iv23liFactoryGate01"
        self.slug = "calliope-factory-gate"
        self.owner = {"login": "acme-corp", "type": "Organization"}
        self.installation_id = 777
        self.installed_on = {"login": "acme-corp", "type": "Organization"}
        self.granted = {"members": "read"}
        self.token = "ghs_" + secrets.token_urlsafe(32)
        self.pem = _pem()
        self.accepted_keys = [_public(self.pem)]
        self.requests: list[SimpleNamespace] = []
        github = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                github._handle(self, "GET")

            def do_POST(self):
                github._handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def calls(self, method: str, path: str) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method == method and r.path == path]

    def token_requests(self) -> list[SimpleNamespace]:
        return self.calls("POST", f"/app/installations/{self.installation_id}/access_tokens")

    def _handle(self, handler, method: str) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        authorization = handler.headers.get("Authorization", "")
        self.requests.append(SimpleNamespace(method=method, path=handler.path, body=body, jwt=authorization))
        status, payload = self._answer(method, handler.path, authorization)
        data = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _answer(self, method: str, path: str, authorization: str) -> tuple[int, dict]:
        if method == "POST" and path.startswith("/app-manifests/") and path.endswith("/conversions"):
            return 201, {
                "id": self.app_id,
                "slug": self.slug,
                "client_id": self.client_id,
                "client_secret": "manifest-client-secret",
                "webhook_secret": None,
                "pem": self.pem,
                "owner": self.owner,
                "html_url": f"https://github.com/apps/{self.slug}",
            }
        if not self._signed_by_the_app(authorization):
            return 401, {"message": "A JSON web token could not be decoded"}
        if method == "GET" and path == "/app":
            return 200, {"id": self.app_id, "slug": self.slug, "owner": self.owner}
        if method == "GET" and path == f"/app/installations/{self.installation_id}":
            return 200, {
                "id": self.installation_id,
                "app_id": self.app_id,
                "account": self.installed_on,
                "target_type": self.installed_on["type"],
                "permissions": dict(self.granted),
            }
        if method == "POST" and path == f"/app/installations/{self.installation_id}/access_tokens":
            return 201, {
                "token": self.token,
                "expires_at": "2099-01-01T00:00:00Z",
                "permissions": dict(self.granted),
                "repository_selection": "selected",
            }
        return 404, {"message": "Not Found"}

    def _signed_by_the_app(self, authorization: str) -> bool:
        if not authorization.startswith("Bearer "):
            return False
        for key in self.accepted_keys:
            try:
                claims = jwt.decode(authorization[len("Bearer ") :], key, algorithms=["RS256"])
            except jwt.PyJWTError:
                continue
            return claims.get("iss") in {str(self.app_id), self.client_id}
        return False


@pytest.fixture
def github(monkeypatch):
    stub = _GitHub()
    monkeypatch.setattr("auth1.scm_app_manifest.GITHUB_API_DEFAULT", stub.base)
    monkeypatch.setattr("astrolift_scm.providers.github_app.GITHUB_API_DEFAULT", stub.base)
    yield stub
    stub.server.shutdown()
    stub.server.server_close()


def _org_member(slug: str, *permissions: Permission):
    org = Organization.objects.create(name=slug, slug=slug)
    user = User.objects.create_user(username=f"{slug}-operator", email=f"{slug}@example.test", password="x")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True)
    if permissions:
        bind_role(user, permissions=list(permissions), kind="ORG", scope_id=org.pk, slug=f"{slug}-role")
    return org, user


@pytest.fixture
def operator(settings):
    settings.APP_BASE_URL = "https://astrolift.test"
    org, user = _org_member("acme-gate", Permission.SCM_CONNECT, Permission.SCM_READ)
    client = Client()
    client.force_login(user)
    return SimpleNamespace(org=org, user=user, client=client)


# What the Providers > Source card sends: its section, on the Source tab.
RETURN_TO = "/providers?section=factory-gate#source"


def _start(op, org_login="acme-corp"):
    return op.client.get(START, {"purpose": "factory_gate", "org": org_login, "return_to": RETURN_TO})


def _register(op, org_login="acme-corp"):
    _start(op, org_login)
    state = op.client.session["scm_github_manifest_state"]["state"]
    return op.client.get(CALLBACK, {"state": state, "code": "manifest-code"})


def _install(op, installation_id=777):
    state = op.client.session["scm_github_install_state"]["state"]
    return op.client.get(
        SETUP, {"state": state, "installation_id": str(installation_id), "setup_action": "install"}
    )


def _manifest(response) -> dict:
    match = re.search(r'name="manifest" value="(.+?)"', response.content.decode())
    assert match is not None
    return json.loads(html.unescape(match.group(1)))


def _app_row(org) -> SourceConnection:
    return SourceConnection.objects.get(organization=org, kind=factory_gate.KIND, deleted_at__isnull=True)


def _audit(action: str, **filters) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action, **filters).order_by("occurred_at", "id"))


def _gql(user, org, query: str, variables: dict | None = None):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    request = RequestFactory().post("/app/gql/config/")
    request.user = user
    request.session = {}
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        return schema.execute_sync(query, variable_values=variables, context_value=StrawberryContext(request))


APP_QUERY = (
    """{ astroliftFactoryGateApp { state appId installationId ownerLogin permissions { name access } } }"""
)
ROTATE = """mutation($pem: String!) {
  rotateFactoryGateAppKey(input: {privateKeyPem: $pem}) {
    ok errors { code message field } data { state appId installationId ownerLogin }
  }
}"""


def _api_token(org, user, settings, *, allowed: bool) -> str:
    issued = mint_api_token()
    row = ApiToken.objects.create(
        user=user,
        organization=org,
        name="factory workflows",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:apps"],
    )
    settings.FACTORY_GATE_TOKEN_CALLERS = (str(row.guid),) if allowed else ()
    return issued.plaintext


def _mint(bearer: str, body: object = None, *, raw: bytes | None = None):
    data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
    return Client().post(
        TOKEN, data=data, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {bearer}"
    )


def _installed_app(org, github) -> SourceConnection:
    encrypted = encrypt_at_rest(github.pem.encode())
    return SourceConnection.objects.create(
        organization=org,
        kind=factory_gate.KIND,
        display_name=f"GitHub App: {github.slug}",
        account_login="acme-corp",
        oauth_client_id=str(github.app_id),
        app_client_id=github.client_id,
        installation_id=str(github.installation_id),
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _no_token_in_audit(token: str) -> None:
    for event in AuditEvent.objects.all():
        stored = json.dumps([event.data, event.target_id, event.actor_display, event.reasoning])
        assert token not in stored, f"{event.action} stored the token"


# ---------------------------------------------------------------------------
# Registration through the manifest flow
# ---------------------------------------------------------------------------


def test_the_manifest_asks_for_organization_members_read_and_nothing_else(operator):
    response = _start(operator)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'action="https://github.com/organizations/acme-corp/settings/apps/new?state=' in body
    manifest = _manifest(response)
    assert manifest["default_permissions"] == {"members": "read"}
    assert manifest["default_events"] == []
    assert manifest["public"] is False
    assert manifest["name"] == "calliope-factory-gate"
    # No webhook and no user OAuth: the App only mints installation tokens.
    for absent in ("hook_attributes", "callback_urls", "request_oauth_on_install"):
        assert absent not in manifest
    assert manifest["redirect_url"] == "https://astrolift.test" + CALLBACK
    assert manifest["setup_url"] == "https://astrolift.test" + SETUP

    pending = _app_row(operator.org)
    assert pending.is_active is False and pending.oauth_client_id == ""
    assert operator.client.session["scm_github_manifest_state"]["purpose"] == "factory_gate"
    assert not SourceConnection.objects.filter(organization=operator.org, kind="github_app_install").exists()


def test_the_app_name_comes_from_the_setting(operator, settings):
    settings.FACTORY_GATE_APP_NAME = "acme-member-reader"

    assert _manifest(_start(operator))["name"] == "acme-member-reader"


def test_registration_needs_the_github_organization(operator):
    response = operator.client.get(START, {"purpose": "factory_gate", "return_to": RETURN_TO})

    assert response.status_code == 302
    # Ahead of the fragment, where the UI reads it; the tab stays in the fragment.
    assert (
        response["Location"] == "/providers?section=factory-gate&scm_error=factory_gate_org_required#source"
    )
    assert not SourceConnection.objects.filter(organization=operator.org, kind=factory_gate.KIND).exists()


def test_registration_and_install_store_the_ids_and_the_key_encrypted_and_audit_both(operator, github):
    callback = _register(operator)

    assert callback.status_code == 302
    location = urlsplit(callback["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"https://github.com/apps/{github.slug}/installations/new"
    )
    assert parse_qs(location.query)["state"] == [operator.client.session["scm_github_install_state"]["state"]]

    app = _app_row(operator.org)
    assert (app.oauth_client_id, app.app_client_id, app.account_login, app.installation_id) == (
        "4242",
        github.client_id,
        "acme-corp",
        "",
    )
    stored = bytes(SourceConnection.objects.values_list("secret_ciphertext", flat=True).get(pk=app.pk))
    assert b"PRIVATE KEY" not in stored and _key_body(github.pem).encode() not in stored
    assert decrypt(EncryptedSecret(app.secret_backend_kind, stored)).decode() == github.pem
    # No user OAuth and no webhook: the client secret GitHub returned is dropped.
    assert bytes(app.oauth_client_secret_ciphertext) == b"" and bytes(app.webhook_secret_ciphertext) == b""
    assert factory_gate.state_of(app) is factory_gate.State.REGISTERED

    (registered,) = _audit(factory_gate.REGISTER)
    assert (registered.actor_id, registered.organization_id, registered.decision) == (
        str(operator.user.pk),
        operator.org.pk,
        "ALLOW",
    )
    assert (registered.data["app_id"], registered.data["purpose"]) == ("4242", "factory_gate")

    setup = _install(operator)

    assert setup.status_code == 302
    assert setup["Location"] == (
        "/providers?section=factory-gate&scm_connected=GitHub%20App%3A%20calliope-factory-gate#source"
    )
    app.refresh_from_db()
    assert (app.installation_id, app.is_active) == ("777", True)
    assert factory_gate.state_of(app) is factory_gate.State.INSTALLED
    # The installation id was confirmed with GitHub, signed by the stored key.
    assert len(github.calls("GET", "/app/installations/777")) == 1
    (installed,) = _audit(factory_gate.INSTALL)
    assert (installed.actor_id, installed.decision, installed.data["purpose"]) == (
        str(operator.user.pk),
        "ALLOW",
        "factory_gate",
    )
    assert (installed.data["app_id"], installed.data["installation_id"]) == ("4242", "777")
    assert not SourceConnection.objects.filter(organization=operator.org, kind="github_app_install").exists()


def test_registration_refuses_an_app_created_on_another_account(operator, github):
    github.owner = {"login": "somebody-else", "type": "Organization"}

    callback = _register(operator)

    assert "scm_error=factory_gate_owner_mismatch" in callback["Location"]
    assert not SourceConnection.objects.filter(organization=operator.org, kind=factory_gate.KIND).exists()
    (refused,) = _audit(factory_gate.REGISTER)
    assert (refused.decision, refused.data["app_id"], refused.data["purpose"]) == (
        "DENY",
        "4242",
        "factory_gate",
    )


def test_install_refuses_an_installation_on_another_account(operator, github):
    _register(operator)
    github.installed_on = {"login": "somebody-else", "type": "Organization"}

    setup = _install(operator)

    assert "scm_error=factory_gate_install_wrong_account" in setup["Location"]
    app = _app_row(operator.org)
    assert app.installation_id == "" and app.is_active is False
    (refused,) = _audit(factory_gate.INSTALL)
    assert (refused.decision, refused.data["installed_on"]) == ("DENY", "somebody-else")


def test_a_second_start_never_creates_a_second_app(operator, github):
    _register(operator)

    again = _start(operator)

    # Registered, not installed: carry on to that App's install page.
    assert again.status_code == 302
    assert again["Location"].startswith(f"https://github.com/apps/{github.slug}/installations/new?state=")
    assert SourceConnection.objects.filter(organization=operator.org, kind=factory_gate.KIND).count() == 1

    _install(operator)
    assert "scm_connected=" in _start(operator)["Location"]

    SourceConnection.objects.filter(pk=_app_row(operator.org).pk).update(secret_ciphertext=b"")
    assert "scm_error=factory_gate_missing_key" in _start(operator)["Location"]
    assert SourceConnection.objects.filter(organization=operator.org, kind=factory_gate.KIND).count() == 1


# ---------------------------------------------------------------------------
# The GraphQL read
# ---------------------------------------------------------------------------


def test_the_state_reads_each_stage_and_missing_key_when_the_key_is_absent(operator, github):
    def read():
        result = _gql(operator.user, operator.org, APP_QUERY)
        assert result.errors is None, result.errors
        return result.data["astroliftFactoryGateApp"]

    assert read() == {
        "state": "NOT_REGISTERED",
        "appId": None,
        "installationId": None,
        "ownerLogin": None,
        "permissions": [],
    }

    app = _installed_app(operator.org, github)
    SourceConnection.objects.filter(pk=app.pk).update(installation_id="")
    assert read()["state"] == "REGISTERED"

    SourceConnection.objects.filter(pk=app.pk).update(installation_id="777")
    assert read() == {
        "state": "INSTALLED",
        "appId": "4242",
        "installationId": "777",
        "ownerLogin": "acme-corp",
        "permissions": [{"name": "members", "access": "read"}],
    }

    SourceConnection.objects.filter(pk=app.pk).update(secret_ciphertext=b"")
    assert read()["state"] == "MISSING_KEY"
    SourceConnection.objects.filter(pk=app.pk).update(installation_id="")
    assert read()["state"] == "MISSING_KEY"


def test_no_query_returns_the_key(operator, github):
    _installed_app(operator.org, github)
    connections = """{
      astroliftSourceConnections { id kind accountLogin installationId oauthClientId appClientId }
      astroliftSourceConnectionsPage(limit: 50) { items { id kind } totalCount }
    }"""

    for query in (APP_QUERY, connections):
        result = _gql(operator.user, operator.org, query)
        assert result.errors is None, result.errors
        returned = json.dumps(result.data)
        assert "PRIVATE KEY" not in returned and _key_body(github.pem) not in returned

    lists = _gql(operator.user, operator.org, connections).data
    assert lists["astroliftSourceConnections"] == []
    assert lists["astroliftSourceConnectionsPage"]["items"] == []

    from config.schema import schema

    fields = {f.python_name for f in schema.get_type_by_name("AstroliftFactoryGateApp").fields}
    assert fields == {"state", "app_id", "installation_id", "owner_login", "permissions"}


def test_the_read_needs_scm_read(operator, github):
    _installed_app(operator.org, github)
    outsider = User.objects.create_user(username="gate-reader-without-scm", password="x")
    Member.objects.create(user=outsider, scope_kind="ORG", scope_id=operator.org.pk, is_active=True)

    denied = _gql(outsider, operator.org, APP_QUERY)

    assert denied.data is None and denied.errors
    assert _gql(operator.user, operator.org, APP_QUERY).data["astroliftFactoryGateApp"]["appId"] == "4242"


def test_an_app_of_another_org_reads_as_not_registered(operator, github):
    _installed_app(operator.org, github)
    other_org, other_user = _org_member("other-gate", Permission.SCM_READ)

    result = _gql(other_user, other_org, APP_QUERY)

    assert result.errors is None
    assert result.data["astroliftFactoryGateApp"]["state"] == "NOT_REGISTERED"


# ---------------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------------


def test_rotation_stores_a_key_github_accepts_encrypted_and_audits_it(operator, github):
    app = _installed_app(operator.org, github)
    new_pem = _pem()
    github.accepted_keys.append(_public(new_pem))

    result = _gql(operator.user, operator.org, ROTATE, {"pem": new_pem})

    assert result.errors is None
    payload = result.data["rotateFactoryGateAppKey"]
    assert payload["ok"] is True
    assert payload["data"] == {
        "state": "INSTALLED",
        "appId": "4242",
        "installationId": "777",
        "ownerLogin": "acme-corp",
    }
    assert "PRIVATE KEY" not in json.dumps(result.data)
    app.refresh_from_db()
    stored = bytes(app.secret_ciphertext)
    assert _key_body(new_pem).encode() not in stored
    assert decrypt(EncryptedSecret(app.secret_backend_kind, stored)).decode() == new_pem.strip()
    # GitHub saw a JWT made from the new key before it was stored.
    assert len(github.calls("GET", "/app")) == 1

    (rotated,) = _audit(factory_gate.ROTATE)
    assert (rotated.actor_id, rotated.decision) == (str(operator.user.pk), "ALLOW")
    assert (rotated.data["app_id"], rotated.data["purpose"]) == ("4242", "factory_gate")
    logged = MutationAuditLog.objects.filter(operation=factory_gate.ROTATE)
    assert logged.exists()
    assert all(_key_body(new_pem) not in json.dumps(row.variables) for row in logged)


def test_rotation_refuses_a_key_github_does_not_accept(operator, github):
    app = _installed_app(operator.org, github)
    before = bytes(app.secret_ciphertext)

    result = _gql(operator.user, operator.org, ROTATE, {"pem": _pem()})

    payload = result.data["rotateFactoryGateAppKey"]
    assert payload["ok"] is False
    assert (payload["errors"][0]["code"], payload["errors"][0]["field"]) == ("PRECONDITION", "privateKeyPem")
    app.refresh_from_db()
    assert bytes(app.secret_ciphertext) == before
    (refused,) = _audit(factory_gate.ROTATE)
    assert (refused.data["app_id"], refused.data["purpose"], refused.data["error_code"]) == (
        "4242",
        "factory_gate",
        "PRECONDITION",
    )


def test_rotation_refuses_text_that_is_not_a_key(operator, github):
    _installed_app(operator.org, github)

    result = _gql(operator.user, operator.org, ROTATE, {"pem": "-----BEGIN RSA PRIVATE KEY-----\nnope\n"})

    assert result.data["rotateFactoryGateAppKey"]["errors"][0]["code"] == "VALIDATION"
    assert github.calls("GET", "/app") == []


def test_rotation_needs_scm_connect(operator, github):
    app = _installed_app(operator.org, github)
    before = bytes(app.secret_ciphertext)
    reader = User.objects.create_user(username="gate-reader-only", password="x")
    Member.objects.create(user=reader, scope_kind="ORG", scope_id=operator.org.pk, is_active=True)
    bind_role(
        reader, permissions=[Permission.SCM_READ], kind="ORG", scope_id=operator.org.pk, slug="gate-reader"
    )
    new_pem = _pem()
    github.accepted_keys.append(_public(new_pem))

    result = _gql(reader, operator.org, ROTATE, {"pem": new_pem})

    assert result.data["rotateFactoryGateAppKey"]["errors"][0]["code"] == "PERMISSION_DENIED"
    app.refresh_from_db()
    assert bytes(app.secret_ciphertext) == before
    assert github.calls("GET", "/app") == []


# ---------------------------------------------------------------------------
# The token mint
# ---------------------------------------------------------------------------


def test_the_mint_refuses_a_caller_that_is_not_allowlisted(operator, github, settings):
    _installed_app(operator.org, github)
    bearer = _api_token(operator.org, operator.user, settings, allowed=False)

    response = _mint(bearer)

    assert response.status_code == 403
    assert response.json()["code"] == "CALLER_NOT_ALLOWED"
    assert github.token_requests() == []
    (refused,) = _audit(factory_gate.TOKEN_MINT)
    assert (refused.decision, refused.data["app_id"], refused.data["purpose"]) == (
        "DENY",
        "4242",
        "factory_gate",
    )
    assert refused.actor_id == str(operator.user.pk)
    assert refused.data["api_token_id"] == str(ApiToken.objects.get(organization=operator.org).guid)


@pytest.mark.parametrize(
    "body",
    [
        {"permissions": {"members": "write"}},
        {"permissions": {"contents": "read"}},
        {"permissions": {"members": "read", "metadata": "read"}},
        {"permissions": {}},
        {"permissions": "members"},
        ["members"],
    ],
)
def test_the_mint_refuses_any_permission_but_members_read(operator, github, settings, body):
    _installed_app(operator.org, github)
    bearer = _api_token(operator.org, operator.user, settings, allowed=True)

    response = _mint(bearer, body)

    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_NOT_ALLOWED"
    assert github.token_requests() == []
    (refused,) = _audit(factory_gate.TOKEN_MINT)
    assert (refused.decision, refused.data["purpose"]) == ("DENY", "factory_gate")


def test_the_mint_refuses_a_body_that_is_not_json(operator, github, settings):
    _installed_app(operator.org, github)
    bearer = _api_token(operator.org, operator.user, settings, allowed=True)

    response = _mint(bearer, raw=b"members=read")

    assert response.status_code == 403
    assert github.token_requests() == []


@pytest.mark.parametrize("body", [None, {"permissions": {"members": "read"}}])
def test_the_mint_returns_a_token_with_the_members_read_scope_only(operator, github, settings, body):
    _installed_app(operator.org, github)
    bearer = _api_token(operator.org, operator.user, settings, allowed=True)

    response = _mint(bearer, body)

    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    assert response.json() == {
        "token": github.token,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "permissions": {"members": "read"},
    }
    (request,) = github.token_requests()
    assert json.loads(request.body) == {"permissions": {"members": "read"}}
    claims = jwt.decode(request.jwt[len("Bearer ") :], github.accepted_keys[0], algorithms=["RS256"])
    assert claims["iss"] == github.client_id

    (minted,) = _audit(factory_gate.TOKEN_MINT)
    assert (minted.decision, minted.actor_id, minted.organization_id) == (
        "ALLOW",
        str(operator.user.pk),
        operator.org.pk,
    )
    assert (minted.data["app_id"], minted.data["purpose"], minted.data["permissions"]) == (
        "4242",
        "factory_gate",
        {"members": "read"},
    )
    _no_token_in_audit(github.token)


def test_the_mint_refuses_a_token_github_scopes_wider(operator, github, settings):
    _installed_app(operator.org, github)
    bearer = _api_token(operator.org, operator.user, settings, allowed=True)
    github.granted = {"members": "read", "metadata": "read"}

    response = _mint(bearer)

    assert response.status_code == 502
    assert response.json()["code"] == "SCOPE_MISMATCH"
    assert github.token not in response.content.decode()
    (refused,) = _audit(factory_gate.TOKEN_MINT)
    assert refused.decision == "DENY"
    _no_token_in_audit(github.token)


def test_the_mint_refuses_a_browser_session(operator, github, settings):
    _installed_app(operator.org, github)
    settings.FACTORY_GATE_TOKEN_CALLERS = ()

    response = operator.client.post(TOKEN, data=b"", content_type="application/json")

    assert response.status_code == 401
    assert github.token_requests() == []


def test_the_mint_answers_not_ready_until_the_app_is_installed_with_a_key(operator, github, settings):
    bearer = _api_token(operator.org, operator.user, settings, allowed=True)
    assert _mint(bearer).json()["code"] == "NOT_REGISTERED"

    app = _installed_app(operator.org, github)
    SourceConnection.objects.filter(pk=app.pk).update(secret_ciphertext=b"")
    response = _mint(bearer)

    assert (response.status_code, response.json()["code"]) == (409, "MISSING_KEY")
    assert github.token_requests() == []


def test_the_mint_uses_only_the_callers_own_organizations_app(operator, github, settings):
    _installed_app(operator.org, github)
    other_org, other_user = _org_member("other-gate-caller")
    bearer = _api_token(other_org, other_user, settings, allowed=True)

    response = _mint(bearer)

    assert (response.status_code, response.json()["code"]) == (409, "NOT_REGISTERED")
    assert github.token_requests() == []
