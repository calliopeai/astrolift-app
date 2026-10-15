"""An environment says which of its actions the viewer may take (#1867).

The Environments list offered pause and resume from account-wide
permissions, so a grant on another project, or a policy that denies deploys
to production only, still showed actions the server refuses. Each
environment's ``viewerPermissions`` comes from the mutations' own checker,
at the owning app, with the environment's name and region as ABAC facts.
"""

from contextlib import contextmanager

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.mutations import LifecycleMutation
from astrolift_lifecycle.schema.mutations.types import EnvironmentByIdInput, SetEnvironmentSettingInput
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.viewer_permissions import environment_viewer_permissions
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

DEPLOY = (Permission.APP_READ, Permission.APP_DEPLOY)


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))


@pytest.fixture
def world():
    world = ScopeWorld("env-vp-1867")
    world.user = make_user("env-vp-1867")
    world.cluster = make_cluster(world, "env-vp-1867")
    world.staging = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="staging"
    )
    world.production = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production"
    )
    world.sibling = AppEnvironment.objects.create(
        registered_app=world.platform_app, tenant_cluster=world.cluster, name="staging"
    )
    return world


@contextmanager
def subject(world, token=None):
    state = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
            yield
    finally:
        reset_current_api_token(state)


def perms(world, env):
    with subject(world):
        return environment_viewer_permissions([env])[env.pk]


def pause(world, env):
    with subject(world):
        return LifecycleMutation().pause_environment(
            make_info(world.user), EnvironmentByIdInput(id=str(env.guid))
        )


def set_setting(world, env):
    with subject(world):
        return LifecycleMutation().set_environment_setting(
            make_info(world.user),
            SetEnvironmentSettingInput(environment_id=str(env.guid), key="LOG_LEVEL", value="debug"),
        )


def test_a_project_grant_offers_its_own_environments_and_not_a_siblings(world):
    bind_role(world.user, permissions=DEPLOY, kind="PROJECT", scope_id=world.medops_project.pk, slug="p-dep")
    assert perms(world, world.staging) == ["app.deploy"]
    assert perms(world, world.sibling) == []
    assert pause(world, world.staging).ok
    denied = pause(world, world.sibling)
    assert not denied.ok and denied.errors[0].code == "PERMISSION_DENIED"


def test_a_production_deny_policy_is_answered_per_environment_not_per_app(world):
    bind_role(
        world.user,
        permissions=(*DEPLOY, Permission.APP_UPDATE),
        kind="ORG",
        scope_id=world.org.pk,
        slug="o-dep",
    )
    Policy.objects.create(
        organization=world.org,
        slug="deny-prod-1867",
        name="Production deny",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"env": ["production"]},
    )
    assert perms(world, world.staging) == ["app.deploy", "app.update"]
    assert perms(world, world.production) == ["app.update"]
    assert pause(world, world.staging).ok
    assert not pause(world, world.production).ok
    assert set_setting(world, world.production).ok


@pytest.mark.parametrize("scopes,team", [("read:apps", False), ("write:apps", True)])
def test_a_bearer_tokens_scope_and_team_ceilings_hold(world, scopes, team):
    bind_role(
        world.user, permissions=(*DEPLOY, Permission.APP_UPDATE), kind="ORG", scope_id=world.org.pk, slug="o"
    )
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        token_hash="env-vp",
        scopes=[scopes],
        name="Bearer",
        team=world.platform if team else None,
    )
    with subject(world, token):
        assert environment_viewer_permissions([world.staging])[world.staging.pk] == []
        # The token's own team keeps what the grant gives it.
        assert bool(environment_viewer_permissions([world.sibling])[world.sibling.pk]) == team


def test_the_list_carries_the_field_with_one_grant_read_for_the_page(world):
    bind_role(world.user, permissions=DEPLOY, kind="ORG", scope_id=world.org.pk, slug="o-list")
    for n in range(4):
        AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=world.cluster, name=f"extra-{n}"
        )
    with subject(world):
        page = LifecycleQuery().astrolift_environments_page(make_info(world.user), page_size=50)
        assert {item.viewer_permissions == ["app.deploy"] for item in page.items} == {True}
        rows = list(AppEnvironment.objects.select_related("tenant_cluster"))
        with CaptureQueriesContext(connection) as one:
            environment_viewer_permissions(rows[:1])
        with CaptureQueriesContext(connection) as many:
            environment_viewer_permissions(rows)
    assert len(many.captured_queries) == len(one.captured_queries)
