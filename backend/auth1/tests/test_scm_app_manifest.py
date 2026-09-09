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
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

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
    assert pending.oauth_client_id == "12345"  # numeric App ID
    # OAuth Client ID lands in its own column (#525) — the OAuth dance
    # at scm_oauth.github_start reads from here, not oauth_client_id.
    assert pending.app_client_id == "Iv1.fake"
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

    # User-to-server OAuth client_secret persisted in its own column so
    # the "Connect my GitHub" dance (scm_oauth.github_callback) can
    # redeem authorization codes against this App row.
    assert pending.oauth_client_secret_backend_kind
    cs_plain = decrypt(
        EncryptedSecret(
            backend_kind=pending.oauth_client_secret_backend_kind,
            backend_ref=bytes(pending.oauth_client_secret_ciphertext),
        )
    ).decode()
    assert cs_plain == "ghs_fake"

    # Row still inactive until the operator finishes the install step
    assert pending.is_active is False

    # Install state primed for the setup callback
    install_state = client.session["scm_github_install_state"]
    assert install_state["connection_guid"] == str(pending.guid)
    assert install_state["state"] == qs["state"][0]


def test_manifest_callback_tolerates_missing_client_secret(org_user_member):
    """If GitHub's response somehow omits ``client_secret`` (older API
    surface, partial install, etc.) the new column stays blank — the
    rest of the registration still completes so the App is usable for
    webhooks + repo listing. The per-user OAuth dance will then surface
    ``config_missing_oauth_secret`` and prompt re-registration."""
    org, user = org_user_member
    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})
    state = client.session["scm_github_manifest_state"]["state"]

    payload = _fake_manifest_payload()
    payload.pop("client_secret")
    with patch(
        "auth1.scm_app_manifest._exchange_manifest_code",
        return_value=(payload, None),
    ):
        resp = client.get(
            "/app/auth1/scm/github/app-manifest/callback",
            {"state": state, "code": "the-code"},
        )
    assert resp.status_code == 302
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.oauth_client_secret_backend_kind == ""
    assert bytes(row.oauth_client_secret_ciphertext) == b""


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


# ---------------------------------------------------------------------------
# App reuse / dedup (GITHUB_APP_CONNECTION_SCOPE)
# ---------------------------------------------------------------------------

_CANONICAL_PEM = b"-----BEGIN RSA PRIVATE KEY-----\ncanonical-pem\n-----END RSA PRIVATE KEY-----\n"
_CANONICAL_CLIENT_SECRET = b"ghs_canonical_secret"
_CANONICAL_WEBHOOK_SECRET = b"wh_canonical_secret"


def _make_installed_app(
    org,
    *,
    slug,
    app_id,
    client_id="Iv1.canonical",
    owner="acme-corp",
    installation_id="111222",
    is_active=True,
    is_orphaned=False,
    created_at=None,
):
    """A fully-created + installed github_app_install connection (the shape
    the manifest callback + setup leave behind)."""
    enc_pem = encrypt_at_rest(_CANONICAL_PEM)
    enc_cs = encrypt_at_rest(_CANONICAL_CLIENT_SECRET)
    enc_ws = encrypt_at_rest(_CANONICAL_WEBHOOK_SECRET)
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        display_name=f"GitHub App: {slug}",
        account_login=owner,
        installation_id=installation_id,
        oauth_client_id=app_id,
        app_client_id=client_id,
        is_active=is_active,
        is_orphaned=is_orphaned,
        secret_backend_kind=enc_pem.backend_kind,
        secret_ciphertext=enc_pem.backend_ref,
        oauth_client_secret_backend_kind=enc_cs.backend_kind,
        oauth_client_secret_ciphertext=enc_cs.backend_ref,
        webhook_secret_backend_kind=enc_ws.backend_kind,
        webhook_secret_ciphertext=enc_ws.backend_ref,
    )
    if created_at is not None:
        # created_at is auto_now_add; a raw UPDATE is the only way to backdate.
        SourceConnection.all_objects.filter(pk=conn.pk).update(created_at=created_at)
        conn.refresh_from_db()
    return conn


def _org_with_member(user, *, name, slug):
    org = Organization.objects.create(name=name, slug=slug)
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True)
    return org


def test_start_per_org_reuses_existing_app(org_user_member, settings):
    """per_org, case (a): an org that already has the App installed does
    NOT mint a new one — start short-circuits to the connected state and
    leaves the connection list unchanged."""
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_org"
    org, user = org_user_member
    _make_installed_app(org, slug="astrolift-acme", app_id="555", owner="acme-corp")
    client = Client()
    _login(client, user)

    resp = client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})

    assert resp.status_code == 302
    assert "scm_connected=" in resp["Location"]
    # No manifest form, no new/pending row.
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 1
    assert not SourceConnection.objects.filter(
        organization=org, display_name="GitHub App (pending registration)"
    ).exists()


def test_start_per_org_creates_when_no_app(org_user_member, settings):
    """per_org, case (c): the first connect in an org with no App falls
    through to the existing manifest CREATE flow (200 auto-submit form +
    a fresh credential-less pending row)."""
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_org"
    org, user = org_user_member
    client = Client()
    _login(client, user)

    resp = client.get("/app/auth1/scm/github/app-manifest/start", {"org": "acme-corp"})

    assert resp.status_code == 200
    assert 'name="manifest"' in resp.content.decode()
    pending = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert pending.is_active is False
    assert pending.oauth_client_id == ""


def test_start_per_org_does_not_reuse_other_orgs_app(settings):
    """per_org, case (c): another org's App is out of scope — start creates
    a new one rather than reusing across the org boundary."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_org"
    user = User.objects.create_user(username="per-org-b", email="pob@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="per-org-a")
    org_b = _org_with_member(user, name="Org B", slug="per-org-b")
    _make_installed_app(org_a, slug="astrolift-a", app_id="555", owner="acme-corp")

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 200
    assert 'name="manifest"' in resp.content.decode()
    pending_b = SourceConnection.objects.get(organization=org_b, kind="github_app_install")
    assert pending_b.oauth_client_id == ""  # a fresh pending, not a copy


def test_start_per_install_installs_existing_app_for_new_org(settings):
    """per_install, case (b): org B connecting when org A already owns the
    canonical App is redirected to INSTALL that existing App — the manifest
    CREATE flow never runs, the App's credentials are copied onto a pending
    row for org B, and the setup callback attaches org B's own
    installation_id to that row."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"

    user = User.objects.create_user(username="install-b", email="ib@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="install-a")
    org_b = _org_with_member(user, name="Org B", slug="install-b")
    canonical = _make_installed_app(
        org_a,
        slug="astrolift-canonical",
        app_id="900900",
        client_id="Iv1.canon",
        owner="acme-corp",
        installation_id="111",
    )

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    # Redirect to install the EXISTING app — not a 200 manifest form.
    assert resp.status_code == 302
    assert resp["Location"].startswith("https://github.com/apps/astrolift-canonical/installations/new")

    pending_b = SourceConnection.objects.get(organization=org_b, kind="github_app_install")
    assert pending_b.pk != canonical.pk
    assert pending_b.is_active is False
    assert pending_b.installation_id == ""
    assert pending_b.oauth_client_id == "900900"
    assert pending_b.app_client_id == "Iv1.canon"
    assert pending_b.account_login == "acme-corp"
    assert pending_b.display_name == "GitHub App: astrolift-canonical"

    # PEM + OAuth client_secret copied (re-encrypted) — the install-time
    # OAuth callback needs both to complete org B's install.
    assert (
        decrypt(
            EncryptedSecret(
                backend_kind=pending_b.secret_backend_kind,
                backend_ref=bytes(pending_b.secret_ciphertext),
            )
        )
        == _CANONICAL_PEM
    )
    assert (
        decrypt(
            EncryptedSecret(
                backend_kind=pending_b.oauth_client_secret_backend_kind,
                backend_ref=bytes(pending_b.oauth_client_secret_ciphertext),
            )
        )
        == _CANONICAL_CLIENT_SECRET
    )

    # Canonical untouched; exactly two app rows total (A's + B's copy).
    assert SourceConnection.objects.filter(organization=org_a, kind="github_app_install").count() == 1
    assert SourceConnection.objects.filter(kind="github_app_install").count() == 2

    # Setup callback attaches org B's installation_id to the pending copy.
    install_state = client.session["scm_github_install_state"]
    assert install_state["connection_guid"] == str(pending_b.guid)
    setup_resp = client.get(
        "/app/auth1/scm/github/app-manifest/setup",
        {
            "state": install_state["state"],
            "installation_id": "222333",
            "setup_action": "install",
        },
    )
    assert setup_resp.status_code == 302
    assert "scm_connected=" in setup_resp["Location"]
    pending_b.refresh_from_db()
    assert pending_b.installation_id == "222333"
    assert pending_b.is_active is True


def test_start_per_install_already_connected_is_noop(settings):
    """per_install, case (a): the org that already has the canonical App
    installed short-circuits to the connected state."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"
    user = User.objects.create_user(username="install-a-user", email="iau@x.example", password="x")
    org_a = _org_with_member(user, name="Org A", slug="install-a2")
    _make_installed_app(org_a, slug="astrolift-canonical", app_id="900900", owner="acme-corp")

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 302
    assert "scm_connected=" in resp["Location"]
    assert SourceConnection.objects.filter(kind="github_app_install").count() == 1


def test_start_unknown_scope_defaults_to_per_org(settings):
    """An unknown GITHUB_APP_CONNECTION_SCOPE normalises to per_org: org A's
    App is NOT shared to org B, so start creates a new one."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "totally-bogus"
    user = User.objects.create_user(username="bogus-b", email="bb@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="bogus-a")
    org_b = _org_with_member(user, name="Org B", slug="bogus-b")
    _make_installed_app(org_a, slug="astrolift-canonical", app_id="900900", owner="acme-corp")

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 200
    assert 'name="manifest"' in resp.content.decode()
    pending_b = SourceConnection.objects.get(organization=org_b, kind="github_app_install")
    assert pending_b.oauth_client_id == ""


def test_start_ignores_stale_pending_and_picks_real_app(settings):
    """A never-completed pending row (no App creds) must not count as a
    reusable canonical — the real, fully-created App is chosen instead."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"
    user = User.objects.create_user(username="stale-b", email="sb@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="stale-a")
    org_b = _org_with_member(user, name="Org B", slug="stale-b")
    # Abandoned pending attempt (is_active=False, no App ID / PEM).
    SourceConnection.objects.create(
        organization=org_a,
        kind="github_app_install",
        display_name="GitHub App (pending registration)",
        is_active=False,
    )
    _make_installed_app(org_a, slug="astrolift-real", app_id="700700", owner="acme-corp")

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 302
    assert resp["Location"].startswith("https://github.com/apps/astrolift-real/installations/new")
    pending_b = SourceConnection.objects.get(organization=org_b, kind="github_app_install", is_active=False)
    assert pending_b.oauth_client_id == "700700"


def test_start_multiple_canonical_picks_oldest(settings):
    """When several reusable Apps exist (the duplicate state) the oldest by
    created_at wins deterministically — even when it has the higher pk."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"
    user = User.objects.create_user(username="multi-c", email="mc@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="multi-a")
    org_c = _org_with_member(user, name="Org C", slug="multi-c")
    now = timezone.now()
    # Created first (lower pk) but a LATER created_at.
    _make_installed_app(
        org_a,
        slug="astrolift-newer",
        app_id="111",
        owner="owner-new",
        installation_id="1",
        created_at=now,
    )
    # Created second (higher pk) but an EARLIER created_at → this is oldest.
    _make_installed_app(
        org_a,
        slug="astrolift-older",
        app_id="222",
        owner="owner-old",
        installation_id="2",
        created_at=now - timedelta(days=1),
    )

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 302
    assert resp["Location"].startswith("https://github.com/apps/astrolift-older/installations/new")
    pending_c = SourceConnection.objects.get(organization=org_c, kind="github_app_install")
    assert pending_c.oauth_client_id == "222"


def test_start_reuse_falls_back_to_create_when_slug_unrecoverable(settings):
    """If the canonical's display_name was renamed away from the
    ``GitHub App: <slug>`` shape, the App slug can't be recovered to build
    an install URL — rather than strand the operator on a broken redirect,
    start falls through to the CREATE flow (200 manifest form)."""
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.GITHUB_APP_CONNECTION_SCOPE = "per_install"
    user = User.objects.create_user(username="rename-b", email="rb@x.example", password="x")
    org_a = Organization.objects.create(name="Org A", slug="rename-a")
    org_b = _org_with_member(user, name="Org B", slug="rename-b")
    canonical = _make_installed_app(org_a, slug="astrolift-canonical", app_id="900900", owner="acme-corp")
    # Operator renamed it (update_source_connection allows this) — the slug
    # no longer lives behind the "GitHub App: " prefix.
    canonical.display_name = "My Renamed Connection"
    canonical.save()

    client = Client()
    _login(client, user)
    resp = client.get("/app/auth1/scm/github/app-manifest/start")

    assert resp.status_code == 200
    assert 'name="manifest"' in resp.content.decode()
    pending_b = SourceConnection.objects.get(organization=org_b, kind="github_app_install")
    assert pending_b.oauth_client_id == ""  # a fresh pending, not a copy


# ---- manifest permissions match what autowire uses (#1713) -----------


def _permissions():
    """The manifest's permission block, built through the real code path."""
    from django.test import RequestFactory

    from auth1.scm_app_manifest import _manifest_json

    request = RequestFactory().get("/", HTTP_HOST="astrolift.example.com")
    manifest = _manifest_json(request, install_slug="astrolift", connection_guid="c" * 8)
    return manifest["default_permissions"]


def test_the_manifest_grants_every_permission_autowire_uses():
    """The manifest asked for four permissions and autowire needs seven, so an
    operator who followed the product's own App-creation flow got an App that
    could not do any of the things autowire exists to do -- and it surfaced as a
    403 from GitHub, which reads like their misconfiguration."""
    perms = _permissions()
    assert perms["contents"] == "write", "cannot commit the CI workflow file"
    assert perms["workflows"] == "write", "GitHub refuses a push touching .github/workflows"
    assert perms["secrets"] == "write", "cannot push Actions secrets"
    assert perms["actions"] == "write", "cannot trigger or read workflow runs"
    assert perms["repository_hooks"] == "write", "cannot install the source webhook"


def test_contents_is_not_read_only():
    """The specific regression: contents: read passes a casual reading of the
    manifest while making the first autowire step impossible."""
    assert _permissions()["contents"] != "read"


def test_workflows_write_accompanies_contents_write():
    """GitHub rejects a push that touches .github/workflows unless workflows is
    granted too, even when contents is write. Granting one without the other
    looks correct and fails at the same step."""
    perms = _permissions()
    if perms.get("contents") == "write":
        assert perms.get("workflows") == "write"
