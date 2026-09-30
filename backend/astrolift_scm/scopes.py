"""SCM owners and bearer ceilings, independent of selected team/project."""

from __future__ import annotations

from django.db.models import Q

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, granted_scopes
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _organization_scope() -> PermissionScope:
    tenant = get_current_tenant()
    return PermissionScope(kind=ScopeKind.ORG, id=(tenant.organization_id if tenant else None) or 0)


def _token(permission: Permission):
    from astrolift_identity.api_tokens import get_current_api_token

    token = get_current_api_token()
    if token is not None and token.organization_id != _organization_scope().id:
        raise PermissionDenied(permission, _organization_scope(), "bearer organization mismatch")
    return token


def _org_scope(permission: Permission) -> PermissionScope:
    scope = _organization_scope()
    token = _token(permission)
    if token is not None and token.team_id is not None:
        raise PermissionDenied(permission, scope, "bearer token does not cover organization resources")
    return scope


def scm_org_scope(permission: Permission):
    def _scope(_args):
        return _org_scope(permission)

    return _scope


def _team_apps(qs, team_id: int, permission: Permission):
    from astrolift_identity.permission_resolver import share_levels

    org_id = _organization_scope().id
    return qs.filter(
        Q(team_id=team_id, team__organization_id=org_id, team__deleted_at__isnull=True)
        | Q(
            project__team_id=team_id,
            project__organization_id=org_id,
            project__deleted_at__isnull=True,
            project__team__organization_id=org_id,
            project__team__deleted_at__isnull=True,
        )
        | Q(
            team_accesses__team_id=team_id,
            team_accesses__team__organization_id=org_id,
            team_accesses__team__deleted_at__isnull=True,
            team_accesses__deleted_at__isnull=True,
            team_accesses__access_level__in=share_levels(permission),
        )
    ).distinct()


def _apps():
    from astrolift_registry.models import RegisteredApp

    return RegisteredApp.objects.filter(
        organization_id=_organization_scope().id,
        organization__deleted_at__isnull=True,
        deleted_at__isnull=True,
    )


def _app_scope(permission: Permission, **lookup) -> PermissionScope:
    app_id = _apps().filter(**lookup).values_list("pk", flat=True).first()
    if app_id is None:
        return _org_scope(permission)
    scope = PermissionScope(kind=ScopeKind.APP, id=app_id)
    token = _token(permission)
    if token is not None and token.team_id is not None:
        if not _team_apps(_apps().filter(pk=app_id), token.team_id, permission).exists():
            raise PermissionDenied(permission, scope, "bearer token does not cover this app")
    return scope


def scm_app_scope(permission: Permission, field: str = "input.app_id", *, by_slug: bool = False):
    def _scope(args):
        value = read_arg(args, field) if by_slug else read_guid(args, field)
        if not value:
            return _org_scope(permission)
        return _app_scope(permission, **{("slug" if by_slug else "guid"): str(value)})

    return _scope


def ssh_key_scope(permission: Permission, field: str = "input.id"):
    def _scope(args):
        from astrolift_scm.models import SshDeployKey

        guid = read_guid(args, field)
        if not guid:
            return _org_scope(permission)
        row = (
            SshDeployKey.objects.filter(
                organization_id=_organization_scope().id, guid=guid, deleted_at__isnull=True
            )
            .values_list("registered_app_id", flat=True)
            .first()
        )
        return _app_scope(permission, pk=row) if row else _org_scope(permission)

    return _scope


def visible_ssh_keys(qs):
    """Org keys require an org grant; app keys follow live app ownership/shares."""
    from astrolift_identity.scope_visibility import visible_apps
    from astrolift_registry.models import AppTeamAccess
    from core.permissions import check_permission

    permission = Permission.SCM_READ
    scopes = granted_scopes(get_current_tenant(), permission)
    apps = _apps()
    shared_ids = AppTeamAccess.objects.filter(
        team_id__in=scopes.team_ids,
        team__organization_id=_organization_scope().id,
        team__deleted_at__isnull=True,
        deleted_at__isnull=True,
        access_level__in=["deployer", "owner"],
    ).values("registered_app_id")
    visible = apps.filter(Q(pk__in=visible_apps(apps, permission).values("pk")) | Q(pk__in=shared_ids))
    token = _token(permission)
    if token is not None and token.team_id is not None:
        visible = _team_apps(visible, token.team_id, permission)
    rows = Q(registered_app_id__in=visible.values("pk"))
    try:
        check_permission(permission, scope=_org_scope(permission))
    except PermissionDenied:
        pass
    else:
        rows |= Q(registered_app__isnull=True)
    return qs.filter(rows)


def require_scm_connect() -> None:
    """Translate the resolver denial to Django's HTTP 403 contract."""
    from django.core.exceptions import PermissionDenied as HttpPermissionDenied

    from core.permissions import check_permission

    try:
        check_permission(Permission.SCM_CONNECT, scope=_org_scope(Permission.SCM_CONNECT))
    except PermissionDenied as exc:
        raise HttpPermissionDenied(str(exc)) from exc
