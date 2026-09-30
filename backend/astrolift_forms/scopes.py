"""Forms and their submissions are organization-owned resources."""

from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import get_current_tenant


def form_organization_scope(permission: Permission) -> PermissionScope:
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    scope = PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
    token = get_current_api_token()
    if token is not None and (token.team_id is not None or token.organization_id != org_id):
        raise PermissionDenied(permission, scope, "bearer token does not cover organization forms")
    return scope


def form_org_scope(permission: Permission):
    def _scope(_args):
        return form_organization_scope(permission)

    return _scope
