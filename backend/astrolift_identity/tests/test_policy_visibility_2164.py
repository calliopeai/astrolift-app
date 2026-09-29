"""Scoped policies must reduce collection rows and effective capabilities."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_identity.permission_resolver import granted_scopes, resolve_effective_permissions_anywhere
from astrolift_identity.scope_visibility import visible_apps, visible_projects
from astrolift_identity.tests import test_operation_context_2164 as operation_support
from astrolift_identity.tests.test_access_enforcement_2157 import _no_opensearch  # noqa: F401
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import get_current_tenant
from core.tests.utils.scope_world import as_tenant, bind_role, make_user

pytestmark = pytest.mark.django_db
operation_world = operation_support.operation_world
policy = operation_support.policy


@pytest.mark.parametrize("scope", ["APP", "PROJECT", "resource"])
def test_narrow_deny_is_absent_from_an_org_granted_app_collection(operation_world, scope):
    w = operation_world
    row = policy(w, action="app.read")
    if scope == "resource":
        row.resource_pattern = {"app_slug": w.world.medops_app.slug}
    else:
        row.scope_level = scope
        row.scope_id = w.world.medops_app.pk if scope == "APP" else w.world.medops_project.pk
    row.save()
    with as_tenant(w.world, w.user):
        rows = visible_apps(RegisteredApp.objects.filter(organization=w.world.org), Permission.APP_READ)
        assert set(rows.values_list("pk", flat=True)) == {w.world.platform_app.pk}
        scopes = granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert not scopes.org
        assert scopes.app_ids == {w.world.platform_app.pk}


def test_capability_is_removed_when_the_only_granted_app_is_denied(operation_world):
    w = operation_world
    user = make_user(f"narrow-{uuid4().hex}")
    bind_role(
        user,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=w.world.medops_app.pk,
        slug=f"role-{uuid4().hex}",
    )
    policy(w, action="app.read", resource={"app_slug": w.world.medops_app.slug})
    with as_tenant(w.world, user):
        assert not granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert Permission.APP_READ.value not in resolve_effective_permissions_anywhere(get_current_tenant())
    bind_role(
        user,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=w.world.platform_app.pk,
        slug=f"role-{uuid4().hex}",
    )
    with as_tenant(w.world, user):
        assert Permission.APP_READ.value in resolve_effective_permissions_anywhere(get_current_tenant())
        assert granted_scopes(get_current_tenant(), Permission.APP_READ).app_ids == {w.world.platform_app.pk}


def test_environment_specific_policy_keeps_an_app_with_an_allowed_environment(operation_world):
    w = operation_world
    policy(w, action="app.read", resource={"env": ["production"]})
    with as_tenant(w.world, w.user):
        scopes = granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert w.world.medops_app.pk in scopes.app_ids
    w.staging.soft_delete()
    with as_tenant(w.world, w.user):
        assert w.world.medops_app.pk not in granted_scopes(get_current_tenant(), Permission.APP_READ).app_ids


def test_narrow_policy_does_not_make_an_allowed_project_inherit_into_a_denied_app(operation_world):
    from astrolift_identity.models import Project

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.PROJECT_READ],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"proj-{uuid4().hex}",
    )
    policy(w, action="*.read", resource={"app_slug": w.world.medops_app.slug})
    with as_tenant(w.world, w.user):
        assert w.world.medops_project in visible_projects(
            Project.objects.filter(organization=w.world.org), Permission.PROJECT_READ
        )
        assert w.world.medops_app not in visible_apps(
            RegisteredApp.objects.filter(organization=w.world.org), Permission.APP_READ
        )


def test_scoped_collection_query_count_does_not_scale_with_app_count(operation_world):
    w = operation_world
    policy(w, action="app.read", resource={"app_slug": "medops*"})
    with as_tenant(w.world, w.user), CaptureQueriesContext(connection) as small:
        granted_scopes(get_current_tenant(), Permission.APP_READ)
    RegisteredApp.objects.bulk_create(
        [
            RegisteredApp(
                organization=w.world.org,
                team=w.world.medops,
                project=w.world.medops_project,
                name=f"App {i}",
                slug=f"extra-{i}-{uuid4().hex}",
            )
            for i in range(25)
        ]
    )
    with as_tenant(w.world, w.user), CaptureQueriesContext(connection) as large:
        granted_scopes(get_current_tenant(), Permission.APP_READ)
    assert len(small) == len(large)
