"""A project that still has apps, managed resources or secret bundles is not deleted (#2479).

``softDeleteProject`` used to soft-delete a populated project and leave its
apps and project-owned resources pointing at a deleted row, where the nav
tree and the project pages no longer reach them. A shared secret bundle also
drops out of every bundle list with its project while attached agents and
apps keep reading its values. The mutation now refuses with ``PRECONDITION``
until they are moved, deregistered, deprovisioned or deleted. The refusal
comes after the permission gate and the not-found lookup, and counts only
rows in the caller's organization.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.mutations import IdentityMutation, SoftDeleteByGuidInput
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, SecretBundle
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(name="world")
def _world():
    world = ScopeWorld("delete-2479")
    world.user = make_user("delete-2479")
    world.cluster = make_cluster(world, "delete-2479")
    return world


def _project(world, slug):
    return Project.objects.create(organization=world.org, team=world.medops, name=slug, slug=slug)


def _app(world, project, slug, **fields):
    return RegisteredApp.objects.create(
        organization=world.org, team=project.team, project=project, name=slug, slug=slug, **fields
    )


def _resource(world, project, name, **fields):
    return ManagedService.objects.create(
        project=project, tenant_cluster=world.cluster, kind=ManagedService.Kind.POSTGRES, name=name, **fields
    )


def _bundle(world, project, slug):
    return SecretBundle.objects.create(
        organization=project.organization,
        project=project,
        tenant_cluster=world.cluster,
        name=slug,
        slug=slug,
        backend_ref=f"projects/{slug}",
    )


def _delete(world, project):
    with as_tenant(world, world.user):
        return IdentityMutation().soft_delete_project(
            make_info(world.user), input=SoftDeleteByGuidInput(id=str(project.guid))
        )


def _assert_refused(result, message):
    assert result.ok is False
    assert [(error.code, error.message) for error in result.errors] == [("PRECONDITION", message)]


def _assert_live(project):
    project.refresh_from_db()
    assert project.deleted_at is None


def test_a_project_with_an_app_is_refused(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)

    result = _delete(world, world.medops_project)

    _assert_refused(
        result,
        "project still has 1 app; move its apps to another project or deregister them before deleting it",
    )
    _assert_live(world.medops_project)
    world.medops_app.refresh_from_db()
    assert world.medops_app.project_id == world.medops_project.pk
    assert world.medops_app.deleted_at is None


def test_a_project_with_a_managed_resource_is_refused(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "shared-2479")
    _resource(world, project, "db")

    result = _delete(world, project)

    _assert_refused(
        result, "project still has 1 managed resource; deprovision its managed resources before deleting it"
    )
    _assert_live(project)


def test_a_project_with_a_secret_bundle_is_refused(world, permission_resolver):
    # The bundle would vanish from every bundle list with its project while
    # the agents and apps attached to it keep reading its values.
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "keys-2479")
    bundle = _bundle(world, project, "jira")

    result = _delete(world, project)

    _assert_refused(result, "project still has 1 secret bundle; delete its secret bundles before deleting it")
    _assert_live(project)
    bundle.refresh_from_db()
    assert bundle.deleted_at is None


def test_the_refusal_lists_apps_resources_and_bundles(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "full-2479")
    _app(world, project, "api-2479")
    _resource(world, project, "db")
    _bundle(world, project, "jira")
    _bundle(world, project, "slack")

    result = _delete(world, project)

    _assert_refused(
        result,
        "project still has 1 app, 1 managed resource and 2 secret bundles; move its apps to another "
        "project or deregister them, deprovision its managed resources and delete its secret bundles "
        "before deleting it",
    )
    _assert_live(project)


def test_the_refusal_counts_every_live_app_and_resource(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "busy-2479")
    _app(world, project, "api-2479")
    _app(world, project, "web-2479")
    _resource(world, project, "db")
    _resource(world, project, "cache")

    result = _delete(world, project)

    _assert_refused(
        result,
        "project still has 2 apps and 2 managed resources; move its apps to another project or "
        "deregister them and deprovision its managed resources before deleting it",
    )
    _assert_live(project)


def test_rows_still_tearing_down_block_the_delete(world, permission_resolver):
    # A deregister or deprovision in flight has not soft-deleted its row yet;
    # the cloud resources behind it may still exist.
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "leaving-2479")
    _app(world, project, "old-2479", provisioning_status=RegisteredApp.ProvisioningStatus.TEARING_DOWN)
    _resource(world, project, "db", status=ManagedService.Status.DEPROVISIONING)

    result = _delete(world, project)

    _assert_refused(
        result,
        "project still has 1 app and 1 managed resource; move its apps to another project or "
        "deregister them and deprovision its managed resources before deleting it",
    )
    _assert_live(project)


def test_a_project_deletes_once_its_apps_and_resources_are_gone(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "fossilhub-2479")
    moved = _app(world, project, "brain-2479")
    _app(world, project, "retired-2479").soft_delete()
    _resource(world, project, "db").soft_delete()
    gone = _bundle(world, project, "jira")
    # Set deleted_at directly: SecretBundle.soft_delete also starts the
    # workflow that removes the materialized Secrets, which is not under test.
    SecretBundle.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
    moved.project = world.medops_project
    moved.save()

    result = _delete(world, project)

    assert result.ok, result.errors
    assert result.data.deleted is True
    project = Project.all_objects.get(pk=project.pk)
    assert project.deleted_at is not None and project.deleted_by_id == world.user.pk
    moved.refresh_from_db()
    assert moved.project_id == world.medops_project.pk and moved.deleted_at is None


def test_the_permission_gate_answers_before_the_refusal(world, permission_resolver):
    # No grant: the caller learns nothing about what the project holds.
    result = _delete(world, world.medops_project)

    assert result.ok is False
    assert [error.code for error in result.errors] == ["PERMISSION_DENIED"]
    assert "app" not in result.errors[0].message
    _assert_live(world.medops_project)


def test_a_foreign_project_with_apps_reads_as_not_found(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    other = Organization.objects.create(name="Globex", slug="globex-delete-2479")
    team = Team.objects.create(organization=other, name="Ops", slug="ops-delete-2479")
    project = Project.objects.create(organization=other, team=team, name="Ops", slug="ops-delete-2479")
    RegisteredApp.objects.create(organization=other, team=team, project=project, name="Ops", slug="ops-2479")

    result = _delete(world, project)

    assert result.ok is False
    assert [(error.code, error.message) for error in result.errors] == [("NOT_FOUND", "project not found")]
    _assert_live(project)


def test_another_organizations_app_is_not_counted(world, permission_resolver):
    # The count is tenant-scoped like every other read here: a row of another
    # organization that names this project neither blocks nor shows up in it.
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "empty-2479")
    other = Organization.objects.create(name="Globex", slug="globex-count-2479")
    team = Team.objects.create(organization=other, name="Ops", slug="ops-count-2479")
    RegisteredApp.objects.create(
        organization=other, team=team, project=project, name="Stray", slug="stray-2479"
    )

    result = _delete(world, project)

    assert result.ok, result.errors
    assert Project.all_objects.get(pk=project.pk).deleted_at is not None


def test_another_organizations_secret_bundle_is_not_counted(world, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_DELETE)
    project = _project(world, "bare-2479")
    other = Organization.objects.create(name="Globex", slug="globex-bundle-2479")
    SecretBundle.objects.create(
        organization=other,
        project=project,
        tenant_cluster=world.cluster,
        name="Stray",
        slug="stray-2479",
        backend_ref="projects/stray-2479",
    )

    result = _delete(world, project)

    assert result.ok, result.errors
    assert Project.all_objects.get(pk=project.pk).deleted_at is not None
