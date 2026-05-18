from __future__ import annotations

from typing import Optional

import strawberry
import strawberry_django
from graphql import GraphQLError
from organization.models import Organization
from organization.models.organization import OrganizationMember
from organization.schema.types import (
    EmployeeEdge,
    EmployeeNode,
    EmployeesConnection,
    EmployeesPageInfo,
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

    @strawberry.field
    def organizations(self, info: Info, query: Optional[str] = None) -> list[OrganizationType]:
        """List organizations the caller is a member of.

        #537: prior implementation returned ``Organization.objects.all()``,
        leaking every install's org list to any authed user.
        """
        _require_authenticated(info)
        allowed = _caller_org_ids(info)
        if allowed is None:
            qs = Organization.objects.all()
        else:
            qs = Organization.objects.filter(id__in=allowed)
        if query:
            terms = [q for q in query.split(" ") if q]
            for term in terms:
                qs = qs.filter(search__icontains=term)
        return qs

    @strawberry.field
    def members(self, info: Info) -> list[OrganizationMemberType]:
        """List members of organizations the caller belongs to.

        #537: prior implementation returned ``OrganizationMember.objects.all()``,
        exposing every org's member list cross-tenant.
        """
        _require_authenticated(info)
        allowed = _caller_org_ids(info)
        if allowed is None:
            return OrganizationMember.objects.all()
        return OrganizationMember.objects.filter(organization_id__in=allowed)

    @strawberry.field
    def employees(
        self,
        info: Info,
        first: Optional[int] = 10,
        offset: Optional[int] = 0,
        search: Optional[str] = None,
        show_deactivated: Optional[bool] = None,
        departments_department_name_icontains: Optional[str] = None,
        departments_position_name_icontains: Optional[str] = None,
    ) -> EmployeesConnection:
        """Paginated list of employees in orgs the caller belongs to.

        #537: prior implementation returned every membership row across
        every tenant; the employees grid would render colleagues from
        other installs' organizations.
        """
        _require_authenticated(info)

        qs = OrganizationMember.objects.select_related("member", "member__profile", "organization").filter(
            deleted_at__isnull=True,
        )
        allowed = _caller_org_ids(info)
        if allowed is not None:
            qs = qs.filter(organization_id__in=allowed)

        if show_deactivated is True:
            qs = qs.filter(is_active=False)
        elif show_deactivated is False:
            qs = qs.filter(is_active=True)

        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(member__first_name__icontains=search)
                | Q(member__last_name__icontains=search)
                | Q(member__email__icontains=search)
                | Q(member__profile__display_name__icontains=search)
            )

        total_count = qs.count()
        page = qs.order_by("member__last_name", "member__first_name")[offset : offset + first]

        edges = [
            EmployeeEdge(
                cursor=str(offset + i),
                node=EmployeeNode.from_membership(m),
            )
            for i, m in enumerate(page)
        ]

        return EmployeesConnection(
            total_count=total_count,
            edges=edges,
            page_info=EmployeesPageInfo(
                has_next_page=(offset + first) < total_count,
            ),
        )
