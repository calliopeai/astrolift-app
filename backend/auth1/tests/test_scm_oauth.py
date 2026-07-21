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
    """Persist an OAuth-app config row carrying an encrypted client secret.

    For github_* kinds the OAuth dance (post-#525) reads
    ``app_client_id`` rather than ``oauth_client_id``. We populate both
    with the same value so existing tests stay equivalent — GitLab
    reads from ``oauth_client_id`` directly, so this is harmless for
    the GitLab branch."""
    enc = encrypt_at_rest(client_secret)
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        display_name=f"{kind} config",
        oauth_client_id=client_id,
        app_client_id=client_id if kind.startswith("github_") else "",
        oauth_redirect_uri="",
        secret_backend_kind=enc.backend_kind,
        secret_ciphertext=enc.backend_ref,
        api_base_url=api_base_url,
        is_active=True,
    )


def _make_github_app_install(
    org,
    *,
    client_id="Iv1.fake-app",
    pem=b"-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
    oauth_client_secret=b"ghs_app_secret",
):
    """Persist a github_app_install row with PEM in secret_ciphertext
    and user-to-server OAuth client_secret in oauth_client_secret_*.

    Mirrors the row shape that ``scm_app_manifest.github_app_manifest_callback``
    writes after a successful manifest exchange."""
    pem_enc = encrypt_at_rest(pem)
    cs_enc = encrypt_at_rest(oauth_client_secret)
    return SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        display_name="GitHub App: astrolift-test",
        # ``client_id`` here represents the App Client ID — that's what
        # the OAuth dance (#525) needs in ``app_client_id``. The
        # numeric App ID would live in ``oauth_client_id``; the
        # existing tests don't exercise webhook payloads so either is
        # fine, but we mirror the post-fix world by writing the same
        # value to both columns.
        oauth_client_id=client_id,
        app_client_id=client_id,
        oauth_redirect_uri="",
        secret_backend_kind=pem_enc.backend_kind,
        secret_ciphertext=pem_enc.backend_ref,
        oauth_client_secret_backend_kind=cs_enc.backend_kind,
        oauth_client_secret_ciphertext=cs_enc.backend_ref,
        installation_id="12345",
        account_login="acme-corp",
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


def test_github_callback_heals_reauth_and_orphan_state(org_user_member):
    """Reconnecting reuses the SAME per-user row (apps referencing it
    keep working) and clears any stale reauth / orphan state a prior 401
    or upstream uninstall left behind (#1171)."""
    from django.utils import timezone

    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app")

    # A previously-dead row: the token 401'd (reauth flagged) and the
    # upstream was uninstalled (orphaned), leaving it inactive.
    stale = SourceConnection.objects.create(
        organization=org,
        user=user,
        parent_oauth_app=config,
        kind="github_oauth_user",
        display_name="GitHub: alice",
        account_login="alice",
        is_active=False,
        reauth_required=True,
        is_orphaned=True,
        orphaned_at=timezone.now(),
        orphaned_reason="token revoked upstream",
    )

    client = Client()
    _login(client, user)
    client.get("/app/auth1/scm/github/start", {"config_id": str(config.guid)})
    state = client.session["scm_oauth_state"]["state"]

    with (
        patch("auth1.scm_oauth._resolve_user_token_via_post", return_value=("gho_Fresh", None)),
        patch("auth1.scm_oauth._resolve_github_login", return_value="alice"),
    ):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {"state": state, "code": "the-code"},
        )
    assert resp.status_code == 302

    # Healed in place — exactly one row, same PK, no second row minted.
    rows = SourceConnection.objects.filter(
        organization=org, user=user, kind="github_oauth_user", parent_oauth_app=config
    )
    assert rows.count() == 1
    healed = rows.get()
    assert healed.pk == stale.pk
    assert healed.is_active is True
    assert healed.reauth_required is False
    assert healed.is_orphaned is False
    assert healed.orphaned_at is None
    assert healed.orphaned_reason == ""
    plain = decrypt(
        EncryptedSecret(
            backend_kind=healed.secret_backend_kind,
            backend_ref=bytes(healed.secret_ciphertext),
        )
    ).decode()
    assert plain == "gho_Fresh"


def test_safe_return_to_rules():
    """Same-origin gate: rooted paths pass; missing / external /
    protocol-relative values fall back to ``default`` (#1171 added the
    ``default`` arg + the ``//host`` rejection)."""
    from auth1.scm_oauth import _safe_return_to

    assert _safe_return_to("/apps/new") == "/apps/new"
    assert _safe_return_to(None) == "/settings/source-providers"
    assert _safe_return_to("") == "/settings/source-providers"
    assert _safe_return_to("https://evil.example/x") == "/settings/source-providers"
    assert _safe_return_to("//evil.example") == "/settings/source-providers"
    # Caller-supplied default (the connect mutation lands on "/").
    assert _safe_return_to(None, default="/") == "/"
    assert _safe_return_to("//evil.example", default="/") == "/"
    assert _safe_return_to("/agents/new", default="/") == "/agents/new"


def test_resolve_client_secret_per_kind(org_user_member):
    """The dispatcher reads the OAuth client_secret out of different
    columns depending on the row's kind:

      - github_oauth_app: the only secret on the row is the OAuth
        client_secret, so it lives in ``secret_ciphertext``.
      - github_app_install: ``secret_ciphertext`` holds the App's PEM,
        so the user-to-server OAuth client_secret lives in
        ``oauth_client_secret_ciphertext``.
    """
    from auth1.scm_oauth import _resolve_client_secret

    org, _ = org_user_member
    oauth_app = _make_oauth_app(org, "github_oauth_app", client_secret=b"oauth-app-secret")
    app_install = _make_github_app_install(org, oauth_client_secret=b"app-install-secret")
    assert _resolve_client_secret(oauth_app) == "oauth-app-secret"
    assert _resolve_client_secret(app_install) == "app-install-secret"


def test_github_callback_against_app_install_parent(org_user_member):
    """End-to-end happy path against a github_app_install parent row:
    the callback decrypts the user-to-server OAuth client_secret from
    the new column, POSTs it to the token endpoint, and creates a
    github_oauth_user child row tied back to the App-install parent.

    Asserts the token POST received the App's user-to-server
    client_secret (not the PEM) so the GitHub exchange would actually
    succeed against real GitHub."""
    org, user = org_user_member
    parent = _make_github_app_install(org, oauth_client_secret=b"app-install-secret")
    client = Client()
    _login(client, user)

    client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(parent.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]

    captured_payload: dict = {}

    def _fake_post(url, payload):
        captured_payload.update(payload)
        return "gho_TestToken", None

    with (
        patch("auth1.scm_oauth._resolve_user_token_via_post", side_effect=_fake_post),
        patch("auth1.scm_oauth._resolve_github_login", return_value="alice"),
    ):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {"state": state, "code": "the-code"},
        )

    assert resp.status_code == 302
    assert "scm_connected=alice" in resp["Location"]

    # The token exchange used the App's user-to-server client_secret,
    # not the PEM that lives in secret_ciphertext.
    assert captured_payload["client_id"] == "Iv1.fake-app"
    assert captured_payload["client_secret"] == "app-install-secret"

    row = SourceConnection.objects.get(
        organization=org,
        kind="github_oauth_user",
        user=user,
        parent_oauth_app=parent,
    )
    assert row.account_login == "alice"
    assert row.display_name == "GitHub: alice"
    plain = decrypt(
        EncryptedSecret(
            backend_kind=row.secret_backend_kind,
            backend_ref=bytes(row.secret_ciphertext),
        )
    ).decode()
    assert plain == "gho_TestToken"


def test_github_callback_app_install_without_oauth_secret_errors(org_user_member):
    """An App-install row registered before the manifest flow persisted
    client_secret (or one where the operator paste-imported only the
    PEM) must surface a clean ``config_missing_oauth_secret`` error
    rather than blowing up on a `decrypt(empty)` or silently sending
    the PEM as the OAuth secret to GitHub."""
    org, user = org_user_member
    # Manually persist an App-install row with the PEM but no OAuth
    # client_secret — emulates the pre-fix legacy state.
    pem_enc = encrypt_at_rest(b"-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n")
    parent = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        # Set both columns so the post-#525 OAuth dance flow gets past
        # the new "missing Client ID" check and exercises the
        # missing-OAuth-secret branch that the test is actually
        # asserting on. Without ``app_client_id`` we'd short-circuit
        # to ``config_missing_client_id`` first.
        oauth_client_id="3705068",
        app_client_id="Iv1.legacy",
        secret_backend_kind=pem_enc.backend_kind,
        secret_ciphertext=pem_enc.backend_ref,
        account_login="acme-corp",
        installation_id="999",
        is_active=True,
    )
    client = Client()
    _login(client, user)
    client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(parent.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]
    resp = client.get(
        "/app/auth1/scm/github/callback",
        {"state": state, "code": "the-code"},
    )
    assert resp.status_code == 302
    assert "scm_error=config_missing_oauth_secret" in resp["Location"]


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


# ---------------------------------------------------------------------------
# Install-time OAuth callback (request_oauth_on_install=true path)  (#761)
# ---------------------------------------------------------------------------


def test_github_install_time_callback_success(org_user_member):
    """Install-time: GitHub sends code + installation_id + setup_action=install.
    The callback skips state validation, anchors via scm_github_install_state,
    exchanges the code, activates the connection, and upserts github_oauth_user."""
    org, user = org_user_member
    connection = _make_github_app_install(org)
    connection.is_active = False
    connection.installation_id = ""
    connection.save()

    client = Client()
    _login(client, user)

    # Seed the install-state cookie as the manifest flow would have done.
    session = client.session
    session["scm_github_install_state"] = {
        "state": "github-generated-state",
        "connection_guid": str(connection.guid),
        "return_to": "/settings/source-providers",
    }
    session.save()

    fake_token = "ghu_install_time_token"
    fake_login = "acme-install-user"

    with (
        patch("auth1.scm_oauth._resolve_user_token_via_post", return_value=(fake_token, None)),
        patch("auth1.scm_oauth._resolve_github_login", return_value=fake_login),
    ):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {
                "code": "install-code",
                "state": "github-generated-state",  # GitHub-generated; we don't validate it
                "installation_id": "99999",
                "setup_action": "install",
            },
        )

    assert resp.status_code == 302
    assert "scm_connected=" in resp["Location"]

    # Connection row should now be active with the installation_id written.
    connection.refresh_from_db()
    assert connection.is_active is True
    assert connection.installation_id == "99999"

    # Per-user github_oauth_user row should exist.
    user_conn = SourceConnection.objects.filter(
        organization=org,
        user=user,
        kind="github_oauth_user",
        parent_oauth_app=connection,
    ).first()
    assert user_conn is not None
    assert user_conn.account_login == fake_login
    plain = decrypt(
        EncryptedSecret(
            backend_kind=user_conn.secret_backend_kind,
            backend_ref=bytes(user_conn.secret_ciphertext),
        )
    )
    assert plain == fake_token.encode()


def test_github_install_time_callback_no_pending_state(org_user_member):
    """Install-time callback with no scm_github_install_state cookie → install_state error."""
    org, user = org_user_member
    _make_github_app_install(org)
    client = Client()
    _login(client, user)

    resp = client.get(
        "/app/auth1/scm/github/callback",
        {
            "code": "install-code",
            "state": "xyz",
            "installation_id": "99999",
            "setup_action": "install",
        },
    )
    assert resp.status_code == 302
    assert "scm_error=no_pending_install_state" in resp["Location"]


def test_github_install_time_callback_exchange_failure(org_user_member):
    """Install-time callback: token exchange failure redirects with exchange_failed."""
    org, user = org_user_member
    connection = _make_github_app_install(org)

    client = Client()
    _login(client, user)

    session = client.session
    session["scm_github_install_state"] = {
        "state": "github-generated-state",
        "connection_guid": str(connection.guid),
        "return_to": "/settings/source-providers",
    }
    session.save()

    with patch("auth1.scm_oauth._resolve_user_token_via_post", return_value=(None, "exchange_failed")):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {
                "code": "bad-code",
                "state": "github-generated-state",
                "installation_id": "99999",
                "setup_action": "install",
            },
        )

    assert resp.status_code == 302
    assert "scm_error=exchange_failed" in resp["Location"]


def test_github_install_time_callback_missing_client_id(org_user_member):
    """Install-time callback: connection row without app_client_id → missing_client_id."""
    org, user = org_user_member
    connection = _make_github_app_install(org, client_id="")

    client = Client()
    _login(client, user)

    session = client.session
    session["scm_github_install_state"] = {
        "state": "github-generated-state",
        "connection_guid": str(connection.guid),
        "return_to": "/settings/source-providers",
    }
    session.save()

    resp = client.get(
        "/app/auth1/scm/github/callback",
        {
            "code": "install-code",
            "state": "github-generated-state",
            "installation_id": "99999",
            "setup_action": "install",
        },
    )
    assert resp.status_code == 302
    assert "scm_error=config_missing_client_id" in resp["Location"]


def test_github_regular_callback_unaffected_by_install_params(org_user_member):
    """Regular callback path must still work even when installation_id is absent."""
    org, user = org_user_member
    config = _make_oauth_app(org, "github_oauth_app")
    client = Client()
    _login(client, user)

    client.get("/app/auth1/scm/github/start", {"config_id": str(config.guid)})
    state = client.session["scm_oauth_state"]["state"]

    with (
        patch("auth1.scm_oauth._resolve_user_token_via_post", return_value=("tok", None)),
        patch("auth1.scm_oauth._resolve_github_login", return_value="regular-user"),
    ):
        resp = client.get(
            "/app/auth1/scm/github/callback",
            {"state": state, "code": "xyz"},
            # No installation_id — stays on regular path
        )

    assert resp.status_code == 302
    assert "scm_connected=regular-user" in resp["Location"]
