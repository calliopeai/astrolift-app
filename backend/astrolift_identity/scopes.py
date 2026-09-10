"""Scope resolvers for team- and project-keyed permission gates (#1717).

Same shape as ``astrolift_registry.scopes``: turn the team or project a
request names into the scope its permission check runs against, so a
TEAM- or PROJECT-scoped binding can satisfy a gate on its own resource.
Lookups are confined to the caller's org; anything else resolves to
``None`` and leaves the stricter org-scope check in place.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def team_scope_by_guid(field: str = "team_id"):
    """Scope on the team named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = args.get(field)
        if not guid:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        from astrolift_identity.models import Team

        team_id = (
            Team.objects.filter(guid=str(guid), organization_id=org_id).values_list("pk", flat=True).first()
        )
        return PermissionScope(kind=ScopeKind.TEAM, id=team_id) if team_id else None

    return _scope
