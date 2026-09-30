"""Whole-list CSV and invitation activity, using real owners, grants and HTTP."""

import csv
import datetime as dt
import io
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.utils import timezone

from astrolift_identity.models import Invitation, Member, Policy, Role, RoleBinding
from astrolift_identity.schema.identity_lists import (
    InvitationsListFilterInput,
    MembersListFilterInput,
    RoleBindingsListFilterInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_identity.tests import test_owner_scopes_2103 as owner
from astrolift_identity.tests.test_identity_pages_1235 import _backfill_audit_event
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role, make_info, make_user

pytestmark = pytest.mark.django_db
world = owner._world


def _csv(result):
    rows = list(csv.DictReader(io.StringIO(result.content)))
    assert len(rows) == result.row_count
    assert result.content_type == "text/csv; charset=utf-8"
    return rows


def _invite(world, email, **kwargs):
    return Invitation.objects.create(
        scope_kind="ORG",
        scope_id=world.org.pk,
        email=email,
        token_hash=uuid4().hex,
        expires_at=timezone.now() + dt.timedelta(days=7),
        **kwargs,
    )


def _grant(world, kind="ORG"):
    return bind_role(
        world.user,
        permissions=[Permission.ORG_MANAGE_MEMBERS],
        kind=kind,
        scope_id={
            "ORG": world.org.pk,
            "TEAM": world.medops.pk,
            "PROJECT": world.medops_project.pk,
            "APP": world.medops_app.pk,
        }[kind],
        slug=f"csv-admin-{uuid4().hex}",
    )


def _binding(world, user, kind, scope_id):
    role = Role.objects.create(
        organization=world.org, name="Reader", slug=f"reader-{uuid4().hex}", scope_level=kind
    )
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


def test_all_csvs_exceed_legacy_and_client_caps_and_match_numbered_order(world):
    _grant(world)
    count = 5003
    users = get_user_model().objects.bulk_create(
        [get_user_model()(username=f"csv-{i:05}", email=f"csv-{i:05}@example.test") for i in range(count)]
    )
    Member.objects.bulk_create([Member(user=u, scope_kind="ORG", scope_id=world.org.pk) for u in users])
    role = Role.objects.create(organization=world.org, name="Export", slug="csv-export", scope_level="ORG")
    RoleBinding.objects.bulk_create(
        [RoleBinding(user=u, role=role, scope_kind="ORG", scope_id=world.org.pk) for u in users]
    )
    Invitation.objects.bulk_create(
        [
            Invitation(
                scope_kind="ORG",
                scope_id=world.org.pk,
                email=u.email,
                role=role,
                token_hash=uuid4().hex,
                expires_at=timezone.now() + dt.timedelta(days=7),
            )
            for u in users
        ]
    )
    # Bulk fixtures exceed autovacuum thresholds inside an uncommitted test
    # transaction; give the real planner the table cardinalities.
    with connection.cursor() as cursor:
        for table in (
            "auth_user",
            "astrolift_identity_member",
            "astrolift_identity_rolebinding",
            "astrolift_identity_invitation",
        ):
            cursor.execute(f"ANALYZE {table}")
    info = make_info(world.user)
    with owner._tenant(world):
        queries = IdentityQuery()
        members = _csv(
            queries.astrolift_members_csv(
                info, search="csv-", sort="-name", filter=MembersListFilterInput(scope_kind=["ORG"])
            )
        )
        invitations = _csv(queries.astrolift_invitations_csv(info, search="csv-", sort="-email"))
        bindings = _csv(
            queries.astrolift_role_bindings_csv(
                info, search="csv-", sort="-name", filter=RoleBindingsListFilterInput(role=["csv-export"])
            )
        )
        page = queries.astrolift_members_page(info, search="csv-", sort="-name", page=26, page_size=200)
    assert len(members) == len(invitations) == len(bindings) == count
    assert members[0]["Name"] == "csv-05002" and members[-1]["Name"] == "csv-00000"
    assert [r["Name"] for r in members[-3:]] == [m.user.username for m in page.items]
    assert page.total_count == count
    assert (
        [r["Email"] for r in members] == [r["Email"] for r in invitations] == [r["Email"] for r in bindings]
    )
    assert all(r["Roles"] == "csv-export@ORG" for r in members)
    assert list(members[0]) == ["Kind", "Name", "Email", "Roles", "Teams", "Status", "Last active", "Joined"]


def test_invitation_activity_uses_only_unambiguous_live_current_org_member(world):
    _grant(world)
    user = make_user("invite-match")
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    invitation = _invite(world, user.email.upper())
    unmatched = _invite(world, "unknown@example.test")
    now = timezone.now()
    current = now - dt.timedelta(days=2)
    _backfill_audit_event(org=world.org, actor_id=str(user.pk), occurred_at=current)
    _backfill_audit_event(org=world.other_org, actor_id=str(user.pk), occurred_at=now)
    user.last_login = now
    user.save(update_fields=["last_login"])
    info = make_info(world.user)
    with owner._tenant(world):
        page = IdentityQuery().astrolift_invitations_page(info, sort="-lastActive")
        assert [str(r.id) for r in page.items] == [str(invitation.guid), str(unmatched.guid)]
        assert page.items[0].last_active_at == current and page.items[1].last_active_at is None
        rows = _csv(IdentityQuery().astrolift_invitations_csv(info, sort="-lastActive"))
        assert rows[0]["Last active"] == current.isoformat() and rows[1]["Last active"] == ""
    # Ambiguous email identities never guess a subject.
    duplicate = make_user("invite-duplicate")
    duplicate.email = user.email
    duplicate.save(update_fields=["email"])
    second = Member.objects.create(user=duplicate, scope_kind="ORG", scope_id=world.org.pk)
    with owner._tenant(world):
        assert all(
            r.last_active_at is None
            for r in IdentityQuery().astrolift_invitations_page(info, sort="name").items
        )
    second.soft_delete()
    for changes in (
        {"is_active": False},
        {"is_active": True, "lifecycle": "deactivated"},
        {"lifecycle": "active", "deleted_at": now},
    ):
        Member.all_objects.filter(pk=member.pk).update(**changes)
        with owner._tenant(world):
            assert all(r["Last active"] == "" for r in _csv(IdentityQuery().astrolift_invitations_csv(info)))


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP"])
def test_subordinate_grant_cannot_export_org_people_or_invitations(world, kind):
    _grant(world, kind)
    with owner._tenant(world):
        for name in ("astrolift_members_csv", "astrolift_invitations_csv"):
            with pytest.raises(PermissionDenied):
                getattr(IdentityQuery(), name)(make_info(world.user))


@pytest.mark.parametrize("ceiling", ["team", "foreign", "read"])
def test_bearer_cannot_borrow_user_org_export_permission(world, ceiling):
    _grant(world)
    with owner._tenant(world), owner._token(world, ceiling):
        for name in ("astrolift_members_csv", "astrolift_invitations_csv"):
            with pytest.raises(PermissionDenied):
                getattr(IdentityQuery(), name)(make_info(world.user))


def test_bindings_export_and_page_filter_before_counts_for_team_bearer_and_policy(world):
    _grant(world)
    user = make_user("csv-target")
    allowed = _binding(world, user, "PROJECT", world.medops_project.pk)
    denied = _binding(world, user, "APP", world.medops_app.pk)
    sibling = _binding(world, user, "APP", world.platform_app.pk)
    # APP and PROJECT targets independently evaluate current owner policy facts.
    Policy.objects.create(
        organization=world.org,
        name="Deny project",
        slug="csv-deny-project",
        scope_level="APP",
        scope_id=world.medops_app.pk,
        effect="DENY",
        action_pattern="org.manage_members",
    )
    info = make_info(world.user)
    with owner._tenant(world), owner._token(world, "team"):
        rows = _csv(IdentityQuery().astrolift_role_bindings_csv(info, search=user.username, sort="name"))
        page = IdentityQuery().astrolift_role_bindings_page(
            info, search=user.username, sort="name", page_size=1
        )
    # A project policy is inherited by its app; resource-pattern facts are not a new grant.
    ids = {r["Binding ID"] for r in rows}
    assert str(sibling.guid) not in ids
    assert page.total_count == len(rows)
    assert str(denied.guid) not in ids
    assert str(allowed.guid) in ids
    allowed.soft_delete()
    with owner._tenant(world), owner._token(world, "team"):
        assert _csv(IdentityQuery().astrolift_role_bindings_csv(info, search=user.username)) == []


def test_filters_subject_alias_and_role_owner_match_page_and_csv(world):
    _grant(world)
    own = _binding(world, world.user, "ORG", world.org.pk)
    foreign_role = Role.objects.create(
        organization=world.other_org, name="Private", slug="foreign-role-secret", scope_level="ORG"
    )
    RoleBinding.objects.create(user=world.user, role=foreign_role, scope_kind="ORG", scope_id=world.org.pk)
    _invite(world, "foreign-link@example.test", role=foreign_role)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    info = make_info(world.user)
    with owner._tenant(world):
        queries = IdentityQuery()
        expected = queries.astrolift_role_bindings_page(
            info, filter=RoleBindingsListFilterInput(holder=["me"]), sort="role"
        )
        alias = queries.astrolift_role_bindings_page(
            info, filter=RoleBindingsListFilterInput(subject=["me"]), sort="role"
        )
        rows = _csv(
            queries.astrolift_role_bindings_csv(
                info, filter=RoleBindingsListFilterInput(subject=["me"]), sort="role"
            )
        )
        assert (
            [str(r.id) for r in alias.items]
            == [str(r.id) for r in expected.items]
            == [r["Binding ID"] for r in rows]
        )
        assert str(own.guid) in {r["Binding ID"] for r in rows}
        assert "foreign-role-secret" not in queries.astrolift_members_csv(info).content
        assert queries.astrolift_invitations_csv(info).row_count == 0
        assert queries.astrolift_invitations_page(info, page=1).total_count == 0


def test_csv_escaping_filters_and_retired_own_source_cleanup(world):
    _grant(world)
    user = make_user("csv-formula")
    user.username = '=SUM(1,2)\n"text"'
    user.save(update_fields=["username"])
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    binding = _binding(world, user, "TEAM", world.medops.pk)
    invitation = _invite(world, "+formula@example.test", status="revoked")
    _invite(world, "other@example.test", status="pending")
    world.medops.soft_delete()
    with owner._tenant(world):
        info = make_info(world.user)
        members = _csv(
            IdentityQuery().astrolift_members_csv(
                info, search="SUM", filter=MembersListFilterInput(scope_kind=["ORG"])
            )
        )
        invitations = _csv(
            IdentityQuery().astrolift_invitations_csv(
                info, filter=InvitationsListFilterInput(status=["revoked"]), sort="name"
            )
        )
        bindings = _csv(IdentityQuery().astrolift_role_bindings_csv(info, search="SUM"))
    assert members[0]["Name"] == "'" + user.username
    assert invitations[0]["Email"] == "'" + invitation.email
    assert invitation.token_hash not in str(invitations)
    assert bindings[0]["Binding ID"] == str(binding.guid)


@pytest.mark.parametrize("active", [True, False])
def test_http_csv_current_org_owner_and_active_account(world, active):
    _grant(world)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    _invite(world, "http-invite@example.test")
    client = Client()
    client.force_login(world.user)
    if not active:
        get_user_model().objects.filter(pk=world.user.pk).update(is_active=False)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        content_type="application/json",
        data={
            "query": '{ astroliftMembersCsv(filter: {scopeKind: ["ORG"]}, sort: "name") { filename content rowCount } astroliftInvitationsCsv { rowCount content } astroliftRoleBindingsCsv(filter: {subject: ["me"]}) { rowCount content } }'
        },
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="WEB",
    )
    if active:
        assert response.status_code == 200 and not response.json().get("errors"), response.content
        assert response.json()["data"]["astroliftMembersCsv"]["rowCount"] == 1
        assert response.json()["data"]["astroliftInvitationsCsv"]["rowCount"] == 1
        assert response.json()["data"]["astroliftRoleBindingsCsv"]["rowCount"] == 1
    else:
        assert response.status_code in {401, 403} or response.json().get("errors"), response.content


@pytest.mark.parametrize("scope", ["ORG", "TEAM", "PROJECT", "APP"])
def test_current_owner_policy_chain_applies_before_binding_count_and_export(world, scope):
    _grant(world)
    user = make_user("policy-target")
    targets = {"TEAM": world.medops.pk, "PROJECT": world.medops_project.pk, "APP": world.medops_app.pk}
    rows = {kind: _binding(world, user, kind, pk) for kind, pk in targets.items()}
    sibling = _binding(world, user, "APP", world.platform_app.pk)
    Policy.objects.create(
        organization=world.org,
        name="Scoped export deny",
        slug="csv-scope-deny",
        scope_level=scope,
        scope_id=world.org.pk if scope == "ORG" else targets[scope],
        effect="DENY",
        action_pattern="org.manage_members",
    )
    with owner._tenant(world):
        queries = IdentityQuery()
        info = make_info(world.user)
        if scope == "ORG":
            for name in ("astrolift_role_bindings_page", "astrolift_role_bindings_csv"):
                with pytest.raises(PermissionDenied):
                    getattr(queries, name)(info, search=user.username)
            return
        page = queries.astrolift_role_bindings_page(
            info, search=user.username, page=1, page_size=1, sort="name"
        )
        exported = _csv(queries.astrolift_role_bindings_csv(info, search=user.username, sort="name"))
    denied = {"TEAM": {"TEAM", "PROJECT", "APP"}, "PROJECT": {"PROJECT", "APP"}, "APP": {"APP"}}[scope]
    expected = {str(row.guid) for kind, row in rows.items() if kind not in denied} | {str(sibling.guid)}
    assert {row["Binding ID"] for row in exported} == expected
    assert page.total_count == len(expected)


@pytest.mark.parametrize("broken", ["foreign-team", "foreign-project", "wrong-project-team"])
def test_incoherent_source_owners_are_absent_from_members_bindings_counts_and_csv(world, broken):
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp

    _grant(world)
    user = make_user("incoherent-target")
    Member.objects.create(user=user, scope_kind="APP", scope_id=world.medops_app.pk)
    _binding(world, user, "APP", world.medops_app.pk)
    if broken == "foreign-team":
        Team.objects.filter(pk=world.medops.pk).update(organization=world.other_org)
    elif broken == "foreign-project":
        Project.objects.filter(pk=world.medops_project.pk).update(organization=world.other_org)
    else:
        RegisteredApp.objects.filter(pk=world.medops_app.pk).update(project=world.platform_project)
    with owner._tenant(world):
        info = make_info(world.user)
        queries = IdentityQuery()
        for page_name, csv_name in (
            ("astrolift_members_page", "astrolift_members_csv"),
            ("astrolift_role_bindings_page", "astrolift_role_bindings_csv"),
        ):
            assert getattr(queries, page_name)(info, search=user.username, page=1).total_count == 0
            assert getattr(queries, csv_name)(info, search=user.username).row_count == 0


def test_subject_me_includes_only_current_org_idp_groups(world):
    _grant(world)
    Member.objects.create(
        user=world.user, scope_kind="ORG", scope_id=world.org.pk, idp_groups=["okta:my-group"]
    )
    role = Role.objects.create(organization=world.org, name="Group", slug="csv-group", scope_level="ORG")
    own = RoleBinding.objects.create(
        role=role, group_external_id="okta:my-group", scope_kind="ORG", scope_id=world.org.pk
    )
    RoleBinding.objects.create(
        role=role, group_external_id="okta:other-group", scope_kind="ORG", scope_id=world.org.pk
    )
    RoleBinding.objects.create(
        role=role, group_external_id="okta:my-group", scope_kind="ORG", scope_id=world.other_org.pk
    )
    with owner._tenant(world):
        result = _csv(
            IdentityQuery().astrolift_role_bindings_csv(
                make_info(world.user), filter=RoleBindingsListFilterInput(subject=["me"], kind=["group"])
            )
        )
    assert [row["Binding ID"] for row in result] == [str(own.guid)]


def test_export_http_rechecks_revocation_and_rejects_partial_generation(world, monkeypatch):
    grant = _grant(world)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    client.force_login(world.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "WEB"}
    query = {"query": "{ astroliftMembersCsv { content rowCount } }"}

    def request():
        return client.post(
            f"/{settings.BASE_URL}gql/config/", data=query, content_type="application/json", **headers
        ).json()

    assert request()["data"]["astroliftMembersCsv"]["rowCount"] == 1
    grant.soft_delete()
    denied = request()
    assert denied.get("errors") and not denied.get("data")
    _grant(world)

    def fail_mid_export(*_args):
        yield []
        raise RuntimeError("export database unavailable")

    monkeypatch.setattr("astrolift_identity.schema.identity_csv._batches", fail_mid_export)
    failed = request()
    assert failed.get("errors") and not failed.get("data")


@pytest.mark.parametrize("ceiling", ["org", "team", "foreign", "read"])
def test_http_export_bearer_scope_ceiling_uses_real_token(world, ceiling):
    from astrolift_identity.api_tokens import mint_token

    _grant(world)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    _binding(world, world.user, "APP", world.medops_app.pk)
    _binding(world, world.user, "APP", world.platform_app.pk)
    issued = mint_token()
    with owner._token(world, ceiling) as token:
        token.token_hash = issued.token_hash
        token.save(update_fields=["token_hash"])
    client = Client()

    def post(query):
        return client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query},
            content_type="application/json",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
            HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}",
        )

    response = post('{ astroliftRoleBindingsCsv(sort: "name") { rowCount content } }')
    if ceiling == "foreign":
        assert response.status_code in {401, 403} or response.json().get("errors")
    elif ceiling == "read":
        assert response.json().get("errors") and not response.json().get("data")
    else:
        assert response.status_code == 200 and not response.json().get("errors"), response.content
        assert response.json()["data"]["astroliftRoleBindingsCsv"]["rowCount"] == (
            1 if ceiling == "team" else 3
        )
        people = post("{ astroliftMembersCsv { rowCount } }").json()
        if ceiling == "team":
            assert people.get("errors") and not people.get("data")
        else:
            assert people["data"]["astroliftMembersCsv"]["rowCount"] == 1
