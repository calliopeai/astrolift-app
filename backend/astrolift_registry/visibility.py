"""Registry collection visibility, including the bearer credential's ceiling."""

from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.permission_resolver import share_levels
from astrolift_identity.scope_visibility import visible_apps
from astrolift_registry.models import AppTeamAccess
from astrolift_registry.scopes import live_app_owners
from core.permissions import Permission, granted_scopes
from core.tenancy import get_current_tenant


def visible_registry_apps(qs, permission: Permission):
    from django.db.models import Q

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = qs.filter(organization_id=org_id, organization__deleted_at__isnull=True)
    scopes = granted_scopes(tenant, permission)
    if not scopes.org:
        primary = visible_apps(qs, permission)
        actor_shares = AppTeamAccess.objects.filter(
            team_id__in=scopes.team_ids,
            team__organization_id=org_id,
            team__deleted_at__isnull=True,
            access_level__in=share_levels(permission),
        ).values("registered_app_id")
        qs = live_app_owners(qs.filter(Q(pk__in=primary.values("pk")) | Q(pk__in=actor_shares)))
    token = get_current_api_token()
    if token is None:
        return qs
    if token.organization_id != org_id:
        return qs.none()
    if token.team_id is None:
        return qs
    shares = AppTeamAccess.objects.filter(
        team_id=token.team_id,
        team__organization_id=org_id,
        team__deleted_at__isnull=True,
        access_level__in=share_levels(permission),
    ).values("registered_app_id")
    return live_app_owners(qs).filter(
        Q(team_id=token.team_id, team__organization_id=org_id, team__deleted_at__isnull=True)
        | Q(team_id__isnull=True, project__team_id=token.team_id)
        | Q(pk__in=shares)
    )
