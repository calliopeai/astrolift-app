"""
Per-user source-provider connection surface (#395).

The account drawer uses ``astrolift_my_connected_accounts`` to render
one chip per provider the org admin has enabled, with the viewer's
own connection state folded in.

These tests exercise the GraphQL resolvers directly (rather than
through the full schema execution path) so we keep them fast while
still hitting the real Postgres database — soft-delete semantics,
unique constraints, and the OAuth-app filter all rely on real DB
behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.connected_accounts import (
    ConnectUserSourceProviderInput,
    DisconnectUserSourceProviderInput,
    MyConnectedAccountsMutation,
    MyConnectedAccountsQuery,
)
from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Profile indexing fires on User.save in some tests; the unit
    surface here doesn't need it and OpenSearch isn't available in
    the test container."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-connacc")


@pytest.fixture
def viewer():
    return User.objects.create(username="viewer@conn", email="viewer@conn.test")


def _info(user=None):
    """Strawberry resolvers read ``info.context.request.user`` to
    identify the viewer."""
    if user is None:
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False, pk=None))
    else:
        # Real User instances already have is_authenticated=True.
        request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _github_oauth_app(org, client_id="cid", login=""):
    # ``client_id`` doubles as ``app_client_id`` for the OAuth dance
    # (see #525) — both columns track the OAuth Client ID for
    # github_oauth_app rows. The test passes the same value through
    # both so a config_id="" empty case still represents "operator
    # hasn't finished setup".
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_APP,
        display_name="Acme GitHub OAuth",
        account_login=login,
        oauth_client_id=client_id,
        app_client_id=client_id,
        oauth_redirect_uri="https://astrolift.test/app/auth1/scm/github/callback",
        is_active=True,
    )


def _gitlab_oauth_app(org, client_id="gcid"):
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_APP,
        display_name="Acme GitLab OAuth",
        oauth_client_id=client_id,
        oauth_redirect_uri="https://astrolift.test/app/auth1/scm/gitlab/callback",
        is_active=True,
    )


def _user_github_token(org, viewer, parent, *, login="viewer-on-github", reauth=False):
    return SourceConnection.objects.create(
        organization=org,
        user=viewer,
        parent_oauth_app=parent,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name=f"GitHub: {login}",
        account_login=login,
        is_active=True,
        reauth_required=reauth,
    )


# ---------------------------------------------------------------------------
# Query — astrolift_my_connected_accounts
# ---------------------------------------------------------------------------


def test_query_empty_when_org_has_no_user_flow_configs(org, viewer):
    q = MyConnectedAccountsQuery()
    with _ctx(org):
        result = q.astrolift_my_connected_accounts(_info(viewer))
    assert result == []


def test_query_returns_one_row_per_enabled_user_flow_config(org, viewer):
    gh = _github_oauth_app(org)
    gl = _gitlab_oauth_app(org)

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    assert {r.provider_kind for r in rows} == {gh.kind, gl.kind}
    # Viewer has no per-user rows yet -> everything not connected.
    for r in rows:
        assert r.is_connected is False
        assert r.linked_account_login is None
        assert r.reauth_required is False


def test_query_reports_connected_state_for_viewer(org, viewer):
    gh = _github_oauth_app(org)
    _user_github_token(org, viewer, gh, login="viewer-on-github")
    _gitlab_oauth_app(org)  # second config without a viewer token

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    by_kind = {r.provider_kind: r for r in rows}
    assert by_kind[gh.kind].is_connected is True
    assert by_kind[gh.kind].linked_account_login == "viewer-on-github"
    assert by_kind[gh.kind].reauth_required is False

    assert by_kind[SourceConnection.Kind.GITLAB_OAUTH_APP].is_connected is False


def test_query_surfaces_reauth_required_state(org, viewer):
    gh = _github_oauth_app(org)
    _user_github_token(org, viewer, gh, login="needs-reauth", reauth=True)

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    [row] = rows
    assert row.is_connected is True  # token row still exists
    assert row.reauth_required is True


def test_query_isolates_viewers(org, viewer):
    """Another user's personal token must not leak as 'connected' to
    this viewer."""
    gh = _github_oauth_app(org)
    other = User.objects.create(username="other@conn", email="other@conn.test")
    _user_github_token(org, other, gh, login="someone-else")

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    [row] = rows
    assert row.is_connected is False
    assert row.linked_account_login is None


def test_query_empty_when_anonymous(org):
    _github_oauth_app(org)
    q = MyConnectedAccountsQuery()
    with _ctx(org):
        result = q.astrolift_my_connected_accounts(_info(user=None))
    assert result == []


def test_query_ignores_soft_deleted_configs(org, viewer):
    gh = _github_oauth_app(org)
    gh.soft_delete()
    _gitlab_oauth_app(org)

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    assert [r.provider_kind for r in rows] == [SourceConnection.Kind.GITLAB_OAUTH_APP]


def test_query_ignores_personal_rows_when_listing_configs(org, viewer):
    """A per-user token (user FK set) must never be surfaced as if it
    were a provider config. Only org-level config rows (user=NULL)
    seed the list."""
    gh = _github_oauth_app(org)
    _user_github_token(org, viewer, gh, login="viewer-on-github")

    q = MyConnectedAccountsQuery()
    with _ctx(org):
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    # Exactly one row (the OAuth-app config), not two.
    assert len(rows) == 1
    assert rows[0].provider_kind == SourceConnection.Kind.GITHUB_OAUTH_APP


# ---------------------------------------------------------------------------
# Mutation — astrolift_connect_user_source_provider
# ---------------------------------------------------------------------------


def test_connect_returns_authorize_url_with_config_id(org, viewer):
    gh = _github_oauth_app(org, client_id="real-client-id")
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_connect_user_source_provider(
            _info(viewer),
            input=ConnectUserSourceProviderInput(provider_config_id=str(gh.guid)),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload is not None
    assert str(payload.provider_config_id) == str(gh.guid)
    assert "/app/auth1/scm/github/start" in payload.authorization_url
    assert f"config_id={gh.guid}" in payload.authorization_url


def test_connect_rejects_unknown_config(org, viewer):
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_connect_user_source_provider(
            _info(viewer),
            input=ConnectUserSourceProviderInput(provider_config_id="00000000-0000-0000-0000-000000000000"),
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"


def test_connect_rejects_pat_config(org, viewer):
    """PATs are org-level shared credentials — there's no per-user
    OAuth flow for them, so the mutation must refuse to start one."""
    pat = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="shared PAT",
        account_login="acme-bot",
        is_active=True,
    )
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_connect_user_source_provider(
            _info(viewer),
            input=ConnectUserSourceProviderInput(provider_config_id=str(pat.guid)),
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"


def test_connect_rejects_config_without_client_id(org, viewer):
    gh = _github_oauth_app(org, client_id="")  # incomplete config
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_connect_user_source_provider(
            _info(viewer),
            input=ConnectUserSourceProviderInput(provider_config_id=str(gh.guid)),
        )
    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"


def test_connect_rejects_anonymous(org):
    gh = _github_oauth_app(org)
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_connect_user_source_provider(
            _info(user=None),
            input=ConnectUserSourceProviderInput(provider_config_id=str(gh.guid)),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Mutation — astrolift_disconnect_user_source_provider
# ---------------------------------------------------------------------------


def test_disconnect_soft_deletes_viewer_row(org, viewer):
    gh = _github_oauth_app(org)
    row = _user_github_token(org, viewer, gh, login="viewer-on-github")

    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="viewer-on-github",
            ),
        )
    assert result.ok, result.errors
    row.refresh_from_db()
    assert row.deleted_at is not None
    assert result.data is not None
    assert str(result.data.disconnected_id) == str(row.guid)


def test_disconnect_requires_typed_confirmation(org, viewer):
    gh = _github_oauth_app(org)
    row = _user_github_token(org, viewer, gh, login="viewer-on-github")

    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="wrong-login",
            ),
        )
    assert result.ok is False
    [err] = result.errors
    assert err.code == "VALIDATION"
    assert err.field == "confirmAccountLogin"
    row.refresh_from_db()
    assert row.deleted_at is None  # untouched


def test_disconnect_typed_confirm_is_case_insensitive(org, viewer):
    gh = _github_oauth_app(org)
    _user_github_token(org, viewer, gh, login="Viewer-On-Github")

    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="viewer-on-github",
            ),
        )
    assert result.ok, result.errors


def test_disconnect_noop_when_viewer_has_no_row(org, viewer):
    gh = _github_oauth_app(org)
    # No per-user token for viewer yet.

    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="",
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.disconnected_id is None


def test_disconnect_only_touches_viewers_own_row(org, viewer):
    """A disconnect from viewer A must not touch user B's row,
    even though both rows point at the same parent_oauth_app config.
    Multi-tenant data isolation at the per-user row layer."""
    gh = _github_oauth_app(org)
    other = User.objects.create(username="other-iso@conn", email="other-iso@conn.test")
    other_row = _user_github_token(org, other, gh, login="someone-else")
    _user_github_token(org, viewer, gh, login="viewer-iso")

    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="viewer-iso",
            ),
        )
    assert result.ok, result.errors
    other_row.refresh_from_db()
    assert other_row.deleted_at is None  # other user's row untouched
    assert other_row.is_active is True


def test_disconnect_rejects_anonymous(org):
    gh = _github_oauth_app(org)
    m = MyConnectedAccountsMutation()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(user=None),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="anything",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_disconnect_clears_account_from_connected_accounts_listing(org, viewer):
    """End-to-end: after disconnect, the query reports is_connected=False."""
    gh = _github_oauth_app(org)
    _user_github_token(org, viewer, gh, login="viewer-roundtrip")

    m = MyConnectedAccountsMutation()
    q = MyConnectedAccountsQuery()
    with _ctx(org):
        result = m.astrolift_disconnect_user_source_provider(
            _info(viewer),
            input=DisconnectUserSourceProviderInput(
                provider_config_id=str(gh.guid),
                confirm_account_login="viewer-roundtrip",
            ),
        )
        assert result.ok, result.errors
        rows = q.astrolift_my_connected_accounts(_info(viewer))

    [row] = rows
    assert row.is_connected is False
    assert row.linked_account_login is None
