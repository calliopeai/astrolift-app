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
from core.scope_args import read_arg
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _scope_for(model_name: str, kind: ScopeKind, **lookup: Any) -> PermissionScope | None:
    org_id = _org_id()
    if org_id is None:
        return None
    from astrolift_identity import models as identity_models

    model = getattr(identity_models, model_name)
    pk = model.objects.filter(organization_id=org_id, **lookup).values_list("pk", flat=True).first()
    return PermissionScope(kind=kind, id=pk) if pk else None


def team_scope_by_guid(field: str = "team_id"):
    """Scope on the team named by ``field`` (a GUID argument).

    ``field`` is a dotted path, so a mutation carrying its target on an
    input object passes ``"input.team_id"``.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_arg(args, field)
        return _scope_for("Team", ScopeKind.TEAM, guid=str(guid)) if guid else None

    return _scope


def team_scope_by_slug(field: str = "team_slug"):
    """Scope on the team named by ``field`` (a slug argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        slug = read_arg(args, field)
        return _scope_for("Team", ScopeKind.TEAM, slug=slug) if slug else None

    return _scope


def project_scope_by_guid(field: str = "project_id"):
    """Scope on the project named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_arg(args, field)
        return _scope_for("Project", ScopeKind.PROJECT, guid=str(guid)) if guid else None

    return _scope


def project_scope_by_slug(field: str = "project_slug"):
    """Scope on the project named by ``field`` (a slug argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        slug = read_arg(args, field)
        return _scope_for("Project", ScopeKind.PROJECT, slug=slug) if slug else None

    return _scope
