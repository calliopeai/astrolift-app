"""Real bindings and HTTP identity boundaries for all 27 SCM surfaces."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Policy, RoleBinding
from astrolift_registry.models import AppTeamAccess
from astrolift_scm.models import ScmWebhookInstallation, SourceConnection, SshDeployKey
from astrolift_scm.schema import mutations as mut
from astrolift_scm.schema.queries import ScmQuery
from astrolift_scm.services.ci_workflow_drift import SyncState
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

ORG_ROUTES = [
    "astrolift_source_connections",
    "astrolift_source_connections_page",
    "astrolift_available_repos",
    "astrolift_source_file",
    "connect_source",
    "connect_existing_github_app",
    "update_source_connection",
    "disconnect_source",
    "rotate_webhook_secret",
    "install_scm_webhook",
]
CI_ROUTES = [
    "push_ci_workflow",
    "resync_astrolift_ci_workflow",
    "pull_ci_workflow_from_repo",
    "adopt_repo_ci_workflow",
    "refresh_ci_workflow_sync_status",
    "open_ci_workflow_reconcile_pr",
]
APP_ROUTES = ["generate_ssh_deploy_key", "delete_ssh_deploy_key", *CI_ROUTES]
COLLECTIONS = ["astrolift_ssh_deploy_keys", "astrolift_ssh_deploy_keys_page"]
ROUTES = [*ORG_ROUTES, *APP_ROUTES, *COLLECTIONS]
HTTP_ROUTES = [
    "github/start",
    "github/callback",
    "gitlab/start",
    "gitlab/callback",
    "github/app-manifest/start",
    "github/app-manifest/callback",
    "github/app-manifest/setup",
]
assert len(ROUTES) + len(HTTP_ROUTES) == 27
PERMISSIONS = [
    Permission.SCM_READ,
    Permission.SCM_CONNECT,
    Permission.SCM_DISCONNECT,
    Permission.SCM_KEY_CREATE,
    Permission.SCM_KEY_DELETE,
    Permission.APP_UPDATE,
]


@pytest.fixture(autouse=True)
def no_external_index(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *args: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *args: None))
    settings.DEBUG = False
    settings.APP_BASE_URL = "https://astrolift.test"
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]


def _connection(org, login, *, user=None, kind="github_pat", active=True):
    secret = encrypt_at_rest(b"test-secret")
    return SourceConnection.objects.create(
        organization=org,
        user=user,
        kind=kind,
        account_login=login,
        app_client_id="Iv123ValidClient",
        oauth_client_id="123",
        is_active=active,
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        oauth_client_secret_backend_kind=secret.backend_kind,
        oauth_client_secret_ciphertext=secret.backend_ref,
    )


def _key(org, app, name):
    return SshDeployKey.objects.create(
        organization=org,
        registered_app=app,
        name=name,
        fingerprint_sha256=name,
        public_key="ssh-ed25519 test",
        private_key_ciphertext=b"encrypted",
    )


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld("scm-2109")
    w.user, w.other_user = make_user("scm-2109"), make_user("scm-other-2109")
    w.other_org = Organization.objects.create(name="Foreign", slug="foreign-scm-2109")
    w.connection = _connection(w.org, "acme")
    w.personal = _connection(w.org, "personal", user=w.user, kind="github_oauth_user")
    w.other_personal = _connection(w.org, "other-personal", user=w.other_user, kind="github_oauth_user")
    w.foreign = _connection(w.other_org, "foreign")
    w.own_key, w.sibling_key = _key(w.org, w.medops_app, "own"), _key(w.org, w.platform_app, "sibling")
    w.org_key = _key(w.org, None, "shared-org")
    w.calls = []

    def record(name, result):
        def fn(*args, **kwargs):
            w.calls.append((name, args, kwargs))
            return result

        return fn

    monkeypatch.setattr("astrolift_scm.schema.queries.list_repos", record("repos", []))
    monkeypatch.setattr("astrolift_scm.schema.queries.fetch_file", record("file", "name = 'demo'"))
    monkeypatch.setattr(
        "astrolift_scm.providers.install_webhook",
        record("hook", SimpleNamespace(hook_id="123", webhook_url="https://astrolift.test/hook")),
    )
    monkeypatch.setattr("astrolift_scm.providers.github_app._mint_jwt", record("jwt", "jwt"))
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.list_app_installations",
        record("installs", ([{"id": 123, "account": {"login": "acme"}}], None)),
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app._exchange_for_installation_token", record("token", "token")
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.fetch_app_metadata",
        record("app-meta", ({"slug": "acme-app"}, None)),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        record(
            "push",
            SimpleNamespace(status="updated", commit_sha="sha", pr_url="https://github.com/acme/repo/pull/1"),
        ),
    )
    monkeypatch.setattr("astrolift_scm.services.workflow_sync._render_and_path", lambda app: ("ci", "ci.yml"))
    for name in ["pull_repo_ci_workflow", "adopt_repo_ci_workflow", "evaluate_and_persist_sync_state"]:
        monkeypatch.setattr(
            f"astrolift_scm.services.ci_workflow_drift.{name}", record(name, SyncState.REPO_DRIFT)
        )
    return w


def _grant(w, kind="ORG", *, foreign=False):
    scope_id = (
        w.other_org.pk
        if foreign
        else {
            "ORG": w.org.pk,
            "TEAM": w.medops.pk,
            "PROJECT": w.medops_project.pk,
            "APP": w.medops_app.pk,
        }[kind]
    )
    return bind_role(w.user, permissions=PERMISSIONS, kind=kind, scope_id=scope_id, slug=uuid.uuid4().hex)


def _tenant(w, *, actor=True, selected="own"):
    team, project = (w.medops, w.medops_project) if selected == "own" else (w.platform, w.platform_project)
    return tenant_context(
        TenantContext(
            organization_id=w.org.pk,
            actor_user_id=w.user.pk if actor else None,
            team_id=team.pk,
            project_id=project.pk,
        )
    )


@contextmanager
def _bearer(w, *, team=True, admin=True, foreign=False):
    token = set_current_api_token(
        SimpleNamespace(
            organization_id=w.other_org.pk if foreign else w.org.pk,
            user_id=w.user.pk,
            team_id=w.medops.pk if team else None,
            scopes=["admin"] if admin else [],
        )
    )
    try:
        yield
    finally:
        reset_current_api_token(token)


def _invoke(w, route, *, sibling=False, app_id=None, connection=None, key=None):
    app = w.platform_app if sibling else w.medops_app
    target = connection or w.connection
    key = key or (w.sibling_key if sibling else w.own_key)
    info = make_info(w.user)
    if route.startswith("astrolift_"):
        args = {}
        if route in ["astrolift_available_repos", "astrolift_source_file"]:
            args["connection_id"] = str(target.guid)
        if route == "astrolift_source_file":
            args.update(repo_full_name="acme/repo", path="README.md", ref="main")
        return getattr(ScmQuery(), route)(info, **args)
    inputs = {
        "connect_source": lambda: mut.ConnectSourceInput(
            kind="github_pat", secret_plaintext="new", account_login="new"
        ),
        "connect_existing_github_app": lambda: mut.ConnectExistingGithubAppInput(
            app_id="123", private_key_pem="pem"
        ),
        "update_source_connection": lambda: mut.UpdateSourceConnectionInput(
            id=target.guid, display_name="changed"
        ),
        "disconnect_source": lambda: mut.DisconnectSourceInput(id=target.guid),
        "rotate_webhook_secret": lambda: mut.RotateWebhookSecretInput(connection_id=target.guid),
        "install_scm_webhook": lambda: mut.InstallScmWebhookInput(
            connection_id=target.guid, repo_full_name="acme/repo"
        ),
        "generate_ssh_deploy_key": lambda: mut.GenerateSshDeployKeyInput(name="new", app_slug=app.slug),
        "delete_ssh_deploy_key": lambda: mut.DeleteSshDeployKeyInput(id=key.guid),
        "push_ci_workflow": lambda: mut.PushCiWorkflowInput(
            app_id=app_id or app.guid, connection_id=target.guid
        ),
    }
    input = inputs[route]() if route in inputs else mut.CiWorkflowSyncActionInput(app_id=app_id or app.guid)
    return getattr(mut.ScmMutation(), route)(info, input=input)


def _snapshot():
    from astrolift_registry.models import RegisteredApp

    return [
        list(model.all_objects.order_by("pk").values())
        for model in [SourceConnection, SshDeployKey, ScmWebhookInstallation, RegisteredApp]
    ]


def _denied(w, route, **kwargs):
    before = _snapshot()
    if route.startswith("astrolift_"):
        with pytest.raises(PermissionDenied):
            _invoke(w, route, **kwargs)
    else:
        result = _invoke(w, route, **kwargs)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    assert _snapshot() == before
    assert w.calls == []


@pytest.mark.parametrize("route", ORG_ROUTES)
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP", "FOREIGN_ORG"])
def test_org_resources_cannot_be_authorized_by_a_selected_descendant(world, route, kind):
    _grant(world, "ORG" if kind == "FOREIGN_ORG" else kind, foreign=kind == "FOREIGN_ORG")
    with _tenant(world):
        _denied(world, route)


@pytest.mark.parametrize("route", ORG_ROUTES + APP_ROUTES)
def test_org_binding_reaches_owned_resource_even_with_sibling_selected(world, route):
    _grant(world)
    with _tenant(world, selected="sibling"):
        result = _invoke(world, route)
    if not route.startswith("astrolift_"):
        assert result.ok, result


@pytest.mark.parametrize("route", APP_ROUTES)
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP"])
def test_app_actions_follow_the_object_owner_not_selected_scope(world, route, kind):
    _grant(world, kind)
    with _tenant(world):
        _denied(world, route, sibling=True)
    with _tenant(world, selected="sibling"):
        assert _invoke(world, route).ok


@pytest.mark.parametrize("route", ROUTES)
def test_anonymous_actor_has_no_scm_access(world, route):
    _grant(world)
    with _tenant(world, actor=False):
        _denied(world, route)


@pytest.mark.parametrize("route", ROUTES)
def test_bearer_permission_and_org_ceilings(world, route):
    _grant(world)
    with _tenant(world), _bearer(world, team=False, admin=False):
        _denied(world, route)
    with _tenant(world), _bearer(world, foreign=True):
        _denied(world, route)


@pytest.mark.parametrize("route", ORG_ROUTES)
@pytest.mark.parametrize("operator", [False, True])
def test_team_bearer_cannot_reach_org_rows_even_for_org_owner_or_operator(world, route, operator):
    _grant(world)
    world.user.is_superuser = operator
    world.user.save(update_fields=["is_superuser"])
    with _tenant(world), _bearer(world):
        _denied(world, route)


@pytest.mark.parametrize("route", APP_ROUTES)
@pytest.mark.parametrize("operator", [False, True])
def test_team_bearer_ceiling_is_the_app_owner_even_when_owner_has_org_grant(world, route, operator):
    _grant(world)
    world.user.is_superuser = operator
    world.user.save(update_fields=["is_superuser"])
    with _tenant(world), _bearer(world):
        _denied(world, route, sibling=True)
        assert _invoke(world, route).ok


@pytest.mark.parametrize("route", CI_ROUTES)
@pytest.mark.parametrize("missing", ["invalid", "absent", "foreign", "deleted"])
def test_ci_scope_misses_are_explicit_org_and_do_not_use_selected_team(world, route, missing):
    _grant(world, "TEAM")
    target = "invalid" if missing == "invalid" else uuid.uuid4()
    if missing in ["foreign", "deleted"]:
        app = world.platform_app
        app.organization = world.other_org if missing == "foreign" else world.org
        if missing == "deleted":
            app.soft_delete()
        else:
            app.save(update_fields=["organization", "updated_at", "version"])
        target = app.guid
    gate = getattr(mut.ScmMutation(), route).__astrolift_permission_gate__
    with _tenant(world):
        args = {"input": SimpleNamespace(app_id=target)}
        assert gate.scope(args) == PermissionScope(kind=ScopeKind.ORG, id=world.org.pk)
        _denied(world, route, app_id=target)


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP", "ORG"])
@pytest.mark.parametrize("route", COLLECTIONS)
def test_key_collections_are_filtered_before_pagination_and_count(world, kind, route):
    _grant(world, kind)
    _key(world.other_org, None, "foreign")
    deleted = _key(world.org, world.medops_app, "deleted")
    deleted.soft_delete()
    with _tenant(world, selected="sibling"):
        result = _invoke(world, route)
    expected = {world.own_key.guid}
    if kind == "ORG":
        expected |= {world.sibling_key.guid, world.org_key.guid}
    rows = result.items if route.endswith("_page") else result
    assert {uuid.UUID(str(row.id)) for row in rows} == expected
    if route.endswith("_page"):
        assert result.total_count == len(expected)


@pytest.mark.parametrize("route", COLLECTIONS)
def test_team_bearer_key_collection_hides_sibling_and_shared_org_keys(world, route):
    _grant(world)
    with _tenant(world), _bearer(world):
        result = _invoke(world, route)
    rows = result.items if route.endswith("_page") else result
    assert [str(row.id) for row in rows] == [str(world.own_key.guid)]


@pytest.mark.parametrize("route", ["generate_ssh_deploy_key", "delete_ssh_deploy_key"])
def test_shared_org_keys_require_org_permission_and_cannot_inherit_upward(world, route):
    _grant(world, "TEAM")
    before = _snapshot()
    with _tenant(world):
        result = (
            mut.ScmMutation().generate_ssh_deploy_key(
                make_info(world.user), input=mut.GenerateSshDeployKeyInput(name="shared")
            )
            if route == "generate_ssh_deploy_key"
            else _invoke(world, route, key=world.org_key)
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert _snapshot() == before


@pytest.mark.parametrize("route", ["delete_ssh_deploy_key", "generate_ssh_deploy_key"])
def test_stale_or_missing_key_owner_requires_explicit_org_grant(world, route):
    _grant(world, "TEAM")
    world.medops_app.soft_delete()
    with _tenant(world):
        _denied(world, route)


@pytest.mark.parametrize("level,allowed", [("viewer", False), ("deployer", True), ("owner", True)])
def test_app_shares_keep_their_existing_write_level_semantics(world, level, allowed):
    _grant(world, "TEAM")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    with _tenant(world), _bearer(world):
        if allowed:
            assert _invoke(world, "refresh_ci_workflow_sync_status", sibling=True).ok
        else:
            _denied(world, "refresh_ci_workflow_sync_status", sibling=True)


@pytest.mark.parametrize("route", ["astrolift_available_repos", "astrolift_source_file"])
def test_named_reads_cannot_borrow_another_users_personal_credentials(world, route):
    _grant(world)
    with _tenant(world):
        denied = _invoke(world, route, connection=world.other_personal)
        assert denied.error_code == "NOT_FOUND" and world.calls == []
        assert _invoke(world, route, connection=world.personal).error_code is None


def test_connection_lists_keep_org_plus_own_personal_visibility(world):
    _grant(world)
    with _tenant(world):
        for route in ["astrolift_source_connections", "astrolift_source_connections_page"]:
            result = _invoke(world, route)
            rows = result.items if route.endswith("_page") else result
            assert {str(row.id) for row in rows} == {str(world.connection.guid), str(world.personal.guid)}


@pytest.mark.parametrize("route", APP_ROUTES)
def test_foreign_org_binding_cannot_operate_on_an_app_in_active_org(world, route):
    _grant(world, foreign=True)
    with _tenant(world):
        _denied(world, route)


@pytest.mark.parametrize(
    "route",
    [
        "update_source_connection",
        "disconnect_source",
        "rotate_webhook_secret",
        "install_scm_webhook",
        "astrolift_available_repos",
        "astrolift_source_file",
    ],
)
def test_org_grant_cannot_read_or_change_a_foreign_connection(world, route):
    _grant(world)
    before = _snapshot()
    with _tenant(world):
        result = _invoke(world, route, connection=world.foreign)
    if route.startswith("astrolift_"):
        assert result.error_code == "NOT_FOUND"
    else:
        assert not result.ok and result.errors[0].code == "NOT_FOUND"
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("route", COLLECTIONS)
def test_shared_app_keys_are_visible_only_at_a_share_level_that_carries_scm_read(world, route):
    _grant(world, "TEAM")
    share = AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level="viewer"
    )
    for level, expected in [
        ("viewer", {world.own_key.guid}),
        ("deployer", {world.own_key.guid, world.sibling_key.guid}),
    ]:
        share.access_level = level
        share.save(update_fields=["access_level", "updated_at", "version"])
        with _tenant(world), _bearer(world):
            result = _invoke(world, route)
        rows = result.items if route.endswith("_page") else result
        assert {uuid.UUID(str(row.id)) for row in rows} == expected


def _http_client(w, *, bearer=False, team=True, scopes=None):
    Member.objects.get_or_create(
        user=w.user, scope_kind="ORG", scope_id=w.org.pk, defaults={"is_active": True}
    )
    client = Client()
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid), "HTTP_X_ASTROLIFT_TEAM": str(w.medops.pk)}
    if bearer:
        issued = mint_token()
        ApiToken.objects.create(
            user=w.user,
            organization=w.org,
            team=w.medops if team else None,
            name="http-scm-2109",
            token_hash=issued.token_hash,
            scopes=scopes if scopes is not None else ["admin"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    else:
        client.force_login(w.user)
    return client, headers


@pytest.mark.parametrize("route", HTTP_ROUTES)
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP", "FOREIGN_ORG", "NONE"])
def test_http_connect_routes_require_org_connect_before_session_or_provider_changes(world, route, kind):
    if kind != "NONE":
        _grant(world, "ORG" if kind == "FOREIGN_ORG" else kind, foreign=kind == "FOREIGN_ORG")
    client, headers = _http_client(world)
    session = client.session
    session["scm_oauth_state"] = {"state": "preserve", "kind": "github"}
    session.save()
    before = _snapshot()
    response = client.get(f"/app/auth1/scm/{route}", **headers)
    assert response.status_code == 403, response.content
    assert client.session["scm_oauth_state"]["state"] == "preserve"
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("route", HTTP_ROUTES)
@pytest.mark.parametrize("operator", [False, True])
def test_http_connect_routes_obey_team_bearer_ceiling(world, route, operator):
    _grant(world)
    world.user.is_superuser = operator
    world.user.save(update_fields=["is_superuser"])
    client, headers = _http_client(world, bearer=True)
    before = _snapshot()
    response = client.get(f"/app/auth1/scm/{route}", **headers)
    assert response.status_code == 403
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("route", HTTP_ROUTES)
def test_http_org_bearer_with_permission_can_enter_each_connect_flow(world, route):
    _grant(world)
    client, headers = _http_client(world, bearer=True, team=False)
    response = client.get(f"/app/auth1/scm/{route}", **headers)
    assert response.status_code == (200 if route == "github/app-manifest/start" else 302)


@pytest.mark.parametrize("route", HTTP_ROUTES)
def test_http_read_only_org_bearer_cannot_connect(world, route):
    _grant(world)
    client, headers = _http_client(world, bearer=True, team=False, scopes=["read:apps"])
    before = _snapshot()
    response = client.get(f"/app/auth1/scm/{route}", **headers)
    assert response.status_code == 403
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("revoked", ["user", "membership"])
def test_http_bearer_requires_an_active_owner_and_membership(world, revoked):
    _grant(world)
    client, headers = _http_client(world, bearer=True, team=False)
    if revoked == "user":
        world.user.is_active = False
        world.user.save(update_fields=["is_active"])
    else:
        Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
            is_active=False
        )
    before = _snapshot()
    response = client.get("/app/auth1/scm/github/app-manifest/start", **headers)
    assert response.status_code == 401
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("path", ["github/app-manifest/setup", "github/callback"])
def test_install_callbacks_refuse_stale_state_from_another_org_before_exchange_or_write(
    world, path, monkeypatch
):
    _grant(world)
    world.foreign.kind = "github_app_install"
    world.foreign.is_active = False
    world.foreign.save(update_fields=["kind", "is_active", "updated_at", "version"])
    monkeypatch.setattr(
        "auth1.scm_oauth._resolve_user_token_via_post", lambda *a: pytest.fail("foreign exchange")
    )
    client, headers = _http_client(world)
    session = client.session
    session["scm_github_install_state"] = {"state": "state", "connection_guid": str(world.foreign.guid)}
    session.save()
    before = _snapshot()
    response = client.get(
        f"/app/auth1/scm/{path}",
        {"state": "state", "installation_id": "123", "setup_action": "install", "code": "code"},
        **headers,
    )
    assert response.status_code == 302 and "scm_error=org_mismatch" in response["Location"]
    assert _snapshot() == before


@pytest.mark.parametrize("route", HTTP_ROUTES)
def test_http_anonymous_connect_route_does_not_start_a_dance(world, route):
    before = _snapshot()
    response = Client().get(f"/app/auth1/scm/{route}")
    assert response.status_code == 302 and "login" in response["Location"]
    assert _snapshot() == before and world.calls == []


def test_http_graphql_app_scoped_key_and_collection_obey_real_token_owner(world):
    _grant(world)
    client, headers = _http_client(world, bearer=True)
    query = "mutation($input: GenerateSshDeployKeyInput!) { generateSshDeployKey(input: $input) { ok errors { code } } }"
    for app, allowed in [(world.platform_app, False), (world.medops_app, True)]:
        before = SshDeployKey.objects.count()
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query, "variables": {"input": {"name": "http", "appSlug": app.slug}}},
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        payload = response.json()["data"]["generateSshDeployKey"]
        assert payload["ok"] is allowed
        assert SshDeployKey.objects.count() == before + int(allowed)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": "{ astroliftSshDeployKeysPage { items { name } totalCount } }"},
        content_type="application/json",
        **headers,
    )
    page = response.json()["data"]["astroliftSshDeployKeysPage"]
    assert {row["name"] for row in page["items"]} == {"own", "http"}
    assert page["totalCount"] == 2


@pytest.mark.parametrize("kind", ["TEAM", "ORG"])
def test_http_selected_team_does_not_grant_org_connection_access(world, kind):
    _grant(world, kind)
    client, headers = _http_client(world)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": "{ astroliftSourceConnections { id } }"},
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
        **headers,
    )
    payload = response.json()
    if kind == "TEAM":
        assert payload.get("errors") and payload.get("data") is None
    else:
        assert {row["id"] for row in payload["data"]["astroliftSourceConnections"]} == {
            str(world.connection.guid),
            str(world.personal.guid),
        }


def test_revoked_org_binding_cannot_finish_previously_authorized_connect(world):
    binding = _grant(world)
    config = _connection(world.org, "oauth", kind="github_oauth_app")
    client, headers = _http_client(world)
    response = client.get("/app/auth1/scm/github/start", {"config_id": str(config.guid)}, **headers)
    assert response.status_code == 302 and "github.com" in response["Location"]
    pending = dict(client.session["scm_oauth_state"])
    RoleBinding.objects.filter(pk=binding.pk).update(deleted_at=timezone.now())
    before = _snapshot()
    response = client.get(
        "/app/auth1/scm/github/callback", {"state": pending["state"], "code": "code"}, **headers
    )
    assert response.status_code == 403
    assert client.session["scm_oauth_state"] == pending
    assert _snapshot() == before and world.calls == []


@pytest.mark.parametrize("route", COLLECTIONS)
@pytest.mark.parametrize("kind", ["ORG", "TEAM"])
@pytest.mark.parametrize("scope", ["APP", "PROJECT", "resource"])
def test_key_collections_preserve_scoped_policy_denials_under_owner_and_share_grants(
    world, route, kind, scope
):
    _grant(world, kind)
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="owner")
    Policy.objects.create(
        organization=world.org,
        name="Deny own key metadata",
        slug=f"scm-deny-{uuid.uuid4().hex}",
        effect="DENY",
        action_pattern="scm.read",
        scope_level=scope if scope != "resource" else "ORG",
        scope_id=(
            world.medops_app.pk if scope == "APP" else world.medops_project.pk if scope == "PROJECT" else None
        ),
        resource_pattern={"app_slug": world.medops_app.slug} if scope == "resource" else {},
        actor_pattern={},
        conditions=[],
    )
    with _tenant(world):
        result = _invoke(world, route)
    rows = result.items if route.endswith("_page") else result
    expected = {str(world.sibling_key.guid)}
    if kind == "ORG":
        expected.add(str(world.org_key.guid))
    assert {str(row.id) for row in rows} == expected
    if route.endswith("_page"):
        assert result.total_count == len(expected)


def test_http_team_bearer_shared_key_collection_does_not_restore_a_policy_denied_app(world):
    _grant(world)
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="owner")
    Policy.objects.create(
        organization=world.org,
        name="Deny shared key metadata",
        slug=f"scm-share-deny-{uuid.uuid4().hex}",
        scope_level="APP",
        scope_id=world.platform_app.pk,
        effect="DENY",
        action_pattern="scm.read",
        resource_pattern={},
        actor_pattern={},
        conditions=[],
    )
    client, headers = _http_client(world, bearer=True)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": "{ astroliftSshDeployKeysPage { items { name } totalCount } }"},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200
    page = response.json()["data"]["astroliftSshDeployKeysPage"]
    assert [row["name"] for row in page["items"]] == ["own"]
    assert page["totalCount"] == 1
