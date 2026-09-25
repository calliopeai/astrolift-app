"""Tests for bulk member/team operations (#416).

Covers ``bulk_revoke_astrolift_role_bindings`` (members list) and
``bulk_assign_astrolift_team_member_roles`` (team members panel):

* happy path — every id succeeds, counts roll up correctly
* partial failure — unknown ids report per-id NOT_FOUND but the
  successful ids still apply
* idempotency — re-running the team assign on the same input flips
  ``already_existed`` instead of creating duplicate rows
* permission deny — both mutations fail-closed when the required
  permission is not granted
* shape validation — empty list / oversized batch / wrong-scope role
  return a top-level VALIDATION error rather than an empty result
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import (
    Member,
    Organization,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.mutations import (
    BulkAssignTeamMemberRolesInput,
    BulkRevokeRoleBindingsInput,
    IdentityMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info(user=None):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _user(email: str | None = None) -> User:
    if email is None:
        email = f"user-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(username=email.split("@")[0], email=email)


def _org_role(slug: str = "developer", scope_level: str = "TEAM") -> Role:
    return Role.objects.create(
        slug=slug,
        name=slug.title(),
        scope_level=scope_level,
        permissions=["app.read"],
    )


def _holds_everything(user, org: Organization) -> None:
    """A granter may hand out only what they hold (#1964)."""
    role = Role.objects.create(
        organization=org,
        slug=f"holds-all-{uuid.uuid4().hex[:6]}",
        name="Holds all",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[p.value for p in Permission],
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


# ---- bulk revoke -----------------------------------------------------


def test_bulk_revoke_happy_path(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-revoke-happy")
    role = _org_role("dev-bulk-revoke-happy")
    actor = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    users = [_user() for _ in range(3)]
    bindings = [
        RoleBinding.objects.create(user=u, role=role, scope_kind="ORG", scope_id=org.id) for u in users
    ]

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=[GUID(str(b.guid)) for b in bindings]),
        )

    assert result.ok, result.errors
    assert result.data.revoked_count == 3
    assert result.data.failed_count == 0
    assert all(r.ok for r in result.data.results)
    # All bindings must be soft-deleted (deleted_at populated).
    for b in bindings:
        b.refresh_from_db()
        assert b.deleted_at is not None


def test_bulk_revoke_partial_failure(permission_resolver):
    """An unknown id in the batch must not block the real ones."""
    org = Organization.objects.create(name="X", slug="bulk-revoke-partial")
    role = _org_role("dev-bulk-revoke-partial")
    actor = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    user = _user()
    real_binding = RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)
    ghost_guid = str(uuid.uuid4())

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=[GUID(str(real_binding.guid)), GUID(ghost_guid)]),
        )

    assert result.ok
    assert result.data.revoked_count == 1
    assert result.data.failed_count == 1

    by_id = {str(r.id): r for r in result.data.results}
    assert by_id[str(real_binding.guid)].ok is True
    ghost = by_id[ghost_guid]
    assert ghost.ok is False
    assert ghost.errors[0].code == "NOT_FOUND"

    real_binding.refresh_from_db()
    assert real_binding.deleted_at is not None


def test_bulk_revoke_dedupes_ids(permission_resolver):
    """Duplicate ids must not double-count in revoked_count."""
    org = Organization.objects.create(name="X", slug="bulk-revoke-dedup")
    role = _org_role("dev-bulk-revoke-dedup")
    actor = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    user = _user()
    b = RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=[GUID(str(b.guid)), GUID(str(b.guid))]),
        )
    assert result.ok
    assert result.data.revoked_count == 1
    assert len(result.data.results) == 1


def test_bulk_revoke_permission_denied(permission_resolver):
    """No grant of ORG_MANAGE_MEMBERS → top-level PERMISSION_DENIED."""
    org = Organization.objects.create(name="X", slug="bulk-revoke-deny")
    role = _org_role("dev-bulk-revoke-deny")
    actor = _user()
    # Intentionally no .grant() — resolver returns False by default.

    user = _user()
    b = RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=[GUID(str(b.guid))]),
        )

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    b.refresh_from_db()
    assert b.deleted_at is None


def test_bulk_revoke_rejects_empty_list(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-revoke-empty")
    actor = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=[]),
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "bindingIds"


def test_bulk_revoke_rejects_oversized_batch(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-revoke-overflow")
    actor = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    # 501 fabricated guids — never touch the DB; the size check fires
    # before any lookup runs.
    guids = [GUID(str(uuid.uuid4())) for _ in range(501)]
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(actor),
            input=BulkRevokeRoleBindingsInput(binding_ids=guids),
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"


# ---- bulk assign team-member roles -----------------------------------


def _team_with_members(org: Organization, n: int = 3) -> tuple[Team, list[Member]]:
    team = Team.objects.create(organization=org, slug=f"t-{uuid.uuid4().hex[:6]}", name="T")
    members: list[Member] = []
    for _ in range(n):
        u = _user()
        members.append(
            Member.objects.create(
                user=u,
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=team.pk,
                is_active=True,
                lifecycle=Member.Lifecycle.ACTIVE,
            )
        )
    return team, members


def test_bulk_assign_team_happy_path(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-assign-happy")
    actor = _user()
    _holds_everything(actor, org)
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-happy", scope_level="TEAM")
    team, members = _team_with_members(org)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(team.guid)),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(m.guid)) for m in members],
            ),
        )

    assert result.ok, result.errors
    assert result.data.assigned_count == 3
    assert result.data.already_assigned_count == 0
    assert result.data.failed_count == 0

    for m in members:
        assert RoleBinding.objects.filter(
            user_id=m.user_id, role=role, scope_kind="TEAM", scope_id=team.pk
        ).exists()


def test_bulk_assign_team_idempotent(permission_resolver):
    """Re-running with the same input is a no-op — flips already_existed."""
    org = Organization.objects.create(name="X", slug="bulk-assign-idem")
    actor = _user()
    _holds_everything(actor, org)
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-idem", scope_level="TEAM")
    team, members = _team_with_members(org, n=2)

    input_payload = BulkAssignTeamMemberRolesInput(
        team_id=GUID(str(team.guid)),
        role_id=GUID(str(role.guid)),
        member_ids=[GUID(str(m.guid)) for m in members],
    )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        first = IdentityMutation().bulk_assign_astrolift_team_member_roles(_info(actor), input=input_payload)
        second = IdentityMutation().bulk_assign_astrolift_team_member_roles(_info(actor), input=input_payload)

    assert first.ok and first.data.assigned_count == 2
    assert second.ok
    assert second.data.assigned_count == 0
    assert second.data.already_assigned_count == 2
    assert all(r.already_existed for r in second.data.results)

    # And no duplicate rows.
    assert RoleBinding.objects.filter(role=role, scope_kind="TEAM", scope_id=team.pk).count() == 2


def test_bulk_assign_team_partial_failure_unknown_member(permission_resolver):
    """An unknown member guid is reported per-id without blocking real ones."""
    org = Organization.objects.create(name="X", slug="bulk-assign-partial")
    actor = _user()
    _holds_everything(actor, org)
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-partial", scope_level="TEAM")
    team, members = _team_with_members(org, n=1)
    ghost = str(uuid.uuid4())

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(team.guid)),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(members[0].guid)), GUID(ghost)],
            ),
        )

    assert result.ok
    assert result.data.assigned_count == 1
    assert result.data.failed_count == 1
    by_id = {str(r.id): r for r in result.data.results}
    assert by_id[ghost].ok is False
    assert by_id[ghost].errors[0].code == "NOT_FOUND"


def test_bulk_assign_team_rejects_other_team_member(permission_resolver):
    """A member of team-A submitted under team-B must be rejected, not
    silently bound into team-B's scope."""
    org = Organization.objects.create(name="X", slug="bulk-assign-cross-team")
    actor = _user()
    _holds_everything(actor, org)
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-cross-team", scope_level="TEAM")
    team_a, members = _team_with_members(org, n=1)
    team_b = Team.objects.create(organization=org, slug="tb", name="B")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(team_b.guid)),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(members[0].guid))],
            ),
        )

    assert result.ok
    assert result.data.failed_count == 1
    assert result.data.assigned_count == 0
    # And no binding leaked into team_b.
    assert not RoleBinding.objects.filter(role=role, scope_kind="TEAM", scope_id=team_b.pk).exists()


def test_bulk_assign_team_permission_denied(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-assign-deny")
    actor = _user()
    # No grant of TEAM_MANAGE_MEMBERS — top-level deny.
    role = _org_role("team-dev-deny", scope_level="TEAM")
    team, members = _team_with_members(org, n=1)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(team.guid)),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(members[0].guid))],
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not RoleBinding.objects.filter(role=role, scope_kind="TEAM").exists()


def test_bulk_assign_team_rejects_wrong_scope_role(permission_resolver):
    """A PROJECT/APP-scope role can't be granted at a TEAM scope — the
    resolver chain would never evaluate it."""
    org = Organization.objects.create(name="X", slug="bulk-assign-wrong-scope")
    actor = _user()
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-wrong", scope_level="PROJECT")
    team, members = _team_with_members(org, n=1)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(team.guid)),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(members[0].guid))],
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "roleId"


def test_team_members_query_filters_by_team(permission_resolver):
    """astrolift_team_members returns only members at the requested
    team's scope, even when other teams have members of their own."""
    from astrolift_identity.schema.queries import IdentityQuery

    org = Organization.objects.create(name="X", slug="team-members-q")
    actor = _user()
    permission_resolver.grant(Permission.TEAM_READ)
    team_a, members_a = _team_with_members(org, n=2)
    team_b, _ = _team_with_members(org, n=1)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_team_members(_info(actor), team_id=GUID(str(team_a.guid)))

    returned_guids = {str(r.id) for r in rows}
    assert returned_guids == {str(m.guid) for m in members_a}


def test_bulk_assign_team_missing_team(permission_resolver):
    org = Organization.objects.create(name="X", slug="bulk-assign-no-team")
    actor = _user()
    permission_resolver.grant(Permission.TEAM_MANAGE_MEMBERS)
    role = _org_role("team-dev-no-team", scope_level="TEAM")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
            _info(actor),
            input=BulkAssignTeamMemberRolesInput(
                team_id=GUID(str(uuid.uuid4())),
                role_id=GUID(str(role.guid)),
                member_ids=[GUID(str(uuid.uuid4()))],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "teamId"
