"""The access UX queries after #2157 (#2126): principal search, grant
preview, policy simulation, condition catalog, single role and policy, and
role lineage.

Every query is checked for org isolation: a caller in one org never sees,
counts or resolves another org's rows.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity import abac
from astrolift_identity.models import GroupRoleMapping, Invitation, Member, Policy, Role, RoleBinding
from astrolift_identity.schema.access_ux import (
    GrantPreviewInput,
    PolicyDraftInput,
    PrincipalRefInput,
    PrincipalSearchFilterInput,
)
from astrolift_identity.schema.mutations import CreateRoleInput, IdentityMutation
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_operations.models import AuditEvent
from core.permissions import Permission
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
    return ScopeWorld(f"x2126-{_tag()}")


@pytest.fixture
def other():
    return ScopeWorld(f"x2126o-{_tag()}")


def _user(prefix="u"):
    email = f"{prefix}-{_tag()}@acme.test"
    return User.objects.create(username=email.split("@")[0], email=email)


def _member(world, user, groups=(), **extra):
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=world.org.pk,
        is_active=True,
        lifecycle=extra.pop("lifecycle", "active"),
        idp_groups=list(groups),
    )
    return user


def _role(*perms, org=None, slug=None):
    return Role.objects.create(
        name=slug or "r",
        slug=slug or f"r-{_tag()}",
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
    user = _member(world, _user("mgr"))
    _bind(
        user,
        _role(Permission.ORG_MANAGE_MEMBERS, Permission.ORG_READ, DEPLOY, READ),
        "ORG",
        world.org.pk,
    )
    return user


@pytest.fixture
def other_manager(other):
    user = _member(other, _user("omgr"))
    _bind(user, _role(Permission.ORG_MANAGE_MEMBERS, Permission.ORG_READ, DEPLOY, READ), "ORG", other.org.pk)
    return user


# ---------------------------------------------------------------------
# Principal search
# ---------------------------------------------------------------------


def _search(world, actor, **kw):
    with _as(world, actor):
        return IdentityQuery().astrolift_principal_search(_info(actor), **kw)


def _seed_principals(world, tag):
    alice = _member(world, _user(f"alice{tag}"), groups=[f"eng{tag}"])
    RoleBinding.objects.create(
        group_external_id=f"ops{tag}", role=_role(READ), scope_kind="TEAM", scope_id=world.medops.pk
    )
    GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id=f"sec{tag}",
        role=_role(READ),
        scope_kind="ORG",
        scope_id=world.org.pk,
    )
    Member.objects.create(user=alice, scope_kind="TEAM", scope_id=world.medops.pk)
    Invitation.objects.create(
        email=f"new{tag}@acme.test", scope_kind="ORG", scope_id=world.org.pk, token_hash="h"
    )
    return alice


def test_principal_search_spans_every_kind(world, manager):
    alice = _seed_principals(world, "zq")
    page = _search(world, manager, search="zq")
    by_kind = {}
    for item in page.items:
        by_kind.setdefault(item.kind, []).append(item)
    assert [i.kind for i in page.items] == ["USER", "GROUP", "GROUP", "GROUP", "INVITATION"]
    assert by_kind["USER"][0].user_id == str(alice.pk) and by_kind["USER"][0].member_id is not None
    assert [g.group_external_id for g in by_kind["GROUP"]] == ["engzq", "opszq", "seczq"]
    eng, ops, sec = by_kind["GROUP"]
    assert (eng.member_count, ops.bindings_count, sec.mappings_count) == (1, 1, 1)
    assert by_kind["INVITATION"][0].name == "newzq@acme.test"
    assert {c.kind: c.count for c in page.counts} == {"USER": 1, "GROUP": 3, "TEAM": 0, "INVITATION": 1}

    teams = _search(
        world, manager, search=world.medops.slug, filter=PrincipalSearchFilterInput(kind=["TEAM"])
    )
    assert [t.team_slug for t in teams.items] == [world.medops.slug]
    assert teams.items[0].member_count == 1
    assert [c.kind for c in teams.counts] == ["TEAM"]


def test_principal_search_pages_across_kinds(world, manager):
    _seed_principals(world, "pg")
    everything = _search(world, manager, search="pg", page_size=50)
    keys = [i.key for i in everything.items]
    seen = []
    for number in (1, 2, 3):
        page = _search(world, manager, search="pg", page=number, page_size=2)
        assert page.total_count == len(keys) and page.page == number
        seen += [i.key for i in page.items]
    assert seen == keys
    assert _search(world, manager, search="pg", page=9, page_size=2).items == []


def test_principal_search_is_org_isolated(world, other, manager, other_manager):
    _seed_principals(other, "iso")
    _member(other, _user("isobob"))
    page = _search(world, manager, search="iso")
    assert page.items == [] and page.total_count == 0
    # The other org's manager sees its own.
    assert _search(other, other_manager, search="iso").total_count > 0
    # An unfiltered search lists this org only.
    everyone = _search(world, manager, page_size=200)
    assert all("iso" not in i.key and "iso" not in i.name for i in everyone.items)
    assert other.medops.slug not in {i.team_slug for i in everyone.items}


def test_principal_search_needs_manage_members(world):
    plain = _member(world, _user("plain"))
    _bind(plain, _role(READ), "ORG", world.org.pk)
    with pytest.raises(Exception, match="org.manage_members"):
        _search(world, plain)


# ---------------------------------------------------------------------
# Grant preview
# ---------------------------------------------------------------------


def _preview(world, actor, **fields):
    with _as(world, actor):
        return IdentityQuery().astrolift_grant_preview(_info(actor), input=GrantPreviewInput(**fields))


def _names(people):
    return {p.user.username for p in people}


def test_grant_to_a_group_counts_members_and_who_already_had_it(world, manager):
    newcomer = _member(world, _user("new"), groups=["eng"])
    veteran = _member(world, _user("vet"), groups=["eng"])
    via = _bind(veteran, _role(DEPLOY), "TEAM", world.medops.pk)
    outsider = _member(world, _user("out"))
    before = RoleBinding.objects.count()

    result = _preview(
        world,
        manager,
        action="GRANT",
        principals=[PrincipalRefInput(kind="GROUP", id="eng")],
        role_id=GUID(str(_role(DEPLOY).guid)),
        scope_kind="APP",
        scope_id=str(world.medops_app.guid),
    )
    assert result.ok, result.errors
    assert result.permissions == [DEPLOY.value]
    assert _names(result.gaining) == {newcomer.username}
    assert _names(result.unchanged) == {veteran.username}
    held = result.unchanged[0]
    assert held.via[0].binding_id == str(via.guid) and held.via[0].inherited is True
    assert held.through == ["group eng"]
    assert outsider.username not in _names(result.gaining + result.unchanged)
    assert [(g.group_external_id, g.member_count) for g in result.groups] == [("eng", 2)]
    assert result.allowed is True
    assert "1 person gain" in result.summary
    assert RoleBinding.objects.count() == before  # read-only


def test_grant_to_a_team_expands_its_members(world, manager):
    on_team = _member(world, _user("t"))
    Member.objects.create(user=on_team, scope_kind="TEAM", scope_id=world.platform.pk)
    result = _preview(
        world,
        manager,
        action="GRANT",
        principals=[PrincipalRefInput(kind="TEAM", id=str(world.platform.guid))],
        role_id=GUID(str(_role(READ).guid)),
        scope_kind="PROJECT",
        scope_id=str(world.platform_project.guid),
    )
    assert result.ok and _names(result.gaining) == {on_team.username}
    assert result.gaining[0].through == [f"team {world.platform.slug}"]
    assert any("cannot hold a binding" in n for n in result.notes)


def test_remove_says_who_loses_and_who_keeps_it(world, manager):
    group_role = _role(DEPLOY)
    binding = RoleBinding.objects.create(
        group_external_id="eng", role=group_role, scope_kind="APP", scope_id=world.medops_app.pk
    )
    loser = _member(world, _user("lose"), groups=["eng"])
    keeper = _member(world, _user("keep"), groups=["eng"])
    _bind(keeper, _role(DEPLOY), "PROJECT", world.medops_project.pk)

    result = _preview(world, manager, action="REMOVE", binding_id=GUID(str(binding.guid)))
    assert result.ok, result.errors
    assert _names(result.losing) == {loser.username}
    assert result.losing[0].lost == [DEPLOY.value]
    assert _names(result.unchanged) == {keeper.username}
    assert result.unchanged[0].kept == [DEPLOY.value]
    assert RoleBinding.objects.filter(pk=binding.pk).exists()


def test_change_role_gains_and_loses(world, manager):
    person = _member(world, _user("chg"))
    binding = _bind(person, _role(READ), "APP", world.medops_app.pk)
    result = _preview(
        world,
        manager,
        action="CHANGE",
        binding_id=GUID(str(binding.guid)),
        role_id=GUID(str(_role(DEPLOY).guid)),
    )
    assert result.ok, result.errors
    assert result.gaining[0].gained == [DEPLOY.value]
    assert result.losing[0].lost == [READ.value]
    assert (result.gaining_count, result.losing_count, result.unchanged_count) == (1, 1, 0)


def test_preview_reports_the_grant_ceiling(world):
    narrow = _member(world, _user("narrow"))
    _bind(narrow, _role(Permission.ORG_MANAGE_MEMBERS, READ), "ORG", world.org.pk)
    target = _member(world, _user("tgt"))
    result = _preview(
        world,
        narrow,
        action="GRANT",
        principals=[PrincipalRefInput(kind="USER", id=str(target.pk))],
        role_id=GUID(str(_role(DEPLOY).guid)),
        scope_kind="ORG",
        scope_id=str(world.org.guid),
    )
    assert result.ok and result.allowed is False and result.refusal


def test_grant_preview_is_org_isolated(world, other, manager):
    stranger = _member(other, _user("stranger"))
    foreign_role = _role(READ, org=other.org)
    foreign_binding = _bind(stranger, _role(READ), "APP", other.medops_app.pk)
    mine = _role(READ)

    def grant(**over):
        fields = {
            "action": "GRANT",
            "principals": [PrincipalRefInput(kind="USER", id=str(manager.pk))],
            "role_id": GUID(str(mine.guid)),
            "scope_kind": "APP",
            "scope_id": str(world.medops_app.guid),
        }
        fields.update(over)
        return _preview(world, manager, **fields)

    assert grant(role_id=GUID(str(foreign_role.guid))).errors == ["role not found"]
    assert grant(scope_id=str(other.medops_app.guid)).errors == ["scope not found"]
    assert grant(scope_id=str(other.medops_app.pk)).errors == ["scope not found"]
    user_ref = [PrincipalRefInput(kind="USER", id=str(stranger.pk))]
    assert grant(principals=user_ref).ok is False
    team_ref = [PrincipalRefInput(kind="TEAM", id=str(other.medops.guid))]
    assert grant(principals=team_ref).ok is False
    removed = _preview(world, manager, action="REMOVE", binding_id=GUID(str(foreign_binding.guid)))
    assert removed.errors == ["role binding not found"]
    # A group reaches only this org's members, even with the same group name.
    _member(other, _user("ogroup"), groups=["shared"])
    mine_in_group = _member(world, _user("mgroup"), groups=["shared"])
    result = grant(principals=[PrincipalRefInput(kind="GROUP", id="shared")])
    assert _names(result.gaining + result.unchanged) == {mine_in_group.username}


# ---------------------------------------------------------------------
# Policy simulation
# ---------------------------------------------------------------------


def _simulate(world, actor, **draft):
    days = draft.pop("days", None)
    with _as(world, actor):
        return IdentityQuery().astrolift_policy_simulation(
            _info(actor), draft=PolicyDraftInput(**draft), days=days
        )


def test_simulation_finds_the_holders_it_denies(world, manager):
    deployer = _member(world, _user("dep"))
    _bind(deployer, _role(DEPLOY), "TEAM", world.medops.pk)
    grouped = _member(world, _user("grp"), groups=["eng"])
    GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="eng",
        role=_role(DEPLOY),
        scope_kind="ORG",
        scope_id=world.org.pk,
    )
    reader = _member(world, _user("rdr"))
    _bind(reader, _role(READ), "ORG", world.org.pk)

    result = _simulate(world, manager, action_pattern="app.deploy")
    assert result.ok, result.errors
    assert result.actions == [DEPLOY.value]
    assert result.sources == ["HOLDERS"] and result.audit_recorded is False
    denied = {h.user.username for h in result.holders if h.outcome == "DENIED"}
    assert {deployer.username, grouped.username, manager.username} <= denied
    assert reader.username not in {h.user.username for h in result.holders}
    assert result.holders_denied_count == result.holders_count == len(denied)


def test_simulation_marks_what_it_cannot_know(world, manager):
    deployer = _member(world, _user("dep"))
    _bind(deployer, _role(DEPLOY), "APP", world.medops_app.pk)
    result = _simulate(
        world,
        manager,
        action_pattern="app.deploy",
        conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}],
    )
    row = next(h for h in result.holders if h.user.username == deployer.username)
    assert row.outcome == "UNKNOWN" and row.unknown == [DEPLOY.value]
    always = [{"kind": "time_window", "days": list(abac.WEEKDAYS), "hours": ["00:00-24:00"]}]
    held = _simulate(world, manager, action_pattern="app.deploy", conditions=always)
    assert held.holders_denied_count == 0 and held.holders_unknown_count == 0
    broken = _simulate(world, manager, action_pattern="app.deploy", conditions=[{"kind": "nope"}])
    assert broken.holders_denied_count == broken.holders_count > 0


def test_simulation_on_one_scope_checks_who_reaches_it(world, manager):
    on_app = _member(world, _user("onapp"))
    _bind(on_app, _role(DEPLOY), "APP", world.medops_app.pk)
    elsewhere = _member(world, _user("else"))
    _bind(elsewhere, _role(DEPLOY), "APP", world.platform_app.pk)
    result = _simulate(
        world, manager, action_pattern="app.*", scope_level="APP", scope_id=str(world.medops_app.guid)
    )
    names = {h.user.username for h in result.holders}
    assert on_app.username in names and manager.username in names
    assert elsewhere.username not in names
    by_slug = _simulate(
        world, manager, action_pattern="app.deploy", resource_pattern={"app_slug": [world.platform_app.slug]}
    )
    assert elsewhere.username in {h.user.username for h in by_slug.holders}
    assert on_app.username not in {h.user.username for h in by_slug.holders}


def test_simulation_replays_recorded_decisions(world, manager):
    AuditEvent.objects.create(
        organization=world.org,
        actor_kind="user",
        actor_id=str(manager.pk),
        action="app.deploy",
        decision="ALLOW",
        data={"permissions": [DEPLOY.value]},
    )
    AuditEvent.objects.create(
        organization=world.org,
        actor_kind="user",
        actor_id=str(manager.pk),
        action="team.update",
        decision="ALLOW",
        data={"permissions": [Permission.TEAM_UPDATE.value]},
    )
    result = _simulate(world, manager, action_pattern="app.deploy")
    assert result.sources == ["HOLDERS", "AUDIT"] and result.audit_recorded
    assert result.decisions_evaluated == 1 and result.decisions_denied_count == 1
    assert result.decisions[0].action == "app.deploy" and result.decisions[0].outcome == "DENIED"
    scoped = _simulate(
        world, manager, action_pattern="app.deploy", scope_level="APP", scope_id=str(world.medops_app.guid)
    )
    assert scoped.decisions[0].outcome == "UNKNOWN"


def test_simulation_is_org_isolated(world, other, manager):
    stranger = _member(other, _user("ostr"))
    _bind(stranger, _role(DEPLOY), "ORG", other.org.pk)
    AuditEvent.objects.create(
        organization=other.org,
        actor_kind="user",
        actor_id=str(stranger.pk),
        action="app.deploy",
        decision="ALLOW",
        data={"permissions": [DEPLOY.value]},
    )
    result = _simulate(world, manager, action_pattern="app.deploy")
    assert stranger.username not in {h.user.username for h in result.holders}
    assert result.audit_recorded is False and result.decisions_evaluated == 0
    foreign = _simulate(
        world, manager, action_pattern="*", scope_level="APP", scope_id=str(other.medops_app.guid)
    )
    assert foreign.ok is False and foreign.errors == ["scope not found"]
    slugged = _simulate(
        world, manager, action_pattern="app.deploy", resource_pattern={"app_slug": [other.medops_app.slug]}
    )
    assert slugged.holders_count == 0


def test_simulation_needs_manage_members(world):
    plain = _member(world, _user("plain"))
    _bind(plain, _role(Permission.ORG_READ), "ORG", world.org.pk)
    with pytest.raises(Exception, match="org.manage_members"):
        _simulate(world, plain)


# ---------------------------------------------------------------------
# Condition catalog
# ---------------------------------------------------------------------


def _subject(world, user, **attrs):
    return abac.new_subject(
        organization_id=world.org.pk,
        actor_user_id=user.pk,
        permission=DEPLOY.value,
        chain=[("ORG", world.org.pk)],
        roles_at_target=[],
        groups=[],
        attrs=abac.RequestAttributes(actor_user_id=user.pk, **attrs),
    )


def test_the_catalog_is_the_evaluator(world, manager):
    with _as(world, manager):
        catalog = IdentityQuery().astrolift_policy_condition_catalog(_info(manager))
    assert {k.kind for k in catalog.conditions} == set(abac._HANDLERS)
    assert {k.key for k in catalog.resource_keys} == {"app_slug", "project_slug", "env", "region"}
    assert {k.key for k in catalog.actor_keys} == {"user_in_groups", "user_role_at_scope"}
    assert catalog.effects == ["ALLOW", "DENY"]

    subject = _subject(
        world,
        manager,
        client_ip="10.1.2.3",
        environment="staging",
        approvals=3,
        authenticated_at=timezone.now(),
        auth_factors=frozenset({"webauthn"}),
    )
    for kind in abac.CONDITION_KINDS:
        outcome = abac._condition(dict(kind.example), subject)
        assert outcome.holds is not None, (kind.kind, outcome.detail)
        for field in kind.fields:
            if field.required:
                missing = {k: v for k, v in kind.example.items() if k != field.name}
                assert abac._condition(missing, subject).holds is None, (kind.kind, field.name)

    for key in abac.RESOURCE_KEYS:
        _matched, why = abac._match_resource({key.key: ["*"]}, subject)
        assert "unknown resource key" not in why
    assert "unknown resource key" in abac._match_resource({"cluster": ["x"]}, subject)[1]
    for key in abac.ACTOR_KEYS:
        assert "unknown actor key" not in abac._match_actor({key.key: ["x"]}, subject)[1]


def test_catalog_needs_org_read(world):
    nobody = _member(world, _user("nobody"))
    with pytest.raises(Exception, match="org.read"):
        with _as(world, nobody):
            IdentityQuery().astrolift_policy_condition_catalog(_info(nobody))


# ---------------------------------------------------------------------
# Single role and policy, role lineage
# ---------------------------------------------------------------------


def _create_role(world, actor, **fields):
    with _as(world, actor):
        return IdentityMutation().create_role(_info(actor), input=CreateRoleInput(**fields))


def test_create_role_records_what_it_duplicated(world, other, manager):
    system = Role.objects.create(
        name="Viewer", slug=f"viewer-{_tag()}", scope_level="ORG", permissions=[READ.value], is_system=True
    )
    made = _create_role(
        world,
        manager,
        slug=f"copy-{_tag()}",
        name="Copy",
        scope_level="ORG",
        permissions=[READ.value, DEPLOY.value],
        duplicated_from_id=GUID(str(system.guid)),
    )
    assert made.ok, made.errors
    copy = Role.objects.get(guid=str(made.data.id))
    assert copy.duplicated_from_id == system.pk

    foreign = _role(READ, org=other.org)
    refused = _create_role(
        world,
        manager,
        slug=f"steal-{_tag()}",
        name="Steal",
        scope_level="ORG",
        permissions=[READ.value],
        duplicated_from_id=GUID(str(foreign.guid)),
    )
    assert refused.ok is False and refused.errors[0].code == "NOT_FOUND"

    with _as(world, manager):
        detail = IdentityQuery().astrolift_role(_info(manager), id=GUID(str(copy.guid)))
        page = IdentityQuery().astrolift_roles_page(_info(manager), search=copy.slug, page=1)
    assert detail.duplicated_from.id == str(system.guid)
    assert detail.duplicated_from.permissions == [READ.value]
    assert page.items[0].duplicated_from.slug == system.slug


def test_single_role_counts_this_orgs_bindings_only(world, other, manager, other_manager):
    shared = Role.objects.create(
        name="Shared", slug=f"shared-{_tag()}", scope_level="ORG", permissions=[READ.value], is_system=True
    )
    _bind(_member(world, _user("a")), shared, "ORG", world.org.pk)
    _bind(_member(other, _user("b")), shared, "ORG", other.org.pk)
    _bind(_member(other, _user("c")), shared, "ORG", other.org.pk)
    with _as(world, manager):
        role = IdentityQuery().astrolift_role(_info(manager), id=GUID(str(shared.guid)))
    assert role.bindings_count == 1


def test_single_role_and_policy_are_org_isolated(world, other, manager):
    own_role = _role(READ, org=world.org)
    foreign_role = _role(READ, org=other.org)
    own_policy = Policy.objects.create(
        organization=world.org, name="P", slug=f"p-{_tag()}", scope_level="ORG", action_pattern="none.none"
    )
    foreign_policy = Policy.objects.create(
        organization=other.org, name="Q", slug=f"q-{_tag()}", scope_level="ORG", action_pattern="none.none"
    )
    with _as(world, manager):
        q = IdentityQuery()
        assert q.astrolift_role(_info(manager), id=GUID(str(own_role.guid))).id == str(own_role.guid)
        assert q.astrolift_role(_info(manager), id=GUID(str(foreign_role.guid))) is None
        assert q.astrolift_role(_info(manager), id=GUID("not-a-guid")) is None
        assert q.astrolift_policy(_info(manager), id=GUID(str(own_policy.guid))).slug == own_policy.slug
        assert q.astrolift_policy(_info(manager), id=GUID(str(foreign_policy.guid))) is None


def test_expired_bindings_do_not_count_as_held(world, manager):
    lapsed = _member(world, _user("lapsed"), groups=["eng"])
    _bind(lapsed, _role(DEPLOY), "APP", world.medops_app.pk, expires_at=timezone.now() - timedelta(minutes=1))
    result = _preview(
        world,
        manager,
        action="GRANT",
        principals=[PrincipalRefInput(kind="GROUP", id="eng")],
        role_id=GUID(str(_role(DEPLOY).guid)),
        scope_kind="APP",
        scope_id=str(world.medops_app.guid),
    )
    assert _names(result.gaining) == {lapsed.username}
