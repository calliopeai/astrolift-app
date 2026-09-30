"""Org owners for cluster/domain gates; team selections cannot grant upward."""

from __future__ import annotations

from django.db.models import Q

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def _org_scope(permission: Permission) -> PermissionScope:
    from astrolift_identity.api_tokens import get_current_api_token

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    scope = PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
    token = get_current_api_token()
    # An org role held by a token's owner cannot widen its team ceiling,
    # including admin-scoped tokens minted for a platform operator.
    if token is not None and (token.team_id is not None or token.organization_id != org_id):
        raise PermissionDenied(permission, scope, "bearer token does not cover organization resources")
    return scope


def cluster_catalog_org_scope(permission: Permission):
    """Creation and org collections have no team/project/app owner."""

    def _scope(_args):
        return _org_scope(permission)

    return _scope


def cluster_org_scope(permission: Permission, field: str = "cluster_id", *, by_slug: bool = False):
    """The visible cluster's org owner, or explicit org on a miss/shared row."""

    def _scope(args):
        from astrolift_clusters.models import TenantCluster

        fallback = _org_scope(permission)
        value = read_arg(args, field) if by_slug else read_guid(args, field)
        if not value or not fallback.id:
            return fallback
        owner_id = (
            TenantCluster.objects.filter(
                Q(organization_id=fallback.id) | Q(organization_id__isnull=True),
                **{("slug" if by_slug else "guid"): str(value)},
            )
            .values_list("organization_id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.ORG, id=owner_id) if owner_id else fallback

    return _scope


def domain_org_scope(permission: Permission, field: str = "input.id", *, by_zone: bool = False):
    """The visible domain's org owner, or explicit org on a miss/shared row."""

    def _scope(args):
        from astrolift_clusters.models import ManagedDomain

        fallback = _org_scope(permission)
        value = read_arg(args, field) if by_zone else read_guid(args, field)
        if not value or not fallback.id:
            return fallback
        value = str(value).strip().lower().rstrip(".") if by_zone else str(value)
        owner_id = (
            ManagedDomain.objects.filter(
                Q(organization_id=fallback.id) | Q(organization_id__isnull=True),
                **{("zone" if by_zone else "guid"): value},
            )
            .values_list("organization_id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.ORG, id=owner_id) if owner_id else fallback

    return _scope
