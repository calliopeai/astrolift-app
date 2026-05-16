"""Tests for ``assignAstroliftAppToProject`` + ``assignableAstroliftProjects`` (#391).

The Settings "Assign project" card calls these two together:

- The mutation re-parents an app under a different project (or
  unassigns it via ``project_guid=null``). The team FK follows the
  project's team so the nav tree stays coherent.
- The query lists the projects the viewer can pick from — gated by
  active RoleBindings at ORG / TEAM / PROJECT scope.

Permission contract:

- ``app.update`` on the source app (decorator gate).
- An active RoleBinding that reaches the destination project (resolver-body
  check). APP-scope bindings don't qualify — assigning an app to a project
  the viewer can't otherwise see would be a silent scope-escalation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import AssignAppToProjectInput, RegistryMutation
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    # The User model's post_save fans out to a Profile + OpenSearch
    # index call; stub both so the registry tests don't need a live
    # search backend. Mirrors test_my_apps_query.
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
    app = RegisteredApp.objects.create(
        organization=org, team=team_a, project=project_a, name="App", slug="my-app"
    )
    role = Role.objects.create(name="updater", slug="updater", permissions=["app.update"])
    return org, team_a, team_b, project_a, project_b, app, role


def _grant_org_access(role, user, org):
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


# ---- mutation ---------------------------------------------------------


def test_assign_to_new_project_repoints_team(permission_resolver):
    org, _, team_b, _, project_b, app, role = _scaffold()
    user = _user("operator")
    _grant_org_access(role, user, org)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_b.guid)),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.project_id == project_b.id
    # Team follows the project so the nav tree stays coherent.
    assert app.team_id == team_b.id
    assert result.data is not None
    assert result.data.project_slug == "tools"
    assert result.data.team_slug == "ops"


def test_unassign_when_project_guid_is_null(permission_resolver):
    """``project_guid=null`` clears the FK without needing any
    project-side permission check — removing scope is the safer
    direction."""
    org, _, _, project_a, _, app, _ = _scaffold()
    user = _user("operator")
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=None),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.project_id is None
    assert result.data is not None
    assert result.data.project_slug == ""
    assert result.data.project_id is None


def test_unassign_is_idempotent(permission_resolver):
    org, _, _, _, _, app, _ = _scaffold()
    app.project = None
    app.save(update_fields=["project", "updated_at", "version"])
    user = _user("operator")
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=None),
        )
    assert result.ok
    app.refresh_from_db()
    assert app.project_id is None


def test_assign_to_same_project_is_noop(permission_resolver):
    org, _, _, project_a, _, app, role = _scaffold()
    user = _user("operator")
    _grant_org_access(role, user, org)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_a.guid)),
        )
    assert result.ok
    app.refresh_from_db()
    assert app.project_id == project_a.id


def test_cross_org_target_refused(permission_resolver):
    """A project in a different org → PRECONDITION, not silent
    cross-tenant re-parenting."""
    org, _, _, _, _, app, role = _scaffold()
    user = _user("operator")
    _grant_org_access(role, user, org)
    permission_resolver.grant(Permission.APP_UPDATE)

    other_org = Organization.objects.create(name="Other", slug="other")
    other_team = Team.objects.create(organization=other_org, name="Core", slug="core")
    other_project = Project.objects.create(organization=other_org, team=other_team, name="X", slug="x")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(
                app_slug=app.slug,
                project_guid=str(other_project.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "projectGuid"


def test_missing_project_membership_denied(permission_resolver):
    """The actor has app.update via the decorator but no RoleBinding
    reaching the destination project — assignment is refused so the
    operator can't park an app under a project they can't otherwise
    see."""
    org, team_a, _, _, project_b, app, role = _scaffold()
    user = _user("operator")
    # Only TEAM-A binding; project_b lives under TEAM-B.
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=team_a.id)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(
                app_slug=app.slug,
                project_guid=str(project_b.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert result.errors[0].field == "projectGuid"


def test_team_binding_covers_project_under_team(permission_resolver):
    org, _, team_b, _, project_b, app, role = _scaffold()
    user = _user("operator")
    # Team-B binding covers project_b (lives under team_b).
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=team_b.id)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_b.guid)),
        )
    assert result.ok, result.errors


def test_project_binding_covers_self(permission_resolver):
    org, _, _, _, project_b, app, role = _scaffold()
    user = _user("operator")
    RoleBinding.objects.create(user=user, role=role, scope_kind="PROJECT", scope_id=project_b.id)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_b.guid)),
        )
    assert result.ok, result.errors


def test_app_only_binding_does_not_cover_project(permission_resolver):
    """An APP-scope binding gives access to that one app — it does NOT
    qualify the actor to assign OTHER apps to the project that hosts it.
    Refuse with PERMISSION_DENIED."""
    org, _, _, _, project_b, app, role = _scaffold()
    user = _user("operator")
    other_app = RegisteredApp.objects.create(
        organization=org,
        team=project_b.team,
        project=project_b,
        name="Other",
        slug="other-app",
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="APP", scope_id=other_app.id)
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_b.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_no_app_update_permission_denied(permission_resolver):
    org, _, _, _, project_b, app, role = _scaffold()
    user = _user("operator")
    _grant_org_access(role, user, org)
    # Intentionally no permission_resolver.grant(APP_UPDATE).

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug=app.slug, project_guid=str(project_b.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_unknown_app_returns_not_found(permission_resolver):
    org, *_ = _scaffold()
    user = _user("operator")
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(app_slug="no-such-app", project_guid=None),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_unknown_project_returns_not_found(permission_resolver):
    org, _, _, _, _, app, _ = _scaffold()
    user = _user("operator")
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryMutation().assign_astrolift_app_to_project(
            _info(),
            input=AssignAppToProjectInput(
                app_slug=app.slug,
                project_guid="00000000-0000-0000-0000-000000000000",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "projectGuid"


# ---- assignable projects query ---------------------------------------


def test_assignable_projects_org_binding_sees_all():
    org, _, _, project_a, project_b, _, role = _scaffold()
    user = _user("orgwide")
    _grant_org_access(role, user, org)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())

    slugs = sorted(p.slug for p in result)
    assert slugs == [project_a.slug, project_b.slug]


def test_assignable_projects_team_binding_scoped():
    org, team_a, _, project_a, _project_b, _, role = _scaffold()
    user = _user("teamview")
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=team_a.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())

    slugs = sorted(p.slug for p in result)
    # team_a holds project_a only.
    assert slugs == [project_a.slug]


def test_assignable_projects_project_binding_scoped():
    org, _, _, _, project_b, _, role = _scaffold()
    user = _user("projview")
    RoleBinding.objects.create(user=user, role=role, scope_kind="PROJECT", scope_id=project_b.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())

    slugs = sorted(p.slug for p in result)
    assert slugs == [project_b.slug]


def test_assignable_projects_no_bindings_returns_empty():
    org, *_ = _scaffold()
    user = _user("nobody")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())
    assert result == []


def test_assignable_projects_app_scope_does_not_grant():
    """An APP-scope binding gives read access to one app — it must
    NOT surface the project beneath that app as an assignment
    target."""
    org, _, _, _, project_b, _, role = _scaffold()
    user = _user("apponly")
    pinned = RegisteredApp.objects.create(
        organization=org,
        team=project_b.team,
        project=project_b,
        name="Pinned",
        slug="pinned",
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="APP", scope_id=pinned.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())

    assert result == []


def test_assignable_projects_superuser_sees_all():
    org, _, _, project_a, project_b, _, _ = _scaffold()
    # "admin" is reserved by the dev-data seeder, hence "platform-admin".
    user = _user("platform-admin", is_superuser=True)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().assignable_astrolift_projects(_info())

    slugs = sorted(p.slug for p in result)
    assert slugs == [project_a.slug, project_b.slug]
