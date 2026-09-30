"""SCM must share the registry's live owner and canonical bearer home."""

import pytest

from astrolift_identity.models import Project, Team
from astrolift_registry.models import AppTeamAccess
from astrolift_scm.tests import test_access_scopes_2109 as scm_fixtures
from astrolift_scm.tests.test_access_scopes_2109 import (
    _bearer,
    _grant,
    _invoke,
    _tenant,
    no_external_index,  # noqa: F401
)

pytestmark = pytest.mark.django_db
world = scm_fixtures.world


def _corrupt_owner(world, case):
    app = world.platform_app
    if case == "project-home-mismatch":
        app.project = world.medops_project
        app.save(update_fields=["project"])
    elif case == "deleted-project":
        world.platform_project.soft_delete()
    elif case == "foreign-project":
        team = Team.objects.create(organization=world.other_org, name="Foreign", slug="foreign-2109")
        app.project = Project.objects.create(
            organization=world.other_org, team=team, name="Foreign", slug="foreign-2109"
        )
        app.save(update_fields=["project"])
    elif case == "foreign-project-team":
        world.platform_project.team = Team.objects.create(
            organization=world.other_org, name="Foreign", slug="foreign-2109"
        )
        world.platform_project.save(update_fields=["team"])
    elif case == "deleted-home-team":
        world.platform.soft_delete()


BAD_OWNERS = (
    "project-home-mismatch",
    "deleted-project",
    "foreign-project",
    "foreign-project-team",
    "deleted-home-team",
)


@pytest.mark.parametrize("case", BAD_OWNERS)
@pytest.mark.parametrize("shared", [False, True])
def test_team_bearer_cannot_delete_key_with_stale_or_inconsistent_app_owner(world, case, shared):
    _grant(world)
    if shared:
        AppTeamAccess.objects.create(
            registered_app=world.platform_app, team=world.medops, access_level="owner"
        )
    _corrupt_owner(world, case)
    with _tenant(world), _bearer(world):
        result = _invoke(world, "delete_ssh_deploy_key", sibling=True)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    world.sibling_key.refresh_from_db()
    assert world.sibling_key.deleted_at is None


@pytest.mark.parametrize("case", BAD_OWNERS)
@pytest.mark.parametrize("route", ["astrolift_ssh_deploy_keys", "astrolift_ssh_deploy_keys_page"])
@pytest.mark.parametrize("bearer", [False, True])
def test_key_collections_filter_invalid_owners_before_counts(world, case, route, bearer):
    _grant(world)
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="owner")
    _corrupt_owner(world, case)
    with _tenant(world):
        if bearer:
            with _bearer(world):
                result = _invoke(world, route)
        else:
            result = _invoke(world, route)
    rows = result.items if route.endswith("_page") else result
    expected = {str(world.own_key.guid)}
    if not bearer:
        expected.add(str(world.org_key.guid))
    assert {str(row.id) for row in rows} == expected
    if route.endswith("_page"):
        assert result.total_count == len(expected)


@pytest.mark.parametrize("case", BAD_OWNERS)
def test_org_authority_keeps_explicit_org_fallback_on_stale_app_key(world, case):
    _grant(world)
    _corrupt_owner(world, case)
    with _tenant(world), _bearer(world, team=False):
        result = _invoke(world, "delete_ssh_deploy_key", sibling=True)
    assert result.ok, result.errors
    world.sibling_key.refresh_from_db()
    assert world.sibling_key.deleted_at is not None


def test_live_permission_share_keeps_sibling_key_deletion(world):
    _grant(world)
    AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level="deployer"
    )
    with _tenant(world), _bearer(world):
        result = _invoke(world, "delete_ssh_deploy_key", sibling=True)
    assert result.ok, result.errors


def test_live_home_team_keeps_key_deletion_without_project(world):
    _grant(world)
    world.medops_app.project = None
    world.medops_app.save(update_fields=["project"])
    with _tenant(world), _bearer(world):
        result = _invoke(world, "delete_ssh_deploy_key")
    assert result.ok, result.errors
