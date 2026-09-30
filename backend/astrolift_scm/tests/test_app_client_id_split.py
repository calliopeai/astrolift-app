"""
SourceConnection.app_client_id — GitHub App Client ID separation (#525).

The numeric **App ID** (e.g. ``3705068``) and the OAuth **Client ID**
(e.g. ``Iv23lic8662KXwe4XKEI``) are distinct values on a GitHub App.
Pre-#525 we conflated them on the ``oauth_client_id`` column, which
sent the App ID into the ``/login/oauth/authorize?client_id=…`` URL
and 404'd at github.com.

This module pins the split end-to-end:

  * createSourceConnection / updateSourceConnection validate the
    Client ID shape (Iv… or 20-char hex) and require it for GitHub
    OAuth-app + App-install kinds.
  * The OAuth start view (auth1.scm_oauth.github_start) sends
    ``app_client_id`` to /login/oauth/authorize, not ``oauth_client_id``.
  * The GitHub-App JWT signer (astrolift_scm.providers.github_app)
    uses ``app_client_id`` as the JWT ``iss`` claim, falling back to
    the legacy numeric App ID in ``oauth_client_id`` with a logged
    deprecation warning.
  * The ``needs_client_id`` property + the GraphQL ``needsClientId``
    field flip true on rows that pre-date the column, so the FE banner
    can surface them.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.test import Client

from astrolift_graphql import GUID
from astrolift_identity.models import Member, Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.schema.mutations import (
    ConnectSourceInput,
    ScmMutation,
    UpdateSourceConnectionInput,
)
from astrolift_scm.schema.types import source_connection_to_type
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


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
    org = Organization.objects.create(name="Acme", slug="acme-clientid")
    user = User.objects.create_user(
        username="clientid-tester",
        email="clientid-tester@acme.example",
        password="x",
    )
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=org.pk,
        is_active=True,
    )
    bind_role(
        user, permissions=[Permission.SCM_CONNECT], kind="ORG", scope_id=org.pk, slug="clientid-connect"
    )
    return org, user


def _info(user=None):
    """Stub Strawberry Info — what @require_permission + tenant_scoped read."""
    if user is None:
        user = SimpleNamespace(is_authenticated=False, pk=None, username="")
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---------------------------------------------------------------------------
# Mutation validation
# ---------------------------------------------------------------------------


def test_connect_source_accepts_github_app_install_with_both_ids(org_user_member, permission_resolver):
    org, user = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    mut = ScmMutation()
    with _ctx(org):
        result = mut.connect_source(
            _info(user),
            input=ConnectSourceInput(
                kind="github_app_install",
                display_name="Acme GitHub App",
                installation_id="987654321",
                secret_plaintext="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
                oauth_client_id="3705068",
                app_client_id="Iv23lic8662KXwe4XKEI",
            ),
        )
    assert result.ok, result.errors
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.oauth_client_id == "3705068"
    assert row.app_client_id == "Iv23lic8662KXwe4XKEI"
    # Happy-path row should NOT show the "needs client id" banner.
    assert row.needs_client_id is False


def test_connect_source_rejects_github_app_install_without_app_client_id(
    org_user_member, permission_resolver
):
    """The prod-blocker case from #525 — operator submits the App ID
    but forgets / doesn't know about the Client ID. We refuse at the
    mutation boundary rather than letting the OAuth dance 404 later."""
    org, user = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    mut = ScmMutation()
    with _ctx(org):
        result = mut.connect_source(
            _info(user),
            input=ConnectSourceInput(
                kind="github_app_install",
                display_name="Acme GitHub App",
                installation_id="987654321",
                secret_plaintext="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
                oauth_client_id="3705068",
                app_client_id=None,
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "appClientId"


def test_connect_source_rejects_malformed_client_id(org_user_member, permission_resolver):
    org, user = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    mut = ScmMutation()
    with _ctx(org):
        result = mut.connect_source(
            _info(user),
            input=ConnectSourceInput(
                kind="github_app_install",
                display_name="Bad Client ID",
                installation_id="1",
                secret_plaintext="pem",
                oauth_client_id="3705068",
                # Looks like an App slug, not a Client ID
                app_client_id="not-a-real-client-id",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert "doesn't look like" in result.errors[0].message


def test_connect_source_accepts_legacy_hex_oauth_app_client_id(org_user_member, permission_resolver):
    """Classic GitHub OAuth Apps mint a 20-char lowercase hex Client
    ID; we accept that shape too so operators with pre-Iv-rollout Apps
    can still register."""
    org, user = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    mut = ScmMutation()
    with _ctx(org):
        result = mut.connect_source(
            _info(user),
            input=ConnectSourceInput(
                kind="github_oauth_app",
                display_name="Legacy OAuth App",
                secret_plaintext="cli-secret",
                oauth_client_id="deadbeef" * 2 + "deadbeef",
                # 20-char hex — legacy OAuth App Client ID shape.
                app_client_id="abcdef0123456789abcd",
            ),
        )
    assert result.ok, result.errors


def test_connect_source_pat_kind_does_not_require_app_client_id(org_user_member, permission_resolver):
    """PATs aren't OAuth — no Client ID needed."""
    org, user = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    mut = ScmMutation()
    with _ctx(org):
        result = mut.connect_source(
            _info(user),
            input=ConnectSourceInput(
                kind="github_pat",
                display_name="bot PAT",
                account_login="acme-bot",
                secret_plaintext="ghp_xxxxx",
            ),
        )
    assert result.ok, result.errors


def test_update_source_connection_rotates_app_client_id_in_place(org_user_member, permission_resolver):
    """The prod-blocker recovery path: an existing connection got the
    column added empty and we let the operator populate it without
    rotating the secret or re-doing the manifest dance."""
    org, _ = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        display_name="Acme",
        oauth_client_id="3705068",
        app_client_id="",
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    assert conn.needs_client_id is True
    mut = ScmMutation()
    with _ctx(org):
        result = mut.update_source_connection(
            _info(),
            input=UpdateSourceConnectionInput(
                id=GUID(str(conn.guid)),
                app_client_id="Iv23lic8662KXwe4XKEI",
            ),
        )
    assert result.ok, result.errors
    conn.refresh_from_db()
    assert conn.app_client_id == "Iv23lic8662KXwe4XKEI"
    assert conn.needs_client_id is False


def test_update_source_connection_rejects_bad_client_id_shape(org_user_member, permission_resolver):
    org, _ = org_user_member
    permission_resolver.grant(Permission.SCM_CONNECT)
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="3705068",
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    mut = ScmMutation()
    with _ctx(org):
        result = mut.update_source_connection(
            _info(),
            input=UpdateSourceConnectionInput(
                id=GUID(str(conn.guid)),
                app_client_id="totally-bogus",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "appClientId"


# ---------------------------------------------------------------------------
# GraphQL type — needs_client_id + app_client_id exposure
# ---------------------------------------------------------------------------


def test_source_connection_type_exposes_app_client_id_and_needs_flag(org_user_member):
    org, _ = org_user_member
    needs = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        # account_login participates in the unique-per-org/kind/login
        # constraint, so the two rows in this test need distinct logins.
        account_login="acme-needs",
        oauth_client_id="3705068",
        app_client_id="",
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    fixed = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        account_login="acme-fixed",
        display_name="Other",
        oauth_client_id="3705069",
        app_client_id="Iv23lic8662KXwe4XKEI",
        installation_id="988",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    out_needs = source_connection_to_type(needs)
    out_fixed = source_connection_to_type(fixed)
    assert out_needs.app_client_id == ""
    assert out_needs.needs_client_id is True
    assert out_fixed.app_client_id == "Iv23lic8662KXwe4XKEI"
    assert out_fixed.needs_client_id is False


def test_needs_client_id_false_for_non_github_kinds(org_user_member):
    org, _ = org_user_member
    gitlab = SourceConnection.objects.create(
        organization=org,
        kind="gitlab_oauth_app",
        oauth_client_id="gl-id",
        app_client_id="",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    assert gitlab.needs_client_id is False


def test_needs_client_id_false_for_per_user_token_rows(org_user_member):
    """Per-user OAuth-user rows attach to a parent_oauth_app — they
    themselves don't drive the OAuth dance, so the banner shouldn't
    fire on them."""
    org, user = org_user_member
    parent = SourceConnection.objects.create(
        organization=org,
        kind="github_oauth_app",
        oauth_client_id="3705068",
        app_client_id="Iv23lic8662KXwe4XKEI",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    per_user = SourceConnection.objects.create(
        organization=org,
        user=user,
        parent_oauth_app=parent,
        kind="github_oauth_user",
        account_login="alice",
        # ``app_client_id`` blank on per-user rows is the norm.
        app_client_id="",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    assert per_user.needs_client_id is False


# ---------------------------------------------------------------------------
# OAuth dance — github_start reads app_client_id, not oauth_client_id
# ---------------------------------------------------------------------------


def test_github_start_uses_app_client_id_for_authorize_url(org_user_member):
    org, user = org_user_member
    SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="3705068",  # numeric App ID — webhook-payload lookups
        app_client_id="Iv23lic8662KXwe4XKEI",  # what /authorize wants
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    config = SourceConnection.objects.get(organization=org, kind="github_app_install")

    client = Client()
    client.force_login(user)
    resp = client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid), "return_to": "/settings/source-providers"},
    )
    assert resp.status_code == 302
    parsed = urlsplit(resp["Location"])
    assert parsed.netloc == "github.com"
    qs = parse_qs(parsed.query)
    # The key assertion: /authorize gets the Client ID, NOT the App ID.
    assert qs["client_id"] == ["Iv23lic8662KXwe4XKEI"]


def test_github_start_redirects_with_error_when_app_client_id_missing(org_user_member):
    """A connection missing the Client ID surfaces a clean error code
    the FE can toast — rather than emitting an /authorize URL that
    would 404 at github.com."""
    org, user = org_user_member
    config = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="3705068",
        app_client_id="",
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    client = Client()
    client.force_login(user)
    resp = client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid)},
    )
    assert resp.status_code == 302
    assert "scm_error=config_missing_client_id" in resp["Location"]


def test_github_callback_posts_app_client_id_to_token_exchange(org_user_member):
    """The token-exchange POST also needs the Client ID — anything
    else would 404 at /login/oauth/access_token."""
    org, user = org_user_member
    secret_enc = encrypt_at_rest(b"app-install-secret")
    pem_enc = encrypt_at_rest(b"-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n")
    config = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="3705068",
        app_client_id="Iv23lic8662KXwe4XKEI",
        secret_backend_kind=pem_enc.backend_kind,
        secret_ciphertext=pem_enc.backend_ref,
        oauth_client_secret_backend_kind=secret_enc.backend_kind,
        oauth_client_secret_ciphertext=secret_enc.backend_ref,
        installation_id="12345",
        account_login="acme-corp",
        is_active=True,
    )

    client = Client()
    client.force_login(user)
    # Round-trip the start view to seat the state cookie.
    client.get(
        "/app/auth1/scm/github/start",
        {"config_id": str(config.guid)},
    )
    state = client.session["scm_oauth_state"]["state"]

    captured: dict = {}

    def _fake_post(url, payload):
        captured.update(payload)
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
    assert captured["client_id"] == "Iv23lic8662KXwe4XKEI"
    assert captured["client_secret"] == "app-install-secret"


# ---------------------------------------------------------------------------
# GitHub App JWT iss — prefer app_client_id, fall back to oauth_client_id
# ---------------------------------------------------------------------------


def _make_app_install_with_pem(org, *, app_client_id="", oauth_client_id="3705068"):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    enc = encrypt_at_rest(pem)
    return SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id=oauth_client_id,
        app_client_id=app_client_id,
        installation_id="987654321",
        secret_backend_kind=enc.backend_kind,
        secret_ciphertext=enc.backend_ref,
        is_active=True,
    )


def test_jwt_iss_uses_app_client_id_when_present(org_user_member, monkeypatch):
    org, _ = org_user_member
    from astrolift_scm.providers import github_app

    github_app._CACHE.clear()
    conn = _make_app_install_with_pem(org, app_client_id="Iv23lic8662KXwe4XKEI", oauth_client_id="3705068")

    captured = {}

    def fake_exchange(api_base, jwt_token, installation_id):
        captured["jwt"] = jwt_token
        return ("ghs_t", time.time() + 3600)

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app._exchange_for_installation_token",
        fake_exchange,
    )

    github_app.installation_token(conn)

    import jwt as pyjwt

    payload = pyjwt.decode(captured["jwt"], options={"verify_signature": False})
    assert payload["iss"] == "Iv23lic8662KXwe4XKEI"


def test_jwt_iss_falls_back_to_oauth_client_id_with_warning(org_user_member, monkeypatch):
    """Legacy connection (pre-#525) with no app_client_id still mints
    a JWT using the numeric App ID, but logs a deprecation warning so
    observability can flag operators that need to migrate.

    We capture the warning by replacing ``logger.warning`` on the
    provider module rather than via pytest's ``caplog`` fixture —
    ``caplog`` is unavailable when pytest is run with
    ``-p no:logging`` (the CI mode for this repo)."""
    org, _ = org_user_member
    from astrolift_scm.providers import github_app

    github_app._CACHE.clear()
    conn = _make_app_install_with_pem(org, app_client_id="", oauth_client_id="3705068")

    captured: dict = {}
    warnings: list = []

    def fake_exchange(api_base, jwt_token, installation_id):
        captured["jwt"] = jwt_token
        return ("ghs_t", time.time() + 3600)

    def fake_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else msg)

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app._exchange_for_installation_token",
        fake_exchange,
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.logger.warning",
        fake_warning,
    )

    github_app.installation_token(conn)

    import jwt as pyjwt

    payload = pyjwt.decode(captured["jwt"], options={"verify_signature": False})
    assert payload["iss"] == "3705068"
    assert any("deprecated" in w.lower() for w in warnings), warnings


def test_jwt_iss_raises_incomplete_config_when_both_ids_missing(org_user_member):
    org, _ = org_user_member
    from astrolift_scm.providers.github_app import (
        GithubAppError,
        installation_token,
    )

    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="",
        app_client_id="",
        installation_id="987",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        is_active=True,
    )
    with pytest.raises(GithubAppError) as exc:
        installation_token(conn)
    assert exc.value.code == "INCOMPLETE_CONFIG"


# ---------------------------------------------------------------------------
# Webhook-signature path is unaffected — still uses oauth_client_id (App ID)
# ---------------------------------------------------------------------------


def test_webhook_signature_path_unaffected_by_app_client_id(org_user_member):
    """Sanity: adding ``app_client_id`` doesn't disturb the webhook
    signature verification path, which keys off the connection's
    webhook secret (not either ID column). This pins that the existing
    webhook receiver still finds + validates a connection whose
    ``oauth_client_id`` (App ID) stayed where it was."""
    org, _ = org_user_member
    secret_enc = encrypt_at_rest(b"wh-secret-1234")
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        oauth_client_id="3705068",
        app_client_id="Iv23lic8662KXwe4XKEI",
        installation_id="987",
        webhook_secret_backend_kind=secret_enc.backend_kind,
        webhook_secret_ciphertext=secret_enc.backend_ref,
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"x",
        is_active=True,
    )
    # The App ID column is still readable for webhook-payload lookups.
    assert conn.oauth_client_id == "3705068"
    # And the new column carries the OAuth Client ID alongside it
    # without overwriting the App ID.
    assert conn.app_client_id == "Iv23lic8662KXwe4XKEI"
