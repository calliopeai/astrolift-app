"""
Identity queries.

Every resolver is decorated with ``@require_permission(...)`` first
and ``@tenant_scoped(...)`` second so the tenant context is resolved
before the permission check. Tenant scoping silently filters
querysets via the manager; the permission check decides whether the
resolver runs at all.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.types import (
    OrganizationType,
    ProjectType,
    TeamType,
    organization_to_type,
    project_to_type,
    team_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class IdentityQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def organization(self, info: Info, slug: str) -> OrganizationType | None:
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            return None
        return organization_to_type(org)

    @strawberry.field
    @require_permission(Permission.ORG_READ)
    @tenant_scoped()
    def organizations(self, info: Info) -> list[OrganizationType]:
        return [organization_to_type(o) for o in Organization.objects.all()[:100]]

    @strawberry.field
    @require_permission(Permission.TEAM_READ)
    @tenant_scoped()
    def teams(self, info: Info) -> list[TeamType]:
        return [team_to_type(t) for t in Team.objects.select_related("organization")[:200]]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def projects(self, info: Info) -> list[ProjectType]:
        qs = Project.objects.select_related("organization", "team")[:200]
        return [project_to_type(p) for p in qs]
