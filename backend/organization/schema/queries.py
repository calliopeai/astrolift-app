from __future__ import annotations

from typing import Optional

import strawberry
import strawberry_django
from astrolift_graphql import MAX_PAGE_LIMIT, PageType, keyset_page, search_q
from graphql import GraphQLError
from organization.models import Organization
from organization.models.organization import OrganizationMember
from organization.schema.types import (
    OrganizationMemberType,
    OrganizationType,
)
from strawberry.types import Info


def _caller_org_ids(info: Info) -> list[int] | None:
    """Return the org ids the caller can see, or None for unrestricted.

    #537 (tenant-isolation sweep): every list-returning resolver in this
    module previously returned the whole table — superuser or not. Callers
    should only see organizations they are a member of. Superusers retain
    cross-tenant visibility for admin / debugging.

    Returns:
        None — caller is a superuser and may see all orgs.
        [] — anonymous or no memberships; resolvers should return empty.
        [int, ...] — concrete allow-list of organization ids.
    """
    user = info.context.user
    if not getattr(user, "is_authenticated", False):
        return []
    if getattr(user, "is_superuser", False):
        return None
    return list(
        OrganizationMember.objects.filter(
            member=user,
            is_active=True,
            deleted_at__isnull=True,
        ).values_list("organization_id", flat=True)
    )


def _require_authenticated(info: Info) -> None:
    if not info.context.user.is_authenticated:
        raise GraphQLError("Authentication required")


def _organizations_qs(info: Info, *, search: Optional[str] = None):
    """Filtered, unordered org list scoped to the caller's memberships.

    Shared by the list field and its paginated sibling so the two can
    never disagree about which orgs a caller may see. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from the
    seek key.

    ``_caller_org_ids`` returns ``[]`` for anonymous / membership-less
    callers, so ``id__in=[]`` fails closed to zero rows; ``None`` is the
    superuser's cross-tenant bypass (#537).

    Module-level rather than a method on ``Query``: Strawberry passes the
    *root value* as ``self`` to a root Query resolver and the view never
    sets one, so ``self`` is ``None`` in every real request. A resolver
    that reaches this through ``self._organizations_qs(...)`` raises
    ``AttributeError`` over HTTP while still passing a test that
    constructs ``Query()`` by hand.
    """
    allowed = _caller_org_ids(info)
    if allowed is None:
        qs = Organization.objects.all()
    else:
        qs = Organization.objects.filter(id__in=allowed)
    if search:
        # ``Organization.search`` is the denormalised "name slug website"
        # column maintained in ``save()``. Terms are ANDed, matching the
        # ``query`` arg this shares with the deprecated list field.
        for term in (t for t in search.split(" ") if t):
            qs = qs.filter(search__icontains=term)
    return qs


def _members_qs(info: Info, *, search: Optional[str] = None):
    """Filtered, unordered membership rows for the caller's orgs.

    Shared by the list field and its paginated sibling. Soft-deleted
    memberships are deliberately NOT filtered out: the list field has
    always included them and both fields must agree on what a row is.

    ``_caller_org_ids`` returns ``[]`` for anonymous / membership-less
    callers, so ``organization_id__in=[]`` fails closed; ``None`` is the
    superuser's cross-tenant bypass (#537).
    """
    allowed = _caller_org_ids(info)
    if allowed is None:
        qs = OrganizationMember.objects.all()
    else:
        qs = OrganizationMember.objects.filter(organization_id__in=allowed)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "member__username",
                "member__email",
                "member__first_name",
                "member__last_name",
                "organization__name",
                "organization__slug",
            )
        )
    return qs


@strawberry.type
class Query:
    @strawberry_django.field
    def organization(self, info: Info, id: strawberry.ID) -> Optional[OrganizationType]:
        """Fetch a single organization by ID, using the per-request cache.

        #537: the resolver previously trusted the cache lookup alone. The
        cache is per-request but its contents are unscoped, so a caller
        could fetch any org by id. Gate on membership (superusers bypass).
        """
        from core.schema.common import GlobalIDUtils

        pk = GlobalIDUtils.get_pk_flexible(id, expected_type="OrganizationType")
        if pk is None:
            return None
        allowed = _caller_org_ids(info)
        if allowed is not None and int(pk) not in allowed:
            return None
        return info.context._organization_cache.get(int(pk))

    @strawberry.field(
        deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use organizationsPage."
    )
    def organizations(self, info: Info, query: Optional[str] = None) -> list[OrganizationType]:
        """List organizations the caller is a member of.

        #537: prior implementation returned ``Organization.objects.all()``,
        leaking every install's org list to any authed user.

        #1235: it also returned the caller's whole membership set in
        DB-arbitrary order, unbounded. Now ordered newest-first and
        capped; ``organizationsPage`` walks past the cap.
        """
        _require_authenticated(info)
        return _organizations_qs(info, search=query).order_by("-created_at", "-pk")[:MAX_PAGE_LIMIT]

    @strawberry.field(description="Cursor-paginated list of the caller's organizations.")
    def organizations_page(
        self,
        info: Info,
        search: Optional[str] = None,
        limit: int = 50,
        after: Optional[str] = None,
    ) -> PageType[OrganizationType]:
        """Cursor-paginated org list (#1235).

        Replaces ``organizations``, which returned an unbounded, unordered
        queryset — fine for a two-org install, a full table scan rendered
        into one response for an operator who administers hundreds.

        Seek key is ``(-created_at, -pk)``. ``Organization`` is a legacy
        ``BaseCoreModel``: its ``guid`` is a non-unique, non-indexed
        UUIDv4, so it cannot carry the tiebreak the way a modern UUIDv7
        ``guid`` does. The integer pk is monotonic and NOT NULL, which is
        what the walk needs.
        """
        _require_authenticated(info)
        page = keyset_page(
            _organizations_qs(info, search=search),
            cursor=after,
            limit=limit,
            sort_field="created_at",
            tiebreak_field="pk",
        )
        # ``OrganizationType`` is a ``strawberry_django`` type over the
        # ``Organization`` model, so the rows already ARE the GraphQL
        # objects — the same instances the list field hands back.
        return page.map(lambda org: org)

    @strawberry.field(deprecation_reason="Caps at 200 rows with no way to reach the 201st. Use membersPage.")
    def members(self, info: Info) -> list[OrganizationMemberType]:
        """List members of organizations the caller belongs to.

        #537: prior implementation returned ``OrganizationMember.objects.all()``,
        exposing every org's member list cross-tenant.

        #1235: it also returned every membership row of every org the
        caller belongs to, unordered and unbounded. Now ordered
        newest-first and capped; ``membersPage`` walks past the cap.
        """
        _require_authenticated(info)
        return _members_qs(info).order_by("-created_at", "-pk")[:MAX_PAGE_LIMIT]

    @strawberry.field(description="Cursor-paginated list of members in the caller's organizations.")
    def members_page(
        self,
        info: Info,
        search: Optional[str] = None,
        limit: int = 50,
        after: Optional[str] = None,
    ) -> PageType[OrganizationMemberType]:
        """Cursor-paginated membership list (#1235).

        Seek key is ``(-created_at, -pk)``. ``OrganizationMember`` extends
        ``Tracking``, which has no ``guid`` field at all, so the tiebreak
        is the integer pk. ``created_at`` is ``auto_now_add`` and NOT
        NULL.

        ``search`` matches the member's identity (username, email, name)
        and the owning organization — the columns an operator scanning a
        people table types into the filter box.
        """
        _require_authenticated(info)
        page = keyset_page(
            _members_qs(info, search=search),
            cursor=after,
            limit=limit,
            sort_field="created_at",
            tiebreak_field="pk",
        )
        # ``OrganizationMemberType`` is a ``strawberry_django`` type over
        # the ``OrganizationMember`` model — the rows are the GraphQL
        # objects, exactly as the list field returns them.
        return page.map(lambda membership: membership)
