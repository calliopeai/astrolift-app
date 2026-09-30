"""Resolve app gates inside the live tenant; misses take an explicit org scope."""

from __future__ import annotations

from typing import Any

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def registry_org_scope(_args=None) -> PermissionScope:
    return PermissionScope(kind=ScopeKind.ORG, id=_org_id() or 0)


def _credential_scope(
    scope: PermissionScope, permission: Permission | None
) -> PermissionScope:
    if permission is None:
        return scope
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.permission_resolver import share_levels
    from astrolift_registry.models import AppTeamAccess, RegisteredApp

    token = get_current_api_token()
    if token is None:
        return scope
    org_id = _org_id()
    if token.organization_id != org_id:
        raise PermissionDenied(
            permission, scope, "credential belongs to another organization"
        )
    if token.team_id is None:
        return scope
    if scope.kind == ScopeKind.APP:
        from django.db.models import Q

        owned = (
            live_app_owners(
                RegisteredApp.objects.filter(pk=scope.id, organization_id=org_id)
            )
            .filter(
                Q(team_id=token.team_id)
                | Q(team_id__isnull=True, project__team_id=token.team_id)
            )
            .exists()
        )
        shared = AppTeamAccess.objects.filter(
            registered_app_id=scope.id,
            registered_app__organization_id=org_id,
            team_id=token.team_id,
            team__organization_id=org_id,
            team__deleted_at__isnull=True,
            access_level__in=share_levels(permission),
        ).exists()
        if owned or shared:
            return scope
    raise PermissionDenied(permission, scope, "app is outside the credential's team")


def live_app_owners(qs):
    """Populated ancestors must be live, tenant-owned and agree about the home team."""
    from django.db.models import F, Q

    org_id = _org_id()
    return qs.filter(
        Q(team_id__isnull=True)
        | Q(team__organization_id=org_id, team__deleted_at__isnull=True),
        Q(project_id__isnull=True)
        | Q(
            project__organization_id=org_id,
            project__deleted_at__isnull=True,
            project__team__organization_id=org_id,
            project__team__deleted_at__isnull=True,
        ),
        Q(team_id__isnull=True)
        | Q(project_id__isnull=True)
        | Q(team_id=F("project__team_id")),
    )


def _app_scope(
    *, permission: Permission | None = None, **lookup: Any
) -> PermissionScope:
    from astrolift_registry.models import RegisteredApp

    app_id = (
        live_app_owners(
            RegisteredApp.objects.filter(
                organization_id=_org_id(),
                organization__deleted_at__isnull=True,
                **lookup,
            )
        )
        .values_list("pk", flat=True)
        .first()
    )
    scope = (
        PermissionScope(kind=ScopeKind.APP, id=app_id)
        if app_id
        else registry_org_scope()
    )
    return _credential_scope(scope, permission)


def app_scope_by_slug(field: str = "app_slug", *, permission: Permission | None = None):
    """Resolve a slug; dotted input paths and existing callers remain supported.

    Supplying the gate's permission also enforces the bearer team/share ceiling.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        if not slug:
            return _credential_scope(registry_org_scope(), permission)
        return _app_scope(slug=slug, permission=permission)

    return _scope


def app_scope_by_guid(field: str = "app_id", *, permission: Permission | None = None):
    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        if not guid:
            return _credential_scope(registry_org_scope(), permission)
        return _app_scope(guid=guid, permission=permission)

    return _scope


def _workload_apps(**lookup):
    from astrolift_registry.models import Workload

    from astrolift_registry.models import RegisteredApp

    return Workload.objects.filter(
        registered_app__in=live_app_owners(RegisteredApp.objects.all()),
        registered_app__organization_id=_org_id(),
        registered_app__deleted_at__isnull=True,
        registered_app__organization__deleted_at__isnull=True,
        **lookup,
    ).values_list("registered_app_id", flat=True)


def app_scope_by_workload_guid(
    field: str = "workload_id", *, permission: Permission | None = None
):
    """Workloads have no separate RBAC scope; their live app owns the gate."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        app_id = _workload_apps(guid=guid).first() if guid else None
        scope = (
            PermissionScope(kind=ScopeKind.APP, id=app_id)
            if app_id
            else registry_org_scope()
        )
        return _credential_scope(scope, permission)

    return _scope


def app_scope_by_workload_slug(
    field: str = "workload_slug", *, permission: Permission | None = None
):
    """An ambiguous workload slug requires org authority rather than picking an app."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        app_ids = list(_workload_apps(slug=slug).distinct()[:2]) if slug else []
        scope = (
            PermissionScope(kind=ScopeKind.APP, id=app_ids[0])
            if len(app_ids) == 1
            else registry_org_scope()
        )
        return _credential_scope(scope, permission)

    return _scope
