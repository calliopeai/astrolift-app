"""Tests for approval-gate inputs on register_app / update_app (#291)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegisterAppInput,
    RegistryMutation,
    UpdateAppInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


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


@pytest.fixture
def info(actor):
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


@pytest.fixture
def actor():
    User = get_user_model()
    return User.objects.create(username="actor", email="actor@test")


@pytest.fixture
def org(seed_cluster):
    org = Organization.objects.create(name="Acme", slug="acme-approval")
    # register_app requires at least one active cluster in the org (#315).
    seed_cluster(org, slug="approval")
    return org


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Platform", slug="platform-approval")


@pytest.fixture
def project(org, team):
    return Project.objects.create(organization=org, team=team, name="Demo", slug="demo-approval")


@pytest.fixture
def approver_users():
    User = get_user_model()
    return [User.objects.create(username=f"approver-{i}", email=f"approver-{i}@test") for i in range(2)]


def _tenant(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def test_register_app_persists_users_policy(org, project, actor, info, approver_users, permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="My App",
                slug="my-approval-app",
                source_repo="acme/my-approval-app",
                requires_approval=True,
                approver_user_ids=[str(approver_users[0].pk), str(approver_users[1].pk)],
                minimum_approvals=2,
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="my-approval-app")
    assert app.requires_approval is True
    assert app.approver_team_id is None
    assert set(app.approver_users.values_list("pk", flat=True)) == {
        approver_users[0].pk,
        approver_users[1].pk,
    }
    assert app.minimum_approvals == 2
    # Type echo reflects the new fields.
    assert result.data.requires_approval is True
    assert result.data.minimum_approvals == 2
    assert set(result.data.approver_user_ids) == {
        str(approver_users[0].pk),
        str(approver_users[1].pk),
    }


def test_register_app_persists_team_policy(org, project, team, actor, info, permission_resolver):
    """A team-only policy gates on team membership; user list stays empty."""
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="Team App",
                slug="team-approval-app",
                source_repo="acme/team-approval-app",
                requires_approval=True,
                approver_team_id=team.guid,
                minimum_approvals=1,
            ),
        )
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="team-approval-app")
    assert app.requires_approval is True
    assert app.approver_team_id == team.id
    assert app.approver_users.count() == 0
    assert app.minimum_approvals == 1


def test_register_app_rejects_cross_org_team(org, project, actor, info, permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    other_org = Organization.objects.create(name="Other", slug="other-approval")
    other_team = Team.objects.create(organization=other_org, name="Tx", slug="tx-other")

    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="X",
                slug="x-app-approval",
                source_repo="acme/x",
                requires_approval=True,
                approver_team_id=other_team.guid,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "approverTeamId"


def test_register_app_rejects_unknown_approver_user(org, project, actor, info, permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="X",
                slug="x-app-uu",
                source_repo="acme/x-uu",
                requires_approval=True,
                approver_user_ids=["9999999"],
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "approverUserIds"


def test_update_app_replaces_approver_users(
    org, project, team, actor, info, approver_users, permission_resolver
):
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        create = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="App",
                slug="app-update",
                source_repo="acme/app-update",
                requires_approval=True,
                approver_user_ids=[str(approver_users[0].pk), str(approver_users[1].pk)],
                minimum_approvals=1,
            ),
        )
        assert create.ok
        update = mut.update_app(
            info,
            input=UpdateAppInput(
                id=create.data.id,
                approver_user_ids=[str(approver_users[1].pk)],
                minimum_approvals=1,
            ),
        )

    assert update.ok, update.errors
    app = RegisteredApp.objects.get(slug="app-update")
    assert set(app.approver_users.values_list("pk", flat=True)) == {approver_users[1].pk}
    assert app.minimum_approvals == 1


def test_register_app_requires_at_least_one_approver_when_gated(
    org, project, actor, info, permission_resolver
):
    """``requiresApproval=True`` with no users + no team is a misconfig —
    refuse rather than fall open on the resolver's any-org-approver
    behavior (the wizard would have surfaced an empty policy otherwise)."""
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="X",
                slug="x-app-empty-policy",
                source_repo="acme/x-empty",
                requires_approval=True,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "approverUserIds"


def test_register_app_rejects_users_and_team_combined(
    org, project, team, actor, info, approver_users, permission_resolver
):
    """The picker is OR: users XOR team. Two approver paths at once
    makes approval-counting ambiguous; the wizard refuses the combo
    inline, the resolver gate keeps the API contract honest."""
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="X",
                slug="x-app-both",
                source_repo="acme/x-both",
                requires_approval=True,
                approver_team_id=team.guid,
                approver_user_ids=[str(approver_users[0].pk)],
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "approverTeamId"


def test_register_app_rejects_min_above_user_count(
    org, project, actor, info, approver_users, permission_resolver
):
    """``minimumApprovals`` must be <= approver_users count or the gate
    would never resolve. Caught on register so a misconfig can't ship."""
    permission_resolver.grant(Permission.APP_CREATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        result = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="X",
                slug="x-app-min-too-high",
                source_repo="acme/x-min-high",
                requires_approval=True,
                approver_user_ids=[str(approver_users[0].pk)],
                minimum_approvals=2,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "minimumApprovals"


def test_update_app_validates_effective_state(
    org, project, team, actor, info, approver_users, permission_resolver
):
    """The cross-field validator runs against the post-update effective
    state, not just the input delta. Flipping a single field that breaks
    the policy must fail even if the call didn't mention the other fields."""
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = RegistryMutation()
    with _tenant(org, actor):
        create = mut.register_app(
            info,
            input=RegisterAppInput(
                project_id=project.guid,
                name="App",
                slug="app-update-effective",
                source_repo="acme/app-update-effective",
                requires_approval=False,
            ),
        )
        assert create.ok, create.errors
        # Flipping requires_approval=True alone now lights up the policy
        # but the app has no team and no users -> validator must refuse.
        result = mut.update_app(
            info,
            input=UpdateAppInput(
                id=create.data.id,
                requires_approval=True,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "approverUserIds"
