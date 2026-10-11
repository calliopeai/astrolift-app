"""Actual scoped HTTP/PG reviewed writes and source-authority acceptance."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import (
    ApiToken,
    GroupRoleMapping,
    Member,
    Organization,
    Role,
    RoleBinding,
    ScimGroup,
    Team,
    TeamMembershipAction,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    user = get_user_model()
    org = Organization.objects.create(name="Teams", slug="teams-2273")
    other_org = Organization.objects.create(name="Other", slug="other-2273")
    actor = user.objects.create_user(username="manager-2273", email="manager@example.test")
    subject = user.objects.create_user(username="person-2273", email="person@example.test")
    actor_member = Member.objects.create(user=actor, scope_kind="ORG", scope_id=org.pk)
    member = Member.objects.create(user=subject, scope_kind="ORG", scope_id=org.pk)
    team = Team.objects.create(organization=org, name="Team", slug="team-2273")
    sibling = Team.objects.create(organization=org, name="Sibling", slug="sibling-2273")
    foreign = Team.objects.create(organization=other_org, name="Foreign", slug="foreign-2273")
    manager = Role.objects.create(
        organization=org,
        name="Team manager",
        slug="manager-2273",
        scope_level="TEAM",
        permissions=["team.read", "team.manage_members"],
    )
    role = Role.objects.create(
        organization=org,
        name="Team reader",
        slug="reader-2273",
        scope_level="TEAM",
        permissions=["team.read"],
    )
    actor_binding = RoleBinding.objects.create(user=actor, role=manager, scope_kind="TEAM", scope_id=team.pk)
    client = Client()
    client.force_login(actor)
    return SimpleNamespace(
        org=org,
        other_org=other_org,
        actor=actor,
        subject=subject,
        actor_member=actor_member,
        member=member,
        team=team,
        sibling=sibling,
        foreign=foreign,
        manager=manager,
        role=role,
        actor_binding=actor_binding,
        client=client,
    )


def http(w, query, variables=None, *, client=None, bearer=None):
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid), "HTTP_X_PLATFORM": "WEB"}
    if bearer:
        headers["HTTP_AUTHORIZATION"] = "Bearer " + bearer
    response = (client or w.client).post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200
    return response.json()


REVIEW = """query($team:GUID!, $person:GUID!, $kind:TeamMembershipChangeKind!, $role:GUID){astroliftTeamMembershipReview(teamId:$team,orgMemberId:$person,kind:$kind,roleId:$role){expectedSource roles{id} membership{teamMemberId canRemove sources{id source scopeKind expired removable}} remainingSources{id source scopeKind}}}"""
CHANGE = """mutation($input:ChangeAstroliftTeamMembershipInput!){changeAstroliftTeamMembership(input:$input){ok errors{code message} data{requestId changeId committed replayed teamId orgMemberId teamMemberId removedBindingIds remainingSources{id source}}}}"""


def reviewed(w, kind="ADD", role=True):
    result = http(
        w,
        REVIEW,
        {
            "team": str(w.team.guid),
            "person": str(w.member.guid),
            "kind": kind,
            "role": str(w.role.guid) if role else None,
        },
    )
    assert not result.get("errors"), result
    return result["data"]["astroliftTeamMembershipReview"]


def command(w, r, kind="ADD", role=True):
    return {
        "requestId": str(uuid.uuid4()),
        "kind": kind,
        "teamId": str(w.team.guid),
        "orgMemberId": str(w.member.guid),
        "roleId": str(w.role.guid) if role else None,
        "expectedSource": r["expectedSource"],
    }


def mutate(w, input, **kwargs):
    result = http(w, CHANGE, {"input": input}, **kwargs)
    assert not result.get("errors"), result
    return result["data"]["changeAstroliftTeamMembership"]


def direct(w, role=None, expired=False):
    member, _ = Member.objects.get_or_create(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk)
    binding = RoleBinding.objects.create(
        user=w.subject,
        role=role or w.role,
        scope_kind="TEAM",
        scope_id=w.team.pk,
        expires_at=timezone.now() - timedelta(minutes=1) if expired else None,
    )
    return member, binding


def test_manager_add_without_org_manage_and_original_receipt_replay(world):
    w = world
    assert "org.manage_members" not in w.manager.permissions
    input = command(w, reviewed(w))
    first = mutate(w, input)
    replay = mutate(w, input)
    assert first["ok"] and first["data"]["committed"] and not first["data"]["replayed"]
    assert replay["ok"] and replay["data"]["replayed"]
    assert first["data"]["changeId"] == replay["data"]["changeId"]
    assert Member.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).count() == 1
    assert RoleBinding.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).count() == 1
    assert TeamMembershipAction.objects.count() == 1
    result = TeamMembershipAction.objects.get().result
    assert set(result) == {"teamId", "orgMemberId", "teamMemberId", "removedBindingIds", "remainingSources"}
    assert w.subject.email not in json.dumps(result)


def test_remove_only_reviewed_direct_expired_and_live_grants_preserves_derived_other_access(world):
    w = world
    member, expired = direct(w, expired=True)
    second = Role.objects.create(
        organization=w.org,
        name="Second reader",
        slug="second-2273",
        scope_level="TEAM",
        permissions=["team.read"],
    )
    live = RoleBinding.objects.create(user=w.subject, role=second, scope_kind="TEAM", scope_id=w.team.pk)
    inherited_role = Role.objects.create(
        organization=w.org,
        name="Inherited",
        slug="inherited-2273",
        scope_level="ORG",
        permissions=["team.read"],
    )
    inherited = RoleBinding.objects.create(
        user=w.subject, role=inherited_role, scope_kind="ORG", scope_id=w.org.pk
    )
    other = RoleBinding.objects.create(user=w.subject, role=w.role, scope_kind="TEAM", scope_id=w.sibling.pk)
    w.member.idp_groups = ["group-2273"]
    w.member.save()
    group = RoleBinding.objects.create(
        group_external_id="group-2273", role=w.role, scope_kind="TEAM", scope_id=w.team.pk
    )
    mapping = GroupRoleMapping.objects.create(
        organization=w.org, group_external_id="group-2273", role=second, scope_kind="TEAM", scope_id=w.team.pk
    )
    r = reviewed(w, "REMOVE", False)
    assert {s["id"] for s in r["membership"]["sources"] if s["removable"]} == {
        str(expired.guid),
        str(live.guid),
    }
    assert next(s for s in r["membership"]["sources"] if s["id"] == str(expired.guid))["expired"]
    input = command(w, r, "REMOVE", False)
    result = mutate(w, input)
    assert result["ok"] and set(result["data"]["removedBindingIds"]) == {str(expired.guid), str(live.guid)}
    assert not Member.objects.filter(pk=member.pk).exists()
    assert not RoleBinding.objects.filter(pk__in=[expired.pk, live.pk]).exists()
    assert RoleBinding.objects.filter(pk__in=[other.pk, inherited.pk, group.pk]).count() == 3
    assert GroupRoleMapping.objects.filter(pk=mapping.pk).exists()
    assert Member.objects.get(pk=w.member.pk).is_active
    w.subject.refresh_from_db()
    assert w.subject.is_active and not w.subject.is_superuser
    assert {s["source"] for s in result["data"]["remainingSources"]} == {
        "INHERITED",
        "IDP_GROUP",
        "IDP_MAPPING",
    }
    assert mutate(w, input)["data"]["replayed"]


@pytest.mark.parametrize(
    "drift",
    [
        "new-binding",
        "role-permissions",
        "member-version",
        "team-version",
        "role-retired",
        "person-reassigned",
        "org-inactive",
        "user-inactive",
    ],
)
def test_review_drift_refuses_without_receipt_or_partial_effect(world, drift):
    w = world
    input = command(w, reviewed(w))
    if drift == "new-binding":
        RoleBinding.objects.create(user=w.subject, role=w.role, scope_kind="TEAM", scope_id=w.team.pk)
    elif drift == "role-permissions":
        Role.objects.filter(pk=w.role.pk).update(permissions=["team.read", "team.manage_members"])
    elif drift == "member-version":
        w.member.save()
    elif drift == "team-version":
        w.team.save()
    elif drift == "role-retired":
        w.role.soft_delete()
    elif drift == "person-reassigned":
        Member.objects.filter(pk=w.member.pk).update(scope_id=w.other_org.pk)
    elif drift == "org-inactive":
        Member.objects.filter(pk=w.member.pk).update(is_active=False)
    else:
        get_user_model().objects.filter(pk=w.subject.pk).update(is_active=False)
    result = mutate(w, input)
    assert not result["ok"] and not TeamMembershipAction.objects.exists()
    assert not Member.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).exists()


def test_unreviewed_grant_prevents_partial_remove(world):
    w = world
    member, original = direct(w)
    r = reviewed(w, "REMOVE", False)
    role = Role.objects.create(
        organization=w.org, name="Another", slug="another-2273", scope_level="TEAM", permissions=["team.read"]
    )
    inserted = RoleBinding.objects.create(user=w.subject, role=role, scope_kind="TEAM", scope_id=w.team.pk)
    result = mutate(w, command(w, r, "REMOVE", False))
    assert not result["ok"] and result["errors"][0]["code"] == "VERSION_MISMATCH"
    assert RoleBinding.objects.filter(pk__in=[original.pk, inserted.pk]).count() == 2
    assert Member.objects.filter(pk=member.pk).exists()


@pytest.mark.parametrize("kind", ["ADD", "REMOVE"])
def test_manager_cannot_grant_or_remove_superior_role(world, kind):
    w = world
    superior = Role.objects.create(
        organization=w.org,
        name="Superior",
        slug="superior-2273",
        scope_level="TEAM",
        permissions=["team.read", "team.manage_members", "app.delete"],
    )
    if kind == "REMOVE":
        member, binding = direct(w, superior)
        assert reviewed(w, "REMOVE", False) is None
        assert (
            Member.objects.filter(pk=member.pk).exists()
            and RoleBinding.objects.filter(pk=binding.pk).exists()
        )
    else:
        w.role = superior
        assert reviewed(w) is None
    assert not TeamMembershipAction.objects.exists()


@pytest.mark.parametrize("scope", ["admin", "read:apps", "team:write"])
def test_actual_bearer_ceiling_preserved(world, scope):
    w = world
    input = command(w, reviewed(w))
    issued = mint_token()
    ApiToken.objects.create(
        user=w.actor, organization=w.org, name="Native", token_hash=issued.token_hash, scopes=[scope]
    )
    result = http(w, CHANGE, {"input": input}, client=Client(), bearer=issued.plaintext)
    if scope == "admin":
        assert result["data"]["changeAstroliftTeamMembership"]["ok"]
    else:
        assert result.get("errors") or not result["data"]["changeAstroliftTeamMembership"]["ok"]
        assert not TeamMembershipAction.objects.exists()


def test_orphan_direct_grant_remove_does_not_fabricate_attachment(world):
    w = world
    binding = RoleBinding.objects.create(user=w.subject, role=w.role, scope_kind="TEAM", scope_id=w.team.pk)
    r = reviewed(w, "REMOVE", False)
    assert r["membership"]["teamMemberId"] is None
    result = mutate(w, command(w, r, "REMOVE", False))
    assert result["ok"] and result["data"]["teamMemberId"] is None
    assert result["data"]["removedBindingIds"] == [str(binding.guid)]


def test_group_only_roster_and_person_teams_remain_read_only(world):
    w = world
    w.member.idp_groups = ["external-team"]
    w.member.save()
    GroupRoleMapping.objects.create(
        organization=w.org,
        group_external_id="external-team",
        role=w.role,
        scope_kind="TEAM",
        scope_id=w.team.pk,
    )
    result = http(
        w,
        "query($team:GUID!, $person:GUID!){astroliftTeamMembershipsPage(teamId:$team){totalCount items{person{orgMemberId} teamMemberId canRemove sources{source removable}}} astroliftPersonTeamMembershipsPage(orgMemberId:$person){totalCount items{team{id}}}}",
        {"team": str(w.team.guid), "person": str(w.member.guid)},
    )
    assert not result.get("errors"), result
    row = next(
        r
        for r in result["data"]["astroliftTeamMembershipsPage"]["items"]
        if r["person"]["orgMemberId"] == str(w.member.guid)
    )
    assert row["teamMemberId"] is None and not row["canRemove"]
    assert row["sources"] == [{"source": "IDP_MAPPING", "removable": False}]
    assert result["data"]["astroliftPersonTeamMembershipsPage"]["items"] == [
        {"team": {"id": str(w.team.guid)}}
    ]


def test_scim_authority_and_tombstone_override_snapshot(world):
    w = world
    w.member.idp_groups = ["managed"]
    w.member.save()
    GroupRoleMapping.objects.create(
        organization=w.org, group_external_id="managed", role=w.role, scope_kind="TEAM", scope_id=w.team.pk
    )
    scim = ScimGroup.objects.create(organization=w.org, display_name="Managed", external_id="managed")
    query = "query($team:GUID!){astroliftTeamMembershipsPage(teamId:$team){items{person{orgMemberId}}}}"

    def people():
        result = http(w, query, {"team": str(w.team.guid)})
        assert not result.get("errors"), result
        return {r["person"]["orgMemberId"] for r in result["data"]["astroliftTeamMembershipsPage"]["items"]}

    assert str(w.member.guid) not in people()
    scim.members.add(w.member)
    assert str(w.member.guid) in people()
    scim.soft_delete()
    assert str(w.member.guid) not in people()


def test_server_candidates_page_beyond_500_excludes_foreign_inactive(world):
    w = world
    users = get_user_model().objects.bulk_create(
        [get_user_model()(username=f"paged-{i:04}", email=f"paged-{i}@example.test") for i in range(505)]
    )
    Member.objects.bulk_create([Member(user=user, scope_kind="ORG", scope_id=w.org.pk) for user in users])
    get_user_model().objects.filter(pk=users[-1].pk).update(is_active=False)
    foreign = get_user_model().objects.create(username="foreign-person")
    Member.objects.create(user=foreign, scope_kind="ORG", scope_id=w.other_org.pk)
    result = http(
        w,
        'query($team:GUID!){astroliftTeamMemberCandidatesPage(teamId:$team,search:"paged-",page:6,pageSize:100){totalCount page items{orgMemberId}}}',
        {"team": str(w.team.guid)},
    )
    assert not result.get("errors"), result
    page = result["data"]["astroliftTeamMemberCandidatesPage"]
    assert page["totalCount"] == 504 and page["page"] == 6 and len(page["items"]) == 4


def test_reused_request_cannot_change_reviewed_tuple(world):
    w = world
    input = command(w, reviewed(w))
    assert mutate(w, input)["ok"]
    input["expectedSource"] = "different"
    result = mutate(w, input)
    assert not result["ok"] and result["errors"][0]["code"] == "CONFLICT"
    assert TeamMembershipAction.objects.count() == 1


def test_exact_team_selector_and_person_lists_reach_beyond_200_teams(world):
    w = world
    owner = Role.objects.create(
        organization=w.org,
        name="Org team reader",
        slug="org-team-reader-2273",
        scope_level="ORG",
        permissions=["team.read", "team.manage_members"],
    )
    RoleBinding.objects.create(user=w.actor, role=owner, scope_kind="ORG", scope_id=w.org.pk)
    Team.objects.bulk_create(
        [Team(organization=w.org, name=f"Paged {i:03}", slug=f"paged-team-{i:03}") for i in range(205)]
    )
    distant = Team.objects.get(slug="paged-team-204")
    Member.objects.create(user=w.subject, scope_kind="TEAM", scope_id=distant.pk)
    result = http(
        w,
        'query($person:GUID!){astroliftTeamMembershipTeam(slug:"paged-team-204"){id} astroliftPersonTeamMembershipsPage(orgMemberId:$person){items{team{id}}} astroliftMembershipTeamsPage(search:"Paged",page:3,pageSize:100){totalCount items{id}}}',
        {"person": str(w.member.guid)},
    )
    assert not result.get("errors"), result
    assert result["data"]["astroliftTeamMembershipTeam"]["id"] == str(distant.guid)
    assert result["data"]["astroliftPersonTeamMembershipsPage"]["items"] == [
        {"team": {"id": str(distant.guid)}}
    ]
    assert result["data"]["astroliftMembershipTeamsPage"]["totalCount"] == 205
    assert len(result["data"]["astroliftMembershipTeamsPage"]["items"]) == 5


def test_sql_role_picker_pages_beyond_200_and_refuses_superior_unknown_foreign_roles(world):
    w = world
    Role.objects.bulk_create(
        [
            Role(
                organization=w.org,
                name=f"Paged {i:03}",
                slug=f"paged-role-{i:03}",
                scope_level="TEAM",
                permissions=["team.read"],
            )
            for i in range(205)
        ]
    )
    Role.objects.create(
        organization=w.other_org,
        name="Paged foreign",
        slug="paged-foreign",
        scope_level="TEAM",
        permissions=["team.read"],
    )
    Role.objects.create(
        organization=w.org,
        name="Paged superior",
        slug="paged-superior",
        scope_level="TEAM",
        permissions=["app.delete"],
    )
    stale = Role.objects.create(
        organization=w.org,
        name="Paged stale",
        slug="paged-stale",
        scope_level="TEAM",
        permissions=["team.read"],
    )
    Role.objects.filter(pk=stale.pk).update(permissions=["unknown.stale.permission"])
    result = http(
        w,
        'query($team:GUID!){astroliftTeamMembershipRolesPage(teamId:$team,search:"Paged",page:3,pageSize:100){totalCount items{id}}}',
        {"team": str(w.team.guid)},
    )
    assert not result.get("errors"), result
    page = result["data"]["astroliftTeamMembershipRolesPage"]
    assert page["totalCount"] == 205 and len(page["items"]) == 5
    w.role = Role.objects.get(slug="paged-role-204")
    r = reviewed(w)
    assert [role["id"] for role in r["roles"]] == [str(w.role.guid)]
    assert mutate(w, command(w, r))["ok"]


def test_team_only_me_navigation_does_not_offer_org_people_or_infrastructure(world):
    w = world
    result = http(
        w,
        "{me{modules{key canView canManage} teamAccessNavigation{canViewTeams canManageTeamMembers canViewPeople canViewRoles canViewPolicies canCheckAccess}}}",
    )
    assert not result.get("errors"), result
    nav = result["data"]["me"]["teamAccessNavigation"]
    assert nav == {
        "canViewTeams": True,
        "canManageTeamMembers": True,
        "canViewPeople": False,
        "canViewRoles": False,
        "canViewPolicies": False,
        "canCheckAccess": False,
    }
    modules = {row["key"]: row for row in result["data"]["me"]["modules"]}
    assert modules["team_access"]["canView"] and modules["team_access"]["canManage"]
    assert not modules["admin"]["canView"]


@pytest.mark.parametrize(
    "shape", ["foreign-team", "foreign-person", "foreign-role", "wrong-level", "inactive-attachment"]
)
def test_current_review_refuses_unsupported_or_foreign_targets(world, shape):
    w = world
    if shape == "foreign-team":
        w.team = w.foreign
    elif shape == "foreign-person":
        other = get_user_model().objects.create(username="foreign-subject-2273")
        w.member = Member.objects.create(user=other, scope_kind="ORG", scope_id=w.other_org.pk)
    elif shape == "foreign-role":
        w.role = Role.objects.create(
            organization=w.other_org,
            name="Foreign role",
            slug="foreign-role",
            scope_level="TEAM",
            permissions=["team.read"],
        )
    elif shape == "wrong-level":
        Role.objects.filter(pk=w.role.pk).update(scope_level="ORG")
    else:
        Member.objects.create(
            user=w.subject, scope_kind="TEAM", scope_id=w.team.pk, is_active=False, lifecycle="suspended"
        )
    result = http(
        w,
        REVIEW,
        {"team": str(w.team.guid), "person": str(w.member.guid), "kind": "ADD", "role": str(w.role.guid)},
    )
    assert result.get("errors") or result["data"]["astroliftTeamMembershipReview"] is None
    assert not TeamMembershipAction.objects.exists()


def test_same_guid_person_reassignment_cannot_replay_old_receipt(world):
    w = world
    input = command(w, reviewed(w))
    assert mutate(w, input)["ok"]
    other = get_user_model().objects.create(username="reassigned-2273")
    Member.objects.filter(pk=w.member.pk).update(user=other)
    result = mutate(w, input)
    assert not result["ok"] and result["errors"][0]["code"] == "CONFLICT"
    assert TeamMembershipAction.objects.count() == 1


@pytest.mark.parametrize("source", ["expired-inherited", "foreign-mapping", "foreign-direct"])
def test_non_authoritative_sources_cannot_manufacture_roster_or_removal(world, source):
    w = world
    if source == "expired-inherited":
        RoleBinding.objects.create(
            user=w.subject,
            role=w.role,
            scope_kind="ORG",
            scope_id=w.org.pk,
            inherits=True,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
    else:
        foreign = Role.objects.create(
            organization=w.other_org,
            name="Foreign private role marker",
            slug="foreign-private",
            scope_level="TEAM",
            permissions=["team.read"],
        )
        if source == "foreign-mapping":
            Member.objects.filter(pk=w.member.pk).update(idp_groups=["managed-group"])
            GroupRoleMapping.objects.create(
                organization=w.org,
                group_external_id="managed-group",
                role=foreign,
                scope_kind="TEAM",
                scope_id=w.team.pk,
            )
        else:
            direct(w, role=foreign)
    result = http(
        w,
        "query($team:GUID!){astroliftTeamMembershipsPage(teamId:$team){items{person{orgMemberId} canRemove sources{roleName}}}}",
        {"team": str(w.team.guid)},
    )
    assert not result.get("errors"), result
    rows = result["data"]["astroliftTeamMembershipsPage"]["items"]
    subject = [row for row in rows if row["person"]["orgMemberId"] == str(w.member.guid)]
    if source == "foreign-direct":
        assert len(subject) == 1 and not subject[0]["canRemove"]
        assert reviewed(w, "REMOVE", role=False) is None
    else:
        assert subject == []
    assert "Foreign private role marker" not in json.dumps(result)


@pytest.mark.parametrize("direction", ["team", "person"])
def test_actual_http_page_projection_query_count_is_bounded_with_mixed_sources(
    world, direction, record_property
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    w = world
    if direction == "team":
        for i in range(25):
            user = get_user_model().objects.create(username=f"bounded-source-{i:02}")
            person = Member.objects.create(
                user=user, scope_kind="ORG", scope_id=w.org.pk, idp_groups=[f"source-{i}"]
            )
            Member.objects.create(user=user, scope_kind="TEAM", scope_id=w.team.pk)
            RoleBinding.objects.create(user=user, role=w.role, scope_kind="TEAM", scope_id=w.team.pk)
            group = ScimGroup.objects.create(
                organization=w.org, display_name=f"Source{i}", external_id=f"source-{i}"
            )
            if i % 2:
                group.members.add(person)
            else:
                group.soft_delete()
            GroupRoleMapping.objects.create(
                organization=w.org,
                group_external_id=f"source-{i}",
                role=w.role,
                scope_kind="TEAM",
                scope_id=w.team.pk,
            )
            RoleBinding.objects.create(
                user=user, role=w.role, scope_kind="ORG", scope_id=w.org.pk, inherits=True
            )
        query = 'query($team:GUID!,$size:Int!){astroliftTeamMembershipsPage(teamId:$team,search:"bounded-source",pageSize:$size){items{person{orgMemberId} sources{source} canRemove}}}'
        variables = {"team": str(w.team.guid)}
        root = "astroliftTeamMembershipsPage"
    else:
        for i in range(25):
            team = Team.objects.create(
                organization=w.org, name=f"Bounded Team{i:02}", slug=f"bounded-team-{i}"
            )
            RoleBinding.objects.create(user=w.actor, role=w.manager, scope_kind="TEAM", scope_id=team.pk)
            Member.objects.create(user=w.subject, scope_kind="TEAM", scope_id=team.pk)
            RoleBinding.objects.create(user=w.subject, role=w.role, scope_kind="TEAM", scope_id=team.pk)
            GroupRoleMapping.objects.create(
                organization=w.org,
                group_external_id="retained-team-group",
                role=w.role,
                scope_kind="TEAM",
                scope_id=team.pk,
            )
        Member.objects.filter(pk=w.member.pk).update(idp_groups=["retained-team-group"])
        query = 'query($person:GUID!,$size:Int!){astroliftPersonTeamMembershipsPage(orgMemberId:$person,search:"Bounded",pageSize:$size){items{team{id canManageMembers} sources{source} canRemove}}}'
        variables = {"person": str(w.member.guid)}
        root = "astroliftPersonTeamMembershipsPage"
    # Warm the real HTTP sidecar so its initial insertion is not counted as projection work.
    http(w, query, {**variables, "size": 1})
    with CaptureQueriesContext(connection) as one:
        first = http(w, query, {**variables, "size": 1})
    with CaptureQueriesContext(connection) as many:
        page = http(w, query, {**variables, "size": 25})
    assert not first.get("errors") and not page.get("errors")
    assert len(first["data"][root]["items"]) == 1
    assert len(page["data"][root]["items"]) == 25
    record_property("query_count_one", len(one))
    record_property("query_count_twenty_five", len(many))
    assert len(many) <= len(one) + 2, (len(one), len(many))
    assert all(row["canRemove"] for row in page["data"][root]["items"])
    if direction == "team":
        assert (
            sum(
                any(s["source"] == "IDP_MAPPING" for s in row["sources"])
                for row in page["data"][root]["items"]
            )
            == 12
        )
        assert all(
            any(s["source"] == "INHERITED" for s in row["sources"]) for row in page["data"][root]["items"]
        )
    else:
        assert all(
            any(s["source"] == "IDP_MAPPING" for s in row["sources"]) for row in page["data"][root]["items"]
        )


@pytest.mark.parametrize("negative", ["target-policy", "superior-role", "team-bearer-ceiling"])
def test_batched_person_roster_keeps_exact_permission_and_grant_negatives(world, negative):
    from astrolift_identity.models import Policy

    w = world
    direct(w)
    Member.objects.create(user=w.subject, scope_kind="TEAM", scope_id=w.sibling.pk)
    RoleBinding.objects.create(user=w.subject, role=w.role, scope_kind="TEAM", scope_id=w.sibling.pk)
    RoleBinding.objects.create(user=w.actor, role=w.manager, scope_kind="TEAM", scope_id=w.sibling.pk)
    bearer = None
    if negative == "target-policy":
        Policy.objects.create(
            organization=w.org,
            name="Membership target deny",
            slug="membership-target-deny",
            scope_level="TEAM",
            scope_id=w.sibling.pk,
            effect="DENY",
            action_pattern="team.manage_members",
        )
    elif negative == "superior-role":
        elevated = Role.objects.create(
            organization=w.org,
            name="Unmanageable superior",
            slug="unmanageable-superior",
            scope_level="TEAM",
            permissions=["team.delete"],
        )
        RoleBinding.objects.create(user=w.subject, role=elevated, scope_kind="TEAM", scope_id=w.sibling.pk)
    else:
        issued = mint_token()
        bearer = issued.plaintext
        ApiToken.objects.create(
            user=w.actor,
            organization=w.org,
            team=w.team,
            name="Bounded admin",
            token_hash=issued.token_hash,
            scopes=["admin"],
        )
    result = http(
        w,
        "query($person:GUID!){astroliftPersonTeamMembershipsPage(orgMemberId:$person){items{team{id canManageMembers} canRemove}}}",
        {"person": str(w.member.guid)},
        bearer=bearer,
    )
    assert not result.get("errors"), result
    rows = {r["team"]["id"]: r for r in result["data"]["astroliftPersonTeamMembershipsPage"]["items"]}
    assert rows[str(w.team.guid)]["canRemove"]
    if negative == "team-bearer-ceiling":
        assert str(w.sibling.guid) not in rows
    else:
        assert not rows[str(w.sibling.guid)]["canRemove"]
        assert rows[str(w.sibling.guid)]["team"]["canManageMembers"] == (negative == "superior-role")


NAVIGATION = """{me{teamAccessNavigation{canViewTeams canManageTeamMembers canViewPeople canViewRoles canViewPolicies canCheckAccess}}}"""
NO_NAVIGATION = {
    "canViewTeams": False,
    "canManageTeamMembers": False,
    "canViewPeople": False,
    "canViewRoles": False,
    "canViewPolicies": False,
    "canCheckAccess": False,
}


def _navigation(w, **kwargs):
    result = http(w, NAVIGATION, **kwargs)
    assert not result.get("errors"), result
    return result["data"]["me"]["teamAccessNavigation"]


def test_navigation_read_only_team_reader_keeps_only_the_scoped_read_hint(world):
    w = world
    w.manager.permissions = ["team.read"]
    w.manager.save()
    assert _navigation(w) == {**NO_NAVIGATION, "canViewTeams": True}


def test_navigation_org_reader_is_not_required_to_have_a_team_grant(world):
    w = world
    w.actor_binding.soft_delete()
    role = Role.objects.create(
        organization=w.org,
        name="Org metadata reader",
        slug="navigation-org-reader",
        scope_level="ORG",
        permissions=["org.read"],
    )
    RoleBinding.objects.create(user=w.actor, role=role, scope_kind="ORG", scope_id=w.org.pk)
    assert _navigation(w) == {**NO_NAVIGATION, "canViewRoles": True, "canViewPolicies": True}


@pytest.mark.parametrize("withdrawal", ["binding", "member", "role", "foreign-scope", "foreign-role"])
def test_navigation_current_withdrawal_and_foreign_authority_leave_all_hints_false(world, withdrawal):
    w = world
    assert _navigation(w)["canManageTeamMembers"]
    if withdrawal == "binding":
        w.actor_binding.soft_delete()
    elif withdrawal == "member":
        w.actor_member.is_active = False
        w.actor_member.save()
    elif withdrawal == "role":
        w.manager.soft_delete()
    elif withdrawal == "foreign-scope":
        w.actor_binding.scope_id = w.foreign.pk
        w.actor_binding.save()
    else:
        foreign = Role.objects.create(
            organization=w.other_org,
            name="Foreign navigation manager",
            slug="navigation-foreign-manager",
            scope_level="TEAM",
            permissions=["team.read", "team.manage_members", "org.read", "org.manage_members"],
        )
        w.actor_binding.role = foreign
        w.actor_binding.save()
    assert _navigation(w) == NO_NAVIGATION


@pytest.mark.parametrize("credential", ["read-only", "team-admin", "revoked", "withdrawn-member"])
def test_navigation_actual_bearer_caps_are_not_account_grants(world, credential):
    w = world
    role = Role.objects.create(
        organization=w.org,
        name="Org navigation administrator",
        slug="navigation-org-admin",
        scope_level="ORG",
        permissions=["org.read", "org.manage_members", "team.read", "team.manage_members"],
    )
    RoleBinding.objects.create(user=w.actor, role=role, scope_kind="ORG", scope_id=w.org.pk)
    issued = mint_token()
    token = ApiToken.objects.create(
        user=w.actor,
        organization=w.org,
        team=w.team if credential == "team-admin" else None,
        name="Navigation credential",
        token_hash=issued.token_hash,
        scopes=["read:apps"] if credential == "read-only" else ["admin"],
    )
    if credential == "revoked":
        token.is_revoked = True
        token.save()
    elif credential == "withdrawn-member":
        w.actor_member.is_active = False
        w.actor_member.save()
    if credential in ("revoked", "withdrawn-member"):
        response = Client().post(
            f"/{settings.BASE_URL}gql/config/",
            data=json.dumps({"query": NAVIGATION}),
            content_type="application/json",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(w.org.guid),
            HTTP_AUTHORIZATION="Bearer " + issued.plaintext,
            HTTP_X_PLATFORM="WEB",
        )
        assert response.status_code == 401
        assert "teamAccessNavigation" not in response.json()
    else:
        result = http(w, NAVIGATION, client=Client(), bearer=issued.plaintext)
        assert not result.get("errors"), result
        expected = {
            **NO_NAVIGATION,
            "canViewTeams": True,
            "canViewRoles": True,
            "canViewPolicies": True,
        }
        if credential == "team-admin":
            expected = {**NO_NAVIGATION, "canViewTeams": True, "canManageTeamMembers": True}
        assert result["data"]["me"]["teamAccessNavigation"] == expected


@pytest.mark.parametrize("organization", ["none", "unknown", "foreign"])
def test_navigation_actual_http_missing_or_unadmitted_org_is_all_false(world, organization):
    w = world
    headers = {"HTTP_X_PLATFORM": "WEB"}
    if organization == "none":
        # Multiple live memberships prevent the existing single-org fallback.
        Member.objects.create(user=w.actor, scope_kind="ORG", scope_id=w.other_org.pk)
    if organization != "none":
        headers["HTTP_X_ASTROLIFT_ORGANIZATION"] = (
            str(uuid.uuid4()) if organization == "unknown" else str(w.other_org.guid)
        )
    response = w.client.post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": NAVIGATION}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200
    result = response.json()
    assert not result.get("errors"), result
    assert result["data"]["me"]["teamAccessNavigation"] == NO_NAVIGATION


def test_navigation_actual_http_anonymous_has_no_self_hints(world):
    response = Client().post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": NAVIGATION}),
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="WEB",
    )
    assert response.status_code == 403
    assert b"teamAccessNavigation" not in response.content


@pytest.mark.parametrize("owner", ["current", "global", "foreign"])
@pytest.mark.parametrize("principal", ["user", "group"])
@pytest.mark.parametrize("level", ["ORG", "TEAM"])
def test_role_owner_admits_only_current_or_global_user_and_group_sources(world, owner, principal, level):
    w = world
    input = command(w, reviewed(w))
    w.actor_binding.soft_delete()
    role = Role.objects.create(
        organization={"current": w.org, "global": None, "foreign": w.other_org}[owner],
        name="Navigation authority source",
        slug="navigation-authority-source",
        scope_level=level,
        permissions=["team.read", "team.manage_members", "org.read", "org.manage_members"],
    )
    if principal == "group":
        w.actor_member.idp_groups = ["navigation-operators"]
        w.actor_member.save()
    RoleBinding.objects.create(
        user=w.actor if principal == "user" else None,
        group_external_id="navigation-operators" if principal == "group" else "",
        role=role,
        scope_kind=level,
        scope_id=w.org.pk if level == "ORG" else w.team.pk,
    )
    expected = (
        NO_NAVIGATION
        if owner == "foreign"
        else {
            **NO_NAVIGATION,
            "canViewTeams": True,
            "canManageTeamMembers": True,
            "canViewPeople": level == "ORG",
            "canViewRoles": level == "ORG",
            "canViewPolicies": level == "ORG",
            "canCheckAccess": level == "ORG",
        }
    )
    nav = _navigation(w)
    review = http(
        w,
        REVIEW,
        {
            "team": str(w.team.guid),
            "person": str(w.member.guid),
            "kind": "ADD",
            "role": str(w.role.guid),
        },
    )
    result = mutate(w, input)
    if owner == "foreign":
        assert not result["ok"]
        assert review.get("errors") or review["data"]["astroliftTeamMembershipReview"] is None
        assert result["errors"][0]["code"] == "PERMISSION_DENIED"
        assert not TeamMembershipAction.objects.exists()
        assert not Member.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).exists()
        assert not RoleBinding.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).exists()
    else:
        assert not review.get("errors"), review
        assert review["data"]["astroliftTeamMembershipReview"] is not None
        assert result["ok"] and result["data"]["committed"]
        assert TeamMembershipAction.objects.count() == 1
    assert nav == expected
