"""The #2157 GraphQL surface: group principals and expiry on grantRole,
updateRoleBinding, GroupRoleMapping list/create/delete, accessOn, and the
scoped, manager-open permissionDiagnose / permissionCompare.

Every new query and mutation is checked for org isolation: a caller in
one org can neither read nor touch another org's rows by guid.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from graphql import GraphQLError

from astrolift_graphql import GUID
from astrolift_identity.models import GroupRoleMapping, Member, Policy, Role, RoleBinding
from astrolift_identity.permission_resolver import resolve
from astrolift_identity.schema.mutations import (
    CreateGroupRoleMappingInput,
    DeleteGroupRoleMappingInput,
    GrantRoleInput,
    IdentityMutation,
    UpdateRoleBindingInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_registry.models import AppTeamAccess, Workload
from core.permissions import Permission, PermissionScope, ScopeKind
from core.schema.types.permission_analysis import PermissionAnalysisQuery
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db
User = get_user_model()

DEPLOY = Permission.APP_DEPLOY
READ = Permission.APP_READ


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _tag() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
def world():
    return ScopeWorld(f"g2157-{_tag()}")


@pytest.fixture
def other():
    return ScopeWorld(f"g2157o-{_tag()}")


def _user(prefix="u"):
    email = f"{prefix}-{_tag()}@acme.test"
    return User.objects.create(username=email.split("@")[0], email=email)


def _member(world, user, groups=()):
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=world.org.pk,
        is_active=True,
        lifecycle="active",
        idp_groups=list(groups),
    )
    return user


def _role(*perms, org=None):
    return Role.objects.create(
        name="r",
        slug=f"r-{_tag()}",
        scope_level="ORG",
        permissions=[p.value for p in perms],
        is_system=False,
        organization=org,
    )


def _bind(user, role, kind, scope_id, **extra):
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id, **extra)


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _as(world, user):
    return tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=user.pk))


@pytest.fixture
def manager(world):
    """A member who may manage members and holds app access to grant."""
    user = _member(world, _user("mgr"))
    _bind(user, _role(Permission.ORG_MANAGE_MEMBERS, DEPLOY, READ, Permission.ORG_READ), "ORG", world.org.pk)
    return user


@pytest.fixture
def other_manager(other):
    user = _member(other, _user("omgr"))
    _bind(user, _role(Permission.ORG_MANAGE_MEMBERS, DEPLOY, READ), "ORG", other.org.pk)
    return user


def _grant(world, actor, **fields):
    with _as(world, actor):
        return IdentityMutation().grant_role(_info(actor), input=GrantRoleInput(**fields))


# ---------------------------------------------------------------------
# grantRole: group principal and expiry
# ---------------------------------------------------------------------


def test_grant_role_to_a_group_grants_its_members(world, manager):
    role = _role(DEPLOY)
    result = _grant(
        world,
        manager,
        group_external_id="eng",
        role_id=GUID(str(role.guid)),
        scope_kind="TEAM",
        scope_guid=GUID(str(world.medops.guid)),
    )
    assert result.ok, result.errors
    assert result.data.user is None and result.data.group_external_id == "eng"

    engineer = _member(world, _user("eng"), groups=["eng"])
    scope = PermissionScope(kind=ScopeKind.APP, id=world.medops_app.pk)
    assert resolve(TenantContext(organization_id=world.org.pk, actor_user_id=engineer.pk), DEPLOY, scope)[0]

    again = _grant(
        world,
        manager,
        group_external_id="eng",
        role_id=GUID(str(role.guid)),
        scope_kind="TEAM",
        scope_guid=GUID(str(world.medops.guid)),
    )
    assert again.ok is False and again.errors[0].code == "CONFLICT"


def test_grant_role_takes_an_expiry(world, manager):
    target = _member(world, _user("t"))
    at = timezone.now() + timedelta(days=1)
    result = _grant(
        world,
        manager,
        user_id=str(target.pk),
        role_id=GUID(str(_role(READ).guid)),
        scope_kind="ORG",
        scope_guid=GUID(str(world.org.guid)),
        expires_at=at,
    )
    assert result.ok, result.errors
    assert RoleBinding.objects.get(guid=str(result.data.id)).expires_at == at


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"user_id": "1", "group_external_id": "eng"},
        {"group_external_id": "x" * 256},
        {"group_external_id": "eng", "expires_at": "past"},
    ],
)
def test_grant_role_validates_its_principal_and_expiry(world, manager, fields):
    if fields.get("expires_at") == "past":
        fields["expires_at"] = timezone.now() - timedelta(minutes=1)
    result = _grant(
        world,
        manager,
        role_id=GUID(str(_role(READ).guid)),
        scope_kind="ORG",
        scope_guid=GUID(str(world.org.guid)),
        **fields,
    )
    assert result.ok is False and result.errors[0].code == "VALIDATION"


def test_a_group_grant_on_another_orgs_scope_is_not_found(world, other, manager):
    result = _grant(
        world,
        manager,
        group_external_id="eng",
        role_id=GUID(str(_role(READ).guid)),
        scope_kind="TEAM",
        scope_guid=GUID(str(other.medops.guid)),
    )
    assert result.ok is False and result.errors[0].code == "NOT_FOUND"
    assert not RoleBinding.objects.filter(group_external_id="eng", scope_id=other.medops.pk).exists()


def test_a_group_grant_is_capped_by_the_callers_reach(world, manager):
    result = _grant(
        world,
        manager,
        group_external_id="eng",
        role_id=GUID(str(_role(Permission.ORG_DELETE).guid)),
        scope_kind="ORG",
        scope_guid=GUID(str(world.org.guid)),
    )
    assert result.ok is False and result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------
# updateRoleBinding
# ---------------------------------------------------------------------


def _update(world, actor, binding, **fields):
    with _as(world, actor):
        return IdentityMutation().update_role_binding(
            _info(actor), input=UpdateRoleBindingInput(id=GUID(str(binding.guid)), **fields)
        )


def test_update_role_binding_changes_role_and_expiry(world, manager):
    target = _member(world, _user("t"))
    binding = _bind(target, _role(READ), "ORG", world.org.pk)
    new_role = _role(DEPLOY)
    at = timezone.now() + timedelta(hours=2)

    result = _update(world, manager, binding, role_id=GUID(str(new_role.guid)), expires_at=at)
    assert result.ok, result.errors
    binding.refresh_from_db()
    assert (binding.role_id, binding.expires_at) == (new_role.pk, at)

    cleared = _update(world, manager, binding, expires_at=None)
    assert cleared.ok
    binding.refresh_from_db()
    assert binding.expires_at is None and binding.role_id == new_role.pk


def test_update_role_binding_is_capped_and_validated(world, manager):
    binding = _bind(_member(world, _user("t")), _role(READ), "ORG", world.org.pk)

    wider = _update(world, manager, binding, role_id=GUID(str(_role(Permission.ORG_DELETE).guid)))
    assert wider.ok is False and wider.errors[0].code == "PERMISSION_DENIED"
    nothing = _update(world, manager, binding)
    assert nothing.ok is False and nothing.errors[0].code == "VALIDATION"
    past = _update(world, manager, binding, expires_at=timezone.now() - timedelta(seconds=1))
    assert past.ok is False and past.errors[0].code == "VALIDATION"


def test_update_role_binding_cannot_reach_another_org(world, other, manager):
    foreign = _bind(_member(other, _user("f")), _role(READ), "ORG", other.org.pk)
    result = _update(world, manager, foreign, expires_at=timezone.now() + timedelta(days=1))
    assert result.ok is False and result.errors[0].code == "NOT_FOUND"
    foreign.refresh_from_db()
    assert foreign.expires_at is None


def test_update_role_binding_needs_manage_members(world):
    plain = _member(world, _user("p"))
    _bind(plain, _role(READ), "ORG", world.org.pk)
    binding = _bind(_member(world, _user("t")), _role(READ), "ORG", world.org.pk)
    result = _update(world, plain, binding, expires_at=timezone.now() + timedelta(days=1))
    assert result.ok is False and result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------
# GroupRoleMapping
# ---------------------------------------------------------------------


def _create_mapping(world, actor, scope, role, group="eng", kind="TEAM"):
    with _as(world, actor):
        return IdentityMutation().create_group_role_mapping(
            _info(actor),
            input=CreateGroupRoleMappingInput(
                group_external_id=group,
                role_id=GUID(str(role.guid)),
                scope_kind=kind,
                scope_guid=GUID(str(scope.guid)),
            ),
        )


def _list_mappings(world, actor, **kw):
    with _as(world, actor):
        return IdentityQuery().astrolift_group_role_mappings_page(_info(actor), **kw)


def test_group_mappings_round_trip(world, manager):
    _member(world, _user("a"), groups=["eng"])
    _member(world, _user("b"), groups=["eng", "ops"])
    role = _role(DEPLOY)

    created = _create_mapping(world, manager, world.medops, role)
    assert created.ok, created.errors
    assert created.data.member_count == 2
    assert created.data.scope_guid == str(world.medops.guid)
    dup = _create_mapping(world, manager, world.medops, role)
    assert dup.ok is False and dup.errors[0].code == "CONFLICT"

    page = _list_mappings(world, manager)
    assert page.total_count == 1 and page.page == 1
    assert page.items[0].group_external_id == "eng"
    assert _list_mappings(world, manager, search="nothing-like-it").total_count == 0

    with _as(world, manager):
        deleted = IdentityMutation().delete_group_role_mapping(
            _info(manager), input=DeleteGroupRoleMappingInput(id=created.data.id)
        )
    assert deleted.ok
    assert _list_mappings(world, manager).total_count == 0


def test_group_mappings_are_org_isolated(world, other, manager, other_manager):
    foreign = GroupRoleMapping.objects.create(
        organization=other.org,
        group_external_id="eng",
        role=_role(READ),
        scope_kind="ORG",
        scope_id=other.org.pk,
    )
    assert _list_mappings(world, manager).total_count == 0
    with _as(world, manager):
        result = IdentityMutation().delete_group_role_mapping(
            _info(manager), input=DeleteGroupRoleMappingInput(id=GUID(str(foreign.guid)))
        )
    assert result.ok is False and result.errors[0].code == "NOT_FOUND"
    assert GroupRoleMapping.objects.filter(pk=foreign.pk).exists()

    into_other = _create_mapping(world, manager, other.medops, _role(READ))
    assert into_other.ok is False and into_other.errors[0].code == "NOT_FOUND"
    foreign_role = _create_mapping(world, manager, world.medops, _role(READ, org=other.org))
    assert foreign_role.ok is False and foreign_role.errors[0].code == "NOT_FOUND"


def test_group_mappings_are_gated_and_capped(world, manager):
    plain = _member(world, _user("p"))
    _bind(plain, _role(READ), "ORG", world.org.pk)
    with pytest.raises(Exception, match="org.manage_members"):
        _list_mappings(world, plain)
    denied = _create_mapping(world, plain, world.medops, _role(READ))
    assert denied.ok is False and denied.errors[0].code == "PERMISSION_DENIED"
    wider = _create_mapping(world, manager, world.org, _role(Permission.ORG_DELETE), kind="ORG")
    assert wider.ok is False and wider.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------
# accessOn
# ---------------------------------------------------------------------


def _access(world, actor, kind, ident, **kw):
    with _as(world, actor):
        return IdentityQuery().astrolift_access_on(_info(actor), scope_kind=kind, scope_id=str(ident), **kw)


def test_access_on_lists_every_source(world, manager):
    direct = _member(world, _user("direct"))
    _bind(direct, _role(READ), "APP", world.medops_app.pk)
    confined = _member(world, _user("confined"))
    _bind(confined, _role(READ), "TEAM", world.medops.pk, inherits=False)
    expired = _member(world, _user("expired"))
    _bind(expired, _role(READ), "APP", world.medops_app.pk, expires_at=timezone.now() - timedelta(minutes=1))
    RoleBinding.objects.create(
        group_external_id="eng", role=_role(DEPLOY), scope_kind="PROJECT", scope_id=world.medops_project.pk
    )
    _member(world, _user("e"), groups=["eng"])
    mapping = GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="ops",
        role=_role(READ),
        scope_kind="APP",
        scope_id=world.medops_app.pk,
    )
    sharer = _member(world, _user("sharer"))
    sharer_binding = _bind(sharer, _role(DEPLOY), "TEAM", world.platform.pk)
    share = AppTeamAccess.objects.create(
        registered_app=world.medops_app, team=world.platform, access_level="viewer"
    )

    page = _access(world, manager, "APP", world.medops_app.guid)
    rows = page.items
    by = {(r.source, r.user.username if r.user else r.group_external_id or r.team_slug): r for r in rows}

    assert ("USER_BINDING", direct.username) in by
    assert by[("USER_BINDING", direct.username)].inherited is False
    assert by[("USER_BINDING", direct.username)].member_id is not None
    # The manager's own org binding reaches the app from an ancestor.
    assert by[("USER_BINDING", manager.username)].inherited is True
    assert by[("USER_BINDING", manager.username)].scope_guid == str(world.org.guid)
    group_row = by[("GROUP_BINDING", "eng")]
    assert group_row.group_member_count == 1 and group_row.inherited is True
    assert by[("GROUP_MAPPING", "ops")].binding_id == str(mapping.guid)
    team_row = by[("TEAM_SHARE", world.platform.slug)]
    assert team_row.principal_kind == "TEAM" and team_row.access_level == "viewer"
    assert team_row.binding_id == str(share.guid)
    via_share = by[("TEAM_SHARE", sharer.username)]
    assert via_share.binding_id == str(sharer_binding.guid) and via_share.share_id == str(share.guid)
    names = {r.user.username for r in rows if r.user}
    assert confined.username not in names and expired.username not in names
    assert page.total_count == len(rows)


def test_access_on_resolves_an_agent_to_its_app(world, manager):
    agent = Workload.objects.create(
        registered_app=world.medops_app, name="Bot", slug="bot", kind=Workload.Kind.AGENT
    )
    direct = _member(world, _user("d"))
    _bind(direct, _role(READ), "APP", world.medops_app.pk)
    rows = _access(world, manager, "AGENT", agent.guid).items
    assert direct.username in {r.user.username for r in rows if r.user}


def test_access_on_pages_and_searches(world, manager):
    for i in range(3):
        _bind(_member(world, _user(f"p{i}")), _role(READ), "APP", world.medops_app.pk)
    first = _access(world, manager, "APP", world.medops_app.guid, page=1, page_size=2)
    second = _access(world, manager, "APP", world.medops_app.guid, page=2, page_size=2)
    assert (first.total_count, first.page, first.page_size, len(first.items)) == (4, 1, 2, 2)
    assert len(second.items) == 2
    assert {r.binding_id for r in first.items}.isdisjoint({r.binding_id for r in second.items})
    only = _access(world, manager, "APP", world.medops_app.guid, search="p1-")
    assert only.total_count == 1


def test_access_on_another_orgs_object_is_empty(world, other, manager):
    _bind(_member(other, _user("x")), _role(READ), "APP", other.medops_app.pk)
    for kind, obj in (("APP", other.medops_app), ("PROJECT", other.medops_project), ("TEAM", other.medops)):
        page = _access(world, manager, kind, obj.guid)
        assert page.items == [] and page.total_count == 0
    # A numeric id of another org's app is not a way in either.
    assert _access(world, manager, "APP", other.medops_app.pk).total_count == 0
    assert _access(world, manager, "APP", "not-a-guid").total_count == 0


def test_access_on_needs_manage_members(world):
    plain = _member(world, _user("p"))
    _bind(plain, _role(READ), "ORG", world.org.pk)
    with pytest.raises(Exception, match="org.manage_members"):
        _access(world, plain, "APP", world.medops_app.guid)


# ---------------------------------------------------------------------
# permissionDiagnose / permissionCompare
# ---------------------------------------------------------------------


def _diagnose(world, caller, target, permission, **kw):
    with _as(world, caller):
        return PermissionAnalysisQuery().permission_diagnose(
            _info(caller), user_id=str(target.pk), permission=permission, **kw
        )


def _steps(diagnosis):
    return {s.check: s for s in diagnosis.steps}


def test_a_manager_diagnoses_a_member_on_a_target_scope(world, manager):
    reba = _member(world, _user("reba"), groups=["eng"])
    _bind(reba, _role(DEPLOY), "TEAM", world.platform.pk)
    AppTeamAccess.objects.create(
        registered_app=world.medops_app, team=world.platform, access_level="deployer"
    )
    Policy.objects.create(
        organization=world.org,
        name="No deploys",
        slug=f"nodeploy-{_tag()}",
        scope_level="ORG",
        effect="DENY",
        action_pattern="app.deploy",
    )

    result = _diagnose(
        world, manager, reba, "app.deploy", scope_type="APP", scope_id=str(world.medops_app.guid)
    )

    steps = _steps(result)
    assert steps["target_scope"].result is True
    assert "eng" in steps["idp_groups"].detail
    assert steps["team_shares"].result is True
    assert steps["rbac"].result is True
    assert steps["abac_policies"].result is False
    assert "nodeploy" in steps["abac_policies"].detail
    assert result.granted is False
    tenant = TenantContext(organization_id=world.org.pk, actor_user_id=reba.pk)
    gate = resolve(tenant, DEPLOY, PermissionScope(kind=ScopeKind.APP, id=world.medops_app.pk))[0]
    assert result.granted is gate


def test_diagnose_on_another_orgs_scope_is_a_failed_step(world, other, manager):
    reba = _member(world, _user("reba"))
    result = _diagnose(
        world, manager, reba, "app.read", scope_type="APP", scope_id=str(other.medops_app.guid)
    )
    assert _steps(result)["target_scope"].result is False
    assert result.granted is False


def test_a_manager_cannot_diagnose_someone_outside_the_org(world, other, manager):
    outsider = _member(other, _user("out"))
    _bind(outsider, _role(READ), "ORG", other.org.pk)
    assert _diagnose(world, manager, outsider, "app.read") is None
    with _as(world, manager):
        assert PermissionAnalysisQuery().effective_permissions(_info(manager), user_id=str(outsider.pk)) == []


def test_a_non_manager_still_diagnoses_only_themself(world):
    plain = _member(world, _user("p"))
    _bind(plain, _role(READ), "ORG", world.org.pk)
    peer = _member(world, _user("peer"))
    assert _diagnose(world, plain, plain, "app.read").granted is True
    with pytest.raises(GraphQLError, match="org.manage_members"):
        _diagnose(world, plain, peer, "app.read")


def test_a_group_grant_shows_in_the_diagnosis(world, manager):
    reba = _member(world, _user("reba"), groups=["eng"])
    RoleBinding.objects.create(
        group_external_id="eng", role=_role(READ), scope_kind="ORG", scope_id=world.org.pk
    )
    result = _diagnose(world, manager, reba, "app.read")
    assert result.granted is True
    assert "via group eng" in _steps(result)["bindings_carrying_this_permission"].detail


def _compare(world, caller, a, b, **kw):
    with _as(world, caller):
        return PermissionAnalysisQuery().permission_compare(
            _info(caller), user_id_a=str(a.pk), user_id_b=str(b.pk), **kw
        )


def test_a_manager_compares_two_members_on_a_scope(world, manager):
    a = _member(world, _user("a"))
    b = _member(world, _user("b"))
    _bind(a, _role(READ, DEPLOY), "APP", world.medops_app.pk)
    _bind(b, _role(READ), "TEAM", world.medops.pk)

    on_app = _compare(world, manager, a, b, scope_type="APP", scope_id=str(world.medops_app.guid))
    assert on_app.only_a == ["app.deploy"] and on_app.shared == ["app.read"]
    on_other_app = _compare(world, manager, a, b, scope_type="APP", scope_id=str(world.platform_app.guid))
    assert on_other_app.shared == [] and on_other_app.only_a == []


def test_compare_is_confined_to_the_org(world, other, manager):
    a = _member(world, _user("a"))
    outsider = _member(other, _user("o"))
    assert _compare(world, manager, a, outsider) is None
    assert _compare(world, manager, a, a, scope_type="APP", scope_id=str(other.medops_app.guid)) is None
    plain = _member(world, _user("p"))
    with pytest.raises(GraphQLError, match="superuser"):
        _compare(world, plain, a, a)
