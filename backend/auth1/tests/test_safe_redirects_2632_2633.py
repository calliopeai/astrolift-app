"""Where the SCM, step-up and dev-login redirects may send the browser (#2632, #2633).

#2632: ``scm_app_manifest._safe_return_to`` kept any value starting with ``/``,
so ``return_to=//evil.example`` became the Location of the manifest, factory
gate and relink redirects and sent a signed-in operator to another host. The
SCM OAuth, step-up and dev-login checks were separate copies with gaps of
their own (``/\\evil.example``, ``http://localhost:@evil.example``). They all
use ``auth1.return_urls.safe_relative_path`` now, which also refuses a path
that names another host once percent-decoded.

#2633: the Providers page keeps its tab in the fragment and sends
``return_to=/providers#source``. ``scm_oauth`` appended ``scm_connected`` or
``scm_error`` after the fragment, where ``ScmCallbackToast`` (it reads the
query) never sees it. ``scm_oauth`` now uses the manifest module's redirect
helpers, which put the parameter in the query (#2589).
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from astrolift_identity.models import Member, Organization
from astrolift_identity.step_up_sso import is_safe_return_url
from astrolift_scm.models import SourceConnection
from auth1 import scm_app_manifest, scm_oauth
from auth1.dev_login import _safe_next
from auth1.return_urls import safe_relative_path
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

MANIFEST_START = "/app/auth1/scm/github/app-manifest/start"
MANIFEST_CALLBACK = "/app/auth1/scm/github/app-manifest/callback"
DEFAULT = "/settings/source-providers"
PROVIDERS = "/providers#source"

# Each names another host to a browser, or to a hop that percent-decodes it.
OFF_HOST = [
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/%2F%2Fevil.example",
    "/%2fevil.example",
    "/%5Cevil.example",
    "/%5c%5cevil.example",
    "/%09/evil.example",
    "/%0A/evil.example",
    "/%252F%252Fevil.example",
    "%2F%2Fevil.example",
    "https://evil.example/x",
    "http://localhost:@evil.example/",
    "javascript:alert(1)",
]

# The shapes the test client can send as a query value.
VIEW_OFF_HOST = [
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "/%2F%2Fevil.example",
    "https://evil.example/x",
]

# Paths on this host, including encoded characters that stay on it decoded.
ON_HOST = [
    DEFAULT,
    PROVIDERS,
    "/providers?section=factory-gate#source",
    "/apps?search=my%20app",
    "/apps/a%2Fb",
]

LOGIN = {
    "github": "auth1.scm_oauth._resolve_github_login",
    "gitlab": "auth1.scm_oauth._resolve_gitlab_login",
}


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture
def operator(settings):
    settings.APP_BASE_URL = "https://astrolift.test"
    org = Organization.objects.create(name="Acme", slug="acme-redirects")
    user = User.objects.create_user(username="redirect-tester", email="redirects@acme.example", password="x")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True)
    bind_role(
        user, permissions=[Permission.SCM_CONNECT], kind="ORG", scope_id=org.pk, slug=f"scm-connect-{org.pk}"
    )
    client = Client()
    client.force_login(user)
    return SimpleNamespace(org=org, client=client)


def _oauth_app(org, kind):
    secret = encrypt_at_rest(b"shh")
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        display_name=f"{kind} config",
        oauth_client_id="client-id",
        app_client_id="client-id" if kind.startswith("github_") else "",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# #2632: the checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", OFF_HOST)
def test_a_value_naming_another_host_falls_back(value):
    assert safe_relative_path(value) is None
    assert scm_app_manifest._safe_return_to(value) == DEFAULT
    # The reconnect mutation (connected_accounts) passes its own default.
    assert scm_oauth._safe_return_to(value, default="/") == "/"
    assert not is_safe_return_url(value)
    assert _safe_next(value) == "/app/admin/"


@pytest.mark.parametrize("value", ON_HOST)
def test_a_path_on_this_host_is_kept(value):
    assert safe_relative_path(value) == value
    assert scm_app_manifest._safe_return_to(value) == value
    assert scm_oauth._safe_return_to(value, default="/") == value
    assert is_safe_return_url(value)
    assert _safe_next(value) == value


@pytest.mark.parametrize(
    "value",
    ["http://localhost:3000/dashboard", "http://127.0.0.1:8000/app/admin/", "https://localhost/"],
)
def test_dev_login_keeps_a_loopback_origin(value):
    assert _safe_next(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:3000@evil.example/",
        "http://127.0.0.1:@evil.example/",
        "http://evil.example\\@localhost:3000/",
        "http://localhost.evil.example/",
    ],
)
def test_dev_login_refuses_a_host_dressed_as_loopback(value):
    assert _safe_next(value) == "/app/admin/"


# ---------------------------------------------------------------------------
# #2632: the views
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", VIEW_OFF_HOST)
def test_the_manifest_start_redirects_only_on_this_host(operator, value):
    response = operator.client.get(MANIFEST_START, {"org": "-", "return_to": value})
    assert response["Location"] == f"{DEFAULT}?scm_error=invalid_org_slug"


def test_the_manifest_start_keeps_a_path_on_this_host(operator):
    response = operator.client.get(MANIFEST_START, {"org": "-", "return_to": PROVIDERS})
    assert response["Location"] == "/providers?scm_error=invalid_org_slug#source"


def test_a_manifest_callback_ignores_a_return_stored_before_the_check(operator):
    session = operator.client.session
    session["scm_github_manifest_state"] = {
        "state": secrets.token_urlsafe(16),
        "connection_guid": "",
        "return_to": "//evil.example",
        "gh_org": "",
    }
    session.save()
    response = operator.client.get(MANIFEST_CALLBACK, {"state": "not-the-state", "code": "x"})
    assert response["Location"] == f"{DEFAULT}?scm_error=state_mismatch"


@pytest.mark.parametrize("host", ["github", "gitlab"])
@pytest.mark.parametrize("value", VIEW_OFF_HOST)
def test_an_oauth_start_keeps_only_a_path_on_this_host(operator, host, value):
    config = _oauth_app(operator.org, f"{host}_oauth_app")
    response = operator.client.get(
        f"/app/auth1/scm/{host}/start", {"config_id": str(config.guid), "return_to": value}
    )
    assert response.status_code == 302
    assert operator.client.session["scm_oauth_state"]["return_to"] == DEFAULT


# ---------------------------------------------------------------------------
# #2633: the outcome goes in the query, ahead of the fragment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["github", "gitlab"])
@pytest.mark.parametrize(
    ("return_to", "landed"),
    [
        (PROVIDERS, "/providers?scm_connected=octo#source"),
        ("/providers?section=hosts#source", "/providers?section=hosts&scm_connected=octo#source"),
        (DEFAULT, f"{DEFAULT}?scm_connected=octo"),
    ],
)
def test_a_connection_lands_with_its_outcome_ahead_of_the_fragment(operator, host, return_to, landed):
    config = _oauth_app(operator.org, f"{host}_oauth_app")
    operator.client.get(
        f"/app/auth1/scm/{host}/start", {"config_id": str(config.guid), "return_to": return_to}
    )
    state = operator.client.session["scm_oauth_state"]["state"]
    with (
        patch("auth1.scm_oauth._resolve_user_token_via_post", return_value=("token", None)),
        patch(LOGIN[host], return_value="octo"),
    ):
        response = operator.client.get(
            f"/app/auth1/scm/{host}/callback", {"state": state, "code": "the-code"}
        )
    assert response["Location"] == landed


@pytest.mark.parametrize("host", ["github", "gitlab"])
def test_a_refused_callback_lands_with_its_code_ahead_of_the_fragment(operator, host):
    config = _oauth_app(operator.org, f"{host}_oauth_app")
    operator.client.get(
        f"/app/auth1/scm/{host}/start", {"config_id": str(config.guid), "return_to": PROVIDERS}
    )
    response = operator.client.get(f"/app/auth1/scm/{host}/callback", {"state": "not-the-state", "code": "x"})
    assert response["Location"] == "/providers?scm_error=state_mismatch#source"


@pytest.mark.parametrize("host", ["github", "gitlab"])
def test_a_refused_start_lands_with_its_code_ahead_of_the_fragment(operator, host):
    response = operator.client.get(f"/app/auth1/scm/{host}/start", {"return_to": PROVIDERS})
    assert response["Location"] == "/providers?scm_error=missing_config_id#source"
