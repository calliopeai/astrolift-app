"""
BYO GitHub App adopt flow + self-orphan-on-404 (#1126).

Two surfaces are pinned here:

  * ``ScmMutation.connect_existing_github_app`` — adopt an EXISTING GitHub
    App (operator pastes App ID + PEM) instead of creating a new one via the
    manifest Bootstrap flow. The three GitHub HTTP calls (installation
    lookup, installation-token mint, GET /app) are stubbed; the JWT mint is
    real local crypto driven by a throwaway RSA key, so the "bad PEM"
    validation exercises the real signing path.

  * ``providers.github_app.installation_token`` self-orphans a connection
    when the token mint returns a definitive 404 (installation / App deleted
    on GitHub), and does NOT orphan on 401 / network errors.

Real Postgres rows; no real network.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User

from astrolift_identity.models import Member, Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import github_app
from astrolift_scm.providers.github_app import GithubAppError
from astrolift_scm.schema.mutations import ConnectExistingGithubAppInput, ScmMutation
from core.permissions import Permission
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

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


_slug_counter = 0


def _org_user():
    global _slug_counter
    _slug_counter += 1
    org = Organization.objects.create(name="Acme", slug=f"acme-byo-{_slug_counter}")
    user = User.objects.create_user(username=f"byo-tester-{_slug_counter}", password="x")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True)
    return org, user


def _info(user=None):
    if user is None:
        user = SimpleNamespace(is_authenticated=False, pk=None, username="")
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _real_pem() -> str:
    """A genuine 2048-bit RSA private key PEM — the JWT mint signs with it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _install(id_: int, login: str, type_: str = "Organization") -> dict:
    return {"id": id_, "account": {"login": login, "type": type_}}


def _stub_github(
    monkeypatch,
    *,
    installations: list[dict],
    exchange=None,
    slug: str = "astrolift-steadymd",
):
    """Install the three GitHub HTTP calls the adopt flow makes."""

    def _list(api_base, jwt_token):
        return installations, None

    def _default_exchange(api_base, jwt_token, installation_id):
        return ("ghs_installation_token", time.time() + 3600)

    def _meta(api_base, jwt_token):
        return {"slug": slug, "name": slug}, None

    monkeypatch.setattr(github_app, "list_app_installations", _list)
    monkeypatch.setattr(github_app, "_exchange_for_installation_token", exchange or _default_exchange)
    monkeypatch.setattr(github_app, "fetch_app_metadata", _meta)


def _adopt(org, user, **overrides):
    kwargs = {
        "app_id": "3705068",
        "private_key_pem": _real_pem(),
        "client_id": "Iv23lic8662KXwe4XKEI",
    }
    kwargs.update(overrides)
    with _ctx(org):
        return ScmMutation().connect_existing_github_app(
            _info(user), input=ConnectExistingGithubAppInput(**kwargs)
        )


# ---------------------------------------------------------------------------
# Happy path — valid creds create/adopt a connection
# ---------------------------------------------------------------------------


def test_adopt_creates_connection_with_encrypted_pem_and_discovered_install(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    pem = _real_pem()
    _stub_github(monkeypatch, installations=[_install(42, "acme-corp")], slug="astrolift-acme")

    result = _adopt(
        org,
        user,
        private_key_pem=pem,
        client_secret="oauth-cli-secret",
        webhook_secret="wh-secret",
    )

    assert result.ok, result.errors
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    # Discovered from the (stubbed) installation lookup, not from input.
    assert row.installation_id == "42"
    assert row.account_login == "acme-corp"
    assert row.oauth_client_id == "3705068"  # numeric App ID
    assert row.app_client_id == "Iv23lic8662KXwe4XKEI"  # OAuth Client ID
    assert row.display_name == "GitHub App: astrolift-acme"
    assert row.is_active is True
    assert row.is_orphaned is False
    # PEM is stored ENCRYPTED and round-trips back to the pasted key
    # (surrounding whitespace is stripped on store).
    assert bytes(row.secret_ciphertext) != pem.strip().encode("utf-8")
    assert decrypt(
        EncryptedSecret(backend_kind=row.secret_backend_kind, backend_ref=bytes(row.secret_ciphertext))
    ) == pem.strip().encode("utf-8")
    # Optional OAuth client secret + webhook secret are encrypted too.
    assert (
        decrypt(
            EncryptedSecret(
                backend_kind=row.oauth_client_secret_backend_kind,
                backend_ref=bytes(row.oauth_client_secret_ciphertext),
            )
        )
        == b"oauth-cli-secret"
    )
    assert (
        decrypt(
            EncryptedSecret(
                backend_kind=row.webhook_secret_backend_kind,
                backend_ref=bytes(row.webhook_secret_ciphertext),
            )
        )
        == b"wh-secret"
    )


def test_adopt_response_never_exposes_secrets(monkeypatch, permission_resolver):
    """The returned SourceConnectionType carries no secret material and never
    echoes the pasted PEM back."""
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    pem = _real_pem()
    _stub_github(monkeypatch, installations=[_install(1, "acme-corp")])

    result = _adopt(org, user, private_key_pem=pem, webhook_secret="top-secret")

    assert result.ok, result.errors
    data = result.data
    for attr in ("secret_ciphertext", "pem", "private_key_pem", "webhook_secret", "client_secret"):
        assert not hasattr(data, attr), attr
    serialized = " ".join(str(getattr(data, f)) for f in vars(data))
    assert pem not in serialized
    assert "top-secret" not in serialized


def test_adopt_without_client_id_uses_app_id_as_issuer(monkeypatch, permission_resolver):
    """clientId is optional — the App ID alone is a valid JWT issuer."""
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(monkeypatch, installations=[_install(7, "acme-corp")])

    result = _adopt(org, user, client_id=None)

    assert result.ok, result.errors
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.oauth_client_id == "3705068"
    assert row.app_client_id == ""


# ---------------------------------------------------------------------------
# Installation discovery — single vs. multi-install disambiguation
# ---------------------------------------------------------------------------


def test_adopt_picks_installation_by_org_login_when_ambiguous(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(
        monkeypatch,
        installations=[_install(11, "other-org"), _install(22, "acme-corp")],
    )

    result = _adopt(org, user, org_login="acme-corp")

    assert result.ok, result.errors
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.installation_id == "22"
    assert row.account_login == "acme-corp"


def test_adopt_ambiguous_without_org_login_errors_cleanly(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(
        monkeypatch,
        installations=[_install(11, "other-org"), _install(22, "acme-corp")],
    )

    result = _adopt(org, user, org_login=None)

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "orgLogin"
    assert "multiple" in result.errors[0].message
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 0


def test_adopt_explicit_org_login_not_installed_errors(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(monkeypatch, installations=[_install(11, "other-org")])

    result = _adopt(org, user, org_login="acme-corp")

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "orgLogin"
    assert "not installed" in result.errors[0].message


def test_adopt_no_installations_errors(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(monkeypatch, installations=[])

    result = _adopt(org, user)

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert "no installations" in result.errors[0].message
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 0


# ---------------------------------------------------------------------------
# Validation — bad input never stores a broken connection
# ---------------------------------------------------------------------------


def test_adopt_bad_pem_returns_validation_and_stores_nothing(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    # No HTTP stubs needed — a bad PEM fails at the local JWT mint, before
    # any network call. If it 500'd instead, this test would error.
    result = _adopt(
        org, user, private_key_pem="-----BEGIN RSA PRIVATE KEY-----\nnope\n-----END RSA PRIVATE KEY-----\n"
    )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "privateKeyPem"
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 0


def test_adopt_non_numeric_app_id_returns_validation(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    result = _adopt(org, user, app_id="Iv23lic8662KXwe4XKEI")

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "appId"


def test_adopt_malformed_client_id_returns_validation(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    result = _adopt(org, user, client_id="not-a-client-id")

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "clientId"


def test_adopt_token_exchange_failure_returns_error_and_stores_nothing(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)

    def _boom(api_base, jwt_token, installation_id):
        raise GithubAppError(
            "AUTH_FAILED", "GitHub rejected (401): bad jwt", recoverable=True, http_status=401
        )

    _stub_github(monkeypatch, installations=[_install(42, "acme-corp")], exchange=_boom)

    result = _adopt(org, user)

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert "couldn't authenticate" in result.errors[0].message
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 0


# ---------------------------------------------------------------------------
# Permission — deny-by-default
# ---------------------------------------------------------------------------


def test_adopt_denied_without_scm_connect_permission(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.deny(Permission.SCM_CONNECT)
    _stub_github(monkeypatch, installations=[_install(42, "acme-corp")])

    result = _adopt(org, user)

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 0


# ---------------------------------------------------------------------------
# Idempotency — re-adopt updates in place + heals a dead connection
# ---------------------------------------------------------------------------


def test_readopt_same_app_updates_in_place_no_duplicate(monkeypatch, permission_resolver):
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    _stub_github(monkeypatch, installations=[_install(42, "acme-corp")])

    first = _adopt(org, user)
    assert first.ok, first.errors
    first_id = first.data.id

    # Re-adopt with a rotated PEM + new installation id for the same App.
    rotated = _real_pem()
    _stub_github(monkeypatch, installations=[_install(99, "acme-corp")], slug="astrolift-acme")
    second = _adopt(org, user, private_key_pem=rotated)

    assert second.ok, second.errors
    assert second.data.id == first_id  # same row
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 1
    row = SourceConnection.objects.get(organization=org, kind="github_app_install")
    assert row.installation_id == "99"  # rotated in place
    assert decrypt(
        EncryptedSecret(backend_kind=row.secret_backend_kind, backend_ref=bytes(row.secret_ciphertext))
    ) == rotated.strip().encode("utf-8")


def test_adopt_heals_orphaned_connection_for_same_account(monkeypatch, permission_resolver):
    """The steadymd case: a dead auto-created App connection (orphaned by the
    404 self-heal) is re-used + un-orphaned when the real App is adopted for
    the same GitHub account."""
    org, user = _org_user()
    permission_resolver.grant(Permission.SCM_CONNECT)
    dead = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        account_login="acme-corp",
        oauth_client_id="1111111",  # the OLD (deleted) App
        installation_id="500",
        is_active=True,
        is_orphaned=True,
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"stale",
    )
    _stub_github(monkeypatch, installations=[_install(42, "acme-corp")])

    result = _adopt(org, user, app_id="3705068")

    assert result.ok, result.errors
    assert SourceConnection.objects.filter(organization=org, kind="github_app_install").count() == 1
    dead.refresh_from_db()
    assert dead.is_orphaned is False
    assert dead.oauth_client_id == "3705068"  # rotated to the real App
    assert dead.installation_id == "42"


# ---------------------------------------------------------------------------
# Part 2 — self-orphan on 404 during installation-token mint
# ---------------------------------------------------------------------------


def _app_install_conn(org):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    enc = encrypt_at_rest(pem)
    return SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        account_login="acme-corp",
        app_client_id="Iv23lic8662KXwe4XKEI",
        oauth_client_id="3705068",
        installation_id="12345",
        secret_backend_kind=enc.backend_kind,
        secret_ciphertext=enc.backend_ref,
        is_active=True,
    )


def test_installation_token_404_self_orphans_connection(monkeypatch):
    org, _ = _org_user()
    github_app._CACHE.clear()
    conn = _app_install_conn(org)

    def _mint_404(api_base, jwt_token, installation_id):
        raise GithubAppError("AUTH_FAILED", "GitHub rejected (404): gone", recoverable=True, http_status=404)

    monkeypatch.setattr(github_app, "_exchange_for_installation_token", _mint_404)

    with pytest.raises(GithubAppError) as exc:
        github_app.installation_token(conn)
    assert exc.value.http_status == 404

    conn.refresh_from_db()
    assert conn.is_orphaned is True
    assert conn.orphaned_at is not None
    assert "deleted" in conn.orphaned_reason


def test_installation_token_401_does_not_orphan(monkeypatch):
    """A 401 is a recoverable auth failure (clock skew, wrong key) — the App
    might still exist, so we must NOT orphan on it."""
    org, _ = _org_user()
    github_app._CACHE.clear()
    conn = _app_install_conn(org)

    def _mint_401(api_base, jwt_token, installation_id):
        raise GithubAppError(
            "AUTH_FAILED", "GitHub rejected (401): bad jwt", recoverable=True, http_status=401
        )

    monkeypatch.setattr(github_app, "_exchange_for_installation_token", _mint_401)

    with pytest.raises(GithubAppError):
        github_app.installation_token(conn)

    conn.refresh_from_db()
    assert conn.is_orphaned is False


def test_installation_token_network_error_does_not_orphan(monkeypatch):
    org, _ = _org_user()
    github_app._CACHE.clear()
    conn = _app_install_conn(org)

    def _mint_net(api_base, jwt_token, installation_id):
        raise GithubAppError("NETWORK", "Couldn't reach GitHub: timeout")

    monkeypatch.setattr(github_app, "_exchange_for_installation_token", _mint_net)

    with pytest.raises(GithubAppError):
        github_app.installation_token(conn)

    conn.refresh_from_db()
    assert conn.is_orphaned is False
