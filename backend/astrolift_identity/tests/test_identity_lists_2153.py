"""The list contract on the identity lists (spec 44 §5.1, #2153).

Members, invitations, teams, roles, role bindings and policies: numbered
pages with an exact ``totalCount``, the declared filters, the declared
sorts, and org isolation for every one of them, including the parts that
reach another table (a role's bindings count, "me", teams, admins).
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    Policy,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.identity_lists import (
    InvitationsListFilterInput,
    MembersListFilterInput,
    PoliciesListFilterInput,
    RoleBindingsListFilterInput,
    RolesListFilterInput,
    TeamsListFilterInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_identity.tests.test_identity_pages_1235 import _backfill_audit_event
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-2153")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other", slug="other-2153")


def _user(handle: str):
    return get_user_model().objects.create(username=handle, email=f"{handle}@acme.test")


@pytest.fixture
def viewer():
    return _user("viewer-2153")


@pytest.fixture
def info(viewer):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=viewer)))


@pytest.fixture
def grant_all(permission_resolver):
    for perm in (Permission.ORG_MANAGE_MEMBERS, Permission.ORG_READ, Permission.TEAM_READ):
        permission_resolver.grant(perm)


def _call(resolver: str, org, viewer, info, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=viewer.id)):
        return getattr(IdentityQuery(), resolver)(info, **kwargs)


def _member(user, kind, scope_id):
    return Member.objects.create(user=user, scope_kind=kind, scope_id=scope_id)


def _role(org, slug, *, permissions=(), system=False):
    return Role.objects.create(
        organization=None if system else org,
        slug=slug,
        name=slug.replace("-", " ").title(),
        scope_level=Role.ScopeLevel.ORG,
        permissions=list(permissions),
        is_system=system,
    )


def _bind(role, *, user=None, group="", kind="ORG", scope_id):
    return RoleBinding.objects.create(
        user=user, group_external_id=group, role=role, scope_kind=kind, scope_id=scope_id
    )


# ---------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------


@pytest.fixture
def people(org, other_org, viewer):
    """Five people in ``org`` (viewer included) on two teams, one outsider."""
    payments = Team.objects.create(organization=org, slug="payments", name="Payments")
    search = Team.objects.create(organization=org, slug="search", name="Search")
    admin_role = _role(org, "org-admin-2153", permissions=[Permission.ORG_MANAGE_MEMBERS.value])
    viewer_role = _role(org, "org-viewer-2153")
    users = {"viewer": viewer}
    for handle in ("alice", "bob", "carol", "dave"):
        users[handle] = _user(f"{handle}-2153")
    for u in users.values():
        _member(u, "ORG", org.id)
    _member(users["viewer"], "TEAM", payments.pk)
    _member(users["alice"], "TEAM", payments.pk)
    _member(users["bob"], "TEAM", search.pk)
    _bind(admin_role, user=users["carol"], scope_id=org.id)
    _bind(viewer_role, user=users["dave"], scope_id=org.id)
    outsider = _user("outsider-2153")
    _member(outsider, "ORG", other_org.id)
    # The outsider is on a team in the other org with the same slug.
    other_payments = Team.objects.create(organization=other_org, slug="payments", name="Payments")
    _member(outsider, "TEAM", other_payments.pk)
    return SimpleNamespace(
        users=users, payments=payments, search=search, admin_role=admin_role, outsider=outsider
    )


def _names(page):
    return [item.user.username for item in page.items]


def test_members_numbered_page_pages_and_counts(org, viewer, info, grant_all, people):
    org_rows = MembersListFilterInput(scope_kind=["ORG"])
    first = _call("astrolift_members_page", org, viewer, info, filter=org_rows, page=1, page_size=2)
    second = _call("astrolift_members_page", org, viewer, info, filter=org_rows, page=2, page_size=2)
    third = _call("astrolift_members_page", org, viewer, info, filter=org_rows, page=3, page_size=2)

    assert first.total_count == 5
    assert (first.page, first.page_size, first.next_cursor) == (1, 2, None)
    walked = _names(first) + _names(second) + _names(third)
    assert walked == sorted(walked), "default sort is name"
    assert set(walked) == {u.username for u in people.users.values()}
    assert "outsider-2153" not in walked


def test_members_sort_desc_and_unknown_key(org, viewer, info, grant_all, people):
    page = _call(
        "astrolift_members_page",
        org,
        viewer,
        info,
        filter=MembersListFilterInput(scope_kind=["ORG"]),
        sort="-name",
    )
    assert _names(page) == sorted(_names(page), reverse=True)
    with pytest.raises(UnsupportedSort):
        _call("astrolift_members_page", org, viewer, info, sort="shoeSize")


def test_members_filters_mine_admin_role_team(org, viewer, info, grant_all, people):
    def names(**f):
        page = _call(
            "astrolift_members_page",
            org,
            viewer,
            info,
            filter=MembersListFilterInput(scope_kind=["ORG"], **f),
            page=1,
        )
        return set(_names(page))

    assert names(mine=True) == {"viewer-2153", "alice-2153"}
    assert "alice-2153" not in names(mine=False)
    assert names(admin=True) == {"carol-2153"}
    assert names(role=["org-viewer-2153"]) == {"dave-2153"}
    assert names(team=["search"]) == {"bob-2153"}
    assert names(team=[str(people.payments.guid)]) == {"viewer-2153", "alice-2153"}


def test_members_rows_name_their_team_and_list_the_users_teams(org, viewer, info, grant_all, people):
    page = _call(
        "astrolift_members_page",
        org,
        viewer,
        info,
        filter=MembersListFilterInput(scope_kind=["TEAM"]),
        page=1,
    )
    by_user = {item.user.username: item for item in page.items}
    assert by_user["bob-2153"].team_slug == "search"
    assert by_user["bob-2153"].team_id == str(people.search.guid)
    assert by_user["alice-2153"].team_name == "Payments"
    assert [t.slug for t in by_user["alice-2153"].teams] == ["payments"]
    # The outsider's team row is in the other org and never appears.
    assert "outsider-2153" not in by_user


def test_members_last_active_sort_and_filter(org, other_org, viewer, info, grant_all, people):
    now = timezone.now()
    _backfill_audit_event(org=org, actor_id=people.users["alice"].pk, occurred_at=now - dt.timedelta(days=1))
    _backfill_audit_event(org=org, actor_id=people.users["bob"].pk, occurred_at=now - dt.timedelta(days=200))
    # Activity in another org does not count here.
    _backfill_audit_event(org=other_org, actor_id=people.users["carol"].pk, occurred_at=now)

    page = _call(
        "astrolift_members_page",
        org,
        viewer,
        info,
        filter=MembersListFilterInput(scope_kind=["ORG"]),
        sort="-lastActive,name",
    )
    assert _names(page)[:2] == ["alice-2153", "bob-2153"]

    def active(window):
        page = _call(
            "astrolift_members_page",
            org,
            viewer,
            info,
            filter=MembersListFilterInput(scope_kind=["ORG"], active=window),
            page=1,
        )
        return set(_names(page))

    assert active("7d") == {"alice-2153"}
    assert active("stale") == {"bob-2153"}
    assert "carol-2153" in active("never")


def test_members_cursor_walk_still_serves_filter(org, viewer, info, grant_all, people):
    page = _call(
        "astrolift_members_page", org, viewer, info, filter=MembersListFilterInput(admin=True), limit=10
    )
    assert page.page is None
    assert _names(page) == ["carol-2153"]


# ---------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------


def _invite(org, email, *, status=Invitation.Status.PENDING, role=None, invited_by=None):
    return Invitation.objects.create(
        email=email,
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.id,
        token_hash=f"h-{email}",
        status=status,
        role=role,
        invited_by=invited_by,
    )


def test_invitations_page_filter_sort_isolation(org, other_org, viewer, info, grant_all):
    role = _role(org, "developer-2153")
    _invite(org, "b@acme.test", invited_by=viewer, role=role)
    _invite(org, "a@acme.test", status=Invitation.Status.ACCEPTED)
    _invite(org, "c@acme.test", invited_by=viewer)
    _invite(other_org, "z@other.test", invited_by=viewer)

    page = _call("astrolift_invitations_page", org, viewer, info, sort="email", page=1, page_size=2)
    assert page.total_count == 3
    assert [i.email for i in page.items] == ["a@acme.test", "b@acme.test"]

    def emails(**f):
        page = _call(
            "astrolift_invitations_page", org, viewer, info, filter=InvitationsListFilterInput(**f), page=1
        )
        return {i.email for i in page.items}

    assert emails(invited_by=["me"]) == {"b@acme.test", "c@acme.test"}
    assert emails(status=["accepted"]) == {"a@acme.test"}
    assert emails(role=["developer-2153"]) == {"b@acme.test"}


# ---------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------


def test_teams_page_mine_sort_isolation(org, other_org, viewer, info, grant_all):
    teams = [Team.objects.create(organization=org, slug=f"t-{c}", name=f"Team {c}") for c in "cab"]
    foreign = Team.objects.create(organization=other_org, slug="t-x", name="Team X")
    _member(viewer, "TEAM", teams[0].pk)
    # Membership of a team in another org never counts as mine here.
    _member(viewer, "TEAM", foreign.pk)

    page = _call("astrolift_teams_page", org, viewer, info, page=1, page_size=2)
    assert page.total_count == 3
    assert [t.slug for t in page.items] == ["t-a", "t-b"]

    mine = _call("astrolift_teams_page", org, viewer, info, filter=TeamsListFilterInput(mine=True), page=1)
    assert [t.slug for t in mine.items] == ["t-c"]
    rest = _call(
        "astrolift_teams_page", org, viewer, info, filter=TeamsListFilterInput(mine=False), sort="-name"
    )
    assert [t.slug for t in rest.items] == ["t-b", "t-a"]


# ---------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------


def test_roles_page_filters_and_bindings_count_is_org_scoped(org, other_org, viewer, info, grant_all):
    system = _role(org, "zz-system-2153", system=True)
    custom = _role(org, "aa-custom-2153")
    custom.created_by = viewer
    custom.save()
    foreign = _role(other_org, "foreign-2153")
    for i in range(2):
        _bind(system, user=_user(f"sys-holder-{i}"), scope_id=org.id)
    # Three bindings of the shared system role in the other org.
    for i in range(3):
        _bind(system, user=_user(f"other-holder-{i}"), scope_id=other_org.id)

    page = _call(
        "astrolift_roles_page",
        org,
        viewer,
        info,
        filter=RolesListFilterInput(scope_level=["ORG"]),
        sort="-bindings,name",
        page=1,
        page_size=100,
    )
    slugs = [r.slug for r in page.items]
    assert foreign.slug not in slugs
    counts = {r.slug: r.bindings_count for r in page.items}
    assert counts["zz-system-2153"] == 2, "the other org's bindings leaked into the count"
    assert counts["aa-custom-2153"] == 0
    assert slugs[0] == "zz-system-2153"

    custom_only = _call(
        "astrolift_roles_page", org, viewer, info, filter=RolesListFilterInput(is_system=False), page=1
    )
    assert [r.slug for r in custom_only.items] == ["aa-custom-2153"]
    by_me = _call(
        "astrolift_roles_page", org, viewer, info, filter=RolesListFilterInput(created_by=["me"]), page=1
    )
    assert [r.slug for r in by_me.items] == ["aa-custom-2153"]


def test_roles_cursor_page_still_works_and_carries_count(org, viewer, info, grant_all):
    _role(org, "cursor-role-2153")
    page = _call("astrolift_roles_page", org, viewer, info, search="cursor-role-2153", limit=5)
    assert page.page is None
    assert [(r.slug, r.bindings_count) for r in page.items] == [("cursor-role-2153", 0)]


# ---------------------------------------------------------------------
# Role bindings
# ---------------------------------------------------------------------


def test_role_bindings_role_id_holder_me_and_isolation(org, other_org, viewer, info, grant_all):
    ops = _role(org, "ops-2153")
    dev = _role(org, "dev-2153")
    foreign_role = _role(other_org, "foreign-role-2153")
    _member(viewer, "ORG", org.id)
    Member.objects.filter(user=viewer, scope_kind="ORG", scope_id=org.id).update(idp_groups=["eng"])
    mine = _bind(ops, user=viewer, scope_id=org.id)
    via_group = _bind(dev, group="eng", scope_id=org.id)
    someone = _bind(dev, user=_user("someone-2153"), scope_id=org.id)
    _bind(foreign_role, user=viewer, scope_id=other_org.id)

    def ids(**kwargs):
        page = _call("astrolift_role_bindings_page", org, viewer, info, page=1, **kwargs)
        return {str(b.id) for b in page.items}, page.total_count

    assert ids(role_id=dev.guid) == ({str(via_group.guid), str(someone.guid)}, 2)
    # A role of another org names nothing here.
    assert ids(role_id=foreign_role.guid) == (set(), 0)
    assert ids(filter=RoleBindingsListFilterInput(holder=["me"]))[0] == {str(mine.guid), str(via_group.guid)}
    assert ids(filter=RoleBindingsListFilterInput(kind=["group"]))[0] == {str(via_group.guid)}
    assert ids(filter=RoleBindingsListFilterInput(holder=["group:eng"]))[0] == {str(via_group.guid)}
    assert ids(filter=RoleBindingsListFilterInput(role=["ops-2153"]))[0] == {str(mine.guid)}
    everything, total = ids()
    assert total == 3, "the other org's binding leaked"


def test_role_bindings_sort_by_holder_and_pages(org, viewer, info, grant_all):
    role = _role(org, "sorted-2153")
    for handle in ("mike", "anna", "zoe"):
        _bind(role, user=_user(f"{handle}-2153"), scope_id=org.id)
    _bind(role, group="b-group", scope_id=org.id)

    first = _call("astrolift_role_bindings_page", org, viewer, info, sort="name", page=1, page_size=3)
    second = _call("astrolift_role_bindings_page", org, viewer, info, sort="name", page=2, page_size=3)
    holders = [b.user.username if b.user else b.group_external_id for b in first.items + second.items]
    assert holders == ["anna-2153", "b-group", "mike-2153", "zoe-2153"]
    assert first.total_count == 4


# ---------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------


def _policy(org, slug, *, effect="DENY", level="ORG", by=None):
    return Policy.objects.create(
        organization=org, slug=slug, name=slug, scope_level=level, effect=effect, created_by=by
    )


def test_policies_page_filters_sort_isolation(org, other_org, viewer, info, grant_all):
    _policy(org, "p-allow", effect="ALLOW", by=viewer)
    _policy(org, "p-deny-team", level="TEAM")
    _policy(org, "p-deny")
    _policy(other_org, "p-foreign", effect="ALLOW", by=viewer)

    page = _call("astrolift_policies_page", org, viewer, info, sort="name", page=1, page_size=2)
    assert page.total_count == 3
    assert [p.slug for p in page.items] == ["p-allow", "p-deny"]

    def slugs(**f):
        page = _call(
            "astrolift_policies_page", org, viewer, info, filter=PoliciesListFilterInput(**f), page=1
        )
        return {p.slug for p in page.items}

    assert slugs(effect=["ALLOW"]) == {"p-allow"}
    assert slugs(scope_level=["TEAM"]) == {"p-deny-team"}
    assert slugs(created_by=["me"]) == {"p-allow"}


# ---------------------------------------------------------------------
# Through the real schema: argument names, input types, camelCase fields
# ---------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


def _execute(document, org, viewer, variables=None):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=viewer.id)):
        return schema.execute_sync(
            document, variable_values=variables, context_value=StrawberryContext(_FakeRequest(viewer))
        )


def test_the_list_contract_executes_through_the_schema(org, viewer, grant_all, people):
    result = _execute(
        """
        query($role: GUID) {
          members: astroliftMembersPage(
            filter: { scopeKind: ["ORG"], mine: true }, sort: "-name", page: 1, pageSize: 25
          ) { items { user { username } teamId teams { slug name } } totalCount page pageSize }
          bindings: astroliftRoleBindingsPage(roleId: $role, filter: { holder: ["me"] }, page: 1) {
            totalCount
          }
          roles: astroliftRolesPage(filter: { isSystem: false }, sort: "name", page: 1) {
            items { slug bindingsCount }
          }
          teams: astroliftTeamsPage(filter: { mine: true }, page: 1) { items { slug } totalCount }
          invites: astroliftInvitationsPage(filter: { invitedBy: ["me"] }, sort: "email", page: 1) {
            totalCount
          }
          policies: astroliftPoliciesPage(filter: { effect: ["ALLOW"] }, sort: "name", page: 1) {
            totalCount
          }
        }
        """,
        org,
        viewer,
        {"role": str(people.admin_role.guid)},
    )
    assert result.errors is None, result.errors
    members = result.data["members"]
    assert [m["user"]["username"] for m in members["items"]] == ["viewer-2153", "alice-2153"]
    assert (members["totalCount"], members["page"], members["pageSize"]) == (2, 1, 25)
    assert members["items"][0]["teams"] == [{"slug": "payments", "name": "Payments"}]
    assert result.data["bindings"]["totalCount"] == 0
    roles = {r["slug"]: r["bindingsCount"] for r in result.data["roles"]["items"]}
    assert roles["org-admin-2153"] == 1
    assert [t["slug"] for t in result.data["teams"]["items"]] == ["payments"]


def test_ui_preferences_execute_through_the_schema(org, viewer):
    saved = _execute(
        """
        mutation {
          updateMyUiPreferences(input: { fleetView: "hive", homeLayout: "apps" }) {
            ok errors { field message } data { fleetView homeLayout homeLayoutAsked motion }
          }
        }
        """,
        org,
        viewer,
    )
    assert saved.errors is None, saved.errors
    payload = saved.data["updateMyUiPreferences"]
    assert payload["ok"], payload["errors"]
    assert payload["data"] == {
        "fleetView": "hive",
        "homeLayout": "apps",
        "homeLayoutAsked": True,
        "motion": "system",
    }

    # An omitted field is unchanged; an explicit null resets.
    reset = _execute(
        "mutation { updateMyUiPreferences(input: { homeLayout: null }) { ok data { fleetView homeLayout } } }",
        org,
        viewer,
    )
    assert reset.errors is None, reset.errors
    assert reset.data["updateMyUiPreferences"]["data"] == {"fleetView": "hive", "homeLayout": None}

    read = _execute(
        "{ astroliftMyUiPreferences { fleetView restrictedSettings restrictedSettingsOrgDefault appearance } }",
        org,
        viewer,
    )
    assert read.errors is None, read.errors
    assert read.data["astroliftMyUiPreferences"] == {
        "fleetView": "hive",
        "restrictedSettings": "show",
        "restrictedSettingsOrgDefault": "show",
        "appearance": {},
    }
