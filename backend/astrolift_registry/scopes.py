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
    scope: PermissionScope, permission: Permission | None, *, allowed_app_ids=None
) -> PermissionScope:
    if permission is None:
        return scope
    from astrolift_identity.api_tokens import get_current_api_token

    token = get_current_api_token()
    if token is None:
        return scope
    org_id = _org_id()
    if token.organization_id != org_id:
        raise PermissionDenied(permission, scope, "credential belongs to another organization")
    if token.team_id is None:
        return scope
    if scope.kind == ScopeKind.APP:
        from astrolift_identity.historical_scopes import HistoricalAppScope

        ids = (
            allowed_app_ids
            if allowed_app_ids is not None
            else _credential_app_ids([scope.id], permission, historical=isinstance(scope, HistoricalAppScope))
        )
        if scope.id in ids:
            return scope
    raise PermissionDenied(permission, scope, "app is outside the credential's team")


def _credential_app_ids(app_ids, permission, *, historical=False):
    """Batch the same ownership/share ceiling used by the scalar scope factory."""
    from django.db.models import Q

    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.historical_scopes import historical_app_owners
    from astrolift_identity.permission_resolver import share_levels
    from astrolift_registry.models import AppTeamAccess, RegisteredApp

    token = get_current_api_token()
    if token is None or token.team_id is None:
        return set(app_ids)
    org_id = _org_id()
    owners = live_app_owners(RegisteredApp.objects.filter(pk__in=app_ids, organization_id=org_id))
    if historical:
        owners = historical_app_owners(RegisteredApp.all_objects.filter(pk__in=app_ids), org_id)
    owned = owners.filter(
        Q(team_id=token.team_id, team__deleted_at__isnull=True)
        | Q(team_id__isnull=True, project__team_id=token.team_id, project__team__deleted_at__isnull=True)
    ).values_list("pk", flat=True)
    shared = AppTeamAccess.objects.filter(
        registered_app_id__in=app_ids,
        registered_app__organization_id=org_id,
        team_id=token.team_id,
        team__organization_id=org_id,
        team__deleted_at__isnull=True,
        access_level__in=share_levels(permission),
    ).values_list("registered_app_id", flat=True)
    return set(owned) | set(shared)


def live_app_owners(qs):
    """Populated ancestors must be live, tenant-owned and agree about the home team."""
    from django.db.models import F, Q

    org_id = _org_id()
    return qs.filter(
        Q(team_id__isnull=True) | Q(team__organization_id=org_id, team__deleted_at__isnull=True),
        Q(project_id__isnull=True)
        | Q(
            project__organization_id=org_id,
            project__deleted_at__isnull=True,
            project__team__organization_id=org_id,
            project__team__deleted_at__isnull=True,
        ),
        Q(team_id__isnull=True) | Q(project_id__isnull=True) | Q(team_id=F("project__team_id")),
    )


def _app_scope(*, permission: Permission | None = None, **lookup: Any) -> PermissionScope:
    from astrolift_registry.models import RegisteredApp

    app_id = (
        live_app_owners(
            RegisteredApp.objects.filter(
                organization_id=_org_id(), organization__deleted_at__isnull=True, **lookup
            )
        )
        .values_list("pk", flat=True)
        .first()
    )
    scope = PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else registry_org_scope()
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
    from astrolift_registry.models import RegisteredApp, Workload

    return Workload.objects.filter(
        registered_app__in=live_app_owners(RegisteredApp.objects.all()),
        registered_app__organization_id=_org_id(),
        registered_app__deleted_at__isnull=True,
        registered_app__organization__deleted_at__isnull=True,
        **lookup,
    ).values_list("registered_app_id", flat=True)


def app_scope_by_workload_guid(field: str = "workload_id", *, permission: Permission | None = None):
    """Workloads have no separate RBAC scope; their live app owns the gate."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        app_id = _workload_apps(guid=guid).first() if guid else None
        scope = PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else registry_org_scope()
        return _credential_scope(scope, permission)

    return _scope


def app_scope_by_workload_slug(field: str = "workload_slug", *, permission: Permission | None = None):
    """An ambiguous workload slug requires org authority rather than picking an app."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        app_ids = list(_workload_apps(slug=slug).distinct()[:2]) if slug else []
        scope = (
            PermissionScope(kind=ScopeKind.APP, id=app_ids[0]) if len(app_ids) == 1 else registry_org_scope()
        )
        return _credential_scope(scope, permission)

    return _scope


def registry_organization_scope(permission: Permission):
    def _scope(_args):
        return _credential_scope(registry_org_scope(), permission)

    return _scope


def registration_project_scope(field: str = "project_id", *, permission: Permission):
    """Creating into a project requires a live destination inside the token's team."""

    def _scope(args):
        from astrolift_identity.api_tokens import get_current_api_token
        from astrolift_identity.models import Project

        guid = read_guid(args, field)
        project = (
            Project.objects.filter(
                guid=guid,
                organization_id=_org_id(),
                organization__deleted_at__isnull=True,
                team__organization_id=_org_id(),
                team__deleted_at__isnull=True,
            ).first()
            if guid
            else None
        )
        scope = PermissionScope(ScopeKind.PROJECT, project.pk) if project else registry_org_scope()
        if guid and project is None and Project.objects.filter(guid=guid, organization_id=_org_id()).exists():
            raise PermissionDenied(permission, scope, "destination ownership is not live")
        token = get_current_api_token()
        if token is not None:
            if token.organization_id != _org_id() or (
                token.team_id is not None and (project is None or token.team_id != project.team_id)
            ):
                raise PermissionDenied(permission, scope, "destination is outside the credential's team")
        return scope

    return _scope


def transfer_destination_scope(*, permission: Permission = Permission.APP_CREATE):
    def _scope(args):
        from astrolift_identity.api_tokens import get_current_api_token
        from astrolift_identity.models import Team

        if read_arg(args, "input.target_project_id") is not None:
            return registration_project_scope("input.target_project_id", permission=permission)(args)
        guid = read_guid(args, "input.target_team_id")
        team = (
            Team.objects.filter(
                guid=guid, organization_id=_org_id(), organization__deleted_at__isnull=True
            ).first()
            if guid
            else None
        )
        scope = PermissionScope(ScopeKind.TEAM, team.pk) if team else registry_org_scope()
        token = get_current_api_token()
        if token is not None and (
            token.organization_id != _org_id()
            or (token.team_id is not None and (team is None or token.team_id != team.pk))
        ):
            raise PermissionDenied(permission, scope, "destination is outside the credential's team")
        return scope

    return _scope
