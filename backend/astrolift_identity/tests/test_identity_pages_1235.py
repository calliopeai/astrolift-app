"""Cursor pagination for the identity list surfaces (#1235, epic #1230).

Eight identity resolvers shipped with a hard slice — 500 rows for
members / role bindings / invitations, 200 for roles / tokens / teams /
projects / policies — and no way to reach the row after it. For an org
that has been inviting for a year, or that grants a role per member,
its own data becomes unreachable from the UI rather than merely slow to
reach. These tests are what stop that coming back.

The four invariants every conversion has to hold are asserted for all
eight through ``PAGE_SPECS``:

* a full walk covers every row exactly once and terminates,
* ``total_count`` counts the filtered set, not the page,
* another org's rows appear in neither ``items`` nor ``total_count``,
* ``search`` narrows ``total_count``, not just the visible page.

The per-resolver tests below them cover what is specific and therefore
easy to break in a conversion: the ``last_active_at`` aggregate and the
inviter-avatar prefetch have to run over the page's rows (running them
over the whole queryset would defeat the pagination they decorate), and
role bindings have to seek on ``granted_at`` — a distinct not-null
column from ``created_at`` — because that is the order the operator
sees.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from astrolift_identity.models import (
    ApiToken,
    Invitation,
    Member,
    Organization,
    Policy,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.decorators import TenantRequired
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

# A walk that never terminates has to fail loudly rather than hang the
# suite. Every fixture below stays well under this many pages.
_WALK_BOUND = 60

# Free-text needle planted in exactly one row per search test. Chosen so
# it collides with nothing in the seeded system-role catalog.
NEEDLE = "zqx-needle"


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """User / Organization writes fire profile-indexing signals; the
    test env has no OpenSearch. Same guard the sibling identity tests use.
    """
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-1235")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other", slug="other-1235")


@pytest.fixture
def actor():
    User = get_user_model()
    return User.objects.create(username="caller-1235", email="caller-1235@acme.test")


@pytest.fixture
def info(actor):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _user(handle: str):
    User = get_user_model()
    return User.objects.create(
        username=handle,
        email=f"{handle}@acme.test",
        first_name=handle.title(),
        last_name="Tester",
    )


def _backfill_audit_event(*, org, actor_id, occurred_at, action="app.read"):
    """Direct INSERT past ``auto_now_add`` + the append-only trigger.

    ``bulk_create`` still runs each field's ``pre_save``, so
    ``occurred_at`` would be stomped to ``now()`` — useless for asserting
    a specific "last active" timestamp. Raw SQL inserts straight into the
    table, which the append-only trigger permits (it refuses only UPDATE
    and DELETE). Mirrors the helper in ``test_members_query.py``.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO astrolift_operations_auditevent
              (guid, organization_id, occurred_at, actor_kind, actor_id,
               actor_display, action, decision, target_kind, target_id,
               target_slug, target_parent_chain, request_id, request_ip,
               request_user_agent, request_session_age_seconds, data, reasoning)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                str(uuid.uuid4()),
                org.id,
                occurred_at,
                "user",
                str(actor_id),
                "",
                action,
                "ALLOW",
                "",
                "",
                "",
                "[]",
                "",
                "",
                "",
                None,
                "{}",
                "[]",
            ],
        )


# ---------------------------------------------------------------------
# Row factories — one per converted resolver
# ---------------------------------------------------------------------


def _member(org, i, *, needle=False):
    tag = NEEDLE if needle else "member"
    user = _user(f"{tag}-{org.slug}-{i}")
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _role(org, i, *, needle=False):
    tag = NEEDLE if needle else "role"
    return Role.objects.create(
        organization=org,
        slug=f"{tag}-{org.slug}-{i}",
        name=f"Role {i}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[],
    )


def _role_binding(org, i, *, needle=False, scope_kind=RoleBinding.ScopeKind.ORG, scope_id=None):
    tag = NEEDLE if needle else "binding"
    return RoleBinding.objects.create(
        user=_user(f"binding-{org.slug}-{i}"),
        role=Role.objects.create(
            organization=org,
            slug=f"{tag}-role-{org.slug}-{i}",
            name=f"Binding role {i}",
            scope_level=Role.ScopeLevel.ORG,
            permissions=[],
        ),
        scope_kind=scope_kind,
        scope_id=org.id if scope_id is None else scope_id,
    )


def _invitation(org, i, *, needle=False, status=Invitation.Status.PENDING, invited_by=None):
    tag = NEEDLE if needle else "invitee"
    return Invitation.objects.create(
        email=f"{tag}-{org.slug}-{i}@acme.test",
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.id,
        token_hash=f"hash-{org.slug}-{i}",
        status=status,
        invited_by=invited_by,
    )


def _api_token(org, i, *, needle=False, last_4=None):
    tag = NEEDLE if needle else "token"
    return ApiToken.objects.create(
        user=_user(f"token-{org.slug}-{i}"),
        organization=org,
        name=f"{tag}-{i}",
        token_hash=f"hash-{org.slug}-{i}",
        token_last_4=last_4 if last_4 is not None else f"{i:04d}",
        scopes=[],
    )


def _team(org, i, *, needle=False):
    tag = NEEDLE if needle else "team"
    return Team.objects.create(organization=org, slug=f"{tag}-{org.slug}-{i}", name=f"Team {i}")


def _project(org, i, *, needle=False):
    tag = NEEDLE if needle else "project"
    home, _ = Team.objects.get_or_create(
        organization=org, slug=f"projects-home-{org.slug}", defaults={"name": "Projects home"}
    )
    return Project.objects.create(
        organization=org, team=home, slug=f"{tag}-{org.slug}-{i}", name=f"Project {i}"
    )


def _policy(org, i, *, needle=False):
    tag = NEEDLE if needle else "policy"
    return Policy.objects.create(
        organization=org,
        slug=f"{tag}-{org.slug}-{i}",
        name=f"Policy {i}",
        scope_level=Policy.ScopeLevel.ORG,
        effect=Policy.Effect.DENY,
        action_pattern="*",
    )


# ---------------------------------------------------------------------
# The conversion contract, asserted once per resolver
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PageSpec:
    """One converted resolver, described well enough to test generically.

    ``visible`` is written independently of the resolver's own filter so
    the assertions are a real expectation rather than a restatement of
    the implementation.
    """

    name: str
    permission: Permission
    page_resolver: str
    list_resolver: str
    make: Callable[..., object]
    visible: Callable[[Organization], object]
    sort_field: str = "created_at"


PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec(
        name="members",
        permission=Permission.ORG_MANAGE_MEMBERS,
        page_resolver="astrolift_members_page",
        list_resolver="astrolift_members",
        make=_member,
        visible=lambda o: Member.objects.filter(scope_kind="ORG", scope_id=o.id),
    ),
    PageSpec(
        name="role_bindings",
        permission=Permission.ORG_MANAGE_MEMBERS,
        page_resolver="astrolift_role_bindings_page",
        list_resolver="astrolift_role_bindings",
        make=_role_binding,
        visible=lambda o: RoleBinding.objects.filter(scope_kind="ORG", scope_id=o.id),
        sort_field="granted_at",
    ),
    PageSpec(
        name="invitations",
        permission=Permission.ORG_MANAGE_MEMBERS,
        page_resolver="astrolift_invitations_page",
        list_resolver="astrolift_invitations",
        make=_invitation,
        visible=lambda o: Invitation.objects.filter(scope_kind="ORG", scope_id=o.id),
    ),
    PageSpec(
        name="roles",
        permission=Permission.ORG_READ,
        page_resolver="astrolift_roles_page",
        list_resolver="astrolift_roles",
        make=_role,
        # System roles carry a null organization and are visible to every
        # tenant; the org's own custom roles sit alongside them.
        visible=lambda o: Role.objects.filter(Q(organization_id=o.id) | Q(organization__isnull=True)),
    ),
    PageSpec(
        name="api_tokens",
        permission=Permission.API_TOKEN_CREATE,
        page_resolver="astrolift_api_tokens_page",
        list_resolver="astrolift_api_tokens",
        make=_api_token,
        visible=lambda o: ApiToken.objects.filter(organization_id=o.id),
    ),
    PageSpec(
        name="teams",
        permission=Permission.TEAM_READ,
        page_resolver="astrolift_teams_page",
        list_resolver="astrolift_teams",
        make=_team,
        visible=lambda o: Team.objects.filter(organization_id=o.id),
    ),
    PageSpec(
        name="projects",
        permission=Permission.PROJECT_READ,
        page_resolver="astrolift_projects_page",
        list_resolver="astrolift_projects",
        make=_project,
        visible=lambda o: Project.objects.filter(organization_id=o.id),
    ),
    PageSpec(
        name="policies",
        permission=Permission.ORG_READ,
        page_resolver="astrolift_policies_page",
        list_resolver="astrolift_policies",
        make=_policy,
        visible=lambda o: Policy.objects.filter(organization_id=o.id),
    ),
)


def _page(spec_or_name, info, **kwargs):
    resolver = spec_or_name if isinstance(spec_or_name, str) else spec_or_name.page_resolver
    return getattr(IdentityQuery(), resolver)(info, **kwargs)


def _list(spec, info):
    return getattr(IdentityQuery(), spec.list_resolver)(info)


def _expected_ids(qs, *, sort_field="created_at"):
    """The guids the resolver should serve, in the DB's own seek order.

    Rows written in a tight loop can share a ``created_at`` to the
    microsecond, which is exactly what the guid tiebreak is for — so the
    expectation is read back from the DB rather than assumed from
    creation order.
    """
    return [str(g) for g in qs.order_by(f"-{sort_field}", "-guid").values_list("guid", flat=True)]


def _walk(resolver: str, org, actor, info, *, limit=3, **kwargs) -> list[str]:
    """Page through the whole stream, returning every item id in order."""
    ids: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        for _ in range(_WALK_BOUND):
            page = _page(resolver, info, limit=limit, after=cursor, **kwargs)
            ids.extend(str(item.id) for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return ids
    raise AssertionError(f"{resolver} walk did not terminate within {_WALK_BOUND} pages")


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_walk_serves_every_row_exactly_once_in_seek_order(spec, org, actor, info, permission_resolver):
    """The regression the epic exists for: with the list field, the row
    after the cap did not exist as far as the UI was concerned."""
    permission_resolver.grant(spec.permission)
    mine = [spec.make(org, i) for i in range(7)]

    walked = _walk(spec.page_resolver, org, actor, info, limit=3)

    assert walked == _expected_ids(spec.visible(org), sort_field=spec.sort_field)
    assert len(set(walked)) == len(walked), "a row was served twice"
    assert {str(r.guid) for r in mine} <= set(walked), "a row was skipped between pages"


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_total_count_is_the_filtered_set_not_the_page(spec, org, actor, info, permission_resolver):
    """A count that reported the page would render "4 results" over a
    table the operator can keep scrolling."""
    permission_resolver.grant(spec.permission)
    for i in range(9):
        spec.make(org, i)
    expected_total = spec.visible(org).count()
    assert expected_total >= 9

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        page = _page(spec, info, limit=4)

    assert len(page.items) == 4
    assert page.next_cursor is not None
    assert page.total_count == expected_total


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_other_orgs_rows_are_invisible(spec, org, other_org, actor, info, permission_resolver):
    """Tenancy is the resolver's job: ``@tenant_scoped`` only asserts a
    tenant exists, it does not filter (#1183)."""
    permission_resolver.grant(spec.permission)
    for i in range(3):
        spec.make(org, i)
    theirs = {str(spec.make(other_org, 100 + i).guid) for i in range(4)}
    mine_total = spec.visible(org).count()

    walked = _walk(spec.page_resolver, org, actor, info, limit=2)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        page = _page(spec, info, limit=50)

    assert set(walked).isdisjoint(theirs), "another org's rows leaked into a page"
    assert len(walked) == mine_total
    assert page.total_count == mine_total, "the count leaked the other org's rows"


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_search_narrows_total_count_not_just_the_page(spec, org, actor, info, permission_resolver):
    """A count that ignored the search would render "7 results" over a
    one-row table."""
    permission_resolver.grant(spec.permission)
    for i in range(6):
        spec.make(org, i)
    hit = spec.make(org, 99, needle=True)
    unfiltered_total = spec.visible(org).count()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        page = _page(spec, info, search=NEEDLE, limit=50)

    assert [str(item.id) for item in page.items] == [str(hit.guid)]
    assert page.total_count == 1
    assert unfiltered_total > 1, "precondition: search actually narrowed something"


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_page_and_deprecated_list_field_serve_the_same_rows(spec, org, actor, info, permission_resolver):
    """Both fields share one queryset builder precisely so they cannot
    drift on what a visible row is. Ordering may differ (the page seeks
    on a not-null key); membership may not."""
    permission_resolver.grant(spec.permission)
    for i in range(5):
        spec.make(org, i)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        listed = {str(item.id) for item in _list(spec, info)}
    walked = set(_walk(spec.page_resolver, org, actor, info, limit=2))

    assert walked == listed


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_page_requires_the_same_permission_as_the_list_field(spec, org, actor, info, permission_resolver):
    permission_resolver.deny(spec.permission)
    spec.make(org, 0)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        with pytest.raises(PermissionDenied):
            _page(spec, info, limit=10)


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.name)
def test_page_without_tenant_context_is_refused_outright(spec, org, info, permission_resolver):
    """Fails closed rather than paging across every org (#1183). The
    decorator rejects first; the resolver's own ``org_id is None`` branch
    (an empty queryset) is defence in depth behind it."""
    permission_resolver.grant(spec.permission)
    spec.make(org, 0)

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            _page(spec, info, limit=10)


# ---------------------------------------------------------------------
# astroliftMembersPage — the last_active_at aggregate
# ---------------------------------------------------------------------


def test_members_page_resolves_last_active_at_on_every_page(org, other_org, actor, info, permission_resolver):
    """The aggregate has to run over the page's rows. Computing it once
    for page one (or over the whole filtered set) is the easy mistake:
    the first leaves later pages blank, the second scans the org's entire
    audit stream to render fifty rows."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    members = [_member(org, i) for i in range(3)]
    oldest = members[0]  # newest-first, so this lands on the LAST page
    seen_at = timezone.now() - timezone.timedelta(hours=3)
    _backfill_audit_event(org=org, actor_id=oldest.user_id, occurred_at=seen_at)
    # Activity recorded in a different org is not activity here.
    _backfill_audit_event(org=other_org, actor_id=members[2].user_id, occurred_at=timezone.now())

    last_active: dict[str, object] = {}
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        for _ in range(_WALK_BOUND):
            page = _page("astrolift_members_page", info, limit=1, after=cursor)
            for item in page.items:
                last_active[str(item.id)] = item.last_active_at
            cursor = page.next_cursor
            if cursor is None:
                break

    assert set(last_active) == {str(m.guid) for m in members}
    resolved = last_active[str(oldest.guid)]
    assert resolved is not None, "the aggregate did not run for a row past page one"
    assert abs((resolved - seen_at).total_seconds()) < 1
    assert last_active[str(members[2].guid)] is None, "another org's audit stream leaked"


def test_members_page_search_matches_the_same_four_user_columns(org, actor, info, permission_resolver):
    """``search`` predates the conversion and the CLI depends on its
    semantics; the page field reuses the list field's exact predicate."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    alice = _member(org, 0)
    alice.user.first_name = "Alice"
    alice.user.last_name = "Anderson"
    alice.user.email = "alice@acme.test"
    alice.user.save()
    bob = _member(org, 1)
    bob.user.email = "robert@elsewhere.test"
    bob.user.save()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        by_last_name = _page("astrolift_members_page", info, search="Anderson")
        by_email_domain = _page("astrolift_members_page", info, search="ELSEWHERE.TEST")
        by_username = _page("astrolift_members_page", info, search="member-acme-1235-0")
        everyone = _page("astrolift_members_page", info, search="  ")

    assert [str(i.id) for i in by_last_name.items] == [str(alice.guid)]
    assert [str(i.id) for i in by_email_domain.items] == [str(bob.guid)]
    assert [str(i.id) for i in by_username.items] == [str(alice.guid)]
    assert everyone.total_count == 2, "a whitespace-only search must not filter"


# ---------------------------------------------------------------------
# astroliftRoleBindingsPage — granted_at, and the label batch
# ---------------------------------------------------------------------


def test_role_bindings_page_seeks_on_granted_at_not_created_at(org, actor, info, permission_resolver):
    """``granted_at`` is a distinct not-null column from ``created_at``
    and is the order the list field (and the UI) shows. Paging on
    ``created_at`` would silently reorder the table mid-walk."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    bindings = [_role_binding(org, i) for i in range(5)]
    # granted_at is auto_now_add, so it starts out agreeing with
    # created_at; .update() bypasses that and forces the two apart.
    base = timezone.now() - timezone.timedelta(days=10)
    for binding, day in zip(bindings, [3, 0, 4, 1, 2], strict=True):
        RoleBinding.objects.filter(pk=binding.pk).update(granted_at=base + timezone.timedelta(days=day))

    visible = RoleBinding.objects.filter(scope_kind="ORG", scope_id=org.id)
    by_granted = _expected_ids(visible, sort_field="granted_at")
    by_created = _expected_ids(visible, sort_field="created_at")
    assert by_granted != by_created, "precondition: the two orders differ"

    walked = _walk("astrolift_role_bindings_page", org, actor, info, limit=2)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        listed = [str(b.id) for b in IdentityQuery().astrolift_role_bindings(info)]

    assert walked == by_granted
    assert listed == by_granted, "the list field's order is the one the page must reproduce"


def test_role_bindings_page_labels_the_scope_of_rows_past_page_one(org, actor, info, permission_resolver):
    """The ``(scope_kind, scope_id)`` → label batch has to run over the
    page's rows; resolving it for every binding in the org would defeat
    the pagination it decorates, and running it only for page one would
    leave later rows with an empty tooltip."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    team = Team.objects.create(organization=org, slug="payments", name="Payments")
    _role_binding(org, 0)
    _role_binding(org, 1, scope_kind=RoleBinding.ScopeKind.TEAM, scope_id=team.id)
    _role_binding(org, 2)

    labels: dict[str, str] = {}
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        for _ in range(_WALK_BOUND):
            page = _page("astrolift_role_bindings_page", info, limit=1, after=cursor)
            for item in page.items:
                labels[str(item.id)] = item.source_scope_label
            cursor = page.next_cursor
            if cursor is None:
                break

    assert len(labels) == 3
    assert all(labels.values()), "a row past page one rendered an empty source-scope label"
    assert sorted(labels.values()) == ["organization acme-1235", "organization acme-1235", "team payments"]


# ---------------------------------------------------------------------
# astroliftInvitationsPage — status facet + inviter prefetch
# ---------------------------------------------------------------------


def test_invitations_page_status_filter_narrows_items_and_count(org, actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    pending = [_invitation(org, i) for i in range(3)]
    for i in range(2):
        _invitation(org, 10 + i, status=Invitation.Status.ACCEPTED)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        every_status = _page("astrolift_invitations_page", info, limit=50)
        only_pending = _page("astrolift_invitations_page", info, status=Invitation.Status.PENDING, limit=50)

    assert every_status.total_count == 5
    assert only_pending.total_count == 3
    assert {str(i.id) for i in only_pending.items} == {str(inv.guid) for inv in pending}


def test_invitations_page_prefetches_the_inviter_avatar_for_every_page(org, actor, info, permission_resolver):
    """The ``UserInfo`` batch has to run over the page's rows — over the
    whole stream it would load every inviter the org has ever had to
    render one page (#418 + #1235)."""
    from auth1.models import UserInfo

    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    inviter = _user("inviter-1235")
    UserInfo.objects.create(
        internal_user=inviter,
        given_name="",
        family_name="",
        nickname="Inviter",
        name="",
        picture="https://cdn.example/inviter.png",
        locale="en",
        updated_at="2025-01-01T00:00:00Z",
        email="inviter-1235@acme.test",
        email_verified=True,
        iss="https://idp.example",
        aud="client",
        iat=0,
        exp=0,
        sub="inviter-1235-sub",
        sid="sid",
        nonce="nonce",
    )
    invitations = [_invitation(org, i, invited_by=inviter) for i in range(3)]

    avatars: dict[str, str | None] = {}
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        for _ in range(_WALK_BOUND):
            page = _page("astrolift_invitations_page", info, limit=1, after=cursor)
            for item in page.items:
                avatars[str(item.id)] = item.invited_by_avatar_url
            cursor = page.next_cursor
            if cursor is None:
                break

    assert set(avatars) == {str(inv.guid) for inv in invitations}
    assert set(avatars.values()) == {"https://cdn.example/inviter.png"}


def test_invitations_page_search_matches_email_and_inviter(org, actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    inviter = _user("kim-1235")
    by_kim = _invitation(org, 0, invited_by=inviter)
    _invitation(org, 1)
    hunted = _invitation(org, 2)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        by_inviter = _page("astrolift_invitations_page", info, search="kim-1235", limit=50)
        by_email = _page("astrolift_invitations_page", info, search=hunted.email.split("@")[0], limit=50)

    assert [str(i.id) for i in by_inviter.items] == [str(by_kim.guid)]
    assert by_inviter.total_count == 1
    assert [str(i.id) for i in by_email.items] == [str(hunted.guid)]


# ---------------------------------------------------------------------
# Remaining per-resolver specifics
# ---------------------------------------------------------------------


def test_roles_page_shows_the_system_catalog_and_only_this_orgs_custom_roles(
    org, other_org, actor, info, permission_resolver
):
    """System roles carry a null organization and belong to every
    tenant; custom roles do not. The conversion has to keep both halves
    of that predicate."""
    permission_resolver.grant(Permission.ORG_READ)
    mine = _role(org, 0)
    theirs = _role(other_org, 1)
    system_slugs = set(Role.objects.filter(organization__isnull=True).values_list("slug", flat=True))
    assert system_slugs, "precondition: the system role catalog is seeded"

    slug_by_id: dict[str, str] = {}
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        for _ in range(_WALK_BOUND):
            page = _page("astrolift_roles_page", info, limit=5, after=cursor)
            for item in page.items:
                slug_by_id[str(item.id)] = item.slug
            cursor = page.next_cursor
            if cursor is None:
                break

    assert str(mine.guid) in slug_by_id
    assert str(theirs.guid) not in slug_by_id, "another org's custom role leaked"
    assert system_slugs <= set(slug_by_id.values())


def test_api_tokens_page_search_matches_the_last_four(org, actor, info, permission_resolver):
    """``token_last_4`` is what an operator has in hand when chasing a
    token seen in an audit log — searching by it is the point."""
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    hunted = _api_token(org, 0, last_4="9f3a")
    for i in range(1, 4):
        _api_token(org, i)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        by_last_4 = _page("astrolift_api_tokens_page", info, search="9f3a", limit=50)
        by_name = _page("astrolift_api_tokens_page", info, search="token-2", limit=50)

    assert [str(i.id) for i in by_last_4.items] == [str(hunted.guid)]
    assert by_last_4.total_count == 1
    assert by_name.total_count == 1


def test_projects_page_search_matches_the_owning_team(org, actor, info, permission_resolver):
    """Operators navigate projects by team, so the team's slug and name
    are part of the project search surface."""
    permission_resolver.grant(Permission.PROJECT_READ)
    payments = Team.objects.create(organization=org, slug="payments", name="Payments")
    on_payments = Project.objects.create(organization=org, team=payments, slug="ledger", name="Ledger")
    _project(org, 0)
    _project(org, 1)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        page = _page("astrolift_projects_page", info, search="payments", limit=50)

    assert [str(i.id) for i in page.items] == [str(on_payments.guid)]
    assert page.total_count == 1


def test_limit_is_clamped_to_the_shared_maximum(org, actor, info, permission_resolver):
    """``limit`` is caller-supplied and reaches the DB slice; the shared
    helper owns the ceiling so no resolver can be asked for 10k rows."""
    from astrolift_graphql import MAX_PAGE_LIMIT

    permission_resolver.grant(Permission.TEAM_READ)
    for i in range(5):
        _team(org, i)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        huge = _page("astrolift_teams_page", info, limit=MAX_PAGE_LIMIT * 100)
        nonsense = _page("astrolift_teams_page", info, limit=-1)

    assert len(huge.items) == 5
    assert huge.next_cursor is None
    assert len(nonsense.items) == 5, "a negative limit falls back to the default, not an empty page"


def test_a_stale_cursor_restarts_instead_of_erroring(org, actor, info, permission_resolver):
    """A bookmarked URL carrying a mangled token is a UX event, not a
    client error worth failing the whole query over."""
    permission_resolver.grant(Permission.TEAM_READ)
    for i in range(3):
        _team(org, i)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        page = _page("astrolift_teams_page", info, after="not-a-real-cursor", limit=50)

    assert len(page.items) == 3
    assert page.total_count == 3
