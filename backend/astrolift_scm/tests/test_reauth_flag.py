"""
Reauth-flag flip on a recoverable auth failure through a per-user
OAuth token (#1171).

When the onboarding repo picker hits a recoverable 401 through a
per-user OAuth token, the resolver flips ``reauth_required`` so the
account drawer + picker surface the amber "Re-authorize" affordance and
the operator can mint a fresh token in place. Org-level rows (PATs, App
installs) and non-recoverable / non-auth failures are left untouched — a
403 or a network blip there is not a stale personal token.

Real Postgres rows; the provider HTTP call is patched at the resolver
seam so we exercise the flag logic, not the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from astrolift_scm.schema.queries import ScmQuery, _flag_reauth_on_user_token
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-reauth")


@pytest.fixture
def viewer():
    return User.objects.create(username="reauth-viewer", email="reauth@acme.test")


def _oauth_app(org):
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_APP,
        display_name="Acme GitHub OAuth",
        oauth_client_id="cid",
        app_client_id="cid",
        is_active=True,
    )


def _user_token(org, viewer, parent, *, kind=SourceConnection.Kind.GITHUB_OAUTH_USER, reauth=False):
    return SourceConnection.objects.create(
        organization=org,
        user=viewer,
        parent_oauth_app=parent,
        kind=kind,
        display_name="GitHub: viewer",
        account_login="viewer",
        is_active=True,
        reauth_required=reauth,
    )


def _recoverable_auth():
    return ProviderError("AUTH_FAILED", "401 unauthorized", recoverable=True)


def _info(user):
    """Strawberry resolvers read ``info.context.request.user``; the
    repo-picker resolver itself only uses the tenant contextvar, but the
    permission gate + shape need a request-ish object."""
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


# ---------------------------------------------------------------------------
# Helper guard matrix
# ---------------------------------------------------------------------------


def test_helper_flags_recoverable_auth_on_user_token(org, viewer):
    parent = _oauth_app(org)
    conn = _user_token(org, viewer, parent)
    _flag_reauth_on_user_token(conn, _recoverable_auth())
    conn.refresh_from_db()
    assert conn.reauth_required is True


def test_helper_flags_gitlab_user_token(org, viewer):
    """The check keys on the ``*_oauth_user`` suffix, so non-GitHub
    per-user tokens flip too."""
    parent = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_APP,
        display_name="Acme GitLab OAuth",
        oauth_client_id="gcid",
        is_active=True,
    )
    conn = _user_token(org, viewer, parent, kind=SourceConnection.Kind.GITLAB_OAUTH_USER)
    _flag_reauth_on_user_token(conn, _recoverable_auth())
    conn.refresh_from_db()
    assert conn.reauth_required is True


def test_helper_ignores_non_recoverable(org, viewer):
    parent = _oauth_app(org)
    conn = _user_token(org, viewer, parent)
    _flag_reauth_on_user_token(conn, ProviderError("AUTH_FAILED", "401", recoverable=False))
    conn.refresh_from_db()
    assert conn.reauth_required is False


def test_helper_ignores_non_auth_code(org, viewer):
    parent = _oauth_app(org)
    conn = _user_token(org, viewer, parent)
    _flag_reauth_on_user_token(conn, ProviderError("API_ERROR", "boom", recoverable=True))
    conn.refresh_from_db()
    assert conn.reauth_required is False


def test_helper_ignores_org_app_install(org):
    """github_app_install is an org-level credential (user=NULL). A 401
    there is an install/permission problem, not a stale personal token."""
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="Acme App install",
        account_login="acme",
        installation_id="123",
        is_active=True,
    )
    _flag_reauth_on_user_token(conn, _recoverable_auth())
    conn.refresh_from_db()
    assert conn.reauth_required is False


def test_helper_ignores_pat(org):
    """A PAT is a shared org credential (user=NULL) with no per-user
    OAuth dance to reconnect through."""
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="shared PAT",
        account_login="acme-bot",
        is_active=True,
    )
    _flag_reauth_on_user_token(conn, _recoverable_auth())
    conn.refresh_from_db()
    assert conn.reauth_required is False


# ---------------------------------------------------------------------------
# Resolver wiring — astrolift_available_repos
# ---------------------------------------------------------------------------


def test_resolver_flags_reauth_on_recoverable_401(org, viewer, permission_resolver):
    parent = _oauth_app(org)
    conn = _user_token(org, viewer, parent)
    permission_resolver.grant(Permission.SCM_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "astrolift_scm.schema.queries.list_repos",
            side_effect=_recoverable_auth(),
        ):
            result = ScmQuery().astrolift_available_repos(_info(viewer), connection_id=str(conn.guid))
    assert result.error_code == "AUTH_FAILED"
    assert result.recoverable is True
    conn.refresh_from_db()
    assert conn.reauth_required is True


def test_resolver_does_not_flag_org_app_install(org, viewer, permission_resolver):
    """A recoverable 401 through an org App-install row must NOT flip the
    per-user reauth flag — the personal OAuth dance wouldn't fix it."""
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="Acme App install",
        account_login="acme",
        installation_id="123",
        is_active=True,
    )
    permission_resolver.grant(Permission.SCM_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "astrolift_scm.schema.queries.list_repos",
            side_effect=_recoverable_auth(),
        ):
            result = ScmQuery().astrolift_available_repos(_info(viewer), connection_id=str(conn.guid))
    assert result.error_code == "AUTH_FAILED"
    conn.refresh_from_db()
    assert conn.reauth_required is False
