"""A selected team/project must not authorize a different explicit object."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity.permission_resolver import (
    resolve,
    resolve_effective_permissions,
    resolve_effective_permissions_for_apps,
)
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def world():
    return ScopeWorld("1743")


@pytest.fixture
def user():
    return make_user("1743")


def _object(world, kind, side="medops"):
    return (
        getattr(world, side + {"APP": "_app", "PROJECT": "_project", "TEAM": "", "ORG": ""}[kind])
        if kind != "ORG"
        else world.org
    )


def _bind(user, world, kind, side="medops"):
    return bind_role(
        user,
        permissions=[Permission.APP_READ],
        kind=kind,
        scope_id=_object(world, kind, side).pk,
        slug=f"read-{world.org.slug}-{kind}-{side}",
    )


def _tenant(world, user, selected_kind=None):
    fields = {"organization_id": world.org.pk, "actor_user_id": user.pk}
    if selected_kind == "TEAM":
        fields["team_id"] = world.medops.pk
    if selected_kind == "PROJECT":
        fields["team_id"] = world.medops.pk
        fields["project_id"] = world.medops_project.pk
    return TenantContext(**fields)


def _assert_access(tenant, kind, target, expected):
    scope = PermissionScope(kind=ScopeKind(kind), id=target.pk)
    assert resolve(tenant, Permission.APP_READ, scope)[0] is expected
    single = resolve_effective_permissions(tenant, extra_scope=(kind, target.pk))
    assert (Permission.APP_READ.value in single) is expected
    if kind == "APP":
        bulk = resolve_effective_permissions_for_apps(tenant, [target])
        assert (Permission.APP_READ.value in bulk[target.pk]) is expected


@pytest.mark.parametrize("selected_kind", ["TEAM", "PROJECT"])
def test_selected_scope_does_not_open_a_sibling_app_at_resolver_entry(world, user, selected_kind):
    _bind(user, world, selected_kind)
    with tenant_context(_tenant(world, user, selected_kind)), pytest.raises(PermissionDenied):
        RegistryQuery().astrolift_app(make_info(user), slug=world.platform_app.slug)


@pytest.mark.parametrize("selected_kind", ["TEAM", "PROJECT"])
@pytest.mark.parametrize("target_kind", ["APP", "PROJECT", "TEAM"])
def test_explicit_target_does_not_inherit_selected_scope(world, user, selected_kind, target_kind):
    _bind(user, world, selected_kind)
    target = _object(world, target_kind, "platform")
    scope = PermissionScope(kind=ScopeKind(target_kind), id=target.pk)
    allowed, _ = resolve(_tenant(world, user, selected_kind), Permission.APP_READ, scope)
    assert not allowed


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_valid_own_and_ancestor_grants_still_authorize_the_target(world, user, kind):
    _bind(user, world, kind)
    tenant = TenantContext(
        organization_id=world.org.pk,
        actor_user_id=user.pk,
        team_id=world.platform.pk,
        project_id=world.platform_project.pk,
    )
    _assert_access(tenant, "APP", world.medops_app, True)


@pytest.mark.parametrize(
    ("target_kind", "grant_kind"),
    [
        ("PROJECT", "PROJECT"),
        ("PROJECT", "TEAM"),
        ("PROJECT", "ORG"),
        ("TEAM", "TEAM"),
        ("TEAM", "ORG"),
        ("ORG", "ORG"),
    ],
)
def test_explicit_parent_scopes_keep_own_and_ancestor_grants(world, user, target_kind, grant_kind):
    _bind(user, world, grant_kind)
    _assert_access(_tenant(world, user), target_kind, _object(world, target_kind), True)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_foreign_explicit_scope_cannot_use_a_binding_in_that_foreign_org(world, user, kind):
    foreign = ScopeWorld("1743-foreign")
    _bind(user, foreign, kind)
    _assert_access(_tenant(world, user), kind, _object(foreign, kind), False)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_missing_explicit_target_cannot_fall_back_to_an_org_grant(world, user, kind):
    _bind(user, world, "ORG")
    scope = PermissionScope(kind=ScopeKind(kind), id=2147483647)
    assert not resolve(_tenant(world, user), Permission.APP_READ, scope)[0]
    assert resolve_effective_permissions(_tenant(world, user), extra_scope=(kind, scope.id)) == set()


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_deleted_target_cannot_fall_back_to_an_org_grant(world, user, kind):
    _bind(user, world, "ORG")
    target = _object(world, kind)
    # Keep the instance stale, as a caller may already have loaded it.
    type(target).objects.filter(pk=target.pk).update(deleted_at=timezone.now())
    _assert_access(_tenant(world, user), kind, target, False)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM"])
def test_deleted_organization_does_not_authorize_its_live_descendants(world, user, kind):
    _bind(user, world, "ORG")
    type(world.org).objects.filter(pk=world.org.pk).update(deleted_at=timezone.now())
    _assert_access(_tenant(world, user), kind, _object(world, kind), False)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_explicit_target_requires_an_active_organization(world, user, kind):
    _bind(user, world, kind)
    _assert_access(TenantContext(actor_user_id=user.pk), kind, _object(world, kind), False)


@pytest.mark.parametrize(
    ("target_kind", "parent_kind"), [("APP", "PROJECT"), ("APP", "TEAM"), ("PROJECT", "TEAM")]
)
@pytest.mark.parametrize("invalid_parent", ["foreign", "deleted"])
def test_invalid_ancestor_cannot_grant_access_through_a_stale_link(
    world, user, target_kind, parent_kind, invalid_parent
):
    parent_world = ScopeWorld("1743-parent") if invalid_parent == "foreign" else world
    _bind(user, parent_world, parent_kind)
    target = _object(world, target_kind)
    parent = _object(parent_world, parent_kind)
    if invalid_parent == "foreign":
        type(target).objects.filter(pk=target.pk).update(**{parent_kind.lower() + "_id": parent.pk})
    else:
        type(parent).objects.filter(pk=parent.pk).update(deleted_at=timezone.now())
    _assert_access(_tenant(world, user), target_kind, target, False)

    _bind(user, world, target_kind)
    _assert_access(_tenant(world, user), target_kind, target, True)


@pytest.mark.parametrize("selected_kind", ["TEAM", "PROJECT"])
def test_targetless_checks_keep_selected_context_semantics(world, user, selected_kind):
    _bind(user, world, selected_kind)
    tenant = _tenant(world, user, selected_kind)
    assert resolve(tenant, Permission.APP_READ, None)[0]
    assert Permission.APP_READ.value in resolve_effective_permissions(tenant)
    _assert_access(tenant, "ORG", world.org, False)


@pytest.mark.parametrize("selected_kind", ["TEAM", "PROJECT"])
def test_single_and_bulk_viewer_permissions_match_the_object_gate(world, user, selected_kind):
    _bind(user, world, selected_kind)
    tenant = _tenant(world, user, selected_kind)
    apps = [world.medops_app, world.platform_app]
    bulk = resolve_effective_permissions_for_apps(tenant, apps)
    for app in apps:
        scope = PermissionScope(kind=ScopeKind.APP, id=app.pk)
        single = resolve_effective_permissions(tenant, extra_scope=("APP", app.pk))
        allowed = resolve(tenant, Permission.APP_READ, scope)[0]
        expected = app.pk == world.medops_app.pk
        assert allowed is expected
        assert (Permission.APP_READ.value in single) is expected
        assert (Permission.APP_READ.value in bulk[app.pk]) is expected


def test_bulk_permissions_do_not_project_a_foreign_org_binding(world, user):
    foreign = ScopeWorld("1743-bulk-foreign")
    _bind(user, foreign, "APP")
    result = resolve_effective_permissions_for_apps(_tenant(world, user), [foreign.medops_app])
    assert result[foreign.medops_app.pk] == set()


def test_bulk_permissions_read_current_ownership_after_an_app_moves(world, user):
    _bind(user, world, "TEAM")
    stale = world.medops_app
    type(stale).objects.filter(pk=stale.pk).update(
        team_id=world.platform.pk, project_id=world.platform_project.pk
    )
    _assert_access(_tenant(world, user), "APP", stale, False)


def test_bulk_permission_queries_do_not_scale_with_app_count(world, user):
    _bind(user, world, "ORG")
    tenant = _tenant(world, user)
    with CaptureQueriesContext(connection) as one:
        resolve_effective_permissions_for_apps(tenant, [world.medops_app])
    with CaptureQueriesContext(connection) as two:
        results = resolve_effective_permissions_for_apps(tenant, [world.medops_app, world.platform_app])
    assert len(one) == len(two)
    assert all(permissions == {Permission.APP_READ.value} for permissions in results.values())
