"""Scope resolvers for managed-service-keyed permission gates (#1731).

Same contract as ``astrolift_registry.scopes``: turn the object a request
names into the scope its permission check runs against, confined to the
caller's org, and return ``None`` for anything that does not resolve so
the stricter org-scope check stays in place.

A managed service is owned either by one app (``registered_app``, the
app-private case) or by a project (``project``, the shared case), so the
scope it checks against follows whichever owns it.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_arg
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def managed_service_scope_by_guid(field: str = "managed_service_id"):
    """Scope on the app -- or, for a shared resource, the project --
    owning the managed service named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_arg(args, field)
        if not guid:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        # No org column on the row: ownership is via the app or the
        # project, and both carry one. Either side matching confines the
        # lookup to the caller's org.
        from django.db.models import Q

        from astrolift_services.models import ManagedService

        row = (
            ManagedService.objects.filter(
                Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
                guid=str(guid),
                deleted_at__isnull=True,
            )
            .values_list("registered_app_id", "project_id")
            .first()
        )
        if row is None:
            return None
        app_id, project_id = row
        if app_id:
            return PermissionScope(kind=ScopeKind.APP, id=app_id)
        if project_id:
            return PermissionScope(kind=ScopeKind.PROJECT, id=project_id)
        return None

    return _scope
