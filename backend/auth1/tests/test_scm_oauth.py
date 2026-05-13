"""
OAuth dance views — GitHub + GitLab.

The HTTP exchange to the SCM host is patched out; we exercise the
view-level state machine (state-cookie round-trip, error-redirect
codes, per-user row upsert) end-to-end with a real database.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import User
from django.test import Client

from astrolift_identity.models import Member, Organization
from astrolift_scm.models import SourceConnection
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture
def org_user_member():
    org = Organization.objects.create(name="Acme", slug="acme-oauth")
    user = User.objects.create_user(
        username="oauth-tester",
        email="oauth-tester@acme.example",
        password="x",
    )
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=org.pk,
        is_active=True,
    )
    return org, user


def _make_oauth_app(org, kind, *, client_id="abc123", client_secret=b"shh", api_base_url=""):
    """Persist an OAuth-app config row carrying an encrypted client secret."""
    enc = encrypt_at_rest(client_secret)
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        display_name=f"{kind} config",
        oauth_client_id=client_id,
        oauth_redirect_uri="",
        secret_backend_kind=enc.backend_kind,
        secret_ciphertext=enc.backend_ref,
        api_base_url=api_base_url,
        is_active=True,
    )


def _login(client, user):
    client.force_login(user)


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def test_github_start_redirects_to_authorize_with_state(org_user_member):
    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app", client_id="gh-client-id")
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid), "return_to": "/settings/source-providers"},
    )
    assert resp.status_code == 302
    parsed = urlsplit(resp["Location"])
    assert parsed.netloc == "github.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["gh-client-id"]
    assert qs["scope"] == ["read:user repo"]
    assert "state" in qs and qs["state"][0]
    # State persisted to the session for callback verification
    session = client.session
    assert session["scm_oauth_state"]["state"] == qs["state"][0]
    assert session["scm_oauth_state"]["kind"] == "github"


def test_github_start_rejects_open_return_to(org_user_member):
    """An attacker can't trick the dance into redirecting to a third-
    party host on success — anything that isn't a same-origin path
    falls back to /settings/source-providers."""
    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app")
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid), "return_to": "https://evil.example/x"},
    )
    assert resp.status_code == 302
    # session.return_to has been clamped to default
    assert client.session["scm_oauth_state"]["return_to"] == "/settings/source-providers"


def test_github_start_missing_config_redirects_with_error(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/start")
    assert resp.status_code == 302
    assert "scm_error=missing_config_id" in resp["Location"]


def test_github_callback_state_mismatch(org_user_member):
    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app")
    client = Client()
    _login(client, user)

    # Prime the session with a known state via /start
    client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid)},
    )

    # Now hit /callback with the wrong state token
    resp = client.get(
        "/app/auth1/scm/github/callback",
        {"state": "WRONG", "code": "abc"},
    )
    assert resp.status_code == 302
    assert "scm_error=state_mismatch" in resp["Location"]


def test_github_callback_persists_user_token(org_user_member):
    """Happy path: callback exchanges the code, fetches the login,
    and stores a github_oauth_user row pointing back at the config."""
    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app")
    client = Client()
    _login(client, user)

    # Pull the state via /start
    start_resp = client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]
    assert start_resp.status_code == 302

    with (
        patch(
            "auth1.scm_oauth._resolve_user_token_via_post",
            return_value=("gho_TestToken", None),
        ),
        patch("auth1.scm_oauth._resolve_github_login", return_value="alice"),
    ):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {"state": state, "code": "the-code"},
        )
    assert resp.status_code == 302
    assert "scm_connected=alice" in resp["Location"]

    row = SourceConnection.objects.get(
        organization=org,
        kind="github_oauth_user",
        user=user,
        parent_oauth_app=config,
    )
    assert row.account_login == "alice"
    assert row.display_name == "GitHub: alice"
    # Token round-trips through core.secrets
    plain = decrypt(
        EncryptedSecret(
            backend_kind=row.secret_backend_kind,
            backend_ref=bytes(row.secret_ciphertext),
        )
    ).decode()
    assert plain == "gho_TestToken"


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_start_redirects_to_gitlab_com_by_default(org_user_member):
    org, user = org_user_member
    config = _make_oauth_app(org, "gitlab_oauth_app", client_id="gl-client-id")
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/gitlab/start",
        {"config_id": str(config.guid)},
    )
    assert resp.status_code == 302
    parsed = urlsplit(resp["Location"])
    assert parsed.netloc == "gitlab.com"
    assert parsed.path == "/oauth/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["gl-client-id"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["read_api read_repository read_user"]


def test_gitlab_start_honors_api_base_url_for_self_hosted(org_user_member):
    """Self-hosted GitLab carries ``api_base_url`` on the config row;
    the authorize redirect must target that host, not gitlab.com."""
    org, user = org_user_member
    config = _make_oauth_app(
        org,
        "gitlab_oauth_app",
        api_base_url="https://gitlab.acme.example/",
    )
    client = Client()
    _login(client, user)
    resp = client.get(
        "/app/auth1/scm/gitlab/start",
        {"config_id": str(config.guid)},
    )
    assert resp.status_code == 302
    parsed = urlsplit(resp["Location"])
    assert parsed.netloc == "gitlab.acme.example"
    assert parsed.path == "/oauth/authorize"


def test_gitlab_start_missing_config_redirects_with_error(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/gitlab/start")
    assert resp.status_code == 302
    assert "scm_error=missing_config_id" in resp["Location"]


def test_gitlab_callback_persists_user_token_with_base_url(org_user_member):
    """Happy path on a self-hosted instance: the user-token row
    inherits ``api_base_url`` from its parent OAuth-app config so the
    repo-listing dispatcher hits the right host."""
    org, user = org_user_member
    config = _make_oauth_app(
        org,
        "gitlab_oauth_app",
        api_base_url="https://gitlab.acme.example",
    )
    client = Client()
    _login(client, user)

    client.get(
        "/app/auth1/scm/gitlab/start",
        {"config_id": str(config.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]

    with (
        patch(
            "auth1.scm_oauth._resolve_user_token_via_post",
            return_value=("glat-Test", None),
        ),
        patch("auth1.scm_oauth._resolve_gitlab_login", return_value="bob"),
    ):
        resp = client.get(
            "/app/auth1/scm/gitlab/callback",
            {"state": state, "code": "the-code"},
        )
    assert resp.status_code == 302
    assert "scm_connected=bob" in resp["Location"]

    row = SourceConnection.objects.get(
        organization=org,
        kind="gitlab_oauth_user",
        user=user,
        parent_oauth_app=config,
    )
    assert row.account_login == "bob"
    assert row.display_name == "GitLab: bob"
    assert row.api_base_url == "https://gitlab.acme.example"


def test_gitlab_callback_state_mismatch(org_user_member):
    org, user = org_user_member
    config = _make_oauth_app(org, "gitlab_oauth_app")
    client = Client()
    _login(client, user)
    client.get(
        "/app/auth1/scm/gitlab/start",
        {"config_id": str(config.guid)},
    )
    resp = client.get(
        "/app/auth1/scm/gitlab/callback",
        {"state": "WRONG", "code": "abc"},
    )
    assert resp.status_code == 302
    assert "scm_error=state_mismatch" in resp["Location"]


def test_gitlab_callback_wrong_kind_in_session(org_user_member):
    """If a user starts a GitHub dance and then races into the
    GitLab callback URL, the kind mismatch must reject — no
    cross-kind state confusion."""
    org, user = org_user_member
    gh_config = _make_oauth_app(org, "github_oauth_app")
    client = Client()
    _login(client, user)
    client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(gh_config.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]
    resp = client.get(
        "/app/auth1/scm/gitlab/callback",
        {"state": state, "code": "abc"},
    )
    assert resp.status_code == 302
    assert "scm_error=no_pending_state" in resp["Location"]
