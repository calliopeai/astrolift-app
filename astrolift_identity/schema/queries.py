"""
Identity queries.

Field names are prefixed with ``astrolift`` so they don't shadow (or
get shadowed by) the legacy ``organization`` app's queries when both
classes merge into the root schema via multiple inheritance.

Each resolver is decorated with ``@require_permission(...)`` first and
``@tenant_scoped(...)`` second so the tenant context is resolved before
the permission check.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_identity.models import (
    Member,
    Organization,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    MemberType,
    OrganizationType,
    ProjectType,
    RoleBindingType,
    RoleType,
    TeamType,
    member_to_type,
    organization_to_type,
    project_to_type,
    role_binding_to_type,
    role_to_type,
    team_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class IdentityQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_organization(self, info: Info, slug: str) -> OrganizationType | None:
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            return None
        return organization_to_type(org)

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_organizations(self, info: Info) -> list[OrganizationType]:
        return [organization_to_type(o) for o in Organization.objects.all()[:100]]

    @strawberry.field
    @require_permission(Permission.TEAM_READ)
    @tenant_scoped()
    def astrolift_teams(self, info: Info) -> list[TeamType]:
        return [team_to_type(t) for t in Team.objects.select_related("organization")[:200]]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_projects(self, info: Info) -> list[ProjectType]:
        qs = Project.objects.select_related("organization", "team")[:200]
        return [project_to_type(p) for p in qs]

    # ---- RBAC queries ------------------------------------------------

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_members(self, info: Info) -> list[MemberType]:
        qs = Member.objects.select_related("user").order_by("-created_at")[:500]
        return [member_to_type(m) for m in qs]

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def astrolift_roles(self, info: Info) -> list[RoleType]:
        qs = Role.objects.order_by("scope_level", "slug")[:200]
        return [role_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def astrolift_role_bindings(self, info: Info) -> list[RoleBindingType]:
        qs = (
            RoleBinding.objects.select_related("user", "role")
            .order_by("-granted_at")[:500]
        )
        return [role_binding_to_type(rb) for rb in qs]
