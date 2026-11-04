"""Re-linking a GitHub App connection through the App install flow (#2601).

``app-manifest/start?connection=<guid>`` sends the browser to GitHub's install
page for that connection's App; GitHub's return, through the setup URL or, for
an App that asks for user authorization on install, the OAuth callback, stores
the installation id on the same row once GitHub confirms it is this App's
installation on the row's account.

The views run through the Django test client against a local GitHub that
answers the installation read, the installation list and the token mint,
checks every App JWT against the App's public key, and records each request.
Real Postgres rows, real roles, real sessions and the real audit writer.
"""

from __future__ import annotations

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

from astrolift_identity.models import Member, Organization
from astrolift_operations.models import AuditEvent
from astrolift_scm.models import SourceConnection
from auth1.scm_app_manifest import RELINK
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

START = "/app/auth1/scm/github/app-manifest/start"
SETUP = "/app/auth1/scm/github/app-manifest/setup"
OAUTH_CALLBACK = "/app/auth1/scm/github/callback"
RETURN_TO = "/providers#source"

APP_ID = 3705068
CLIENT_ID = "Iv23liRelink2601Ab"
SLUG = "astrolift-ai"
_ONE = re.compile(r"/app/installations/(\d+)")
_MINT = re.compile(r"/app/installations/(\d+)/access_tokens")


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture(autouse=True)
def _no_user_token_exchange(monkeypatch):
    """A re-link never redeems the OAuth code GitHub sends on install."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a re-link redeemed the OAuth code")

    monkeypatch.setattr("auth1.scm_oauth._resolve_user_token_via_post", refuse)


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class _GitHub:
    """GitHub's App endpoints for one App: its installations and the token mint."""

    def __init__(self, app_id: int = APP_ID, client_id: str = CLIENT_ID):
        self.app_id = app_id
        self.client_id = client_id
        self.pem = _pem()
        self.public_key = serialization.load_pem_private_key(self.pem.encode(), password=None).public_key()
        # installation id -> (the App id GitHub reports for it, the account it is on)
        self.installations: dict[str, tuple[int, str]] = {}
        # installation id -> the HTTP status its read answers instead
        self.refuse: dict[str, int] = {}
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

    def reads(self) -> list[str]:
        """The installation ids read one by one, in order."""
        return [
            match.group(1)
            for request in self.requests
            if request.method == "GET" and (match := _ONE.fullmatch(request.path))
        ]

    def mints(self) -> list[str]:
        return [
            match.group(1)
            for request in self.requests
            if request.method == "POST" and (match := _MINT.fullmatch(request.path))
        ]

    def _handle(self, handler, method: str) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        if length:
            handler.rfile.read(length)
        self.requests.append(SimpleNamespace(method=method, path=handler.path))
        status, payload = self._answer(method, handler.path, handler.headers.get("Authorization", ""))
        data = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _answer(self, method: str, path: str, authorization: str) -> tuple[int, object]:
        if not self._signed_by_the_app(authorization):
            return 401, {"message": "A JSON web token could not be decoded"}
        ours = {i: login for i, (app_id, login) in self.installations.items() if app_id == self.app_id}
        if method == "GET" and path.startswith("/app/installations?"):
            return 200, [{"id": int(i), "account": {"login": login}} for i, login in ours.items()]
        match = _ONE.fullmatch(path)
        if method == "GET" and match:
            installation = match.group(1)
            if installation in self.refuse:
                return self.refuse[installation], {"message": "refused"}
            if installation in self.installations:
                app_id, login = self.installations[installation]
                return 200, {
                    "id": int(installation),
                    "app_id": app_id,
                    "account": {"login": login, "type": "Organization"},
                    "target_type": "Organization",
                }
        match = _MINT.fullmatch(path)
        if method == "POST" and match and match.group(1) in ours:
            return 201, {"token": "ghs_" + secrets.token_urlsafe(16), "expires_at": "2099-01-01T00:00:00Z"}
        return 404, {"message": "Not Found"}

    def _signed_by_the_app(self, authorization: str) -> bool:
        if not authorization.startswith("Bearer "):
            return False
        try:
            claims = jwt.decode(authorization[len("Bearer ") :], self.public_key, algorithms=["RS256"])
        except jwt.PyJWTError:
            return False
        return claims.get("iss") in {str(self.app_id), self.client_id}


@pytest.fixture
def github(monkeypatch):
    stub = _GitHub()
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
    org, user = _org_member("acme-relink", Permission.SCM_CONNECT, Permission.SCM_READ)
    client = Client()
    client.force_login(user)
    return SimpleNamespace(org=org, user=user, client=client)


def _app(org, github, login: str = "ConflictHQ", installation_id: str = "", **fields) -> SourceConnection:
    key = encrypt_at_rest(github.pem.encode())
    values = {
        "organization": org,
        "kind": SourceConnection.Kind.GITHUB_APP_INSTALL,
        "display_name": f"GitHub App: {SLUG}",
        "account_login": login,
        "oauth_client_id": str(APP_ID),
        "app_client_id": CLIENT_ID,
        "installation_id": installation_id,
        "secret_backend_kind": key.backend_kind,
        "secret_ciphertext": key.backend_ref,
        **fields,
    }
    return SourceConnection.objects.create(**values)


def _orphaned(org, github, installation_id: str = "111") -> SourceConnection:
    """The reported shape after the App was removed from ConflictHQ: a row whose installation is gone."""
    from django.utils import timezone

    return _app(
        org,
        github,
        installation_id=installation_id,
        is_orphaned=True,
        orphaned_at=timezone.now(),
        orphaned_reason="github installation uninstalled by owner",
    )


def _start(op, connection) -> object:
    return op.client.get(START, {"connection": str(connection), "return_to": RETURN_TO})


def _return(op, path: str, installation_id: str, setup_action: str):
    """GitHub's redirect after the install page, with the state it was given."""
    params = {
        "state": op.client.session["scm_github_install_state"]["state"],
        "installation_id": installation_id,
        "setup_action": setup_action,
    }
    if path == OAUTH_CALLBACK:
        params["code"] = "install-time-oauth-code"
    return op.client.get(path, params)


def _relink(op, connection, installation_id: str, *, path: str = SETUP, setup_action: str = "update"):
    started = _start(op, connection.guid)
    assert started.status_code == 302, started
    return _return(op, path, installation_id, setup_action)


def _audits() -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=RELINK).order_by("occurred_at", "id"))


def _snapshot(row: SourceConnection) -> tuple:
    row.refresh_from_db()
    return (
        row.installation_id,
        row.is_active,
        row.is_orphaned,
        row.orphaned_reason,
        row.account_login,
        row.version,
        row.deleted_at,
    )


def _seed_install_state(client, connection_guid: str, *, relink: bool = True) -> str:
    """Put an install state in the session as a start would."""
    state = secrets.token_urlsafe(24)
    session = client.session
    session["scm_github_install_state"] = {
        "state": state,
        "connection_guid": connection_guid,
        "return_to": RETURN_TO,
        **({"relink": True} if relink else {}),
    }
    session.save()
    return state


def _gql(user, org, query: str):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    request = RequestFactory().post("/app/gql/config/")
    request.user = user
    request.session = {}
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        return schema.execute_sync(query, context_value=StrawberryContext(request))


PAGE = "{ astroliftSourceConnectionsPage(limit: 100) { items { id status statusDetail } } }"


def _status(op, row: SourceConnection) -> tuple[str, str]:
    result = _gql(op.user, op.org, PAGE)
    assert result.errors is None, result.errors
    (item,) = [i for i in result.data["astroliftSourceConnectionsPage"]["items"] if i["id"] == str(row.guid)]
    return item["status"], item["statusDetail"]


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def test_the_start_sends_the_browser_to_the_apps_install_page_for_that_row(operator, github):
    row = _orphaned(operator.org, github)
    before = _snapshot(row)

    response = _start(operator, row.guid)

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"https://github.com/apps/{SLUG}/installations/new"
    )
    pending = operator.client.session["scm_github_install_state"]
    assert parse_qs(location.query)["state"] == [pending["state"]]
    assert (pending["connection_guid"], pending["return_to"], pending["relink"]) == (
        str(row.guid),
        RETURN_TO,
        True,
    )
    # No pending copy and no change: the row itself is what the return completes.
    assert SourceConnection.all_objects.filter(organization=operator.org).count() == 1
    assert _snapshot(row) == before
    assert github.requests == []


def test_the_start_needs_scm_connect(github):
    org, user = _org_member("acme-relink-read-only", Permission.SCM_READ)
    row = _orphaned(org, github)
    client = Client()
    client.force_login(user)

    response = client.get(START, {"connection": str(row.guid), "return_to": RETURN_TO})

    assert response.status_code == 403
    assert "scm_github_install_state" not in client.session


def test_the_start_refuses_a_row_outside_the_active_organization(operator, github):
    rival, _ = _org_member("rival-relink")
    foreign = _orphaned(rival, github)
    pat = SourceConnection.objects.create(
        organization=operator.org, kind=SourceConnection.Kind.GITHUB_PAT, account_login="ci-bot"
    )
    gate = _app(operator.org, github, kind=SourceConnection.Kind.GITHUB_FACTORY_GATE_APP)

    for guid in (foreign.guid, pat.guid, gate.guid, "8c7d7f43-0000-4000-8000-000000000000", "not-a-guid"):
        response = _start(operator, guid)

        assert response.status_code == 302, guid
        assert response["Location"] == "/providers?scm_error=connection_missing#source", guid
        assert "scm_github_install_state" not in operator.client.session
    foreign.refresh_from_db()
    assert foreign.is_orphaned is True
    assert github.requests == []


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"oauth_client_id": ""}, id="no-app-id"),
        pytest.param({"secret_ciphertext": b""}, id="no-key"),
        pytest.param({"account_login": ""}, id="no-account"),
        pytest.param({"display_name": "ConflictHQ source"}, id="renamed-so-no-app-slug"),
        pytest.param({"api_base_url": "https://ghe.example.test/api/v3"}, id="github-enterprise"),
    ],
)
def test_the_start_refuses_a_row_it_cannot_install(operator, github, fields):
    row = _orphaned(operator.org, github)
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()

    response = _start(operator, row.guid)

    assert response["Location"] == "/providers?scm_error=relink_unavailable#source"
    assert "scm_github_install_state" not in operator.client.session


# ---------------------------------------------------------------------------
# Return
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [SETUP, OAUTH_CALLBACK], ids=["setup-url", "oauth-callback"])
@pytest.mark.parametrize("setup_action", ["install", "update"])
def test_a_relink_stores_the_confirmed_installation_on_the_same_row(operator, github, path, setup_action):
    row = _orphaned(operator.org, github, installation_id="111")
    github.installations = {"222": (APP_ID, "ConflictHQ")}

    response = _relink(operator, row, "222", path=path, setup_action=setup_action)

    assert response.status_code == 302
    assert response["Location"] == "/providers?scm_connected=GitHub%20App%3A%20astrolift-ai#source"
    row.refresh_from_db()
    assert (row.installation_id, row.is_active, row.is_orphaned, row.orphaned_at, row.orphaned_reason) == (
        "222",
        True,
        False,
        None,
        "",
    )
    assert SourceConnection.all_objects.filter(organization=operator.org).count() == 1
    # Confirmed with GitHub, signed with the row's own key.
    assert github.reads() == ["222"]
    (event,) = _audits()
    assert (event.decision, event.actor_id, event.organization_id) == (
        "ALLOW",
        str(operator.user.pk),
        operator.org.pk,
    )
    assert (event.target_kind, event.target_id) == ("source_connection", str(row.guid))
    assert {k: event.data[k] for k in ("installation_id", "previous_installation_id", "installed_on")} == {
        "installation_id": "222",
        "previous_installation_id": "111",
        "installed_on": "ConflictHQ",
    }
    assert (event.data["setup_action"], event.data["app_id"], event.data["error_code"]) == (
        setup_action,
        str(APP_ID),
        None,
    )
    # A re-link does not redeem the install-time OAuth code into a user token.
    assert not SourceConnection.objects.filter(kind="github_oauth_user").exists()


def test_a_relink_completes_an_install_that_never_finished(operator, github):
    row = _app(operator.org, github, is_active=False)
    github.installations = {"333": (APP_ID, "conflicthq")}

    response = _relink(operator, row, "333", setup_action="install")

    assert "scm_connected=" in response["Location"]
    row.refresh_from_db()
    assert (row.installation_id, row.is_active) == ("333", True)
    # GitHub's account login matches the row's whatever its case; the row keeps its own.
    assert row.account_login == "ConflictHQ"


def test_the_status_reads_active_on_the_next_load_after_a_relink(operator, github):
    row = _app(operator.org, github)

    assert _status(operator, row)[0] == "INSTALLATION_MISSING"
    # The operator installs the App on ConflictHQ. Within 60 s a load reads
    # the cached verdict and does not ask GitHub.
    github.installations = {"444": (APP_ID, "ConflictHQ")}
    asked = len(github.requests)
    assert _status(operator, row)[0] == "INSTALLATION_MISSING"
    assert len(github.requests) == asked

    _relink(operator, row, "444")

    assert _status(operator, row) == ("ACTIVE", "")
    assert github.mints() == ["444"]


@pytest.mark.parametrize(
    "installation",
    [
        pytest.param({"555": (APP_ID, "calliopeai")}, id="another-account"),
        pytest.param({"555": (999, "ConflictHQ")}, id="another-app"),
        pytest.param({}, id="unknown-to-this-app"),
    ],
)
@pytest.mark.parametrize("path", [SETUP, OAUTH_CALLBACK], ids=["setup-url", "oauth-callback"])
def test_a_relink_refuses_an_installation_that_is_not_this_apps_on_the_rows_account(
    operator, github, installation, path
):
    row = _orphaned(operator.org, github, installation_id="111")
    before = _snapshot(row)
    github.installations = installation

    response = _relink(operator, row, "555", path=path)

    assert response["Location"] == "/providers?scm_error=install_wrong_account#source"
    assert _snapshot(row) == before
    (event,) = _audits()
    assert (event.decision, event.data["error_code"], event.data["installation_id"]) == (
        "DENY",
        "WRONG_INSTALLATION",
        "555",
    )
    assert event.data["installed_on"] == next(iter(installation.values()), (0, ""))[1]


def test_a_relink_refuses_an_installation_another_organization_holds(operator, github):
    rival, _ = _org_member("rival-holder")
    held = _app(rival, github, installation_id="666")
    row = _orphaned(operator.org, github)
    before, held_before = _snapshot(row), _snapshot(held)
    github.installations = {"666": (APP_ID, "ConflictHQ")}

    response = _relink(operator, row, "666")

    assert response["Location"] == "/providers?scm_error=install_in_use#source"
    assert _snapshot(row) == before
    assert _snapshot(held) == held_before
    (event,) = _audits()
    assert (event.decision, event.data["error_code"]) == ("DENY", "INSTALLATION_IN_USE")


@pytest.mark.parametrize("path", [SETUP, OAUTH_CALLBACK], ids=["setup-url", "oauth-callback"])
def test_a_forged_state_naming_another_organizations_row_is_refused(operator, github, path):
    rival, _ = _org_member("rival-forged")
    foreign = _orphaned(rival, github)
    before = _snapshot(foreign)
    github.installations = {"777": (APP_ID, "ConflictHQ")}
    state = _seed_install_state(operator.client, str(foreign.guid))
    params = {"state": state, "installation_id": "777", "setup_action": "update", "code": "c"}

    response = operator.client.get(path, params)

    assert response["Location"] == "/providers?scm_error=org_mismatch#source"
    assert _snapshot(foreign) == before
    assert github.requests == [] and _audits() == []


@pytest.mark.parametrize("path", [SETUP, OAUTH_CALLBACK], ids=["setup-url", "oauth-callback"])
def test_a_state_naming_no_row_is_refused(operator, github, path):
    state = _seed_install_state(operator.client, "8c7d7f43-0000-4000-8000-000000000000")

    response = operator.client.get(
        path, {"state": state, "installation_id": "777", "setup_action": "update", "code": "c"}
    )

    assert response["Location"] == "/providers?scm_error=connection_missing#source"
    assert github.requests == []


def test_the_setup_url_checks_the_state_it_was_given(operator, github):
    row = _orphaned(operator.org, github)
    before = _snapshot(row)
    github.installations = {"222": (APP_ID, "ConflictHQ")}
    _start(operator, row.guid)

    response = operator.client.get(
        SETUP, {"state": "not-the-state", "installation_id": "222", "setup_action": "update"}
    )

    assert response["Location"] == "/providers?scm_error=state_mismatch#source"
    assert _snapshot(row) == before


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"installation_id": "222", "setup_action": "request"}, id="requested-not-installed"),
        pytest.param({"installation_id": "", "setup_action": "install"}, id="no-installation-id"),
    ],
)
def test_a_relink_github_did_not_complete_changes_nothing(operator, github, params):
    row = _orphaned(operator.org, github)
    before = _snapshot(row)
    _start(operator, row.guid)
    state = operator.client.session["scm_github_install_state"]["state"]

    response = operator.client.get(SETUP, {"state": state, **params})

    assert response["Location"] == "/providers?scm_error=install_incomplete#source"
    assert _snapshot(row) == before
    assert github.requests == [] and _audits() == []


@pytest.mark.parametrize(
    ("refusal", "error_code"),
    [(502, "INSTALLATION_UNCONFIRMED"), (401, "INSTALLATION_UNCONFIRMED"), (None, "KEY_UNUSABLE")],
)
def test_a_relink_github_cannot_confirm_changes_nothing(operator, github, refusal, error_code):
    row = _orphaned(operator.org, github)
    github.installations = {"222": (APP_ID, "ConflictHQ")}
    if refusal is None:
        # A key that no longer decrypts: nothing can sign for the App.
        row.secret_ciphertext = encrypt_at_rest(b"not a private key").backend_ref
        row.save()
    else:
        github.refuse = {"222": refusal}
    before = _snapshot(row)

    response = _relink(operator, row, "222")

    assert response["Location"] == "/providers?scm_error=install_unconfirmed#source"
    assert _snapshot(row) == before
    (event,) = _audits()
    # GitHub was not asked or did not answer: an allowed attempt that failed, not a refusal.
    assert (event.decision, event.data["error_code"]) == ("ALLOW", error_code)


def test_an_install_that_is_not_a_relink_still_refuses_update_on_the_oauth_callback(operator, github):
    pending = _app(operator.org, github, is_active=False)
    before = _snapshot(pending)
    github.installations = {"888": (APP_ID, "ConflictHQ")}
    state = _seed_install_state(operator.client, str(pending.guid), relink=False)

    response = operator.client.get(
        OAUTH_CALLBACK, {"state": state, "installation_id": "888", "setup_action": "update", "code": "c"}
    )

    assert "scm_error=install_incomplete" in response["Location"]
    assert _snapshot(pending) == before
    assert github.requests == [] and _audits() == []


# ---------------------------------------------------------------------------
# The status detail names the fix
# ---------------------------------------------------------------------------


def test_the_status_detail_names_the_row_action_where_it_applies(operator, github):
    relinkable = _orphaned(operator.org, github)
    never_registered = SourceConnection.objects.create(
        organization=operator.org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App (pending registration)",
        account_login="acme-pending",
        is_active=False,
    )
    enterprise = _app(
        operator.org,
        github,
        login="acme-ghe",
        installation_id="111",
        is_orphaned=True,
        api_base_url="https://ghe.example.test/api/v3",
    )

    assert _status(operator, relinkable) == (
        "INSTALLATION_MISSING",
        "GitHub no longer has this App's installation on ConflictHQ. Use Install on GitHub in this row's "
        "actions, and pick ConflictHQ on GitHub.",
    )
    assert _status(operator, never_registered)[1] == (
        "The install on acme-pending never finished, so there is no installation. Install it under "
        "Providers → Source → Connect to GitHub → Install Astrolift on a GitHub org, with acme-pending as "
        "the org."
    )
    assert _status(operator, enterprise)[1].endswith(
        "Connect to GitHub → Install Astrolift on a GitHub org, with acme-ghe as the org."
    )
    assert github.requests == []
