# ruff: noqa: F811
"""Real HTTP bearer requests enforce row and destination owners before workflows."""

import pytest

from astrolift_lifecycle.models import DevEnvironment
from astrolift_lifecycle.tests.test_builder_authz_1872_1878 import (
    _bearer,
    _create,
    _dev_env,
    _member,
    _promote,
    _running,
    _sync,
    _untouched,
    workflow_starts,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("route", ["create", "sync", "promote"])
def test_org_role_cannot_widen_a_team_bearer_to_a_sibling_destination(world, workflow_starts, route):
    user = _member(world.a, "owner-2104", ("org_admin", "ORG", world.a.pk))
    headers = {**_bearer(user, world.a, team=world.eng), "HTTP_X_ASTROLIFT_TEAM": str(world.eng.pk)}
    dev = _dev_env(world.a, world.cluster_a, user, team=world.ops if route == "sync" else world.eng)
    before = list(DevEnvironment.objects.values())
    response = {
        "create": lambda: _create(headers, team_slug="ops"),
        "sync": lambda: _sync(dev, headers),
        "promote": lambda: _promote(dev, headers, team_slug="ops"),
    }[route]()
    assert response.status_code == 403, response.content
    assert response.json()["reason"] == "outside_token_team"
    assert list(DevEnvironment.objects.values()) == before
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


@pytest.mark.parametrize("route", ["sync", "promote"])
@pytest.mark.parametrize("owner", ["missing", "deleted", "foreign"])
def test_org_role_team_bearer_cannot_lend_its_team_to_invalid_row_owner(world, workflow_starts, route, owner):
    user = _member(world.a, "stale-owner-2104", ("org_admin", "ORG", world.a.pk))
    team = {"missing": None, "deleted": world.eng, "foreign": world.team_b}[owner]
    dev = _dev_env(world.a, world.cluster_a, user, team=team)
    headers = _bearer(user, world.a, team=world.eng)
    if owner == "deleted":
        world.eng.soft_delete()
    response = _sync(dev, headers) if route == "sync" else _promote(dev, headers, team_slug="ops")
    assert response.status_code == 403, response.content
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


def test_team_bearer_with_org_role_preserves_its_actual_destination(world, workflow_starts):
    user = _member(world.a, "allowed-owner-2104", ("org_admin", "ORG", world.a.pk))
    headers = _bearer(user, world.a, team=world.eng)
    dev = _running(_create(headers))
    assert _sync(dev, headers).status_code == 200
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    response = _promote(dev, headers, team_slug="eng")
    assert response.status_code == 202, response.content
    app = RegisteredApp.objects.get()
    assert app.team_id == world.eng.pk
    assert app.organization_id == world.a.pk
    assert len(workflow_starts) == 4


def test_missing_row_team_requires_org_grant_and_org_credential(world, workflow_starts):
    team_user = _member(world.a, "team-owner-2104", ("team_admin", "TEAM", world.eng.pk))
    dev = _dev_env(world.a, world.cluster_a, team_user)
    denied = _sync(dev, _bearer(team_user, world.a))
    assert denied.status_code == 403
    assert denied.json()["reason"] == "missing_permission"
    assert _untouched(dev)
    assert workflow_starts == []
    org_user = _member(world.a, "org-owner-2104", ("org_admin", "ORG", world.a.pk))
    assert _sync(dev, _bearer(org_user, world.a)).status_code == 200
    assert workflow_starts == ["SyncDevEnvironmentFilesWorkflow"]


@pytest.mark.parametrize("route", ["sync", "promote"])
@pytest.mark.parametrize("invalid", ["deleted", "inactive"])
def test_builder_invalid_persisted_cluster_starts_no_workflow(world, workflow_starts, route, invalid):
    user = _member(world.a, "cluster-owner-2104", ("org_admin", "ORG", world.a.pk))
    dev = _dev_env(world.a, world.cluster_a, user, team=world.eng)
    if invalid == "deleted":
        world.cluster_a.soft_delete()
    else:
        world.cluster_a.is_active = False
        world.cluster_a.save()
    headers = _bearer(user, world.a)
    response = _sync(dev, headers) if route == "sync" else _promote(dev, headers, team_slug="eng")
    assert response.status_code == 409, response.content
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


@pytest.mark.parametrize("invalid", ["deleted-project", "mismatched-home"])
def test_repromote_rejects_stale_or_incoherent_app_ancestry_before_writes(world, workflow_starts, invalid):
    from astrolift_identity.models import Project

    user = _member(world.a, "repromote-owner-2104", ("org_admin", "ORG", world.a.pk))
    project = Project.objects.create(
        organization=world.a,
        team=world.eng if invalid == "deleted-project" else world.ops,
        name="Shipped",
        slug="repromote-project-2104",
    )
    app = RegisteredApp.objects.create(
        organization=world.a,
        team=world.eng,
        project=project,
        name="Shipped",
        slug="shipped",
        default_tenant_cluster=world.cluster_a,
    )
    dev = _dev_env(world.a, world.cluster_a, user, team=world.eng)
    dev.promoted_app = app
    dev.save()
    if invalid == "deleted-project":
        project.soft_delete()
    before = list(RegisteredApp.objects.values())
    response = _promote(dev, _bearer(user, world.a), team_slug="eng")
    assert response.status_code == 409, response.content
    assert list(RegisteredApp.objects.values()) == before
    assert _untouched(dev)
    assert workflow_starts == []
