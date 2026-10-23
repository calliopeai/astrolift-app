"""Another organization's teams, projects and people read as missing (#1866).

The registry's ownership and approval mutations take a team, a project or a
person by id. Each used to fetch the id across the whole install and refuse a
foreign one afterwards with a different answer from a missing one
(PRECONDITION, PERMISSION_DENIED, or another NOT_FOUND message), so the answer
said the guid existed. Approver user pks were worse: any user on the install
was accepted, and a pending deploy then showed that person's name and email in
``awaitingApprovers``. Pks are sequential, so that enumerated the install.

Every lookup now carries the organization, so a foreign guid or pk gets the
envelope a missing one gets and nothing is written. An app's approvers are
the organization's active members, the people the approval picker offers, and
stored rows naming anyone else neither show nor count.

The caller holds a real organization role binding, so the gates run the way
they do in production.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Member, Organization, Project, Team
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations.helpers import _is_eligible_approver
from astrolift_lifecycle.schema.types import deployment_to_type
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.schema.mutations import (
    AssignAppToProjectInput,
    GrantTeamAccessInput,
    MoveAppToTeamInput,
    RegisterAgentRepoInput,
    RegisterAppInput,
    RegisterAppRepoInput,
    RegistryMutation,
    RevokeTeamAccessInput,
    TransferAppInput,
    UpdateAppInput,
)
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


def _org_member(user, org, **fields):
    return Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk, **fields)


@pytest.fixture
def world(seed_cluster):
    world = ScopeWorld("xorg-1866")
    world.user = make_user("xorg-1866")
    _org_member(world.user, world.org)
    bind_role(
        world.user,
        permissions=(
            Permission.APP_CREATE,
            Permission.APP_UPDATE,
            Permission.APP_TRANSFER,
            Permission.AGENT_CREATE,
            Permission.ORG_MANAGE_MEMBERS,
        ),
        kind="ORG",
        scope_id=world.org.pk,
        slug="xorg-1866-admin",
    )
    world.info = make_info(world.user)
    world.cluster = seed_cluster(world.org, slug="xorg-1866")
    world.foreign_org = Organization.objects.create(name="Globex", slug="globex-xorg-1866")
    world.foreign_team = Team.objects.create(organization=world.foreign_org, name="Ops", slug="ops-xorg-1866")
    world.foreign_project = Project.objects.create(
        organization=world.foreign_org, team=world.foreign_team, name="Ops", slug="ops-xorg-1866"
    )
    return world


def _envelope(result):
    return result.ok, result.data, [dataclasses.asdict(error) for error in result.errors]


def _state(world):
    """Every row these mutations write: apps, team grants, approver lists."""
    apps = RegisteredApp.all_objects.order_by("pk")
    return (
        list(
            apps.values_list(
                "pk", "team_id", "project_id", "approver_team_id", "requires_approval", "version"
            )
        ),
        set(AppTeamAccess.all_objects.values_list("pk", "team_id", "access_level", "deleted_at")),
        {app.pk: set(app.approver_users.values_list("pk", flat=True)) for app in apps},
    )


def _app_id(world):
    return str(world.medops_app.guid)


ROUTES = {
    "transferApp team": (
        "team",
        lambda world, guid: RegistryMutation().transfer_app(
            world.info, input=TransferAppInput(app_id=_app_id(world), target_team_id=guid)
        ),
        ("target team not found", "targetTeamId"),
    ),
    "transferApp project": (
        "project",
        lambda world, guid: RegistryMutation().transfer_app(
            world.info, input=TransferAppInput(app_id=_app_id(world), target_project_id=guid)
        ),
        ("target project not found", "targetProjectId"),
    ),
    "assignAstroliftAppToProject": (
        "project",
        lambda world, guid: RegistryMutation().assign_astrolift_app_to_project(
            world.info, input=AssignAppToProjectInput(app_slug=world.medops_app.slug, project_guid=guid)
        ),
        ("project not found", "projectGuid"),
    ),
    "moveAppToTeam": (
        "team",
        lambda world, guid: RegistryMutation().move_app_to_team(
            world.info, input=MoveAppToTeamInput(app_id=_app_id(world), target_team_id=guid)
        ),
        ("target team not found", "targetTeamId"),
    ),
    "grantTeamAccessToApp": (
        "team",
        lambda world, guid: RegistryMutation().grant_team_access_to_app(
            world.info, input=GrantTeamAccessInput(app_id=_app_id(world), team_id=guid, access_level="owner")
        ),
        ("team not found", "teamId"),
    ),
    "revokeTeamAccessFromApp": (
        "team",
        lambda world, guid: RegistryMutation().revoke_team_access_from_app(
            world.info, input=RevokeTeamAccessInput(app_id=_app_id(world), team_id=guid)
        ),
        ("team not found", "teamId"),
    ),
    "updateApp approver team": (
        "team",
        lambda world, guid: RegistryMutation().update_app(
            world.info, input=UpdateAppInput(id=_app_id(world), requires_approval=True, approver_team_id=guid)
        ),
        ("approver team not found", "approverTeamId"),
    ),
    "registerApp approver team": (
        "team",
        lambda world, guid: RegistryMutation().register_app(
            world.info,
            input=RegisterAppInput(
                project_id=str(world.medops_project.guid),
                slug="xorg-new",
                source_repo="acme/xorg-new",
                requires_approval=True,
                approver_team_id=guid,
            ),
        ),
        ("approver team not found", "approverTeamId"),
    ),
    "registerApp project": (
        "project",
        lambda world, guid: RegistryMutation().register_app(
            world.info, input=RegisterAppInput(project_id=guid, slug="xorg-new", source_repo="acme/xorg-new")
        ),
        ("project not found", "projectId"),
    ),
    "registerAgentRepo": (
        "project",
        lambda world, guid: RegistryMutation().register_agent_repo(
            world.info, input=RegisterAgentRepoInput(project_id=guid, source_repo="acme/agents")
        ),
        ("project not found", "projectId"),
    ),
    "registerAppRepo": (
        "project",
        lambda world, guid: RegistryMutation().register_app_repo(
            world.info, input=RegisterAppRepoInput(project_id=guid, source_repo="acme/monorepo")
        ),
        ("project not found", "projectId"),
    ),
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_a_foreign_guid_reads_like_a_missing_one(world, route):
    kind, call, (message, field) = ROUTES[route]
    foreign = {"team": world.foreign_team, "project": world.foreign_project}[kind]
    before = _state(world)

    with as_tenant(world, world.user):
        from_foreign = call(world, str(foreign.guid))
        from_missing = call(world, str(uuid4()))

    assert _envelope(from_foreign) == _envelope(from_missing)
    assert [(e.code, e.message, e.field) for e in from_foreign.errors] == [("NOT_FOUND", message, field)]
    assert _state(world) == before


# ---- approver users ----------------------------------------------------


@pytest.fixture
def people(world):
    """One user in each membership state the approver list tells apart."""
    member = make_user("xorg-member")
    _org_member(member, world.org)
    foreign = make_user("xorg-foreign")
    _org_member(foreign, world.foreign_org)
    removed = make_user("xorg-removed")
    _org_member(removed, world.org, deleted_at=timezone.now())
    deactivated = make_user("xorg-deactivated")
    _org_member(deactivated, world.org, is_active=False, lifecycle=Member.Lifecycle.DEACTIVATED)
    disabled = make_user("xorg-disabled")
    disabled.is_active = False
    disabled.save(update_fields=["is_active"])
    _org_member(disabled, world.org)
    team_only = make_user("xorg-team-only")
    Member.objects.create(user=team_only, scope_kind=Member.ScopeKind.TEAM, scope_id=world.medops.pk)
    # Scope ids are per kind: a project membership whose id equals the
    # org's pk is not an org membership.
    same_id = make_user("xorg-same-id")
    Member.objects.create(user=same_id, scope_kind=Member.ScopeKind.PROJECT, scope_id=world.org.pk)
    return SimpleNamespace(
        member=member,
        foreign=foreign,
        removed=removed,
        deactivated=deactivated,
        disabled=disabled,
        team_only=team_only,
        same_id=same_id,
    )


def _missing_pk():
    return (get_user_model().objects.order_by("-pk").values_list("pk", flat=True).first() or 0) + 1000


def _set_approvers(world, pk, *, register):
    with as_tenant(world, world.user):
        if register:
            return RegistryMutation().register_app(
                world.info,
                input=RegisterAppInput(
                    project_id=str(world.medops_project.guid),
                    slug="xorg-approved",
                    source_repo="acme/xorg-approved",
                    requires_approval=True,
                    approver_user_ids=[str(pk)],
                ),
            )
        return RegistryMutation().update_app(
            world.info, input=UpdateAppInput(id=_app_id(world), approver_user_ids=[str(pk)])
        )


def _shape(result, pk):
    """The envelope with the refused pk masked, so two refusals compare."""
    ok, data, errors = _envelope(result)
    return ok, data, [{**error, "message": error["message"].replace(str(pk), "<pk>")} for error in errors]


@pytest.mark.parametrize("register", [False, True], ids=["updateApp", "registerApp"])
@pytest.mark.parametrize("state", ["foreign", "removed", "deactivated", "disabled", "team_only", "same_id"])
def test_an_approver_outside_the_organization_reads_like_an_unknown_user(world, people, state, register):
    person = getattr(people, state)
    missing = _missing_pk()
    before = _state(world)

    refused = _set_approvers(world, person.pk, register=register)
    unknown = _set_approvers(world, missing, register=register)

    assert _shape(refused, person.pk) == _shape(unknown, missing)
    assert [(e.code, e.message, e.field) for e in refused.errors] == [
        ("NOT_FOUND", f"approver users not found: [{person.pk}]", "approverUserIds")
    ]
    assert _state(world) == before


def test_one_outsider_refuses_the_whole_list(world, people):
    with as_tenant(world, world.user):
        result = RegistryMutation().update_app(
            world.info,
            input=UpdateAppInput(
                id=_app_id(world), approver_user_ids=[str(people.member.pk), str(people.foreign.pk)]
            ),
        )

    assert [(e.code, e.message) for e in result.errors] == [
        ("NOT_FOUND", f"approver users not found: [{people.foreign.pk}]")
    ]
    assert not world.medops_app.approver_users.exists()


def test_the_list_accepts_exactly_the_people_the_picker_offers(world, people):
    with as_tenant(world, world.user):
        offered = {
            int(user.id)
            for user in IdentityQuery().astrolift_org_members_for_approval_picker(
                world.info, org_slug=world.org.slug
            )
        }
    candidates = [world.user, *vars(people).values()]
    accepted = {user.pk for user in candidates if _set_approvers(world, user.pk, register=False).ok}

    assert offered == accepted == {world.user.pk, people.member.pk}
    assert set(world.medops_app.approver_users.values_list("pk", flat=True)) == {people.member.pk}


# ---- rows stored before the fix -----------------------------------------


@pytest.fixture
def pending(world):
    """A deploy of the MedOps app waiting on one approval."""
    app = world.medops_app
    app.requires_approval = True
    app.minimum_approvals = 1
    app.save(update_fields=["requires_approval", "minimum_approvals"])
    env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=world.cluster, name="production", url="", required_approvals=1
    )
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.PENDING_APPROVAL.value,
        image_tag="v1",
        approvals_required=1,
    )


def test_a_stored_outsider_neither_shows_nor_counts(world, people, pending):
    app = world.medops_app
    outsiders = [person for name, person in vars(people).items() if name != "member"]
    app.approver_users.set([people.member, *outsiders])

    awaiting = deployment_to_type(pending).awaiting_approvers

    assert [(a.user_id, a.email) for a in awaiting] == [(str(people.member.pk), people.member.email)]
    assert _is_eligible_approver(app, user_id=people.member.pk)
    for outsider in outsiders:
        assert not _is_eligible_approver(app, user_id=outsider.pk), outsider.username


def test_a_list_with_no_member_left_refuses_everyone(world, people, pending):
    # Fail closed: the outsider no longer counts, but the list still names
    # someone, so it does not open to every approve_deploy holder.
    world.medops_app.approver_users.set([people.foreign])

    assert not _is_eligible_approver(world.medops_app, user_id=world.user.pk)
    assert not _is_eligible_approver(world.medops_app, user_id=people.member.pk)
    assert people.foreign.email not in {a.email for a in deployment_to_type(pending).awaiting_approvers}


# ---- the approver team, and a policy nobody can satisfy ----------------


@pytest.fixture
def holder(world):
    """An org member holding ``app.approve_deploy`` at the org: the person
    awaited, and admitted, while the app names no approver at all."""
    user = make_user("xorg-holder")
    _org_member(user, world.org)
    bind_role(
        user,
        permissions=(Permission.APP_APPROVE_DEPLOY,),
        kind="ORG",
        scope_id=world.org.pk,
        slug="xorg-1866-approver",
    )
    return user


def _on_team(person, team):
    Member.objects.create(user=person, scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk)


def _awaiting(pending):
    return [int(a.user_id) for a in deployment_to_type(pending).awaiting_approvers]


def test_an_approver_team_counts_only_the_organizations_active_members(world, people, pending):
    # SCIM deprovisioning deactivates the ORG row and leaves the TEAM rows,
    # so a person who left the org is still on the team.
    app = world.medops_app
    for name, person in vars(people).items():
        if name != "team_only":  # already on MedOps
            _on_team(person, world.medops)
    app.approver_team = world.medops
    app.save(update_fields=["approver_team"])

    assert _awaiting(pending) == [people.member.pk]
    assert _is_eligible_approver(app, user_id=people.member.pk)
    for name, person in vars(people).items():
        if name != "member":
            assert not _is_eligible_approver(app, user_id=person.pk), name


def _outsiders_listed(world, people):
    world.medops_app.approver_users.set([people.foreign, people.removed])


def _disabled_listed(world, people):
    world.medops_app.approver_users.set([people.disabled, people.deactivated])


def _empty_team(world, people):
    world.medops_app.approver_team = world.platform
    world.medops_app.save(update_fields=["approver_team"])


def _team_of_leavers(world, people):
    _on_team(people.deactivated, world.platform)
    _on_team(people.removed, world.platform)
    _empty_team(world, people)


@pytest.mark.parametrize(
    "policy", [_outsiders_listed, _disabled_listed, _empty_team, _team_of_leavers], ids=lambda f: f.__name__
)
def test_a_policy_nobody_can_satisfy_awaits_nobody(world, people, pending, holder, policy):
    """The gate refuses every approver here, the org's approve_deploy
    holders included, so the widget awaits nobody and says to check the
    policy, rather than asking those holders for a vote it would refuse."""
    app = world.medops_app
    # Control: with no list and no team the holder is awaited and admitted.
    assert _awaiting(pending) == [holder.pk]
    assert _is_eligible_approver(app, user_id=holder.pk)

    policy(world, people)

    assert _awaiting(pending) == []
    assert not _is_eligible_approver(app, user_id=holder.pk)
