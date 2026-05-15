"""Tests for ``astrolift_my_apps`` — viewer-scoped app list (#312).

The resolver is self-service: it returns the set of registered apps
the caller can reach by any active RoleBinding on the app, project,
team, or organization. There's no @require_permission gate; the
viewer's bindings ARE the gate.

Cases covered:

* Org-scoped binding sees every app in the org.
* Team-scoped binding sees apps under that team.
* Project-scoped binding sees apps under that project.
* App-scoped binding sees only that one app.
* Expired bindings don't grant access.
* No tenant context → empty list (we never leak across tenants).
* Superusers see every app in the active tenant.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team_a = Team.objects.create(organization=org, name="Eng", slug="eng")
    team_b = Team.objects.create(organization=org, name="Ops", slug="ops")
    project_a = Project.objects.create(organization=org, team=team_a, name="Demo", slug="demo")
    project_b = Project.objects.create(organization=org, team=team_b, name="Tools", slug="tools")
    apps = {
        "alpha": RegisteredApp.objects.create(
            organization=org, team=team_a, project=project_a, name="Alpha", slug="alpha"
        ),
        "beta": RegisteredApp.objects.create(
            organization=org, team=team_a, project=project_a, name="Beta", slug="beta"
        ),
        "gamma": RegisteredApp.objects.create(
            organization=org, team=team_b, project=project_b, name="Gamma", slug="gamma"
        ),
    }
    role = Role.objects.create(name="reader", slug="reader", permissions=["app.read"])
    return org, team_a, team_b, project_a, project_b, apps, role


def _slugs(items):
    return sorted(t.slug for t in items)


def test_no_tenant_raises_tenant_required():
    """``@tenant_scoped`` is the visibility boundary — the resolver
    refuses to answer if there's no resolved tenant context. Strawberry
    surfaces this as a GraphQL error to the client."""
    from core.decorators import TenantRequired

    with pytest.raises(TenantRequired):
        RegistryQuery().astrolift_my_apps(_info())


def test_no_bindings_returns_empty():
    org, *_ = _scaffold()
    user = _user("nobody")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())
    assert result == []


def test_org_binding_sees_every_app_in_org():
    org, team_a, _, project_a, _, apps, role = _scaffold()
    user = _user("orgwide")
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    assert _slugs(result) == ["alpha", "beta", "gamma"]


def test_team_binding_sees_apps_under_team():
    org, team_a, _, _, _, apps, role = _scaffold()
    user = _user("teamview")
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=team_a.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    # team_a owns alpha + beta; gamma lives under team_b.
    assert _slugs(result) == ["alpha", "beta"]


def test_project_binding_sees_apps_under_project():
    org, _, _, project_a, _, apps, role = _scaffold()
    user = _user("projview")
    RoleBinding.objects.create(user=user, role=role, scope_kind="PROJECT", scope_id=project_a.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    # project_a contains alpha + beta.
    assert _slugs(result) == ["alpha", "beta"]


def test_app_binding_sees_only_that_app():
    org, _, _, _, _, apps, role = _scaffold()
    user = _user("appview")
    RoleBinding.objects.create(user=user, role=role, scope_kind="APP", scope_id=apps["gamma"].id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    assert _slugs(result) == ["gamma"]


def test_expired_binding_does_not_grant():
    org, _, _, _, _, apps, role = _scaffold()
    user = _user("expired")
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind="APP",
        scope_id=apps["alpha"].id,
        expires_at=timezone.now() - timedelta(hours=1),
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())
    assert result == []


def test_unexpired_binding_still_grants():
    org, _, _, _, _, apps, role = _scaffold()
    user = _user("notyet")
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind="APP",
        scope_id=apps["alpha"].id,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())
    assert _slugs(result) == ["alpha"]


def test_superuser_sees_every_app_in_tenant():
    org, *_ = _scaffold()
    su = _user("su-myapps", is_superuser=True, is_staff=True)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=su.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    assert _slugs(result) == ["alpha", "beta", "gamma"]


def test_soft_deleted_app_hidden():
    org, _, _, _, _, apps, role = _scaffold()
    user = _user("orgview2")
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)

    apps["beta"].soft_delete()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    assert _slugs(result) == ["alpha", "gamma"]


def test_cross_org_apps_filtered_by_tenant():
    """An ORG-binding on org_a must not surface apps in org_b even if
    the same actor also holds a binding in org_b — tenant context is
    the visibility boundary."""
    org_a, *_ = _scaffold()
    org_b = Organization.objects.create(name="Other", slug="other")
    team_b = Team.objects.create(organization=org_b, name="Core", slug="core")
    project_b = Project.objects.create(organization=org_b, team=team_b, name="Misc", slug="misc")
    app_b = RegisteredApp.objects.create(
        organization=org_b, team=team_b, project=project_b, name="Delta", slug="delta"
    )

    user = _user("multi")
    role = Role.objects.get(slug="reader")
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org_a.id)
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org_b.id)

    # Acting in org_a's tenant context — delta (org_b) must not appear.
    with tenant_context(TenantContext(organization_id=org_a.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())
    assert _slugs(result) == ["alpha", "beta", "gamma"]

    # Flip tenant context — only delta appears.
    with tenant_context(TenantContext(organization_id=org_b.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())
    assert _slugs(result) == ["delta"]
    # also pin the row to keep the scaffold lint quiet
    assert app_b.organization_id == org_b.id
