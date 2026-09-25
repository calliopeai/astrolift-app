"""A caller hands out only permissions they hold (#1964).

``org.manage_members`` gated every path that hands out access and none of
them looked at *what* was handed out, so a custom role carrying that one
permission could grant ``org_owner``. Everything here runs through the real
resolver and real bindings, so the gate and the ceiling both read RoleBinding
rows; no permission stub is installed.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity.grants import grant_ceiling
from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    OrganizationAllowlistedDomain,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.permission_resolver import resolve
from astrolift_identity.schema.mutations import (
    AddOrganizationAllowlistDomainInput,
    BulkAssignTeamMemberRolesInput,
    CreateInvitationInput,
    CreateRoleInput,
    GrantRoleInput,
    IdentityMutation,
    ResendInvitationInput,
    UpdateRoleInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_identity.system_roles import SYSTEM_ROLES
from core.mutations import AuditEntry, register_audit_writer
from core.permissions import Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()

RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")
STOCK_SLUGS = [slug for slug, *_rest in SYSTEM_ROLES]
SCOPES = ("ORG", "TEAM", "PROJECT")


@pytest.fixture
def stock():
    """The catalogue as the resync migration writes it (see test_stock_roles_1864)."""
    RESYNC.upsert_system_roles(django_apps, None)
    return {slug: Role.objects.get(slug=slug, is_system=True, organization=None) for slug in STOCK_SLUGS}


class _World:
    def __init__(self):
        tag = uuid.uuid4().hex[:6]
        self.org = Organization.objects.create(name="Grants", slug=f"grants-{tag}")
        self.team = Team.objects.create(organization=self.org, name="Eng", slug=f"eng-{tag}")
        self.project = Project.objects.create(
            organization=self.org, team=self.team, name="Api", slug=f"api-{tag}"
        )

    def scope(self, kind: str):
        return {"ORG": self.org, "TEAM": self.team, "PROJECT": self.project}[kind]


@pytest.fixture
def world():
    return _World()


@pytest.fixture
def audit_capture():
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as original

    register_audit_writer(captured.append)
    yield captured
    register_audit_writer(original)


def _user(tag: str = "u", **extra) -> User:
    email = f"{tag}-{uuid.uuid4().hex[:8]}@grants.test"
    return User.objects.create(username=email.split("@")[0], email=email, **extra)


def _member(world: _World, user: User) -> User:
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=world.org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _custom_role(
    world: _World, *permissions: Permission, level: str = "ORG", slug: str | None = None
) -> Role:
    return Role.objects.create(
        organization=world.org,
        slug=slug or f"custom-{uuid.uuid4().hex[:6]}",
        name="Custom",
        scope_level=level,
        permissions=[p.value for p in permissions],
    )


def _bind(user: User, role: Role, kind: str, scope_id: int, **extra) -> RoleBinding:
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id, **extra)


def _info(user: User):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _as(world: _World, user: User, **selected):
    return tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=user.id, **selected))


def _grant(world: _World, actor: User, target: User, role: Role, kind: str = "ORG", **selected):
    with _as(world, actor, **selected):
        return IdentityMutation().grant_role(
            _info(actor),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind=kind,
                scope_guid=GUID(str(world.scope(kind).guid)),
            ),
        )


def _picker(world: _World, actor: User, **selected) -> set[str]:
    with _as(world, actor, **selected):
        return {r.slug for r in IdentityQuery().astrolift_roles_i_can_grant(_info(actor))}


def _denied(result) -> bool:
    return result.ok is False and result.errors[0].code == "PERMISSION_DENIED"


def _member_manager(world: _World, *extra: Permission) -> User:
    """A member of the org whose only role is a custom one with org.manage_members (plus ``extra``)."""
    user = _member(world, _user("mm"))
    _bind(user, _custom_role(world, Permission.ORG_MANAGE_MEMBERS, *extra), "ORG", world.org.id)
    return user


def _stock_holder(world: _World, stock, slug: str) -> User:
    user = _member(world, _user(slug))
    _bind(user, stock[slug], "ORG", world.org.id)
    return user


# ---------------------------------------------------------------------------
# grantRole
# ---------------------------------------------------------------------------


def test_manage_members_alone_cannot_make_itself_org_owner(world, stock, audit_capture):
    actor = _member_manager(world)

    result = _grant(world, actor, actor, stock["org_owner"])

    assert _denied(result)
    assert not RoleBinding.objects.filter(user=actor, role=stock["org_owner"]).exists()
    allowed, _reason = resolve(
        TenantContext(organization_id=world.org.id, actor_user_id=actor.id),
        Permission.ORG_DELETE,
        PermissionScope(kind=ScopeKind.ORG, id=world.org.id),
    )
    assert allowed is False
    # A refused escalation is audited as a denial, not as a grant.
    grant_rows = [e for e in audit_capture if e.action == "role_binding.grant"]
    assert [(e.decision, e.error_code) for e in grant_rows] == [("DENY", "PERMISSION_DENIED")]


@pytest.mark.parametrize("kind", SCOPES)
@pytest.mark.parametrize("slug", STOCK_SLUGS)
def test_manage_members_alone_is_refused_every_stock_role_at_every_scope(world, stock, slug, kind):
    actor = _member_manager(world)
    target = _member(world, _user("t"))
    assert set(stock[slug].permissions) - {Permission.ORG_MANAGE_MEMBERS.value}

    result = _grant(world, actor, target, stock[slug], kind)

    assert _denied(result), result
    assert not RoleBinding.objects.filter(user=target).exists()


@pytest.mark.parametrize("kind", SCOPES)
def test_manage_members_grants_a_role_within_its_own_permissions(world, kind):
    actor = _member_manager(world, Permission.APP_READ)
    target = _member(world, _user("t"))
    within = _custom_role(world, Permission.APP_READ)

    result = _grant(world, actor, target, within, kind)

    assert result.ok, result.errors
    scope_id = world.scope(kind).id
    assert RoleBinding.objects.filter(user=target, role=within, scope_kind=kind, scope_id=scope_id).exists()


@pytest.mark.parametrize("kind", SCOPES)
def test_owner_grants_org_owner_at_every_scope(world, stock, kind):
    owner = _stock_holder(world, stock, "org_owner")
    target = _member(world, _user("t"))

    result = _grant(world, owner, target, stock["org_owner"], kind)

    assert result.ok, result.errors


def test_admin_is_refused_exactly_the_roles_carrying_what_admin_lacks(world, stock):
    """Org admin holds everything but org.delete and billing.update, so it
    can no longer promote anyone, itself included, to owner or billing. The
    picker never offered those two."""
    admin = _stock_holder(world, stock, "org_admin")
    admin_perms = set(stock["org_admin"].permissions)
    wider = {slug for slug in STOCK_SLUGS if not set(stock[slug].permissions) <= admin_perms}
    assert wider == {"org_owner", "org_billing"}

    outcomes = {slug: _grant(world, admin, _member(world, _user("t")), stock[slug]) for slug in STOCK_SLUGS}

    assert {slug for slug, result in outcomes.items() if _denied(result)} == wider
    assert all(result.ok for slug, result in outcomes.items() if slug not in wider)
    assert _denied(_grant(world, admin, admin, stock["org_owner"]))


def test_platform_operator_grants_anything_without_a_binding(world, stock):
    operator = _user("root", is_superuser=True)
    target = _member(world, _user("t"))

    assert _grant(world, operator, target, stock["org_owner"]).ok


def test_the_ceiling_is_where_the_binding_lands_not_the_selected_context(world):
    """org.manage_members held on a team passes the targetless gate while that
    team is selected; what it may hand out is still decided per target scope."""
    actor = _member(world, _user("team-mm"))
    _bind(
        actor,
        _custom_role(world, Permission.ORG_MANAGE_MEMBERS, Permission.APP_READ),
        "TEAM",
        world.team.id,
    )
    within = _custom_role(world, Permission.APP_READ)

    at_org = _grant(world, actor, _member(world, _user("t")), within, "ORG", team_id=world.team.id)
    at_team = _grant(world, actor, _member(world, _user("t")), within, "TEAM", team_id=world.team.id)

    assert _denied(at_org)
    assert at_team.ok, at_team.errors
    # The picker answers for org scope, where an invitation lands.
    assert within.slug not in _picker(world, actor, team_id=world.team.id)


def test_owning_another_org_does_not_lift_the_ceiling_here(world, stock):
    actor = _member_manager(world)
    elsewhere = _World()
    _bind(actor, stock["org_owner"], "ORG", elsewhere.org.id)

    assert _denied(_grant(world, actor, _member(world, _user("t")), stock["org_owner"]))


def test_an_expired_binding_does_not_count(world, stock):
    actor = _member_manager(world)
    _bind(actor, stock["org_owner"], "ORG", world.org.id, expires_at=timezone.now() - timedelta(minutes=1))

    assert _denied(_grant(world, actor, _member(world, _user("t")), stock["org_owner"]))


def test_nothing_is_grantable_on_a_scope_outside_the_active_org(world, stock):
    owner = _stock_holder(world, stock, "org_owner")
    elsewhere = _World()
    tenant = TenantContext(organization_id=world.org.id, actor_user_id=owner.id)

    here = grant_ceiling(tenant, scope_kind="TEAM", scope_id=world.team.id)
    there = grant_ceiling(tenant, scope_kind="TEAM", scope_id=elsewhere.team.id)

    assert here.allows([Permission.ORG_DELETE.value])
    assert not there.allows([Permission.APP_READ.value])


# ---------------------------------------------------------------------------
# The picker, grantRole and createInvitation agree on every stock role
# ---------------------------------------------------------------------------


def _actor_of_kind(world: _World, stock, kind: str) -> tuple[User, set[str]]:
    """An actor and the permissions it holds at org scope."""
    if kind == "member_manager":
        return _member_manager(world), {Permission.ORG_MANAGE_MEMBERS.value}
    if kind == "viewer_and_member_manager":
        user = _member_manager(world)
        _bind(user, stock["org_viewer"], "ORG", world.org.id)
        return user, {Permission.ORG_MANAGE_MEMBERS.value, *stock["org_viewer"].permissions}
    return _stock_holder(world, stock, kind), set(stock[kind].permissions)


@pytest.mark.parametrize("slug", STOCK_SLUGS)
@pytest.mark.parametrize(
    "actor_kind", ["member_manager", "viewer_and_member_manager", "org_admin", "org_owner"]
)
def test_picker_grant_role_and_invitation_agree(world, stock, actor_kind, slug):
    actor, held = _actor_of_kind(world, stock, actor_kind)
    expected = set(stock[slug].permissions) <= held

    offered = slug in _picker(world, actor)
    granted = _grant(world, actor, _member(world, _user("t")), stock[slug])
    with _as(world, actor):
        invited = IdentityMutation().create_invitation(
            _info(actor),
            input=CreateInvitationInput(email=f"new-{uuid.uuid4().hex[:8]}@grants.test", role_slug=slug),
        )

    assert offered is expected
    assert granted.ok is expected, granted.errors
    assert invited.ok is expected, invited.errors
    if not expected:
        assert _denied(granted) and _denied(invited)


# ---------------------------------------------------------------------------
# The other paths that hand out access
# ---------------------------------------------------------------------------


def test_bulk_team_assign_is_capped_at_the_team_admins_reach(world, stock):
    """team.manage_members gates it and an ORG-level role passes its scope
    check, so a team admin could otherwise hand org_owner to the team."""
    admin = _member(world, _user("team-admin"))
    _bind(admin, stock["team_admin"], "TEAM", world.team.id)
    member_user = _user("tm")
    member = Member.objects.create(
        user=member_user,
        scope_kind=Member.ScopeKind.TEAM,
        scope_id=world.team.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    within = _custom_role(world, Permission.TEAM_READ, Permission.APP_READ, level="TEAM")

    def assign(role: Role):
        with _as(world, admin):
            return IdentityMutation().bulk_assign_astrolift_team_member_roles(
                _info(admin),
                input=BulkAssignTeamMemberRolesInput(
                    team_id=GUID(str(world.team.guid)),
                    role_id=GUID(str(role.guid)),
                    member_ids=[GUID(str(member.guid))],
                ),
            )

    assert _denied(assign(stock["org_owner"]))
    assert _denied(assign(stock["team_owner"]))  # team.delete, which team admin lacks
    assert not RoleBinding.objects.filter(user=member_user).exists()
    allowed = assign(within)
    assert allowed.ok and allowed.data.assigned_count == 1, allowed.errors


def test_invitation_role_is_capped_and_nothing_is_stored(world, stock):
    actor = _member_manager(world)

    with _as(world, actor):
        result = IdentityMutation().create_invitation(
            _info(actor),
            input=CreateInvitationInput(email="accomplice@grants.test", role_slug="org_owner"),
        )

    assert _denied(result)
    assert not Invitation.objects.filter(email="accomplice@grants.test").exists()


def test_resend_is_capped_at_the_resenders_reach(world, stock):
    """A resend mints a live link to the invitation's role."""
    owner = _stock_holder(world, stock, "org_owner")
    actor = _member_manager(world)
    with _as(world, owner):
        created = IdentityMutation().create_invitation(
            _info(owner),
            input=CreateInvitationInput(email="new-admin@grants.test", role_slug="org_admin"),
        )
    assert created.ok, created.errors
    token_hash = Invitation.objects.get(email="new-admin@grants.test").token_hash

    with _as(world, actor):
        refused = IdentityMutation().resend_invitation(
            _info(actor), input=ResendInvitationInput(id=created.data.invitation.id)
        )
    assert _denied(refused)
    assert Invitation.objects.get(email="new-admin@grants.test").token_hash == token_hash

    with _as(world, owner):
        resent = IdentityMutation().resend_invitation(
            _info(owner), input=ResendInvitationInput(id=created.data.invitation.id)
        )
    assert resent.ok, resent.errors


def test_allowlist_default_role_is_capped(world, stock):
    """The default role lands on every future sign-in from the domain."""
    actor = _member_manager(world)
    within = _custom_role(world, Permission.ORG_MANAGE_MEMBERS)

    def add(domain: str, role_slug: str | None):
        with _as(world, actor):
            return IdentityMutation().add_organization_allowlist_domain(
                _info(actor),
                input=AddOrganizationAllowlistDomainInput(domain=domain, default_role_slug=role_slug),
            )

    assert _denied(add("takeover.test", "org_owner"))
    assert not OrganizationAllowlistedDomain.objects.filter(domain="takeover.test").exists()
    assert add("plain.test", None).ok
    assert add("within.test", within.slug).ok


def test_a_new_role_cannot_carry_more_than_its_author_holds(world):
    """Whoever grants a role trusts the name and slug its author chose; here
    the author even reuses a stock role's slug."""
    actor = _member_manager(world)

    def create(slug: str, permissions: list[str]):
        with _as(world, actor):
            return IdentityMutation().create_role(
                _info(actor),
                input=CreateRoleInput(slug=slug, name=slug, scope_level="ORG", permissions=permissions),
            )

    assert _denied(create("org_viewer", [p.value for p in Permission]))
    assert not Role.objects.filter(organization=world.org, slug="org_viewer").exists()
    assert create("member-desk", [Permission.ORG_MANAGE_MEMBERS.value]).ok


def test_editing_your_own_role_cannot_add_what_you_lack(world):
    actor = _member(world, _user("mm"))
    own = _custom_role(world, Permission.ORG_MANAGE_MEMBERS, Permission.APP_READ)
    _bind(actor, own, "ORG", world.org.id)
    before = list(own.permissions)

    def update(role: Role, **changes):
        with _as(world, actor):
            return IdentityMutation().update_role(
                _info(actor), input=UpdateRoleInput(id=role.guid, **changes)
            )

    refused = update(own, permissions=[*before, Permission.ORG_DELETE.value])

    assert _denied(refused)
    own.refresh_from_db()
    assert own.permissions == before
    tenant = TenantContext(organization_id=world.org.id, actor_user_id=actor.id)
    assert (
        resolve(tenant, Permission.ORG_DELETE, PermissionScope(kind=ScopeKind.ORG, id=world.org.id))[0]
        is False
    )


def test_editing_a_role_may_remove_rename_or_add_what_you_hold(world):
    actor = _member_manager(world, Permission.APP_READ)
    other = _custom_role(world, Permission.ORG_MANAGE_MEMBERS, Permission.APP_DEPLOY)

    def update(**changes):
        with _as(world, actor):
            return IdentityMutation().update_role(
                _info(actor), input=UpdateRoleInput(id=other.guid, **changes)
            )

    assert update(name="Renamed").ok
    # Removing app.deploy, which the editor lacks, takes nothing it could not grant.
    assert update(permissions=[Permission.ORG_MANAGE_MEMBERS.value]).ok
    assert update(permissions=[Permission.ORG_MANAGE_MEMBERS.value, Permission.APP_READ.value]).ok
    other.refresh_from_db()
    assert (other.name, other.permissions) == (
        "Renamed",
        [Permission.ORG_MANAGE_MEMBERS.value, Permission.APP_READ.value],
    )
