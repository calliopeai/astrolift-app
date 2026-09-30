"""Billing belongs to the selected organization, regardless of row attribution."""

from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import get_current_tenant


def billing_org_scope(_args) -> PermissionScope:
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    scope = PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
    token = get_current_api_token()
    # An owner's org role cannot widen a team-bound bearer to org billing.
    if token is not None and (token.team_id is not None or token.organization_id != org_id):
        raise PermissionDenied(
            Permission.BILLING_READ, scope, "bearer token does not cover organization billing"
        )
    return scope
