"""Managed-service metric owners, independent of the selected team (#2111)."""

from __future__ import annotations

from django.db.models import Q

from astrolift_identity.api_tokens import get_current_api_token
from astrolift_registry.scopes import app_scope_by_guid
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _organization_scope() -> PermissionScope:
    tenant = get_current_tenant()
    scope = PermissionScope(kind=ScopeKind.ORG, id=(tenant.organization_id if tenant else None) or 0)
    token = get_current_api_token()
    if token is not None and (token.organization_id != scope.id or token.team_id is not None):
        raise PermissionDenied(
            Permission.APP_READ, scope, "bearer token does not cover organization resources"
        )
    return scope


def managed_service_metrics_scope(field: str = "managed_service_id"):
    """Use the live app or project owner; unresolved owners require org authority."""

    def resolve(args):
        from astrolift_services.models import ManagedService

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        guid = read_guid(args, field)
        if not org_id or not guid:
            return _organization_scope()
        service = (
            ManagedService.objects.filter(
                Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
                guid=guid,
                deleted_at__isnull=True,
            )
            .select_related("registered_app", "project__team")
            .first()
        )
        if service is None:
            return _organization_scope()
        if service.registered_app_id:
            return app_scope_by_guid("app_id", permission=Permission.APP_READ)(
                {"app_id": service.registered_app.guid}
            )
        project = service.project
        if (
            project is None
            or project.deleted_at is not None
            or project.organization_id != org_id
            or (
                project.team_id
                and (project.team.deleted_at is not None or project.team.organization_id != org_id)
            )
        ):
            return _organization_scope()
        scope = PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
        token = get_current_api_token()
        if token is not None and (
            token.organization_id != org_id
            or (token.team_id is not None and token.team_id != project.team_id)
        ):
            raise PermissionDenied(Permission.APP_READ, scope, "bearer token does not cover this project")
        return scope

    return resolve
