"""The scope factories the sweep threads through the gates (#1731).

``require_permission(scope=...)`` hands a factory the resolver's bound
arguments and uses what it returns as the scope the permission check runs
against. Three properties have to hold for every one of them, because the
gate is only as good as the weakest factory:

* it resolves the named object to the scope that owns it,
* it never resolves an object outside the caller's org,
* registry and lifecycle misses return an explicit org scope, so a selected
  team cannot substitute for the missing target (#2104, #2105).

The third is the one with teeth. Route params arrive verbatim -- a page
like ``/agents/runs/overview`` sends ``id="overview"`` -- and the factory
runs before the resolver's own not-found handling, so a raw UUID lookup
turns a page that should render empty into a 500.
"""

from __future__ import annotations

import pytest

from astrolift_agents.scopes import agent_workload_app_scope
from astrolift_identity.scopes import (
    project_scope_by_guid,
    team_scope_by_guid,
)
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.scopes import deployment_app_scope, environment_app_scope
from astrolift_registry.models import Workload
from astrolift_registry.scopes import (
    app_scope_by_guid,
    app_scope_by_slug,
    app_scope_by_workload_guid,
)
from core.permissions import Permission, PermissionDenied, ScopeKind, check_permission
from core.scope_args import read_arg, read_guid
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_cluster, make_user

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


@pytest.fixture
def world():
    w = ScopeWorld("sf1731")
    w.cluster = make_cluster(w, "sf1731")
    w.env = AppEnvironment.objects.create(registered_app=w.medops_app, tenant_cluster=w.cluster, name="prod")
    w.deployment = Deployment.objects.create(
        registered_app=w.medops_app, app_environment=w.env, image_tag="v1"
    )
    w.worker = Workload.objects.create(registered_app=w.medops_app, name="Worker", slug="worker-sf1731")
    return w


@pytest.fixture
def reba():
    return make_user("sf1731")


# ---------------------------------------------------------------------
# reading the key out of the bound arguments
# ---------------------------------------------------------------------


def test_read_arg_follows_a_dotted_path_into_an_input_object():
    class _Input:
        app_slug = "qs-ops"

    assert read_arg({"input": _Input()}, "input.app_slug") == "qs-ops"
    assert read_arg({"app_slug": "qs-ops"}, "app_slug") == "qs-ops"


def test_read_arg_reads_an_absent_segment_as_none():
    assert read_arg({}, "input.app_slug") is None
    assert read_arg({"input": None}, "input.app_slug") is None


def test_read_arg_treats_strawberrys_unset_as_absent():
    import strawberry

    assert read_arg({"app_slug": strawberry.UNSET}, "app_slug") is None


def test_read_guid_rejects_a_value_that_is_not_a_uuid():
    """A route param like ``/agents/runs/overview`` must not reach a
    UUIDField lookup -- that raises, and a 500 replaces an empty page."""
    assert read_guid({"id": "overview"}, "id") is None
    assert read_guid({"id": "01920000-0000-7000-8000-000000000000"}, "id") == (
        "01920000-0000-7000-8000-000000000000"
    )


# ---------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------


def test_app_scope_by_slug_resolves_the_app(world, reba):
    with as_tenant(world, reba):
        scope = app_scope_by_slug("app_slug")({"app_slug": world.medops_app.slug})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_app_scope_by_guid_resolves_the_app(world, reba):
    with as_tenant(world, reba):
        scope = app_scope_by_guid("input.id")({"input": type("I", (), {"id": str(world.medops_app.guid)})()})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_workload_guid_resolves_to_its_owning_app(world, reba):
    """Bindings stop at APP, so a workload-keyed gate checks the app."""
    with as_tenant(world, reba):
        scope = app_scope_by_workload_guid("workload_id")({"workload_id": str(world.worker.guid)})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_deployment_guid_resolves_to_its_owning_app(world, reba):
    with as_tenant(world, reba):
        scope = deployment_app_scope("id")({"id": str(world.deployment.guid)})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_environment_guid_resolves_to_its_owning_app(world, reba):
    with as_tenant(world, reba):
        scope = environment_app_scope("id")({"id": str(world.env.guid)})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_team_and_project_guids_resolve_to_their_own_scopes(world, reba):
    with as_tenant(world, reba):
        team = team_scope_by_guid("team_id")({"team_id": str(world.medops.guid)})
        project = project_scope_by_guid("project_id")({"project_id": str(world.medops_project.guid)})
    assert (team.kind, team.id) == (ScopeKind.TEAM, world.medops.id)
    assert (project.kind, project.id) == (ScopeKind.PROJECT, world.medops_project.id)


def test_an_agent_slug_resolves_to_its_owning_app(world, reba):
    bind_role(
        reba,
        permissions=[Permission.AGENT_READ],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="agent-scope",
    )
    with as_tenant(world, reba):
        scope = agent_workload_app_scope("agent_slug")({"agent_slug": world.worker.slug})
    assert scope.kind == ScopeKind.APP
    assert scope.id == world.medops_app.id


def test_an_agent_slug_that_matches_two_apps_requires_an_explicit_org_check(world, reba):
    """Workload slugs are unique per app, not per org. Picking whichever
    row came back first would hand one app's binding authority over
    another's workload."""
    Workload.objects.create(registered_app=world.platform_app, name="Worker", slug=world.worker.slug)
    bind_role(
        reba, permissions=[Permission.AGENT_READ], kind="ORG", scope_id=world.org.pk, slug="agent-scope-org"
    )
    with as_tenant(world, reba):
        scope = agent_workload_app_scope("agent_slug")({"agent_slug": world.worker.slug})
    assert scope.kind == ScopeKind.ORG
    assert scope.id == world.org.pk


# ---------------------------------------------------------------------
# the two ways a factory must decline
# ---------------------------------------------------------------------


def test_an_object_in_another_org_resolves_to_nothing(world, reba):
    elsewhere = ScopeWorld("sf1731b")
    with as_tenant(world, reba):
        assert app_scope_by_slug("app_slug")({"app_slug": elsewhere.medops_app.slug}).kind == ScopeKind.ORG
        assert app_scope_by_guid("app_id")({"app_id": str(elsewhere.medops_app.guid)}).kind == ScopeKind.ORG


@pytest.mark.parametrize(
    "key",
    ["", None, "overview", "01920000-0000-7000-8000-000000000000"],
)
def test_an_unresolvable_lifecycle_key_requires_org_authority(world, reba, key):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="selected-team-read",
    )
    with tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=reba.pk,
            team_id=world.medops.pk,
            project_id=world.medops_project.pk,
        )
    ):
        check_permission(
            Permission.APP_READ,
            scope=environment_app_scope("id", permission=Permission.APP_READ)({"id": str(world.env.guid)}),
        )
        for factory in (deployment_app_scope, environment_app_scope):
            scope = factory("id", permission=Permission.APP_READ)({"id": key})
            assert (scope.kind, scope.id) == (ScopeKind.ORG, world.org.pk)
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ, scope=scope)
