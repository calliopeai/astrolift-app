"""Live registry targets, selected scope and credential ceilings (#2105)."""

from contextlib import contextmanager

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
