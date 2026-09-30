"""organizationsPage / membersPage — cursor pagination (#1235).

``organizations`` and ``members`` are the two oldest list resolvers in
the tree and the only two that shipped with no bound at all: both
returned a raw ``QuerySet`` with no ``.order_by()`` and no slice. On a
two-org install that reads as "fine"; on an install where the operator
administers hundreds of orgs, or an org with thousands of memberships,
every call was a full table read rendered into one GraphQL response, in
whatever order the plan happened to produce.

These tests pin the conversion: the legacy fields now truncate at a
known bound in a deterministic order, the page fields walk past it, and
neither field can drift from the other because they share one queryset
builder.

Both models predate ``BaseCoreModel``'s UUIDv7 ``guid``:
``Organization``'s ``guid`` is a non-unique, non-indexed UUIDv4 and
``OrganizationMember`` has no ``guid`` at all, so the seek key on both
is ``(-created_at, -pk)``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from astrolift_graphql import MAX_PAGE_LIMIT
from organization.models import Organization
from organization.models.organization import OrganizationMember
from organization.schema.queries import Query as OrgQuery

pytestmark = pytest.mark.django_db

User = get_user_model()


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


class _FakeRequest:
    """Minimal request stand-in for ``StrawberryContext`` (mirrors
    ``core/tests/test_tenant_isolation.py``)."""

    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


@pytest.fixture
def caller(db):
    return User.objects.create(username="caller-1235", email="caller-1235@example.test")


@pytest.fixture
def operator(db):
    return User.objects.create_superuser(
        username="org-root-1235",
        email="org-root-1235@example.test",
        password="x",
    )


def _org(name: str) -> Organization:
    return Organization.objects.create(name=name)


def _join(org: Organization, user, **kwargs) -> OrganizationMember:
    return OrganizationMember.objects.create(organization=org, member=user, is_active=True, **kwargs)


def _db_org_slugs(**filters) -> list[str]:
    """Org slugs in the DB's own seek order.

    ``created_at`` is ``auto_now_add``, so rows written in a tight loop
    can share one value. Asserting against creation order would be
    asserting against a coincidence; this is the ordering the walk is
    contractually required to reproduce.
    """
    return list(
        Organization.objects.filter(**filters).order_by("-created_at", "-pk").values_list("slug", flat=True)
    )


def _db_member_pks(**filters) -> list[int]:
    return list(
        OrganizationMember.objects.filter(**filters)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)
    )


def _walk_orgs(user, *, limit: int, **kwargs) -> list[str]:
    slugs: list[str] = []
    cursor: str | None = None
    query = OrgQuery()
    for _ in range(50):  # bounded so a non-terminating walk fails loudly
        page = query.organizations_page(_info(user), limit=limit, after=cursor, **kwargs)
        slugs.extend(org.slug for org in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return slugs
    raise AssertionError("walk did not terminate")


def _walk_members(user, *, limit: int, **kwargs) -> list[int]:
    pks: list[int] = []
    cursor: str | None = None
    query = OrgQuery()
    for _ in range(50):  # bounded so a non-terminating walk fails loudly
        page = query.members_page(_info(user), limit=limit, after=cursor, **kwargs)
        pks.extend(m.pk for m in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return pks
    raise AssertionError("walk did not terminate")


# ===========================================================================
# organizations / organizationsPage
# ===========================================================================


def test_organizations_walk_reaches_every_row_exactly_once(caller):
    """205 orgs is not a hypothetical: an install operator's own account
    accumulates one membership per org they administer."""
    for n in range(205):
        _join(_org(f"Walk Org {n:03d}"), caller)

    slugs = _walk_orgs(caller, limit=50)
    assert len(slugs) == 205
    assert len(set(slugs)) == 205, "an org was served twice"
    assert slugs == _db_org_slugs()


def test_organizations_walk_is_newest_first_across_uneven_page_sizes(caller):
    for n in range(7):
        _join(_org(f"Uneven Org {n}"), caller)

    expected = _db_org_slugs()
    assert _walk_orgs(caller, limit=3) == expected
    assert _walk_orgs(caller, limit=1) == expected
    assert _walk_orgs(caller, limit=7) == expected


def test_organizations_list_field_now_truncates_deterministically(caller):
    """The list field used to return all 205 in plan order. It now caps —
    which only means something because the ordering is deterministic."""
    for n in range(205):
        _join(_org(f"Capped Org {n:03d}"), caller)

    rows = list(OrgQuery().organizations(_info(caller)))
    assert len(rows) == MAX_PAGE_LIMIT
    assert [o.slug for o in rows] == _db_org_slugs()[:MAX_PAGE_LIMIT]


def test_organizations_page_clamps_an_absurd_limit(caller):
    for n in range(205):
        _join(_org(f"Clamp Org {n:03d}"), caller)

    page = OrgQuery().organizations_page(_info(caller), limit=999999999)
    assert len(page.items) == MAX_PAGE_LIMIT
    assert page.total_count == 205
    assert page.next_cursor is not None


def test_organizations_total_count_is_the_whole_result_set(caller):
    for n in range(12):
        _join(_org(f"Count Org {n:02d}"), caller)

    page = OrgQuery().organizations_page(_info(caller), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_organizations_from_other_installs_appear_in_neither_items_nor_count(caller):
    """``_caller_org_ids`` is the whole tenant boundary on this surface
    (#537) — the page field has to inherit it, count included."""
    _join(_org("Mine"), caller)
    stranger = User.objects.create(username="stranger-1235", email="stranger-1235@example.test")
    _join(_org("Theirs"), stranger)

    page = OrgQuery().organizations_page(_info(caller), limit=50)
    assert [o.slug for o in page.items] == ["mine"]
    assert page.total_count == 1, "the count leaked another install's org"


def test_organizations_page_denies_by_default_for_a_membershipless_caller(caller):
    """``_caller_org_ids`` returns ``[]``, which must read as zero rows —
    not as "no filter"."""
    _org("Unreachable")

    page = OrgQuery().organizations_page(_info(caller), limit=50)
    assert page.items == []
    assert page.total_count == 0


def test_organizations_page_refuses_anonymous_callers(caller):
    from django.contrib.auth.models import AnonymousUser

    _join(_org("Mine"), caller)
    with pytest.raises(PermissionDenied, match="Authentication required"):
        OrgQuery().organizations_page(_info(AnonymousUser()))


def test_organizations_page_keeps_the_superuser_bypass(caller, operator):
    """``_caller_org_ids`` returns ``None`` for superusers — cross-tenant
    visibility for admin / debugging is a deliberate #537 semantic and
    must survive the conversion."""
    _join(_org("Alpha Co"), caller)
    _org("Unjoined Co")

    page = OrgQuery().organizations_page(_info(operator), limit=50)
    assert {o.slug for o in page.items} == {"alpha-co", "unjoined-co"}
    assert page.total_count == 2


def test_organizations_search_narrows_total_count_not_just_the_page(caller):
    """A count that ignored ``search`` would render "3 results" over a
    one-row table."""
    for name in ("Alpha Widgets", "Beta Widgets", "Gamma Tools"):
        _join(_org(name), caller)

    page = OrgQuery().organizations_page(_info(caller), search="Widgets")
    assert {o.slug for o in page.items} == {"alpha-widgets", "beta-widgets"}
    assert page.total_count == 2

    narrower = OrgQuery().organizations_page(_info(caller), search="Alpha Widgets")
    assert [o.slug for o in narrower.items] == ["alpha-widgets"]
    assert narrower.total_count == 1, "multi-term search must AND, not OR"


def test_organizations_search_matches_the_slug_too(caller):
    """``Organization.search`` is the denormalised "name slug website"
    column, so a slug fragment is a legitimate query."""
    _join(_org("Alpha Widgets"), caller)
    _join(_org("Gamma Tools"), caller)

    page = OrgQuery().organizations_page(_info(caller), search="gamma-tools")
    assert [o.slug for o in page.items] == ["gamma-tools"]


def test_organizations_list_and_page_agree_on_what_a_row_is(caller):
    """Both fields call ``_organizations_qs`` — this is what stops the
    deprecated field and its replacement from drifting apart on scope or
    on search semantics."""
    for name in ("Alpha Widgets", "Beta Widgets", "Gamma Tools"):
        _join(_org(name), caller)
    stranger = User.objects.create(username="drift-1235", email="drift-1235@example.test")
    _join(_org("Widgets Inc"), stranger)

    listed = [o.slug for o in OrgQuery().organizations(_info(caller), query="Widgets")]
    paged = [o.slug for o in OrgQuery().organizations_page(_info(caller), search="Widgets").items]
    assert listed == paged
    assert "widgets-inc" not in paged


def test_pages_resolve_through_the_schema_and_not_only_under_direct_call(caller):
    """Every other test here constructs ``OrgQuery()``. A real request
    does not: Strawberry passes the *root value* as ``self`` to a root
    Query resolver and the view never sets one, so ``self`` is ``None``
    over HTTP. A resolver reaching its queryset builder through
    ``self._organizations_qs(...)`` raises ``AttributeError`` on every
    real call while passing this whole module — that is why the builders
    are module-level, and this is what pins it.

    It also exercises the other thing direct invocation skips: these two
    Page types carry raw Django model instances (``OrganizationType`` is
    a ``strawberry_django`` type), so field resolution off the model is
    only proven through the schema.
    """
    from config.schema import schema
    from core.schema.context import StrawberryContext

    alpha = _org("Alpha Co")
    _join(alpha, caller)
    _join(_org("Beta Co"), caller)

    orgs = schema.execute_sync(
        "{ organizationsPage(limit: 5) { items { name slug guid createdAt } totalCount nextCursor } }",
        context_value=StrawberryContext(_FakeRequest(caller)),
    )
    assert orgs.errors is None, orgs.errors
    page = orgs.data["organizationsPage"]
    assert page["totalCount"] == 2
    assert {o["slug"] for o in page["items"]} == {"alpha-co", "beta-co"}
    assert all(o["guid"] and o["createdAt"] for o in page["items"]), "model fields did not resolve"
    assert page["nextCursor"] is None

    members = schema.execute_sync(
        "{ membersPage(limit: 5) { items { isActive createdAt version } totalCount } }",
        context_value=StrawberryContext(_FakeRequest(caller)),
    )
    assert members.errors is None, members.errors
    assert members.data["membersPage"]["totalCount"] == 2
    assert all(m["createdAt"] for m in members.data["membersPage"]["items"])


def test_organizations_cursor_round_trips_through_the_schema(caller):
    """The cursor a client receives has to be usable as an ``after``
    variable on the next request — the whole contract of the field."""
    from config.schema import schema
    from core.schema.context import StrawberryContext

    for n in range(5):
        _join(_org(f"Round Trip {n}"), caller)

    first = schema.execute_sync(
        "{ organizationsPage(limit: 2) { items { slug } nextCursor } }",
        context_value=StrawberryContext(_FakeRequest(caller)),
    )
    assert first.errors is None, first.errors
    cursor = first.data["organizationsPage"]["nextCursor"]
    assert cursor

    second = schema.execute_sync(
        "query($a: String) { organizationsPage(limit: 2, after: $a) { items { slug } } }",
        variable_values={"a": cursor},
        context_value=StrawberryContext(_FakeRequest(caller)),
    )
    assert second.errors is None, second.errors
    seen = [o["slug"] for o in first.data["organizationsPage"]["items"]]
    seen += [o["slug"] for o in second.data["organizationsPage"]["items"]]
    assert seen == _db_org_slugs()[:4]


# ===========================================================================
# members / membersPage
# ===========================================================================


def _fill_memberships(org: Organization, count: int) -> None:
    """Populate live accounts so paging exercises the active-membership boundary."""
    prefix = uuid4().hex
    users = User.objects.bulk_create(
        [
            User(username=f"page-{prefix}-{n}", email=f"page-{prefix}-{n}@example.test", is_active=True)
            for n in range(count)
        ]
    )
    OrganizationMember.objects.bulk_create(
        [OrganizationMember(organization=org, member=user, is_active=True) for user in users]
    )


def test_members_walk_reaches_every_row_exactly_once(caller):
    org = _org("Big Org")
    _join(org, caller)
    _fill_memberships(org, 204)

    pks = _walk_members(caller, limit=50)
    assert len(pks) == 205
    assert len(set(pks)) == 205, "a membership was served twice"
    assert pks == _db_member_pks(organization=org)


def test_members_walk_is_newest_first_across_uneven_page_sizes(caller):
    org = _org("Uneven Org")
    _join(org, caller)
    _fill_memberships(org, 6)

    expected = _db_member_pks(organization=org)
    assert _walk_members(caller, limit=3) == expected
    assert _walk_members(caller, limit=1) == expected
    assert _walk_members(caller, limit=7) == expected


def test_members_list_field_now_truncates_deterministically(caller):
    org = _org("Capped Org")
    _join(org, caller)
    _fill_memberships(org, 204)

    rows = list(OrgQuery().members(_info(caller)))
    assert len(rows) == MAX_PAGE_LIMIT
    assert [m.pk for m in rows] == _db_member_pks(organization=org)[:MAX_PAGE_LIMIT]


def test_members_total_count_is_the_whole_result_set(caller):
    org = _org("Count Org")
    _join(org, caller)
    _fill_memberships(org, 11)

    page = OrgQuery().members_page(_info(caller), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_members_of_other_orgs_appear_in_neither_items_nor_count(caller):
    """#537's leak was ``OrganizationMember.objects.all()``. The page
    field inherits the fix through ``_members_qs`` — including in the
    count, which is the half that is easy to forget."""
    mine = _org("Mine")
    _join(mine, caller)
    theirs = _org("Theirs")
    stranger = User.objects.create(username="other-member-1235", email="other-1235@example.test")
    _join(theirs, stranger)

    page = OrgQuery().members_page(_info(caller), limit=50)
    assert {m.organization_id for m in page.items} == {mine.pk}
    assert page.total_count == 1, "the count leaked another org's membership"


def test_members_page_denies_by_default_for_a_membershipless_caller(caller):
    org = _org("Unreachable")
    _fill_memberships(org, 3)

    page = OrgQuery().members_page(_info(caller), limit=50)
    assert page.items == []
    assert page.total_count == 0


def test_members_page_refuses_anonymous_callers(caller):
    from django.contrib.auth.models import AnonymousUser

    _join(_org("Mine"), caller)
    with pytest.raises(PermissionDenied, match="Authentication required"):
        OrgQuery().members_page(_info(AnonymousUser()))


def test_members_page_keeps_the_superuser_bypass(caller, operator):
    mine = _org("Alpha Co")
    _join(mine, caller)
    theirs = _org("Beta Co")
    _fill_memberships(theirs, 2)

    page = OrgQuery().members_page(_info(operator), limit=50)
    assert {m.organization_id for m in page.items} == {mine.pk, theirs.pk}
    assert page.total_count == 3


def test_members_search_narrows_total_count_not_just_the_page(caller):
    org = _org("Search Org")
    _join(org, caller)
    ana = User.objects.create(username="ana-1235", email="ana@example.test", first_name="Ana")
    bob = User.objects.create(username="bob-1235", email="bob@example.test", first_name="Bob")
    _join(org, ana)
    _join(org, bob)

    by_username = OrgQuery().members_page(_info(caller), search="ana-1235")
    assert [m.member_id for m in by_username.items] == [ana.pk]
    assert by_username.total_count == 1

    by_email = OrgQuery().members_page(_info(caller), search="bob@example")
    assert [m.member_id for m in by_email.items] == [bob.pk]
    assert by_email.total_count == 1

    by_first_name = OrgQuery().members_page(_info(caller), search="Ana")
    assert [m.member_id for m in by_first_name.items] == [ana.pk]
    assert by_first_name.total_count == 1


def test_members_search_matches_the_owning_organization(caller):
    """A caller in several orgs filters the people table by org name;
    that is why ``search`` reaches through the organization FK."""
    alpha = _org("Alpha Co")
    beta = _org("Beta Co")
    _join(alpha, caller)
    _join(beta, caller)

    page = OrgQuery().members_page(_info(caller), search="Beta")
    assert {m.organization_id for m in page.items} == {beta.pk}
    assert page.total_count == 1


def test_members_search_omits_null_accounts_even_when_organization_matches(caller):
    org = _org("Nullable Org")
    _join(org, caller)
    OrganizationMember.objects.bulk_create(
        [OrganizationMember(organization=org, member=None, is_active=True) for _ in range(3)]
    )

    page = OrgQuery().members_page(_info(caller), search="Nullable")
    assert page.total_count == 1
    assert [row.member_id for row in page.items] == [caller.pk]


def test_members_page_and_list_omit_soft_deleted_memberships(caller):
    org = _org("Soft Org")
    caller_membership = _join(org, caller)
    gone = _join(org, caller)
    gone.deleted_at = gone.created_at
    gone.save(update_fields=["deleted_at"])

    page = OrgQuery().members_page(_info(caller), limit=50)
    assert {m.pk for m in page.items} == {caller_membership.pk}
    assert page.total_count == 1
    assert {m.pk for m in OrgQuery().members(_info(caller))} == {caller_membership.pk}


def test_members_garbage_cursor_restarts_from_the_top(caller):
    org = _org("Cursor Org")
    _join(org, caller)
    _fill_memberships(org, 3)

    page = OrgQuery().members_page(_info(caller), limit=10, after="not-a-real-cursor")
    assert [m.pk for m in page.items] == _db_member_pks(organization=org)
