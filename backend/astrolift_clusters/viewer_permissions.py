"""What the viewer may do to a cluster, for clients to offer only that (#1867).

The same ``check_permission`` the cluster resolvers use, at the same scope
(the owning organization), so an API token's ceiling and policy apply too. A
shared cluster (no owning organization) takes every write from the platform
operator alone (``_require_operator_for_shared``), so a tenant sees read only.
Advisory: the mutation still decides.
"""

from __future__ import annotations

from typing import Any

from core.permissions import Permission

READ = (Permission.CLUSTER_READ,)
WRITES = (
    Permission.CLUSTER_UPDATE,
    Permission.CLUSTER_MANAGE,
    Permission.CLUSTER_USERS,
    Permission.CLUSTER_UNREGISTER,
)


def cluster_viewer_permissions(info: Any, *, shared: bool) -> list[str]:
    """Sorted permission keys, worked out once per request and kind of cluster."""
    from core.permissions import (
        PermissionDenied,
        PermissionScope,
        ScopeKind,
        check_permission,
        is_platform_operator,
    )
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        return []
    context = info.context
    memo = getattr(context, "_cluster_viewer_permissions", None)
    if memo is None:
        memo = context._cluster_viewer_permissions = {}
    key = (tenant.organization_id, tenant.actor_user_id, shared)
    if key in memo:
        return memo[key]
    scope = PermissionScope(kind=ScopeKind.ORG, id=tenant.organization_id)
    held = []
    for permission in READ + WRITES:
        try:
            check_permission(permission, scope=scope)
        except PermissionDenied:
            continue
        held.append(permission.value)
    if shared:
        if not is_platform_operator(getattr(getattr(context, "request", None), "user", None)):
            held = [p for p in held if p == Permission.CLUSTER_READ.value]
    memo[key] = sorted(held)
    return memo[key]
