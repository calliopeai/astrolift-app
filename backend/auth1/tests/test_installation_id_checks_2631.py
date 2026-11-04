"""Every path that stores a GitHub App installation id asks GitHub first (#2631).

The setup URL and the install-time OAuth callback of a normal install, the
Connect source form (``connectSource``), the BYO adopt
(``connectExistingGithubApp``) and finding an installation by account
(``github_app.installation_token``) keep an id only once GitHub, asked with the
App's own key, reports it as this App's installation, on the account the
operator named when one was named, and no other organization's connection
holds it. A re-link (#2601) already did; its suite,
``test_relink_app_installation_2601``, provides the local GitHub used here,
which checks every App JWT against the App's public key.

The world is ``GITHUB_APP_CONNECTION_SCOPE=per_install``: one App shared by
every organization, installed on acme-corp for organization A. The operator of
organization B holds scm.connect and scm.read in B only and tries to attach A's
installation to a row of B. Real Postgres rows, roles, sessions and the real
audit writer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import Client, RequestFactory

from astrolift_operations.models import AuditEvent
from astrolift_scm.models import SourceConnection
from auth1.tests.test_relink_app_installation_2601 import (  # noqa: F401 (fixtures)
    APP_ID,
    CLIENT_ID,
    OAUTH_CALLBACK,
    RETURN_TO,
    SETUP,
    SLUG,
    START,
    _app,
    _GitHub,
    _no_debug_toolbar,
    _no_opensearch,
    _org_member,
    _return,
    _snapshot,
    _status,
)
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

INSTALL = "scm.github_app.install"
# The shared App's installations: A's on acme-corp, and one on beta-corp no
# organization holds yet.
HELD = "111"
FREE = "222"
PATHS = pytest.mark.parametrize("path", [SETUP, OAUTH_CALLBACK], ids=["setup-url", "oauth-callback"])


@pytest.fixture(autouse=True)
def _user_token(monkeypatch):
    """The OAuth code an install-time return carries is redeemed at github.com,
    not at the App API the local GitHub answers."""
    monkeypatch.setattr(
        "auth1.scm_oauth._resolve_user_token_via_post", lambda url, payload: ("ghu_operator_b", None)
    )
    monkeypatch.setattr("auth1.scm_oauth._resolve_github_login", lambda token: "operator-b")


@pytest.fixture
def github(monkeypatch):
    stub = _GitHub()
    monkeypatch.setattr("astrolift_scm.providers.github_app.GITHUB_API_DEFAULT", stub.base)
    yield stub
    stub.server.shutdown()
    stub.server.server_close()


@pytest.fixture
def world(settings, github):
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"
    org_a, _ = _org_member("acme-a-2631")
    client_secret = encrypt_at_rest(b"the-apps-oauth-client-secret")
    holder = _app(
        org_a,
        github,
        login="acme-corp",
        installation_id=HELD,
        oauth_client_secret_backend_kind=client_secret.backend_kind,
        oauth_client_secret_ciphertext=client_secret.backend_ref,
    )
    org_b, user_b = _org_member("beta-b-2631", Permission.SCM_CONNECT, Permission.SCM_READ)
    client = Client()
    client.force_login(user_b)
    github.installations = {HELD: (APP_ID, "acme-corp"), FREE: (APP_ID, "beta-corp")}
    return SimpleNamespace(org_a=org_a, holder=holder, org=org_b, user=user_b, client=client)


def _start(world, account: str = "") -> SourceConnection:
    """B's Connect to GitHub: the shared App exists, so B gets a pending copy of it
    and the browser goes to GitHub's install page."""
    params = {"return_to": RETURN_TO, **({"org": account} if account else {})}
    response = world.client.get(START, params)
    assert response.status_code == 302, response
    assert response["Location"].startswith(f"https://github.com/apps/{SLUG}/installations/new")
    return SourceConnection.objects.get(organization=world.org, kind=SourceConnection.Kind.GITHUB_APP_INSTALL)


def _install_audits() -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=INSTALL).order_by("occurred_at", "id"))


def _user_tokens(world):
    return SourceConnection.objects.filter(
        organization=world.org, kind=SourceConnection.Kind.GITHUB_OAUTH_USER
    )


def _gql(world, query: str, variables: dict) -> dict:
    from config.schema import schema
    from core.schema.context import StrawberryContext

    request = RequestFactory().post("/app/gql/config/")
    request.user = world.user
    request.session = {}
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        result = schema.execute_sync(
            query, variable_values=variables, context_value=StrawberryContext(request)
        )
    assert result.errors is None, result.errors
    return result.data


# ---------------------------------------------------------------------------
# An install's return: the setup URL and the install-time OAuth callback
# ---------------------------------------------------------------------------


@PATHS
@pytest.mark.parametrize("account", ["acme-corp", ""], ids=["named-as-acme-corp", "no-account-named"])
def test_b_cannot_attach_the_installation_a_holds(world, github, path, account):
    """The reported attack: B starts an install of the shared App and returns
    with A's installation id in place of its own."""
    pending = _start(world, account)
    before, held_before = _snapshot(pending), _snapshot(world.holder)

    response = _return(world, path, HELD, "install")

    assert "scm_error=install_in_use" in response["Location"]
    assert _snapshot(pending) == before
    assert _snapshot(world.holder) == held_before
    assert github.reads() == [HELD] and github.mints() == []
    assert not _user_tokens(world).exists()
    (event,) = _install_audits()
    assert (event.decision, event.data["error_code"], event.data["installation_id"]) == (
        "DENY",
        "INSTALLATION_IN_USE",
        HELD,
    )
    assert (event.organization_id, event.actor_id, event.target_kind, event.target_id) == (
        world.org.pk,
        str(world.user.pk),
        "source_connection",
        str(pending.guid),
    )


@PATHS
@pytest.mark.parametrize("account", ["beta-corp", ""], ids=["named-as-beta-corp", "no-account-named"])
def test_an_install_stores_the_installation_github_confirms(world, github, path, account):
    """B's own installation goes on B's row. Without an account named, the row
    starts on the App's default login (acme-corp) and takes the account GitHub
    installed on, as #2576 requires."""
    pending = _start(world, account)

    response = _return(world, path, FREE, "install")

    assert "scm_connected=" in response["Location"]
    pending.refresh_from_db()
    assert (pending.installation_id, pending.is_active, pending.account_login) == (FREE, True, "beta-corp")
    assert github.reads() == [FREE]
    (event,) = _install_audits()
    assert (event.decision, event.data["error_code"], event.data["installed_on"]) == (
        "ALLOW",
        None,
        "beta-corp",
    )
    assert (event.target_id, event.data["installation_id"], event.data["setup_action"]) == (
        str(pending.guid),
        FREE,
        "install",
    )
    assert _user_tokens(world).count() == (1 if path == OAUTH_CALLBACK else 0)


@PATHS
def test_an_install_refuses_an_installation_on_another_account_than_the_one_named(world, github, path):
    pending = _start(world, "gamma-corp")
    before = _snapshot(pending)

    response = _return(world, path, FREE, "install")

    assert "scm_error=install_wrong_account" in response["Location"]
    assert _snapshot(pending) == before
    assert not _user_tokens(world).exists()
    (event,) = _install_audits()
    assert (event.decision, event.data["error_code"], event.data["installed_on"]) == (
        "DENY",
        "WRONG_INSTALLATION",
        "beta-corp",
    )


@PATHS
@pytest.mark.parametrize(
    "installation",
    [pytest.param({"555": (999, "beta-corp")}, id="another-app"), pytest.param({}, id="unknown-to-this-app")],
)
def test_an_install_refuses_an_installation_that_is_not_this_apps(world, github, path, installation):
    pending = _start(world)
    before = _snapshot(pending)
    github.installations = {**github.installations, **installation}

    response = _return(world, path, "555", "install")

    assert "scm_error=install_wrong_account" in response["Location"]
    assert _snapshot(pending) == before
    (event,) = _install_audits()
    assert (event.decision, event.data["error_code"]) == ("DENY", "WRONG_INSTALLATION")


@pytest.mark.parametrize(
    ("refusal", "error_code"),
    [(502, "INSTALLATION_UNCONFIRMED"), (401, "INSTALLATION_UNCONFIRMED"), (None, "KEY_UNUSABLE")],
)
def test_an_install_github_cannot_confirm_changes_nothing(world, github, refusal, error_code):
    pending = _start(world, "beta-corp")
    if refusal is None:
        pending.secret_ciphertext = encrypt_at_rest(b"not a private key").backend_ref
        pending.save()
    else:
        github.refuse = {FREE: refusal}
    before = _snapshot(pending)

    response = _return(world, SETUP, FREE, "install")

    assert "scm_error=install_unconfirmed" in response["Location"]
    assert _snapshot(pending) == before
    (event,) = _install_audits()
    # GitHub was not asked or did not answer: an allowed attempt that failed.
    assert (event.decision, event.data["error_code"]) == ("ALLOW", error_code)


# ---------------------------------------------------------------------------
# Connect source: an installation id typed with a pasted App key
# ---------------------------------------------------------------------------

CONNECT = """
mutation($input: ConnectSourceInput!) {
  connectSource(input: $input) { ok errors { code message field } data { id installationId accountLogin } }
}
"""


def _connect(world, github, **fields) -> dict:
    variables = {
        "kind": SourceConnection.Kind.GITHUB_APP_INSTALL.value,
        "displayName": "B's App",
        "secretPlaintext": github.pem,
        "appClientId": CLIENT_ID,
        **fields,
    }
    return _gql(world, CONNECT, {"input": variables})["connectSource"]


@pytest.mark.parametrize("app_id", [str(APP_ID), None], ids=["app-id-given", "no-app-id"])
def test_connect_source_stores_an_installation_github_confirms(world, github, app_id):
    """The form has no App ID field for this kind; GitHub's 404 for another
    App's installation settles a row without one."""
    result = _connect(world, github, installationId=FREE, accountLogin="beta-corp", oauthClientId=app_id)

    assert result["ok"], result["errors"]
    row = SourceConnection.objects.get(organization=world.org)
    assert (row.installation_id, row.account_login, row.is_active) == (FREE, "beta-corp", True)
    assert github.reads() == [FREE]


@pytest.mark.parametrize(
    ("fields", "code", "field"),
    [
        pytest.param(
            {"installationId": HELD, "accountLogin": "acme-corp"},
            "PERMISSION_DENIED",
            "installationId",
            id="held-by-another-organization",
        ),
        pytest.param(
            {"installationId": HELD, "accountLogin": "acme-corp", "kind": "github_factory_gate_app"},
            "PERMISSION_DENIED",
            "installationId",
            id="factory-gate-kind-held-by-another-organization",
        ),
        pytest.param(
            {"installationId": FREE, "accountLogin": "gamma-corp"},
            "PERMISSION_DENIED",
            "installationId",
            id="on-another-account",
        ),
        pytest.param(
            {"installationId": "555", "accountLogin": "beta-corp"},
            "PERMISSION_DENIED",
            "installationId",
            id="not-this-apps",
        ),
        pytest.param(
            # No App ID and no account to compare: GitHub's 404 alone refuses it.
            {"installationId": "555"},
            "PERMISSION_DENIED",
            "installationId",
            id="not-this-apps-nothing-to-compare",
        ),
        pytest.param(
            {"installationId": "222 ", "accountLogin": "beta-corp"},
            "VALIDATION",
            "installationId",
            id="not-numeric",
        ),
        pytest.param(
            {"installationId": FREE, "accountLogin": "beta-corp", "secretPlaintext": "not a key"},
            "VALIDATION",
            "secretPlaintext",
            id="key-cannot-sign",
        ),
    ],
)
def test_connect_source_refuses_an_installation_github_does_not_confirm(world, github, fields, code, field):
    if fields.get("kind") == "github_factory_gate_app":
        # A's factory gate App on the same installation id, as the held row.
        world.holder.kind = SourceConnection.Kind.GITHUB_FACTORY_GATE_APP
        world.holder.save()

    result = _connect(world, github, **fields)

    assert result["ok"] is False
    assert (result["errors"][0]["code"], result["errors"][0]["field"]) == (code, field)
    assert not SourceConnection.objects.filter(organization=world.org).exists()
    (event,) = AuditEvent.objects.filter(action="scm.connect")
    assert (event.decision, event.data["error_code"]) == (
        "DENY" if code == "PERMISSION_DENIED" else "ALLOW",
        code,
    )


# ---------------------------------------------------------------------------
# Adopt an existing App, and finding an installation by account
# ---------------------------------------------------------------------------

ADOPT = """
mutation($input: ConnectExistingGithubAppInput!) {
  connectExistingGithubApp(input: $input) { ok errors { code message field } data { installationId accountLogin } }
}
"""

UPDATE = """
mutation($input: UpdateSourceConnectionInput!) {
  updateSourceConnection(input: $input) { ok errors { code message } }
}
"""


def test_adopt_refuses_the_installation_another_organization_holds(world, github):
    """With the shared App's key, B adopts the App on acme-corp: refused before
    any token is minted. Its own account still adopts."""

    def adopt(account: str) -> dict:
        variables = {
            "appId": str(APP_ID),
            "privateKeyPem": github.pem,
            "clientId": CLIENT_ID,
            "orgLogin": account,
        }
        return _gql(world, ADOPT, {"input": variables})["connectExistingGithubApp"]

    refused = adopt("acme-corp")

    assert refused["ok"] is False
    assert (refused["errors"][0]["code"], refused["errors"][0]["field"]) == ("PERMISSION_DENIED", "orgLogin")
    assert not SourceConnection.objects.filter(organization=world.org).exists()
    assert github.mints() == []
    (event,) = AuditEvent.objects.filter(action="scm.connect_existing_github_app")
    assert (event.decision, event.data["error_code"]) == ("DENY", "PERMISSION_DENIED")

    adopted = adopt("beta-corp")

    assert adopted["ok"], adopted["errors"]
    assert adopted["data"] == {"installationId": FREE, "accountLogin": "beta-corp"}


@pytest.mark.parametrize(
    ("account", "found", "status"),
    [("acme-corp", "", "INSTALLATION_MISSING"), ("beta-corp", FREE, "ACTIVE")],
    ids=["acme-corp-held-by-a", "beta-corp-free"],
)
def test_finding_an_installation_by_account_refuses_one_another_organization_holds(
    world, github, account, found, status
):
    """No id in the browser at all: B names an account at start, switches the
    pending row on, and the next status load finds the installation by account."""
    pending = _start(world, account)
    switched = _gql(world, UPDATE, {"input": {"id": str(pending.guid), "isActive": True}})[
        "updateSourceConnection"
    ]
    assert switched["ok"], switched["errors"]

    assert _status(world, pending)[0] == status

    pending.refresh_from_db()
    assert pending.installation_id == found
    assert github.mints() == ([found] if found else [])
    events = _install_audits()
    if found:
        assert events == []
        return
    (event,) = events
    assert (
        event.decision,
        event.data["error_code"],
        event.data["installation_id"],
        event.data["found_by"],
    ) == (
        "DENY",
        "INSTALLATION_IN_USE",
        HELD,
        "account",
    )
    assert (event.organization_id, event.actor_id, event.target_id) == (
        world.org.pk,
        str(world.user.pk),
        str(pending.guid),
    )
    # A's row and its webhooks stay A's.
    assert SourceConnection.objects.get(installation_id=HELD).pk == world.holder.pk
