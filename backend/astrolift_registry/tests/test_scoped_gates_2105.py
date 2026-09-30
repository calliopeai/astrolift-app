"""Live registry targets, selected scope and credential ceilings (#2105)."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_registry.models import AppTeamAccess, Workload
from astrolift_registry.scopes import (
    app_scope_by_guid,
    app_scope_by_slug,
    app_scope_by_workload_guid,
    app_scope_by_workload_slug,
)
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    world = ScopeWorld("registry2105")
    world.user = make_user("registry2105")
    world.workload = Workload.objects.create(registered_app=world.medops_app, name="Web", slug="web")
    return world


@contextmanager
def _tenant(world, *, token_team=None, token_org=None, scopes=("admin",)):
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        token = None
        if token_team is not None or token_org is not None:
            token = set_current_api_token(
                ApiToken.objects.create(
                    user=world.user,
                    name="regression token",
                    token_hash="hash",
                    organization_id=token_org or world.org.pk,
                    team_id=token_team,
                    scopes=list(scopes),
                )
            )
        try:
            yield
        finally:
            if token is not None:
                reset_current_api_token(token)


@pytest.mark.parametrize(
    "factory,key",
    [
        (app_scope_by_slug, "app_slug"),
        (app_scope_by_guid, "app_id"),
        (app_scope_by_workload_guid, "workload_id"),
        (app_scope_by_workload_slug, "workload_slug"),
    ],
)
@pytest.mark.parametrize("value", [None, "", "missing", "01920000-0000-7000-8000-000000000000"])
def test_missing_targets_never_inherit_selected_team(world, factory, key, value):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    with _tenant(world):
        scope = factory(key)({key: value})
        assert scope == PermissionScope(ScopeKind.ORG, world.org.pk)
        with pytest.raises(PermissionDenied):
            check_permission(Permission.APP_READ, scope=scope)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_live_app_binding_and_sibling_isolation(world, kind):
    own = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    bind_role(world.user, permissions=[Permission.APP_READ], kind=kind, scope_id=own, slug="reader")
    with _tenant(world):
        check_permission(Permission.APP_READ, scope=app_scope_by_slug()({"app_slug": world.medops_app.slug}))
        sibling = app_scope_by_slug()({"app_slug": world.platform_app.slug})
        if kind == "ORG":
            check_permission(Permission.APP_READ, scope=sibling)
        else:
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ, scope=sibling)


def test_workload_miss_deleted_parent_and_ambiguous_slug_require_org(world):
    Workload.objects.create(registered_app=world.platform_app, name="Other", slug="web")
    with _tenant(world):
        assert app_scope_by_workload_slug()({"workload_slug": "web"}).kind == ScopeKind.ORG
        world.medops_app.deleted_at = timezone.now()
        world.medops_app.save()
        assert app_scope_by_workload_guid()({"workload_id": str(world.workload.guid)}).kind == ScopeKind.ORG


@pytest.mark.parametrize("permission", [Permission.APP_READ, Permission.APP_UPDATE, Permission.APP_READ_LOGS])
def test_team_bearer_cannot_borrow_users_org_grant_for_sibling_or_miss(world, permission):
    bind_role(world.user, permissions=[permission], kind="ORG", scope_id=world.org.pk, slug="admin")
    factory = app_scope_by_guid(permission=permission)
    with _tenant(world, token_team=world.medops.pk):
        check_permission(permission, scope=factory({"app_id": str(world.medops_app.guid)}))
        for key in [str(world.platform_app.guid), None, "missing"]:
            with pytest.raises(PermissionDenied):
                factory({"app_id": key})


@pytest.mark.parametrize(
    "level,permission,allowed",
    [
        ("viewer", Permission.APP_READ, True),
        ("viewer", Permission.APP_UPDATE, False),
        ("viewer", Permission.APP_READ_LOGS, False),
        ("deployer", Permission.APP_UPDATE, True),
        ("owner", Permission.APP_READ_LOGS, True),
    ],
)
def test_bearer_shared_apps_follow_the_actual_permission(world, level, permission, allowed):
    bind_role(world.user, permissions=[permission], kind="ORG", scope_id=world.org.pk, slug="admin")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    with _tenant(world, token_team=world.medops.pk):
        factory = app_scope_by_slug(permission=permission)
        if allowed:
            check_permission(permission, scope=factory({"app_slug": world.platform_app.slug}))
        else:
            with pytest.raises(PermissionDenied):
                factory({"app_slug": world.platform_app.slug})


def test_foreign_credential_and_foreign_target_fail_closed(world):
    foreign = ScopeWorld("foreignregistry2105")
    with _tenant(world):
        assert app_scope_by_guid()({"app_id": str(foreign.medops_app.guid)}).kind == ScopeKind.ORG
    with _tenant(world, token_org=foreign.org.pk):
        with pytest.raises(PermissionDenied):
            app_scope_by_slug(permission=Permission.APP_READ)({"app_slug": world.medops_app.slug})


@pytest.mark.parametrize("owner", ["project", "team", "foreign_project", "foreign_team", "incoherent"])
def test_stale_owners_require_org_even_when_another_ancestor_is_live(world, owner):
    foreign = ScopeWorld("stale2105")
    if owner == "project":
        world.medops_project.deleted_at = timezone.now()
        world.medops_project.save()
    elif owner in ("team", "project_team"):
        world.medops.deleted_at = timezone.now()
        world.medops.save()
        if owner == "project_team":
            world.medops_app.team = None
            world.medops_app.save()
    else:
        setattr(
            world.medops_app,
            "project" if owner == "foreign_project" else "team",
            foreign.medops_project
            if owner == "foreign_project"
            else foreign.medops
            if owner == "foreign_team"
            else world.platform,
        )
        world.medops_app.save()
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    with _tenant(world):
        for scope in [
            app_scope_by_slug()({"app_slug": world.medops_app.slug}),
            app_scope_by_guid()({"app_id": str(world.medops_app.guid)}),
            app_scope_by_workload_guid()({"workload_id": str(world.workload.guid)}),
            app_scope_by_workload_slug()({"workload_slug": world.workload.slug}),
        ]:
            assert scope == PermissionScope(ScopeKind.ORG, world.org.pk)
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ, scope=scope)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("surface", ["apps", "apps_page", "workloads", "workloads_page", "containers"])
def test_collections_filter_before_paging_and_allow_their_owned_rows(world, kind, surface):
    from astrolift_registry.models import Container
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    sibling = Workload.objects.create(registered_app=world.platform_app, name="Sibling", slug="web")
    Container.objects.create(workload=world.workload, name="own")
    Container.objects.create(workload=sibling, name="sibling")
    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    bind_role(world.user, permissions=[Permission.APP_READ], kind=kind, scope_id=scope_id, slug="reader")
    with _tenant(world):
        query = RegistryQuery()
        result = getattr(query, "astrolift_" + surface)(make_info(world.user))
    if surface.endswith("page"):
        assert result.total_count == (2 if kind == "ORG" else 1)
        rows = result.items
    else:
        rows = result
    assert len(rows) == (2 if kind == "ORG" else 1)
    if kind != "ORG":
        assert (
            str(rows[0].id)
            == str(world.workload.guid if surface.startswith("workloads") else world.medops_app.guid)
            if surface != "containers"
            else rows[0].name == "own"
        )


def test_collection_team_bearer_keeps_shared_rows_but_cannot_borrow_org_admin(world):
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    bind_role(world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="admin")
    Workload.objects.create(registered_app=world.platform_app, name="Shared", slug="web")
    with _tenant(world, token_team=world.medops.pk):
        assert len(RegistryQuery().astrolift_workloads(make_info(world.user))) == 1
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="viewer")
    with _tenant(world, token_team=world.medops.pk):
        page = RegistryQuery().astrolift_workloads_page(make_info(world.user))
        assert page.total_count == 2
        assert len(page.items) == 2


def test_team_grant_reads_explicitly_shared_app_in_list_and_direct_query(world):
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="viewer")
    with _tenant(world):
        assert len(RegistryQuery().astrolift_apps(make_info(world.user))) == 2
        assert RegistryQuery().astrolift_app(make_info(world.user), slug=world.platform_app.slug).id == str(
            world.platform_app.guid
        )


def test_ambiguous_container_slug_never_lends_access_to_a_sibling_app(world):
    from astrolift_registry.models import Container
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    sibling = Workload.objects.create(
        registered_app=world.platform_app, name="Sibling", slug=world.workload.slug
    )
    Container.objects.create(workload=world.workload, name="own")
    Container.objects.create(workload=sibling, name="sibling")
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    with _tenant(world):
        rows = RegistryQuery().astrolift_containers(make_info(world.user), workload_slug=world.workload.slug)
    assert [row.name for row in rows] == ["own"]


@pytest.mark.parametrize("surface", ["transfer", "assign"])
def test_org_admin_team_token_cannot_reparent_into_sibling_team(world, surface):
    from astrolift_registry.schema.mutations import (
        AssignAppToProjectInput,
        RegistryMutation,
        TransferAppInput,
    )
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_UPDATE, Permission.APP_TRANSFER, Permission.APP_CREATE],
        kind="ORG",
        scope_id=world.org.pk,
        slug="admin",
    )
    with _tenant(world, token_team=world.medops.pk):
        if surface == "transfer":
            result = RegistryMutation().transfer_app(
                make_info(world.user),
                input=TransferAppInput(
                    app_id=str(world.medops_app.guid), target_project_id=str(world.platform_project.guid)
                ),
            )
        else:
            result = RegistryMutation().assign_astrolift_app_to_project(
                make_info(world.user),
                input=AssignAppToProjectInput(
                    app_slug=world.medops_app.slug, project_guid=str(world.platform_project.guid)
                ),
            )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    world.medops_app.refresh_from_db()
    assert world.medops_app.project_id == world.medops_project.pk
    assert world.medops_app.team_id == world.medops.pk


@pytest.mark.parametrize("destination_grant", [False, True])
def test_transfer_requires_separate_source_and_destination_grants(world, destination_grant):
    from astrolift_registry.schema.mutations import RegistryMutation, TransferAppInput
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_TRANSFER],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="transfer",
    )
    if destination_grant:
        bind_role(
            world.user,
            permissions=[Permission.APP_CREATE],
            kind="PROJECT",
            scope_id=world.platform_project.pk,
            slug="destination",
        )
    with _tenant(world):
        result = RegistryMutation().transfer_app(
            make_info(world.user),
            input=TransferAppInput(
                app_id=str(world.medops_app.guid), target_project_id=str(world.platform_project.guid)
            ),
        )
    assert result.ok is destination_grant
    world.medops_app.refresh_from_db()
    assert world.medops_app.project_id == (
        world.platform_project.pk if destination_grant else world.medops_project.pk
    )


@pytest.mark.parametrize(
    "method,permission",
    [
        ("register_app", Permission.APP_CREATE),
        ("register_app_repo", Permission.APP_CREATE),
        ("register_agent_repo", Permission.AGENT_CREATE),
    ],
)
def test_registration_bearer_cannot_create_in_sibling_project(world, method, permission):
    from astrolift_registry.schema.mutations import RegistryMutation
    from core.tests.utils.scope_world import make_info

    bind_role(world.user, permissions=[permission], kind="ORG", scope_id=world.org.pk, slug="admin")
    with _tenant(world, token_team=world.medops.pk):
        result = getattr(RegistryMutation(), method)(
            make_info(world.user),
            input=SimpleNamespace(project_id=str(world.platform_project.guid), source_repo="acme/repo"),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert world.platform_app.__class__.objects.filter(project=world.platform_project).count() == 1


def test_project_binding_can_register_an_app_in_its_owned_project(world, seed_cluster, monkeypatch):
    from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_CREATE],
        kind="PROJECT",
        scope_id=world.medops_project.pk,
        slug="creator",
    )
    seed_cluster(world.org)
    monkeypatch.setattr(
        "astrolift_registry.schema.mutations.registration._bootstrap_app_environments", lambda app, envs: None
    )
    with _tenant(world, token_team=world.medops.pk, scopes=("app:onboard",)):
        result = RegistryMutation().register_app(
            make_info(world.user),
            input=RegisterAppInput(
                project_id=str(world.medops_project.guid), source_repo="", slug="new-owned"
            ),
        )
    assert result.ok, result.errors
    assert world.medops_app.__class__.objects.filter(
        slug="new-owned", project=world.medops_project, team=world.medops
    ).exists()


def test_read_only_bearer_cannot_update_even_its_owned_app(world):
    from astrolift_registry.schema.mutations import RegistryMutation, UpdateAppInput
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user, permissions=[Permission.APP_UPDATE], kind="ORG", scope_id=world.org.pk, slug="admin"
    )
    with _tenant(world, token_team=world.medops.pk, scopes=("read:apps",)):
        result = RegistryMutation().update_app(
            make_info(world.user), input=UpdateAppInput(id=str(world.medops_app.guid), name="Denied")
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    world.medops_app.refresh_from_db()
    assert world.medops_app.name == "QsOps"


OBJECT_MUTATIONS = [
    "register_app",
    "register_agent_repo",
    "register_app_repo",
    "update_app",
    "set_app_subdomain",
    "soft_delete_app",
    "tear_down_app",
    "transfer_app",
    "update_manifest",
    "apply_staged_manifest",
    "sync_manifest_from_repo",
    "push_manifest_to_repo",
    "move_app_to_team",
    "grant_team_access_to_app",
    "revoke_team_access_from_app",
    "resync_astrolift_manifest_from_repo",
    "assign_astrolift_app_to_project",
    "update_astrolift_security_policy",
    "pause_astrolift_app_webhook_deploys",
    "resume_astrolift_app_webhook_deploys",
    "set_retention_policy",
    "archive_app",
    "restore_app",
]
OBJECT_QUERIES = [
    "astrolift_app",
    "astrolift_app_team_accesses",
    "astrolift_app_team_accesses_page",
    "astrolift_workload",
    "astrolift_workload_scaling_status",
    "astrolift_rendered_manifest",
    "astrolift_workload_manifest",
    "astrolift_app_doctor",
]


def _target_input(world, app, project, team):
    return SimpleNamespace(
        id=str(app.guid),
        app_id=str(app.guid),
        app_slug=app.slug,
        project_id=str(project.guid),
        project_guid=str(project.guid),
        target_project_id=str(project.guid),
        target_team_id=str(team.guid),
        team_id=str(team.guid),
        source_repo="acme/repo",
    )


@pytest.mark.parametrize("method", OBJECT_MUTATIONS)
@pytest.mark.parametrize("team_token", [False, True])
def test_every_object_mutation_refuses_sibling_before_writes(world, method, team_token):
    from astrolift_registry.schema.mutations import RegistryMutation
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=list(Permission),
        kind="ORG" if team_token else "TEAM",
        scope_id=world.org.pk if team_token else world.medops.pk,
        slug="operator",
    )
    original = world.platform_app.__class__.objects.get(pk=world.platform_app.pk)
    with _tenant(world, token_team=world.medops.pk if team_token else None):
        result = getattr(RegistryMutation(), method)(
            make_info(world.user),
            input=_target_input(world, world.platform_app, world.platform_project, world.platform),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    world.platform_app.refresh_from_db()
    assert world.platform_app.version == original.version
    assert world.platform_app.deleted_at is None
    assert world.platform_app.project_id == original.project_id
    assert world.platform_app.manifest_raw == original.manifest_raw


@pytest.mark.parametrize("method", OBJECT_QUERIES)
@pytest.mark.parametrize("team_token", [False, True])
def test_every_object_reader_refuses_sibling_before_fetch(world, method, team_token):
    import inspect

    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_READ],
        kind="ORG" if team_token else "TEAM",
        scope_id=world.org.pk if team_token else world.medops.pk,
        slug="reader",
    )
    resolver = getattr(RegistryQuery(), method)
    target = "slug" if method == "astrolift_app" else "app_slug"
    params = {target: world.platform_app.slug}
    if method != "astrolift_app" and "slug" in inspect.signature(resolver).parameters:
        params["slug"] = "web"
    if "workload_slug" in inspect.signature(resolver).parameters:
        params["workload_slug"] = "web"
    with _tenant(world, token_team=world.medops.pk if team_token else None):
        with pytest.raises(PermissionDenied):
            resolver(make_info(world.user), **params)


@pytest.mark.parametrize("subject", ["team_grant", "team_token", "org_grant"])
def test_org_source_scan_does_not_use_selected_team_authority(world, subject, monkeypatch):
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_READ],
        kind="TEAM" if subject == "team_grant" else "ORG",
        scope_id=world.medops.pk if subject == "team_grant" else world.org.pk,
        slug="reader",
    )
    calls = []
    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync.discover_app_manifests",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(status="ok", apps=[], error=""),
    )
    with _tenant(world, token_team=world.medops.pk if subject == "team_token" else None):
        if subject == "org_grant":
            assert RegistryQuery().scan_app_manifests(make_info(world.user), source_repo="acme/repo").ok
        else:
            with pytest.raises(PermissionDenied):
                RegistryQuery().scan_app_manifests(make_info(world.user), source_repo="acme/repo")
    assert len(calls) == (1 if subject == "org_grant" else 0)


def test_another_orgs_binding_reaches_no_registry_object_or_collection(world):
    from astrolift_registry.schema.queries import RegistryQuery
    from core.tests.utils.scope_world import make_info

    foreign = ScopeWorld("foreignbinding2105")
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=foreign.org.pk, slug="foreign"
    )
    with _tenant(world):
        with pytest.raises(PermissionDenied):
            RegistryQuery().astrolift_app(make_info(world.user), slug=world.medops_app.slug)
        with pytest.raises(PermissionDenied):
            RegistryQuery().astrolift_workloads(make_info(world.user))


def test_org_operator_foreign_app_id_returns_not_found_without_mutation(world):
    from astrolift_registry.schema.mutations import RegistryMutation, UpdateAppInput
    from core.tests.utils.scope_world import make_info

    foreign = ScopeWorld("foreignwrite2105")
    bind_role(
        world.user, permissions=[Permission.APP_UPDATE], kind="ORG", scope_id=world.org.pk, slug="updater"
    )
    with _tenant(world):
        result = RegistryMutation().update_app(
            make_info(world.user), input=UpdateAppInput(id=str(foreign.medops_app.guid), name="Denied")
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    foreign.medops_app.refresh_from_db()
    assert foreign.medops_app.name == "QsOps"


def test_graphql_collection_counts_and_mutation_denial_use_actual_wire_fields(world):
    from config.schema import schema
    from core.tests.utils.scope_world import make_info

    Workload.objects.create(registered_app=world.platform_app, name="Sibling", slug="web")
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_UPDATE],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="operator",
    )
    context = make_info(world.user).context
    with _tenant(world):
        page = schema.execute_sync(
            "{ astroliftWorkloadsPage { totalCount items { name } } }", context_value=context
        )
        denied = schema.execute_sync(
            "mutation($input: UpdateAppInput!) { updateApp(input: $input) { ok errors { code } } }",
            variable_values={"input": {"id": str(world.platform_app.guid), "name": "Denied"}},
            context_value=context,
        )
        allowed = schema.execute_sync(
            "mutation($input: UpdateAppInput!) { updateApp(input: $input) { ok errors { code } data { name } } }",
            variable_values={"input": {"id": str(world.medops_app.guid), "name": "Owned"}},
            context_value=context,
        )
    assert page.errors is None
    assert page.data["astroliftWorkloadsPage"] == {"totalCount": 1, "items": [{"name": "Web"}]}
    assert denied.errors is None
    assert denied.data["updateApp"] == {"ok": False, "errors": [{"code": "PERMISSION_DENIED"}]}
    assert allowed.errors is None
    assert allowed.data["updateApp"]["ok"]
    assert allowed.data["updateApp"]["data"]["name"] == "Owned"
    world.platform_app.refresh_from_db()
    assert world.platform_app.name == "Gateway"


@pytest.mark.parametrize("surface", ["read", "preview", "write"])
@pytest.mark.parametrize("team_token", [False, True])
def test_edge_access_routes_refuse_sibling_app(world, surface, team_token):
    from astrolift_registry.schema.app_access import AppAccessMutation, AppAccessQuery, SetAppAccessInput
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_ACCESS],
        kind="ORG" if team_token else "TEAM",
        scope_id=world.org.pk if team_token else world.medops.pk,
        slug="operator",
    )
    with _tenant(world, token_team=world.medops.pk if team_token else None):
        if surface == "write":
            result = AppAccessMutation().set_app_access(
                make_info(world.user),
                SetAppAccessInput(app_slug=world.platform_app.slug, users=["denied@example.com"]),
            )
            assert not result.ok
            assert result.errors[0].code == "PERMISSION_DENIED"
        else:
            with pytest.raises(PermissionDenied):
                if surface == "read":
                    AppAccessQuery().astrolift_app_access(
                        make_info(world.user), app_slug=world.platform_app.slug
                    )
                else:
                    AppAccessQuery().astrolift_app_access_preview(
                        make_info(world.user), app_slug=world.platform_app.slug, groups=[], users=[]
                    )
    world.platform_app.refresh_from_db()
    assert world.platform_app.edge_access == {}


@pytest.mark.parametrize(
    "method",
    [
        "register_app",
        "register_app_repo",
        "register_agent_repo",
        "transfer_app",
        "assign_astrolift_app_to_project",
    ],
)
@pytest.mark.parametrize("owner", ["deleted_team", "foreign_team"])
def test_org_operator_cannot_create_or_move_into_stale_project_ancestry(world, method, owner):
    from astrolift_registry.schema.mutations import RegistryMutation
    from core.tests.utils.scope_world import make_info

    bind_role(world.user, permissions=list(Permission), kind="ORG", scope_id=world.org.pk, slug="operator")
    if owner == "deleted_team":
        world.platform.deleted_at = timezone.now()
        world.platform.save()
    else:
        foreign = ScopeWorld("foreign-destination")
        world.platform_project.__class__.objects.filter(pk=world.platform_project.pk).update(
            team_id=foreign.medops.pk
        )
        world.platform_project.refresh_from_db()
    before = world.medops_app.__class__.objects.count()
    with _tenant(world):
        result = getattr(RegistryMutation(), method)(
            make_info(world.user),
            input=_target_input(world, world.medops_app, world.platform_project, world.platform),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert world.medops_app.__class__.objects.count() == before
    world.medops_app.refresh_from_db()
    assert world.medops_app.project_id == world.medops_project.pk
    assert world.medops_app.team_id == world.medops.pk
