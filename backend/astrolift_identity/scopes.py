"""Scope resolvers for team- and project-keyed permission gates (#1717).

Same shape as ``astrolift_registry.scopes``: turn the team or project a
request names into the scope its permission check runs against, so a
TEAM- or PROJECT-scoped binding can satisfy a gate on its own resource.
Lookups are confined to live owners in the caller's org. A miss checks an
explicit organization scope, independent of the selected team or project.
"""

from __future__ import annotations

from typing import Any

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _org_scope() -> PermissionScope:
    return PermissionScope(kind=ScopeKind.ORG, id=_org_id() or 0)


def _credential_scope(scope: PermissionScope, permission: Permission | None) -> PermissionScope:
    if permission is None:
        return scope
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.models import Project, Team

    token = get_current_api_token()
    if token is None:
        return scope
    if token.organization_id != _org_id():
        raise PermissionDenied(permission, scope, "credential belongs to another organization")
    if token.team_id is None:
        return scope
    team_id = scope.id if scope.kind == ScopeKind.TEAM else None
    if scope.kind == ScopeKind.PROJECT:
        team_id = (
            Project.objects.filter(pk=scope.id, organization_id=_org_id())
            .values_list("team_id", flat=True)
            .first()
        )
    if (
        team_id == token.team_id
        and Team.objects.filter(
            pk=team_id,
            organization_id=_org_id(),
            organization__deleted_at__isnull=True,
        ).exists()
    ):
        return scope
    raise PermissionDenied(permission, scope, "owner is outside the credential's team")


def identity_organization_scope(permission: Permission):
    def _scope(_args):
        return _credential_scope(_org_scope(), permission)

    return _scope


def _scope_for(
    model_name: str, kind: ScopeKind, *, permission: Permission | None = None, **lookup: Any
) -> PermissionScope:
    org_id = _org_id()
    if org_id is None:
        return _credential_scope(_org_scope(), permission)
    from astrolift_identity import models as identity_models

    model = getattr(identity_models, model_name)
    qs = model.objects.filter(organization_id=org_id, organization__deleted_at__isnull=True, **lookup)
    if kind == ScopeKind.PROJECT:
        qs = qs.filter(team__organization_id=org_id, team__deleted_at__isnull=True)
    pk = qs.values_list("pk", flat=True).first()
    return _credential_scope(PermissionScope(kind=kind, id=pk) if pk else _org_scope(), permission)


def team_scope_by_guid(field: str = "team_id", *, permission: Permission | None = None):
    """Scope on the team named by ``field`` (a GUID argument).

    ``field`` is a dotted path, so a mutation carrying its target on an
    input object passes ``"input.team_id"``.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        return (
            _scope_for("Team", ScopeKind.TEAM, permission=permission, guid=guid)
            if guid
            else _credential_scope(_org_scope(), permission)
        )

    return _scope


def team_scope_by_slug(field: str = "team_slug", *, permission: Permission | None = None):
    """Scope on the team named by ``field`` (a slug argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        return (
            _scope_for("Team", ScopeKind.TEAM, permission=permission, slug=slug)
            if slug
            else _credential_scope(_org_scope(), permission)
        )

    return _scope


def project_scope_by_guid(field: str = "project_id", *, permission: Permission | None = None):
    """Scope on the project named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        guid = read_guid(args, field)
        return (
            _scope_for("Project", ScopeKind.PROJECT, permission=permission, guid=guid)
            if guid
            else _credential_scope(_org_scope(), permission)
        )

    return _scope


def project_scope_by_slug(field: str = "project_slug", *, permission: Permission | None = None):
    """Scope on the project named by ``field`` (a slug argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope:
        slug = read_arg(args, field)
        return (
            _scope_for("Project", ScopeKind.PROJECT, permission=permission, slug=slug)
            if slug
            else _credential_scope(_org_scope(), permission)
        )

    return _scope


def project_slug_available_scope(args: dict[str, Any]) -> PermissionScope:
    """An edit checks its project; an availability check checks the destination team."""
    permission = Permission.PROJECT_UPDATE
    project_guid = read_guid(args, "exclude_id")
    if project_guid is None:
        return team_scope_by_guid("team_id", permission=permission)(args)
    from astrolift_identity.models import Project

    team_guid = read_guid(args, "team_id")
    project = Project.objects.filter(
        guid=project_guid,
        organization_id=_org_id(),
        organization__deleted_at__isnull=True,
        team__guid=team_guid,
        team__organization_id=_org_id(),
        team__deleted_at__isnull=True,
    ).first()
    scope = PermissionScope(kind=ScopeKind.PROJECT, id=project.pk) if project else _org_scope()
    return _credential_scope(scope, permission)


def _live_identity_scope(kind, scope_id, *, permission):
    if kind == "TEAM":
        return _scope_for("Team", ScopeKind.TEAM, permission=permission, pk=scope_id)
    if kind == "PROJECT":
        return _scope_for("Project", ScopeKind.PROJECT, permission=permission, pk=scope_id)
    if kind == "APP":
        from astrolift_registry.scopes import _app_scope

        return _app_scope(permission=permission, pk=scope_id)
    return _credential_scope(_org_scope(), permission)


def grant_destination_scope(args):
    from astrolift_identity.schema.mutations.helpers import _resolve_scope_pk_in_org

    kind = str(read_arg(args, "input.scope_kind") or "").upper()
    guid = read_guid(args, "input.scope_guid")
    scope_id = _resolve_scope_pk_in_org(kind, str(guid), _org_id()) if guid else None
    return _live_identity_scope(kind, scope_id, permission=Permission.ORG_MANAGE_MEMBERS)


def role_binding_scope(args):
    from astrolift_identity.models import RoleBinding
    from astrolift_identity.schema.queries import _org_scope_q

    guid = read_guid(args, "input.id")
    binding = RoleBinding.objects.filter(guid=guid).filter(_org_scope_q(_org_id())).first() if guid else None
    return binding_owner_scope(binding)


def binding_owner_scope(binding):
    return _live_identity_scope(
        binding.scope_kind if binding else "ORG",
        binding.scope_id if binding else None,
        permission=Permission.ORG_MANAGE_MEMBERS,
    )
