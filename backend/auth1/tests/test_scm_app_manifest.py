"""
GitHub App manifest flow — one-click operator-side App registration.

We stub out the HTTP call to ``api.github.com/app-manifests/<code>/conversions``
and exercise the view-level state machine end-to-end against a real
database. The test suite covers:

  * start endpoint renders an auto-submitting form pointing at the
    right GitHub URL for org vs personal-account installs;
  * start endpoint pre-allocates a ``github_app_install`` row with
    ``is_active=False`` whose ``guid`` is embedded in the webhook URL
    inside the manifest;
  * callback endpoint exchanges the code for credentials, attaches the
    PEM + app_id + owner login to the row, and redirects to the
    GitHub install URL;
  * callback rejects state mismatches and missing codes;
  * setup endpoint records the installation_id and flips ``is_active``;
  * setup endpoint refuses to activate a connection on a non-``install``
    setup_action.
"""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import User
from django.test import Client

from astrolift_identity.models import Member, Organization
from astrolift_scm.models import SourceConnection
from core.secrets import EncryptedSecret, decrypt

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
def org_user_member(settings):
    settings.APP_BASE_URL = "https://astrolift.test"
    org = Organization.objects.create(name="Acme", slug="acme-manifest")
    user = User.objects.create_user(
        username="manifest-tester",
        email="manifest@acme.example",
        password="x",
    )
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=org.pk,
        is_active=True,
    )
    return org, user


def _login(client, user):
    client.force_login(user)


# ---------------------------------------------------------------------------
# start endpoint
# ---------------------------------------------------------------------------


def test_manifest_start_renders_form_for_org(org_user_member):
    org, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/start",
        {"org": "acme-corp", "return_to": "/settings/source-providers"},
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "github.com/organizations/acme-corp/settings/apps/new" in body
    assert 'name="manifest"' in body

    # Pre-allocated row exists, inactive, kind=github_app_install
    pending = SourceConnection.objects.get(organization=org, kind="github_app_install", is_active=False)
    # Webhook URL inside the manifest references that row's guid
    assert f"/app/auth1/scm/github/webhook/{pending.guid}/" in body
    # Session state primed for the callback
    session = client.session
    assert session["scm_github_manifest_state"]["connection_guid"] == str(pending.guid)
    assert session["scm_github_manifest_state"]["gh_org"] == "acme-corp"


def test_manifest_start_personal_account_path(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get("/app/auth1/scm/github/app-manifest/start")
    assert resp.status_code == 200
    body = resp.content.decode()
    # No org → /settings/apps/new (not /organizations/<x>/settings/apps/new)
    assert "github.com/settings/apps/new" in body
    assert "github.com/organizations/" not in body


def test_manifest_start_rejects_invalid_org_slug(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/start",
        {"org": "has spaces in it"},
    )
    assert resp.status_code == 302
    assert "scm_error=invalid_org_slug" in resp["Location"]


def test_manifest_start_clamps_open_return_to(org_user_member):
    """An attacker can't bounce the operator off to a third-party URL
    on completion — anything that isn't a same-origin path falls back
    to the canonical /settings/source-providers."""
    _, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/start",
        {"return_to": "https://evil.example/x"},
    )
    assert resp.status_code == 200
    session = client.session
    assert session["scm_github_manifest_state"]["return_to"] == "/settings/source-providers"


# ---------------------------------------------------------------------------
# callback endpoint
# ---------------------------------------------------------------------------


def _fake_manifest_payload() -> dict:
    return {
        "id": 12345,
        "slug": "astrolift-test",
        "client_id": "Iv1.fake",
        "client_secret": "ghs_fake",
        "webhook_secret": "wh-secret",
        "pem": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
        "html_url": "https://github.com/apps/astrolift-test",
        "owner": {"login": "acme-corp", "type": "Organization"},
    }


def test_manifest_callback_persists_credentials_and_redirects_to_install(org_user_member):
    org, user = org_user_member
    client = Client()
    _login(client, user)

    # Step 1: start primes the session + pre-allocates the row
    start_resp = client.get(
        "/app/auth1/scm/github/app-manifest/start",
        {"org": "acme-corp"},
    )
    assert start_resp.status_code == 200
    pending = SourceConnection.objects.get(organization=org, kind="github_app_install")
    state = client.session["scm_github_manifest_state"]["state"]

    # Step 2: callback — we patch the HTTP exchange
    with patch(
        "auth1.scm_app_manifest._exchange_manifest_code",
        return_value=(_fake_manifest_payload(), None),
    ):
        cb_resp = client.get(
            "/app/auth1/scm/github/app-manifest/callback",
            {"state": state, "code": "the-code"},
        )

    assert cb_resp.status_code == 302
    location = cb_resp["Location"]
    assert location.startswith("https://github.com/apps/astrolift-test/installations/new")
    parsed = urlsplit(location)
    qs = parse_qs(parsed.query)
    assert "state" in qs

    # Row updated in place
    pending.refresh_from_db()
    assert pending.oauth_client_id == "12345"  # app_id
    assert pending.account_login == "acme-corp"
    assert pending.display_name == "GitHub App: astrolift-test"
    plain = decrypt(
        EncryptedSecret(
            backend_kind=pending.secret_backend_kind,
            backend_ref=bytes(pending.secret_ciphertext),
        )
    ).decode()
    assert "BEGIN RSA PRIVATE KEY" in plain

    # Webhook secret stored encrypted
    assert pending.webhook_secret_backend_kind
    wh_plain = decrypt(
        EncryptedSecret(
            backend_kind=pending.webhook_secret_backend_kind,
            backend_ref=bytes(pending.webhook_secret_ciphertext),
        )
    ).decode()
    assert wh_plain == "wh-secret"

    # Row still inactive until the operator finishes the install step
    assert pending.is_active is False

    # Install state primed for the setup callback
    install_state = client.session["scm_github_install_state"]
    assert install_state["connection_guid"] == str(pending.guid)
    assert install_state["state"] == qs["state"][0]


def test_manifest_callback_rejects_state_mismatch(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/callback",
        {"state": "WRONG", "code": "abc"},
    )
    assert resp.status_code == 302
    assert "scm_error=state_mismatch" in resp["Location"]


def test_manifest_callback_no_pending_state(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/callback",
        {"state": "anything", "code": "abc"},
    )
    assert resp.status_code == 302
    assert "scm_error=no_pending_state" in resp["Location"]


def test_manifest_callback_rolls_back_pending_row_on_exchange_failure(org_user_member):
    """When the github.com round-trip fails, the pre-allocated row must
    be cleaned up so a retry doesn't run into the unique constraint and
    so the UI's connection list doesn't grow stuck pending entries."""
    org, user = org_user_member
    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})
    state = client.session["scm_github_manifest_state"]["state"]
    pending = SourceConnection.objects.get(organization=org, kind="github_app_install")

    with patch(
        "auth1.scm_app_manifest._exchange_manifest_code",
        return_value=(None, "exchange_expired"),
    ):
        resp = client.get(
            "/app/auth1/scm/github/app-manifest/callback",
            {"state": state, "code": "the-code"},
        )

    assert resp.status_code == 302
    assert "scm_error=exchange_expired" in resp["Location"]
    # Soft-deleted: vanishes from the default manager but stays in all_objects
    assert not SourceConnection.objects.filter(pk=pending.pk).exists()
    rolled_back = SourceConnection.all_objects.get(pk=pending.pk)
    assert rolled_back.deleted_at is not None


# ---------------------------------------------------------------------------
# setup endpoint
# ---------------------------------------------------------------------------


def test_manifest_setup_records_installation_id_and_activates(org_user_member):
    org, user = org_user_member
    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})
    state = client.session["scm_github_manifest_state"]["state"]
    with patch(
        "auth1.scm_app_manifest._exchange_manifest_code",
        return_value=(_fake_manifest_payload(), None),
    ):
        client.get(
            "/app/auth1/scm/github/app-manifest/callback",
            {"state": state, "code": "the-code"},
        )
    install_state = client.session["scm_github_install_state"]["state"]

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/setup",
        {
            "state": install_state,
            "installation_id": "987654321",
            "setup_action": "install",
        },
    )
    assert resp.status_code == 302
    assert "scm_connected=" in resp["Location"]

    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.installation_id == "987654321"
    assert row.is_active is True


def test_manifest_setup_refuses_non_install_action(org_user_member):
    _, user = org_user_member
    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})
    state = client.session["scm_github_manifest_state"]["state"]
    with patch(
        "auth1.scm_app_manifest._exchange_manifest_code",
        return_value=(_fake_manifest_payload(), None),
    ):
        client.get(
            "/app/auth1/scm/github/app-manifest/callback",
            {"state": state, "code": "the-code"},
        )
    install_state = client.session["scm_github_install_state"]["state"]

    resp = client.get(
        "/app/auth1/scm/github/app-manifest/setup",
        {
            "state": install_state,
            "installation_id": "987654321",
            "setup_action": "request",
        },
    )
    assert resp.status_code == 302
    assert "scm_error=install_incomplete" in resp["Location"]


# ---------------------------------------------------------------------------
# manifest payload shape
# ---------------------------------------------------------------------------


def test_manifest_payload_declares_minimal_permissions(org_user_member):
    """The HTML form embeds the manifest as a hidden input value. We
    decode it back out to assert the permission/event surface stays
    minimal — broadening permissions later should be an explicit edit
    on github.com, not a silent shrink/grow from a code change."""
    _, user = org_user_member
    client = Client()
    _login(client, user)
    resp = client.get(
        "/app/auth1/scm/github/app-manifest/start",
        {"org": "acme-corp"},
    )
    body = resp.content.decode()
    # Pull the manifest value out — it's HTML-escaped JSON.
    import re as _re

    m = _re.search(r'name="manifest" value="(.+?)"', body)
    assert m is not None
    import html as _html

    raw = _html.unescape(m.group(1))
    manifest = json.loads(raw)
    assert manifest["public"] is False
    assert manifest["default_permissions"] == {
        "contents": "read",
        "metadata": "read",
        "pull_requests": "write",
        "checks": "write",
    }
    assert set(manifest["default_events"]) == {"push", "pull_request"}
    assert manifest["url"].startswith("https://astrolift.test")
    assert manifest["redirect_url"].endswith("/app/auth1/scm/github/app-manifest/callback")
    assert manifest["setup_url"].endswith("/app/auth1/scm/github/app-manifest/setup")
    assert manifest["hook_attributes"]["url"].startswith(
        "https://astrolift.test/app/auth1/scm/github/webhook/"
    )
