"""For every stock role, what the UI is offered is what the server allows (#1867).

The issue's acceptance: no action the UI offers returns a permission error.
The web app offers an action from the object's ``viewerPermissions`` (apps
through ``useAppCan``, clusters through the cluster settings, environments
through the Environments list), so each field is held, role by role, against
the check that admits the action: the scalar checker at the object's scope
for every app and cluster permission, and the environment mutations
themselves. Equality, not just inclusion, so an allowed action is not hidden
either. Workload restart and scale have the same matrix in
``astrolift_registry/tests/test_viewer_actions_1867.py``.
"""

from contextlib import contextmanager

import pytest

from astrolift_clusters.viewer_permissions import READ, WRITES, cluster_viewer_permissions
from astrolift_identity.models import RoleBinding
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_identity.tests.test_stock_roles_1864 import _stock_roles
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.mutations import LifecycleMutation
from astrolift_lifecycle.schema.mutations.types import EnvironmentByIdInput, SetEnvironmentSettingInput
from astrolift_lifecycle.viewer_permissions import environment_viewer_permissions
from astrolift_registry.schema.queries import _viewer_permissions_for_apps
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))


@pytest.fixture
def world():
    world = ScopeWorld("caps-1867")
    world.user = make_user("caps-1867")
    world.cluster = make_cluster(world, "caps-1867")
    world.environments = [
        AppEnvironment.objects.create(registered_app=app, tenant_cluster=world.cluster, name="staging")
        for app in (world.medops_app, world.platform_app)
    ]
    return world


@contextmanager
def subject(world):
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        yield


def allowed(permission, scope) -> bool:
    try:
        check_permission(permission, scope=scope)
    except PermissionDenied:
        return False
    return True


def environment_actions(world, env):
    """Each environment action the Environments list offers, and what it needs."""
    mutation, info = LifecycleMutation(), make_info(world.user)
    yield "app.deploy", mutation.pause_environment(info, EnvironmentByIdInput(id=str(env.guid)))
    yield "app.deploy", mutation.resume_environment(info, EnvironmentByIdInput(id=str(env.guid)))
    yield (
        "app.update",
        mutation.set_environment_setting(
            info, SetEnvironmentSettingInput(environment_id=str(env.guid), key="LOG_LEVEL", value="debug")
        ),
    )


@pytest.mark.parametrize("role_slug", [slug for slug, *_ in SYSTEM_ROLES])
def test_every_stock_role_is_offered_exactly_what_the_server_allows(world, role_slug):
    role = _stock_roles()[role_slug]
    kind = role.scope_level
    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    RoleBinding.objects.create(user=world.user, role=role, scope_kind=kind, scope_id=scope_id)

    with subject(world):
        apps = (world.medops_app, world.platform_app)
        offered = _viewer_permissions_for_apps(apps)
        for app in apps:
            scope = PermissionScope(ScopeKind.APP, app.pk)
            for permission in Permission:
                assert (permission.value in offered[app.pk]) == allowed(permission, scope), (
                    role_slug,
                    app.slug,
                    permission.value,
                )

        offered = cluster_viewer_permissions(make_info(world.user), shared=False)
        org = PermissionScope(ScopeKind.ORG, world.org.pk)
        for permission in READ + WRITES:
            assert (permission.value in offered) == allowed(permission, org), (role_slug, permission.value)

        offered = environment_viewer_permissions(world.environments)
        for env in world.environments:
            for needs, result in environment_actions(world, env):
                assert result.ok == (needs in offered[env.pk]), (role_slug, env.registered_app_id, needs)
                if not result.ok:
                    assert result.errors[0].code == "PERMISSION_DENIED"
