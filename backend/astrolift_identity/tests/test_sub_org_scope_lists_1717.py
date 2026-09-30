"""Sub-org role bindings have to work on collection resolvers (#1717).

A user whose only binding is TEAM-scoped could not list teams at all,
including her own: ``@require_permission(Permission.TEAM_READ)`` with no
``scope=`` collapses the resolver's candidate scopes to the active org,
so only an ORG-scoped binding can ever match. Granting org admin was the
only thing that made the screen work, which is a large over-grant for
"let her see her own team".

The fix is two halves that have to stay together, and these tests assert
both: the gate accepts a grant held at *any* scope in the org, and the
resolver then narrows its rows to the scopes that grant actually covers.
Either half alone is a bug -- the gate alone hands a team-scoped user
every row in the org, the filter alone still denies her at the door.

Coverage runs downward only, matching the resolver's inheritance walk:
ORG covers everything, TEAM covers that team's projects and apps,
PROJECT covers that project's apps. Holding a permission on a project
never reveals its parent team.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.schema.queries import IdentityQuery, MeType
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied, granted_scopes
from core.tenancy import TenantContext, tenant_context

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


class _World:
    """One org with two teams, a project and an app under each."""

    def __init__(self, slug_suffix: str):
        self.org = Organization.objects.create(name="Acme", slug=f"acme-{slug_suffix}")
        self.medops = Team.objects.create(organization=self.org, name="MedOps", slug=f"medops-{slug_suffix}")
        self.platform = Team.objects.create(
            organization=self.org, name="Platform", slug=f"platform-{slug_suffix}"
        )
        self.medops_project = Project.objects.create(
            organization=self.org, team=self.medops, name="Intake", slug=f"intake-{slug_suffix}"
        )
        self.platform_project = Project.objects.create(
            organization=self.org, team=self.platform, name="Core", slug=f"core-{slug_suffix}"
        )
        self.medops_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.medops,
            project=self.medops_project,
            name="QsOps",
            slug=f"qs-ops-{slug_suffix}",
            provisioning_status="ready",
        )
        self.platform_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.platform,
            project=self.platform_project,
            name="Gateway",
            slug=f"gateway-{slug_suffix}",
            provisioning_status="ready",
        )


@pytest.fixture
def world():
    return _World("1717")


@pytest.fixture
def reba():
    User = get_user_model()
    return User.objects.create(username="reba-1717", email="reba-1717@acme.test")


@pytest.fixture
def info(reba):
    return SimpleNamespace(context=SimpleNamespace(user=reba, request=SimpleNamespace(user=reba)))


def _bind(user, *, permissions, kind, scope_id, slug):
    role = Role.objects.create(
        name=slug,
        slug=slug,
        scope_level=getattr(Role.ScopeLevel, kind),
        permissions=[p.value for p in permissions],
        is_system=False,
    )
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


def _as(world, reba):
    return tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=reba.id))


def _team_slugs(info):
    page = IdentityQuery().astrolift_teams_page(info, limit=50)
    return {t.slug for t in page.items}


def _project_slugs(info):
    page = IdentityQuery().astrolift_projects_page(info, limit=50)
    return {p.slug for p in page.items}


def _app_slugs(info):
    return {a.slug for a in RegistryQuery().astrolift_apps(info)}


# ---------------------------------------------------------------------
# The reported symptom
# ---------------------------------------------------------------------


def test_team_scoped_binding_lists_exactly_that_team(world, reba, info):
    """The bug: this returned nothing until she was made an org admin."""

    _bind(reba, permissions=[Permission.TEAM_READ], kind="TEAM", scope_id=world.medops.id, slug="td-1717")
    with _as(world, reba):
        assert _team_slugs(info) == {world.medops.slug}


def test_no_binding_anywhere_is_still_denied(world, reba, info):
    """The any-scope gate is weaker, not absent."""

    with _as(world, reba), pytest.raises(PermissionDenied):
        _team_slugs(info)


def test_org_scoped_binding_still_lists_every_team(world, reba, info):
    _bind(reba, permissions=[Permission.TEAM_READ], kind="ORG", scope_id=world.org.id, slug="oa-1717")
    with _as(world, reba):
        assert _team_slugs(info) == {world.medops.slug, world.platform.slug}


# ---------------------------------------------------------------------
# Cross-tenant: a binding in another org grants nothing here
# ---------------------------------------------------------------------


def test_team_binding_in_another_org_grants_nothing(world, reba, info):
    elsewhere = _World("1717b")
    _bind(
        reba,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=elsewhere.medops.id,
        slug="td-elsewhere-1717",
    )
    with _as(world, reba), pytest.raises(PermissionDenied):
        _team_slugs(info)


def test_scope_ids_from_another_org_never_reach_the_row_filter(world, reba):
    """Confinement happens in ``granted_scopes``, not only at the query."""

    elsewhere = _World("1717c")
    _bind(
        reba,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=elsewhere.medops.id,
        slug="td-elsewhere-c-1717",
    )
    _bind(
        reba, permissions=[Permission.TEAM_READ], kind="TEAM", scope_id=world.medops.id, slug="td-here-1717"
    )
    with _as(world, reba):
        scopes = granted_scopes(
            TenantContext(organization_id=world.org.id, actor_user_id=reba.id), Permission.TEAM_READ
        )
    assert scopes.team_ids == frozenset({world.medops.id})


# ---------------------------------------------------------------------
# Downward inheritance across the three collection surfaces
# ---------------------------------------------------------------------


def test_team_binding_covers_that_teams_projects(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.PROJECT_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="tp-1717",
    )
    with _as(world, reba):
        assert _project_slugs(info) == {world.medops_project.slug}


def test_project_binding_covers_only_that_project(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.PROJECT_READ],
        kind="PROJECT",
        scope_id=world.platform_project.id,
        slug="pp-1717",
    )
    with _as(world, reba):
        assert _project_slugs(info) == {world.platform_project.slug}


def test_project_binding_does_not_reveal_the_parent_team(world, reba, info):
    """Inheritance runs down, never up.

    The gate lets her in -- she does hold ``team.read`` somewhere -- and
    the row filter is what keeps the parent team out of the result. That
    split is the design: an any-scope gate is never the thing that
    decides which rows a caller sees.
    """

    _bind(
        reba,
        permissions=[Permission.TEAM_READ],
        kind="PROJECT",
        scope_id=world.medops_project.id,
        slug="pt-1717",
    )
    with _as(world, reba):
        assert _team_slugs(info) == set()


def test_app_binding_lists_exactly_that_app(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="ad-1717",
    )
    with _as(world, reba):
        assert _app_slugs(info) == {world.medops_app.slug}


def test_team_binding_covers_that_teams_apps(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.platform.id,
        slug="ta-1717",
    )
    with _as(world, reba):
        assert _app_slugs(info) == {world.platform_app.slug}


def test_org_binding_lists_every_app(world, reba, info):
    _bind(reba, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.id, slug="oaa-1717")
    with _as(world, reba):
        assert _app_slugs(info) == {world.medops_app.slug, world.platform_app.slug}


# ---------------------------------------------------------------------
# Grants are per-permission, not per-scope
# ---------------------------------------------------------------------


def test_a_binding_without_the_permission_grants_no_rows(world, reba, info):
    """Holding *some* role on MedOps is not holding ``team.read`` on it."""

    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="ar-only-1717",
    )
    with _as(world, reba), pytest.raises(PermissionDenied):
        _team_slugs(info)


def test_expired_binding_grants_nothing(world, reba, info):
    from datetime import timedelta

    from django.utils import timezone

    binding = _bind(
        reba,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="exp-1717",
    )
    binding.expires_at = timezone.now() - timedelta(hours=1)
    binding.save(update_fields=["expires_at"])
    with _as(world, reba), pytest.raises(PermissionDenied):
        _team_slugs(info)


# ---------------------------------------------------------------------
# Detail resolvers: the object being acted on is the scope
# ---------------------------------------------------------------------
#
# The list half above is only useful if the row it returns can then be
# opened. These assert the other half: a gate on a request that names one
# app or team runs against that app or team, not against the org.


def test_app_scoped_binding_opens_that_app(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="ad-detail-1717",
    )
    with _as(world, reba):
        app = RegistryQuery().astrolift_app(info, slug=world.medops_app.slug)
    assert app is not None and app.slug == world.medops_app.slug


def test_app_scoped_binding_does_not_open_another_app(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="ad-other-1717",
    )
    with _as(world, reba), pytest.raises(PermissionDenied):
        RegistryQuery().astrolift_app(info, slug=world.platform_app.slug)


@pytest.mark.parametrize("resolver", ["astrolift_workloads", "astrolift_workloads_page"])
def test_team_scoped_binding_opens_that_teams_workloads(world, reba, info, resolver):
    """Collection admission retains only rows covered by the TEAM grant."""

    own = Workload.objects.create(registered_app=world.medops_app, name="Own", slug="own-1717")
    Workload.objects.create(registered_app=world.platform_app, name="Sibling", slug="sibling-1717")

    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="tw-1717",
    )
    with _as(world, reba):
        query = getattr(RegistryQuery(), resolver)
        for slug, expected in (
            (None, [str(own.guid)]),
            (world.medops_app.slug, [str(own.guid)]),
            (world.platform_app.slug, []),
        ):
            result = query(info, app_slug=slug)
            rows = result.items if resolver.endswith("_page") else result
            assert [str(row.id) for row in rows] == expected
            if resolver.endswith("_page"):
                assert result.total_count == len(expected)


@pytest.mark.parametrize("resolver", ["astrolift_workloads", "astrolift_workloads_page"])
def test_workload_collection_without_any_read_grant_is_denied(world, reba, info, resolver):
    Workload.objects.create(registered_app=world.medops_app, name="Own", slug="own-1717")
    with _as(world, reba), pytest.raises(PermissionDenied):
        getattr(RegistryQuery(), resolver)(info, app_slug=world.medops_app.slug)


def test_team_scoped_binding_reads_its_own_team_members(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="tm-1717",
    )
    with _as(world, reba):
        assert IdentityQuery().astrolift_team_members(info, team_id=world.medops.guid) == []
    with _as(world, reba), pytest.raises(PermissionDenied):
        IdentityQuery().astrolift_team_members(info, team_id=world.platform.guid)


def test_scope_is_read_from_a_positional_argument_too(world, reba, info):
    """The scope callable binds the resolver's signature, so it does not
    care whether the caller passed the slug positionally."""

    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="ad-positional-1717",
    )
    with _as(world, reba):
        app = RegistryQuery().astrolift_app(info, world.medops_app.slug)
    assert app is not None and app.slug == world.medops_app.slug


def test_unknown_slug_falls_back_to_the_stricter_org_check(world, reba, info):
    """An app slug that resolves to nothing must not become an open door."""

    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="ad-unknown-1717",
    )
    with _as(world, reba), pytest.raises(PermissionDenied):
        RegistryQuery().astrolift_app(info, slug="no-such-app-1717")


# ---------------------------------------------------------------------
# The capability manifest that decides what the nav renders
# ---------------------------------------------------------------------
#
# Without these the rest of the fix is invisible: the lists would return
# her rows while the shell hid the modules that link to them.


def test_my_permissions_include_a_team_scoped_grant(world, reba, info):
    _bind(
        reba,
        permissions=[Permission.APP_READ, Permission.APP_DEPLOY],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="mp-1717",
    )
    with _as(world, reba):
        slugs = IdentityQuery().astrolift_my_permissions(info)
    assert set(slugs) == {Permission.APP_READ.value, Permission.APP_DEPLOY.value}


def test_my_permissions_exclude_a_grant_from_another_org(world, reba, info):
    elsewhere = _World("1717d")
    _bind(
        reba,
        permissions=[Permission.APP_DEPLOY],
        kind="TEAM",
        scope_id=elsewhere.medops.id,
        slug="mp-elsewhere-1717",
    )
    with _as(world, reba):
        assert IdentityQuery().astrolift_my_permissions(info) == []


def test_modules_light_up_for_a_team_scoped_grant(world, reba, info):
    """A team developer must see the Apps module, not an empty shell."""

    _bind(
        reba,
        permissions=[Permission.APP_READ, Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="mod-1717",
    )
    with _as(world, reba):
        rows = {r.key: r for r in MeType(id="me", profile=None).modules(info)}
    assert rows["apps"].can_view is True
    assert rows["apps"].can_create is True
    assert rows["agents"].can_view is False


# ---------------------------------------------------------------------
# The upward walk a scoped check runs
# ---------------------------------------------------------------------


def test_a_scoped_check_climbs_from_the_object_not_the_tenant(world, reba, info):
    """A gate aimed at one app has to see that app's team.

    ``_candidate_scopes`` used to project the chain from the tenant
    context alone, so a check carrying ``("APP", id)`` saw the app and
    the org and nothing between. A team lead with a TEAM binding was
    denied her own team's app.
    """

    from astrolift_identity.permission_resolver import resolve
    from core.permissions import PermissionScope, ScopeKind

    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="climb-1717",
    )
    tenant = TenantContext(organization_id=world.org.id, actor_user_id=reba.id)
    granted, _ = resolve(
        tenant, Permission.APP_READ, PermissionScope(kind=ScopeKind.APP, id=world.medops_app.id)
    )
    assert granted is True

    denied, _ = resolve(
        tenant, Permission.APP_READ, PermissionScope(kind=ScopeKind.APP, id=world.platform_app.id)
    )
    assert denied is False


def test_the_upward_walk_stops_at_the_org_boundary(world, reba):
    """An app in another org contributes none of its ancestry."""

    from astrolift_identity.permission_resolver import resolve
    from core.permissions import PermissionScope, ScopeKind

    elsewhere = _World("1717e")
    _bind(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=elsewhere.medops.id,
        slug="climb-elsewhere-1717",
    )
    tenant = TenantContext(organization_id=world.org.id, actor_user_id=reba.id)
    granted, _ = resolve(
        tenant, Permission.APP_READ, PermissionScope(kind=ScopeKind.APP, id=elsewhere.medops_app.id)
    )
    assert granted is False
