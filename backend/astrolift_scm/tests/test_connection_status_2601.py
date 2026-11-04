"""Source connection status, checked against GitHub (#2601).

Providers > Source read a GitHub App connection as active from its stored
``is_active`` flag, so an App never installed on its account showed active
while every repository read through it failed. ``status`` and
``statusDetail`` now come from an installation token mint for a GitHub App.

They run through the served schema against a local GitHub that answers the
App's installation list and token mint, checks every App JWT against the
App's public key, and records each request. Real Postgres rows, real roles
and the real cache throughout.
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.test import RequestFactory

from astrolift_identity.models import Member, Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.services import connection_status
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

APP_ID = 3705068
CLIENT_ID = "Iv23liStatus2601Ab"
_MINT = re.compile(r"/app/installations/(\d+)/access_tokens")


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class _GitHub:
    """GitHub's App endpoints for one App: its installations and the token mint."""

    def __init__(self):
        self.pem = _pem()
        self.public_key = serialization.load_pem_private_key(self.pem.encode(), password=None).public_key()
        # installation id -> the account it is installed on
        self.installations: dict[str, str] = {}
        # installation id -> the HTTP status its token mint answers instead
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

    def mints(self) -> list[str]:
        """The installation ids a token was asked for, in order."""
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
        if method == "GET" and path.startswith("/app/installations?"):
            return 200, [
                {"id": int(i), "account": {"login": login}} for i, login in self.installations.items()
            ]
        match = _MINT.fullmatch(path)
        if method == "POST" and match:
            installation = match.group(1)
            if installation in self.refuse:
                return self.refuse[installation], {"message": "refused"}
            if installation in self.installations:
                return 201, {
                    "token": "ghs_" + secrets.token_urlsafe(16),
                    "expires_at": "2099-01-01T00:00:00Z",
                }
        return 404, {"message": "Not Found"}

    def _signed_by_the_app(self, authorization: str) -> bool:
        if not authorization.startswith("Bearer "):
            return False
        try:
            claims = jwt.decode(authorization[len("Bearer ") :], self.public_key, algorithms=["RS256"])
        except jwt.PyJWTError:
            return False
        return claims.get("iss") in {str(APP_ID), CLIENT_ID}


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
def operator():
    org, user = _org_member("acme-status", Permission.SCM_READ)
    return SimpleNamespace(org=org, user=user)


def _app(org, github, login: str, installation_id: str = "", *, is_active: bool = True) -> SourceConnection:
    key = encrypt_at_rest(github.pem.encode())
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: astrolift-ai",
        account_login=login,
        oauth_client_id=str(APP_ID),
        app_client_id=CLIENT_ID,
        installation_id=installation_id,
        secret_backend_kind=key.backend_kind,
        secret_ciphertext=key.backend_ref,
        is_active=is_active,
    )


def _gql(user, org, query: str, variables: dict | None = None):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    request = RequestFactory().post("/app/gql/config/")
    request.user = user
    request.session = {}
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        return schema.execute_sync(query, variable_values=variables, context_value=StrawberryContext(request))


PAGE = (
    "{ astroliftSourceConnectionsPage(limit: 100) { items { accountLogin isActive status statusDetail } } }"
)


def _statuses(op) -> dict[str, dict]:
    result = _gql(op.user, op.org, PAGE)
    assert result.errors is None, result.errors
    return {row["accountLogin"]: row for row in result.data["astroliftSourceConnectionsPage"]["items"]}


def _closed_port_url() -> str:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


def test_an_app_with_no_installation_on_its_account_reads_installation_missing(operator, github):
    """The reported case: astrolift-ai on ConflictHQ while the App is installed on calliopeai only."""
    github.installations = {"167938702": "calliopeai"}
    _app(operator.org, github, "ConflictHQ")

    row = _statuses(operator)["ConflictHQ"]

    assert row["isActive"] is True
    assert row["status"] == "INSTALLATION_MISSING"
    assert row["statusDetail"] == (
        "GitHub has no installation of this App on ConflictHQ. Use Install on GitHub in this row's actions, "
        "and pick ConflictHQ on GitHub."
    )
    assert github.mints() == []


def test_an_installed_app_reads_active_from_a_token_mint(operator, github):
    github.installations = {"158055468": "ConflictHQ"}
    conn = _app(operator.org, github, "ConflictHQ")

    row = _statuses(operator)["ConflictHQ"]

    assert (row["status"], row["statusDetail"]) == ("ACTIVE", "")
    assert github.mints() == ["158055468"]
    conn.refresh_from_db()
    assert conn.installation_id == "158055468"


def test_a_deleted_installation_reads_missing_and_orphans_the_row(operator, github):
    conn = _app(operator.org, github, "ConflictHQ", installation_id="158055468")

    first = _statuses(operator)["ConflictHQ"]
    second = _statuses(operator)["ConflictHQ"]

    assert first["status"] == second["status"] == "INSTALLATION_MISSING"
    assert first["statusDetail"].startswith("GitHub no longer has this App's installation on ConflictHQ.")
    assert first["statusDetail"] == second["statusDetail"]
    conn.refresh_from_db()
    assert conn.is_orphaned is True
    # The second load read the orphan flag; GitHub was asked once.
    assert github.mints() == ["158055468"]


@pytest.mark.parametrize("refusal", [401, 403])
def test_refused_credentials_read_inactive_without_orphaning(operator, github, refusal):
    github.installations = {"158055468": "ConflictHQ"}
    github.refuse = {"158055468": refusal}
    conn = _app(operator.org, github, "ConflictHQ", installation_id="158055468")

    row = _statuses(operator)["ConflictHQ"]

    assert row["status"] == "INACTIVE"
    assert row["statusDetail"].startswith(f"GitHub refused this App's credentials (HTTP {refusal}).")
    conn.refresh_from_db()
    assert conn.is_orphaned is False


def test_a_github_error_reads_unverified(operator, github):
    github.installations = {"158055468": "ConflictHQ"}
    github.refuse = {"158055468": 502}
    _app(operator.org, github, "ConflictHQ", installation_id="158055468")

    row = _statuses(operator)["ConflictHQ"]

    assert (row["status"], row["statusDetail"]) == (
        "UNVERIFIED",
        "GitHub answered HTTP 502 to the installation check.",
    )


def test_an_unreachable_github_reads_unverified(operator, github, monkeypatch):
    conn = _app(operator.org, github, "ConflictHQ", installation_id="158055468")
    monkeypatch.setattr("astrolift_scm.providers.github_app.GITHUB_API_DEFAULT", _closed_port_url())

    row = _statuses(operator)["ConflictHQ"]

    assert row["status"] == "UNVERIFIED"
    assert row["statusDetail"].startswith("Couldn't reach GitHub")
    conn.refresh_from_db()
    assert conn.is_orphaned is False


def test_stored_flags_settle_other_kinds_and_unfinished_installs_without_github(operator, github):
    SourceConnection.objects.create(
        organization=operator.org, kind=SourceConnection.Kind.GITHUB_PAT, account_login="ci-bot"
    )
    SourceConnection.objects.create(
        organization=operator.org,
        kind=SourceConnection.Kind.GITLAB_PAT,
        account_login="old-bot",
        is_active=False,
    )
    _app(operator.org, github, "pending-org", is_active=False)

    rows = _statuses(operator)

    assert (rows["ci-bot"]["status"], rows["ci-bot"]["statusDetail"]) == ("ACTIVE", "")
    assert (rows["old-bot"]["status"], rows["old-bot"]["statusDetail"]) == (
        "INACTIVE",
        "Inactive in Astrolift.",
    )
    assert rows["pending-org"]["status"] == "INSTALLATION_MISSING"
    assert rows["pending-org"]["statusDetail"].startswith("The install on pending-org never finished")
    assert github.requests == []


def test_the_check_runs_only_when_a_status_field_is_selected(operator, github):
    github.installations = {"158055468": "ConflictHQ"}
    _app(operator.org, github, "ConflictHQ", installation_id="158055468")

    result = _gql(
        operator.user, operator.org, "{ astroliftSourceConnectionsPage { items { accountLogin isActive } } }"
    )

    assert result.errors is None, result.errors
    assert github.requests == []


def test_one_response_checks_at_most_the_cap_and_a_later_load_checks_the_rest(operator, github):
    cap = connection_status.CHECKS_PER_RESPONSE
    for n in range(cap + 2):
        github.installations[str(1000 + n)] = f"org-{n}"
        _app(operator.org, github, f"org-{n}", installation_id=str(1000 + n))

    first = _statuses(operator)

    assert sorted(r["status"] for r in first.values()) == ["ACTIVE"] * cap + ["UNVERIFIED"] * 2
    assert len(github.mints()) == cap
    for row in first.values():
        if row["status"] == "UNVERIFIED":
            assert row["statusDetail"] == connection_status.NOT_CHECKED.detail

    second = _statuses(operator)

    assert {r["status"] for r in second.values()} == {"ACTIVE"}
    # The checked rows came from the cache; each installation was asked for once.
    assert sorted(github.mints()) == sorted(github.installations)


def test_no_check_starts_once_the_time_budget_is_spent(operator, github, monkeypatch):
    for n in range(3):
        github.installations[str(2000 + n)] = f"slow-{n}"
        _app(operator.org, github, f"slow-{n}", installation_id=str(2000 + n))
    budget = connection_status.CHECK_BUDGET_SECONDS
    ticks = [0.0, budget - 1, budget]
    monkeypatch.setattr(connection_status, "time", SimpleNamespace(monotonic=lambda: ticks.pop(0)))

    rows = _statuses(operator)

    assert sorted(r["status"] for r in rows.values()) == ["ACTIVE", "ACTIVE", "UNVERIFIED"]
    assert len(github.mints()) == 2


def test_a_verdict_is_reused_until_the_row_changes(operator, github):
    # A refused mint is neither token-cached nor orphaned, so only the verdict cache spares GitHub.
    github.installations = {"158055468": "ConflictHQ"}
    github.refuse = {"158055468": 401}
    conn = _app(operator.org, github, "ConflictHQ", installation_id="158055468")

    _statuses(operator)
    _statuses(operator)
    assert github.mints() == ["158055468"]

    conn.display_name = "GitHub App: astrolift-ai (ConflictHQ)"
    conn.save()
    _statuses(operator)
    assert github.mints() == ["158055468", "158055468"]


UPDATE = """mutation($id: GUID!) {
  updateSourceConnection(input: {id: $id, displayName: "renamed"}) {
    ok errors { message } data { status statusDetail }
  }
}"""


def test_a_caller_without_scm_read_gets_no_check(github):
    org, user = _org_member("acme-connect-only", Permission.SCM_CONNECT)
    github.installations = {"158055468": "ConflictHQ"}
    conn = _app(org, github, "ConflictHQ", installation_id="158055468")

    result = _gql(user, org, UPDATE, {"id": str(conn.guid)})

    assert result.errors is None, result.errors
    payload = result.data["updateSourceConnection"]
    assert payload["ok"] is True, payload["errors"]
    assert (payload["data"]["status"], payload["data"]["statusDetail"]) == (
        "UNVERIFIED",
        connection_status.NOT_PERMITTED.detail,
    )
    assert github.requests == []


def test_a_connection_of_another_organization_is_never_checked(operator, github):
    rival, _ = _org_member("rival-status")
    github.installations = {"158055468": "ConflictHQ"}
    foreign = _app(rival, github, "ConflictHQ", installation_id="158055468")

    assert "ConflictHQ" not in _statuses(operator)
    with tenant_context(TenantContext(organization_id=operator.org.pk, actor_user_id=operator.user.pk)):
        verdict = connection_status.ResponseStatus().verdict(foreign)

    assert verdict == connection_status.NOT_PERMITTED
    assert github.requests == []
