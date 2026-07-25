"""Tests for editable team / project slugs + the live slug-availability
queries.

Covers the rename mutations (`update_team` / `update_project` now accept
a `slug`) and the `astroliftTeamSlugAvailable` /
`astroliftProjectSlugAvailable` queries that back the FE's live check.

Scope invariants under test:

* A team slug is unique **per organization**; a project slug is unique
  **per team** (the DB partial constraints + the resolver checks agree).
* Both rename mutations validate slug shape, return a structured field
  error on an invalid slug or a collision (never raise / 500), and are
  gated on TEAM_UPDATE / PROJECT_UPDATE + tenant scope.
* The availability queries fail closed cross-tenant and treat an invalid
  slug as unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.mutations import (
    IdentityMutation,
    UpdateProjectInput,
    UpdateTeamInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-slug-edit")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug="globex-slug-edit")


@pytest.fixture
def team(org):
    return Team.objects.create(name="Engineering", slug="engineering", organization=org)


@pytest.fixture
def project(team):
    return Project.objects.create(name="API", slug="api", team=team)


# ---- update_team: slug -------------------------------------------------


def test_update_team_slug_happy_path(team, org, permission_resolver):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug="platform")
        )
    assert result.ok is True
    assert result.data is not None
    assert result.data.slug == "platform"
    team.refresh_from_db()
    assert team.slug == "platform"


def test_update_team_name_and_slug_together(team, org, permission_resolver):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(),
            input=UpdateTeamInput(id=str(team.guid), name="Platform Eng", slug="platform-eng"),
        )
    assert result.ok is True
    team.refresh_from_db()
    assert team.name == "Platform Eng"
    assert team.slug == "platform-eng"


def test_update_team_slug_collision_returns_conflict(team, org, permission_resolver):
    # A sibling team already owns the target slug in the same org.
    Team.objects.create(name="Platform", slug="platform", organization=org)
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug="platform")
        )
    assert result.ok is False
    assert result.errors[0].code == "CONFLICT"
    assert result.errors[0].field == "slug"
    # The row is untouched on a rejected write.
    team.refresh_from_db()
    assert team.slug == "engineering"


def test_update_team_slug_same_across_orgs_is_allowed(team, org, other_org, permission_resolver):
    # Another org owning the same slug must NOT block this org's rename —
    # team slugs are unique per org, not globally.
    Team.objects.create(name="Platform", slug="platform", organization=other_org)
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug="platform")
        )
    assert result.ok is True
    team.refresh_from_db()
    assert team.slug == "platform"


def test_update_team_slug_unchanged_is_ok(team, org, permission_resolver):
    # Re-submitting the current slug must not read as a self-collision.
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), name="Engineering!", slug="engineering")
        )
    assert result.ok is True
    team.refresh_from_db()
    assert team.slug == "engineering"
    assert team.name == "Engineering!"


@pytest.mark.parametrize("bad_slug", ["Engineering", "1team", "-team", "team-", "te_am", "a" * 41, ""])
def test_update_team_slug_invalid_returns_validation(team, org, permission_resolver, bad_slug):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug=bad_slug)
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "slug"
    team.refresh_from_db()
    assert team.slug == "engineering"


def test_update_team_slug_requires_permission(team, org, permission_resolver):
    # TEAM_UPDATE ungranted → resolver-top @require_permission denies;
    # @mutation_audit translates it into the failure envelope.
    with _ctx(org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug="platform")
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    team.refresh_from_db()
    assert team.slug == "engineering"


def test_update_team_cross_tenant_returns_not_found(team, org, other_org, permission_resolver):
    # Caller is in other_org; the team guid belongs to org → not found,
    # and the row is never touched.
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(other_org):
        result = IdentityMutation().update_team(
            _info(), input=UpdateTeamInput(id=str(team.guid), slug="platform")
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    team.refresh_from_db()
    assert team.slug == "engineering"


# ---- update_project: slug ---------------------------------------------


def test_update_project_slug_happy_path(project, org, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug="gateway")
        )
    assert result.ok is True
    assert result.data.slug == "gateway"
    project.refresh_from_db()
    assert project.slug == "gateway"


def test_update_project_slug_collision_per_team_returns_conflict(project, team, org, permission_resolver):
    # Sibling project in the SAME team owns the target slug.
    Project.objects.create(name="Gateway", slug="gateway", team=team)
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug="gateway")
        )
    assert result.ok is False
    assert result.errors[0].code == "CONFLICT"
    assert result.errors[0].field == "slug"
    project.refresh_from_db()
    assert project.slug == "api"


def test_update_project_slug_free_in_other_team_is_allowed(project, org, permission_resolver):
    # The same slug taken in a DIFFERENT team of the same org must not
    # block — project slugs are unique per team, not per org.
    other_team = Team.objects.create(name="Data", slug="data", organization=org)
    Project.objects.create(name="Gateway", slug="gateway", team=other_team)
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug="gateway")
        )
    assert result.ok is True
    project.refresh_from_db()
    assert project.slug == "gateway"


@pytest.mark.parametrize("bad_slug", ["API", "1api", "-api", "api-", "ap_i", "a" * 41, ""])
def test_update_project_slug_invalid_returns_validation(project, org, permission_resolver, bad_slug):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug=bad_slug)
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "slug"
    project.refresh_from_db()
    assert project.slug == "api"


def test_update_project_slug_requires_permission(project, org, permission_resolver):
    with _ctx(org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug="gateway")
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    project.refresh_from_db()
    assert project.slug == "api"


def test_update_project_cross_tenant_returns_not_found(project, org, other_org, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(other_org):
        result = IdentityMutation().update_project(
            _info(), input=UpdateProjectInput(id=str(project.guid), slug="gateway")
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    project.refresh_from_db()
    assert project.slug == "api"


# ---- astroliftTeamSlugAvailable ---------------------------------------


def test_team_slug_available_true_for_free_slug(team, org, permission_resolver):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        assert IdentityQuery().astrolift_team_slug_available(_info(), slug="brand-new") is True


def test_team_slug_available_false_for_taken_slug(team, org, permission_resolver):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        assert IdentityQuery().astrolift_team_slug_available(_info(), slug="engineering") is False


def test_team_slug_available_false_for_invalid_slug(team, org, permission_resolver):
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        assert IdentityQuery().astrolift_team_slug_available(_info(), slug="Not_Valid") is False


def test_team_slug_available_true_when_excluding_self(team, org, permission_resolver):
    # The slug is taken by `team` itself; excluding it reads as available
    # so an unchanged slug in the rename form shows green.
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        assert (
            IdentityQuery().astrolift_team_slug_available(
                _info(), slug="engineering", exclude_id=str(team.guid)
            )
            is True
        )


def test_team_slug_available_true_after_soft_delete(team, org, permission_resolver):
    # A soft-deleted team's slug is reclaimable (partial unique index).
    team.soft_delete()
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(org):
        assert IdentityQuery().astrolift_team_slug_available(_info(), slug="engineering") is True


def test_team_slug_available_isolated_across_orgs(team, org, other_org, permission_resolver):
    # "engineering" is taken in `org` but free in `other_org`.
    permission_resolver.grant(Permission.TEAM_UPDATE)
    with _ctx(other_org):
        assert IdentityQuery().astrolift_team_slug_available(_info(), slug="engineering") is True


def test_team_slug_available_requires_permission(team, org, permission_resolver):
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_team_slug_available(_info(), slug="brand-new")


# ---- astroliftProjectSlugAvailable ------------------------------------


def test_project_slug_available_true_for_free_slug(project, team, org, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        assert (
            IdentityQuery().astrolift_project_slug_available(
                _info(), team_id=str(team.guid), slug="brand-new"
            )
            is True
        )


def test_project_slug_available_false_for_taken_slug(project, team, org, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        assert (
            IdentityQuery().astrolift_project_slug_available(_info(), team_id=str(team.guid), slug="api")
            is False
        )


def test_project_slug_available_true_in_other_team(project, org, permission_resolver):
    # "api" is taken in `team` but free in a sibling team — per-team scope.
    other_team = Team.objects.create(name="Data", slug="data", organization=org)
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        assert (
            IdentityQuery().astrolift_project_slug_available(
                _info(), team_id=str(other_team.guid), slug="api"
            )
            is True
        )


def test_project_slug_available_true_when_excluding_self(project, team, org, permission_resolver):
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(org):
        assert (
            IdentityQuery().astrolift_project_slug_available(
                _info(), team_id=str(team.guid), slug="api", exclude_id=str(project.guid)
            )
            is True
        )


def test_project_slug_available_false_for_cross_tenant_team(
    project, team, org, other_org, permission_resolver
):
    # A team guid from another org must fail closed even for a free slug.
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    with _ctx(other_org):
        assert (
            IdentityQuery().astrolift_project_slug_available(
                _info(), team_id=str(team.guid), slug="brand-new"
            )
            is False
        )


def test_project_slug_available_requires_permission(project, team, org, permission_resolver):
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_project_slug_available(
                _info(), team_id=str(team.guid), slug="brand-new"
            )
