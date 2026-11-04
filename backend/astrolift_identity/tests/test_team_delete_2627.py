"""A team that still has projects or apps is not deleted (#2627).

``softDeleteTeam`` used to soft-delete a populated team. Its projects and apps
kept pointing at the deleted row, and the routes and lists that walk live
owners (``_resolve_project``, ``live_projects``, ``live_app_owners``) dropped
them, so they could no longer be renamed, moved, deregistered or deleted. The
mutation now refuses with ``PRECONDITION`` until they are moved, deregistered or
deleted, as ``softDeleteProject`` does for a project's apps (#2479), and also
names the managed resources and shared secret bundles its projects still own.
The refusal comes after the permission gate and the not-found lookup, and counts
only rows in the caller's organization.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.mutations import IdentityMutation, SoftDeleteByGuidInput
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, SecretBundle
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(name="world")
def _world():
    world = ScopeWorld("delete-2627")
    world.user = make_user("delete-2627")
    world.cluster = make_cluster(world, "delete-2627")
    return world


def _team(world, slug):
    return Team.objects.create(organization=world.org, name=slug, slug=slug)


def _project(team, slug):
    return Project.objects.create(organization=team.organization, team=team, name=slug, slug=slug)


def _app(team, slug, project=None, **fields):
    return RegisteredApp.objects.create(
        organization=team.organization, team=team, project=project, name=slug, slug=slug, **fields
    )


def _resource(world, project, name, **fields):
    return ManagedService.objects.create(
        project=project, tenant_cluster=world.cluster, kind=ManagedService.Kind.POSTGRES, name=name, **fields
    )


def _bundle(world, project, slug, organization=None):
    return SecretBundle.objects.create(
        organization=organization or project.organization,
        project=project,
        tenant_cluster=world.cluster,
        name=slug,
        slug=slug,
        backend_ref=f"projects/{slug}",
    )


def _delete(world, team):
    with as_tenant(world, world.user):
        return IdentityMutation().soft_delete_team(
            make_info(world.user), input=SoftDeleteByGuidInput(id=str(team.guid))
        )


def _assert_refused(result, message):
    assert result.ok is False
    assert [(error.code, error.message) for error in result.errors] == [("PRECONDITION", message)]


def _assert_live(team):
    team.refresh_from_db()
    assert team.deleted_at is None


def test_a_team_with_a_project_and_an_app_is_refused(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)

    result = _delete(world, world.medops)

    _assert_refused(
        result,
        "team still has 1 app and 1 project; move its apps to another team or deregister them and "
        "delete its projects before deleting it",
    )
    _assert_live(world.medops)
    world.medops_project.refresh_from_db()
    assert world.medops_project.deleted_at is None
    world.medops_app.refresh_from_db()
    assert world.medops_app.team_id == world.medops.pk and world.medops_app.deleted_at is None


def test_an_app_outside_any_project_is_refused(world, permission_resolver):
    # An unassigned app hangs off the team alone; no project refusal covers it.
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "loose-2627")
    _app(team, "loose-api-2627")

    result = _delete(world, team)

    _assert_refused(
        result, "team still has 1 app; move its apps to another team or deregister them before deleting it"
    )
    _assert_live(team)


def test_an_empty_project_is_refused(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "planned-2627")
    _project(team, "later-2627")

    result = _delete(world, team)

    _assert_refused(result, "team still has 1 project; delete its projects before deleting it")
    _assert_live(team)


def test_the_refusal_names_what_its_projects_own(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "full-2627")
    project = _project(team, "shop-2627")
    _app(team, "api-2627", project=project)
    _app(team, "cron-2627")
    _resource(world, project, "db")
    _bundle(world, project, "jira")
    _bundle(world, project, "slack")

    result = _delete(world, team)

    _assert_refused(
        result,
        "team still has 2 apps, 1 managed resource, 2 secret bundles and 1 project; move its apps to "
        "another team or deregister them, deprovision its managed resources, delete its secret bundles "
        "and delete its projects before deleting it",
    )
    _assert_live(team)


def test_rows_still_tearing_down_block_the_delete(world, permission_resolver):
    # A deregister or deprovision in flight has not soft-deleted its row yet;
    # the cloud resources behind it may still exist.
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "leaving-2627")
    project = _project(team, "old-2627")
    _app(team, "old-api-2627", provisioning_status=RegisteredApp.ProvisioningStatus.TEARING_DOWN)
    _resource(world, project, "db", status=ManagedService.Status.DEPROVISIONING)

    result = _delete(world, team)

    _assert_refused(
        result,
        "team still has 1 app, 1 managed resource and 1 project; move its apps to another team or "
        "deregister them, deprovision its managed resources and delete its projects before deleting it",
    )
    _assert_live(team)


def test_a_team_deletes_once_its_projects_and_apps_are_gone(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "fossilhub-2627")
    gone = _project(team, "gone-2627")
    # Left behind by a project deleted before #2479. Nothing lists it any
    # more and deleting the team changes nothing for it, so it does not block.
    _resource(world, gone, "db")
    gone.soft_delete()
    _app(team, "retired-2627").soft_delete()
    moved = _app(team, "brain-2627")
    moved.team = world.platform
    moved.save()

    result = _delete(world, team)

    assert result.ok, result.errors
    assert result.data.deleted is True
    team = Team.all_objects.get(pk=team.pk)
    assert team.deleted_at is not None and team.deleted_by_id == world.user.pk
    moved.refresh_from_db()
    assert moved.team_id == world.platform.pk and moved.deleted_at is None


def test_the_permission_gate_answers_before_the_refusal(world, permission_resolver):
    # No grant: the caller learns nothing about what the team holds.
    result = _delete(world, world.medops)

    assert result.ok is False
    assert [error.code for error in result.errors] == ["PERMISSION_DENIED"]
    assert "project" not in result.errors[0].message and "app" not in result.errors[0].message
    _assert_live(world.medops)


def test_a_foreign_team_with_projects_and_apps_reads_as_not_found(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)
    other = Organization.objects.create(name="Globex", slug="globex-delete-2627")
    team = Team.objects.create(organization=other, name="Ops", slug="ops-delete-2627")
    _app(team, "ops-2627", project=_project(team, "ops-delete-2627"))

    result = _delete(world, team)

    assert result.ok is False
    assert [(error.code, error.message) for error in result.errors] == [("NOT_FOUND", "team not found")]
    _assert_live(team)


def test_another_organizations_projects_and_apps_are_not_counted(world, permission_resolver):
    # The counts are tenant-scoped like every other read here: rows of another
    # organization that name this team neither block nor show up in it.
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "empty-2627")
    other = Organization.objects.create(name="Globex", slug="globex-count-2627")
    stray = _project(Team.objects.create(organization=other, name="Ops", slug="ops-count-2627"), "stray-2627")
    # ``Project.save`` copies the team's org onto the row; write the foreign
    # org directly so the row names this team and keeps its own org.
    Project.objects.filter(pk=stray.pk).update(team=team)
    RegisteredApp.objects.create(organization=other, team=team, name="Stray", slug="stray-2627")

    result = _delete(world, team)

    assert result.ok, result.errors
    assert Team.all_objects.get(pk=team.pk).deleted_at is not None


def test_another_organizations_bundle_in_its_project_is_not_counted(world, permission_resolver):
    permission_resolver.grant(Permission.TEAM_DELETE)
    team = _team(world, "bare-2627")
    project = _project(team, "bare-2627")
    other = Organization.objects.create(name="Globex", slug="globex-bundle-2627")
    _bundle(world, project, "stray-2627", organization=other)

    result = _delete(world, team)

    _assert_refused(result, "team still has 1 project; delete its projects before deleting it")
    _assert_live(team)
