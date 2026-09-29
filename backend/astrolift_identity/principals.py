"""
One principal search across users, IdP groups, teams and invitations (#2126).

The Grant access flow picks who from one box, and the People list shows
users and groups as one list of principals, so both read this. Four sources,
each confined to the caller's org and fail closed without one:

* users: the org's ORG membership rows (any lifecycle), matched on
  username, email and name;
* IdP groups: every group the org knows of, from its members' stored
  groups (``Member.idp_groups``), its group role bindings and its group
  role mappings, matched on the group id;
* teams: the org's teams, matched on slug and name;
* invitations: the org's pending invitations, matched on email.

The list is numbered and ordered by kind (users, groups, teams,
invitations), then by name within a kind, so a page is a window over the
four sources laid end to end. Each source is sorted and sliced in SQL
except the groups, whose ids live in a JSON column and two other tables and
are gathered as one sorted set first; ``totalCount`` is exact.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from django.db.models import CharField, Count, F, Func, Q
from django.db.models.functions import Lower

from astrolift_graphql import DEFAULT_PAGE_SIZE, MAX_PAGE_LIMIT, clamp_limit, filter_values, search_q

KINDS = ("USER", "GROUP", "TEAM", "INVITATION")

MAX_TERM = 254


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    """One kind of principal: how many match, and a window of them."""

    kind: str
    count: int
    rows: Callable[[int, int], list[Any]]


@dataclasses.dataclass(frozen=True, slots=True)
class PrincipalPage:
    rows: list[tuple[str, Any]]
    page: int
    page_size: int
    total_count: int
    counts: dict[str, int]


def search(
    org_id: int | None, term: str | None, filter_input: Any, *, page: int | None, page_size: int | None
):
    """The page of principals matching ``term`` and ``filter.kind``."""

    size = clamp_limit(page_size, default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_LIMIT)
    number = page if page is not None and page > 0 else 1
    wanted = [k.upper() for k in filter_values(filter_input).get("kind", KINDS)]
    needle = (term or "").strip()[:MAX_TERM]
    sources = [] if org_id is None else [_SOURCES[k](org_id, needle) for k in KINDS if k in wanted]

    total = sum(s.count for s in sources)
    start, stop = (number - 1) * size, number * size
    rows: list[tuple[str, Any]] = []
    offset = 0
    for source in sources:
        lo, hi = max(start - offset, 0), min(stop - offset, source.count)
        if lo < hi:
            rows.extend((source.kind, row) for row in source.rows(lo, hi))
        offset += source.count
    return PrincipalPage(
        rows=rows,
        page=number,
        page_size=size,
        total_count=total,
        counts={s.kind: s.count for s in sources},
    )


def _users(org_id: int, needle: str) -> Source:
    from astrolift_identity.models import Member

    qs = Member.objects.select_related("user").filter(scope_kind=Member.ScopeKind.ORG, scope_id=org_id)
    if needle:
        qs = qs.filter(
            search_q(needle, "user__username", "user__email", "user__first_name", "user__last_name")
        )
    ordered = qs.order_by(Lower("user__username"), "pk")
    return Source("USER", qs.count(), lambda lo, hi: list(ordered[lo:hi]))


def org_group_ids(org_id: int) -> list[str]:
    """Every IdP group id the org knows of, sorted: its members' stored
    groups, its group role bindings and its group role mappings."""

    from astrolift_identity.models import GroupRoleMapping, Member, RoleBinding
    from astrolift_identity.schema.queries import _org_scope_q

    stored = (
        Member.objects.filter(scope_kind=Member.ScopeKind.ORG, scope_id=org_id, is_active=True)
        .annotate(group=Func(F("idp_groups"), function="jsonb_array_elements_text", output_field=CharField()))
        .values_list("group", flat=True)
        .distinct()
    )
    bound = (
        RoleBinding.objects.filter(_org_scope_q(org_id), user__isnull=True)
        .values_list("group_external_id", flat=True)
        .distinct()
    )
    mapped = (
        GroupRoleMapping.objects.filter(organization_id=org_id)
        .values_list("group_external_id", flat=True)
        .distinct()
    )
    return sorted({g for g in (*stored, *bound, *mapped) if g}, key=lambda g: (g.lower(), g))


@dataclasses.dataclass(frozen=True, slots=True)
class GroupRow:
    group_external_id: str
    member_count: int
    bindings_count: int
    mappings_count: int


def _groups(org_id: int, needle: str) -> Source:
    groups = org_group_ids(org_id)
    if needle:
        low = needle.lower()
        groups = [g for g in groups if low in g.lower()]

    def rows(lo: int, hi: int) -> list[GroupRow]:
        return group_rows(org_id, groups[lo:hi])

    return Source("GROUP", len(groups), rows)


def group_rows(org_id: int, groups: list[str]) -> list[GroupRow]:
    """Member, binding and mapping counts for a page of groups, one query
    per count (members: one per group, as ``group_member_counts`` does)."""

    from astrolift_identity.idp_groups import group_member_counts
    from astrolift_identity.models import GroupRoleMapping, RoleBinding
    from astrolift_identity.schema.queries import _org_scope_q

    if not groups:
        return []
    members = group_member_counts(org_id, groups)
    bindings = dict(
        RoleBinding.objects.filter(_org_scope_q(org_id), user__isnull=True, group_external_id__in=groups)
        .order_by()
        .values("group_external_id")
        .annotate(n=Count("pk"))
        .values_list("group_external_id", "n")
    )
    mappings = dict(
        GroupRoleMapping.objects.filter(organization_id=org_id, group_external_id__in=groups)
        .order_by()
        .values("group_external_id")
        .annotate(n=Count("pk"))
        .values_list("group_external_id", "n")
    )
    return [
        GroupRow(
            group_external_id=g,
            member_count=members.get(g, 0),
            bindings_count=bindings.get(g, 0),
            mappings_count=mappings.get(g, 0),
        )
        for g in groups
    ]


def _teams(org_id: int, needle: str) -> Source:
    from astrolift_identity.models import Member, Team

    qs = Team.objects.filter(organization_id=org_id)
    if needle:
        qs = qs.filter(search_q(needle, "slug", "name"))
    ordered = qs.order_by(Lower("name"), "pk")

    def rows(lo: int, hi: int) -> list[Any]:
        teams = list(ordered[lo:hi])
        counts = dict(
            Member.objects.filter(scope_kind=Member.ScopeKind.TEAM, scope_id__in=[t.pk for t in teams])
            .order_by()
            .values("scope_id")
            .annotate(n=Count("pk"))
            .values_list("scope_id", "n")
        )
        for team in teams:
            team._member_count = counts.get(team.pk, 0)
        return teams

    return Source("TEAM", qs.count(), rows)


def _invitations(org_id: int, needle: str) -> Source:
    from astrolift_identity.models import Invitation

    qs = Invitation.objects.filter(
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org_id,
        status=Invitation.Status.PENDING,
        deleted_at__isnull=True,
    ).select_related("role")
    if needle:
        qs = qs.filter(Q(email__icontains=needle))
    ordered = qs.order_by(Lower("email"), "pk")
    return Source("INVITATION", qs.count(), lambda lo, hi: list(ordered[lo:hi]))


_SOURCES: dict[str, Callable[[int, str], Source]] = {
    "USER": _users,
    "GROUP": _groups,
    "TEAM": _teams,
    "INVITATION": _invitations,
}
