"""Live identity collections intersect grants with the bearer team's ceiling."""

from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.scope_visibility import visible_projects, visible_teams
from core.permissions import Permission
from core.tenancy import get_current_tenant


def _token_ceiling(qs, *, team_field):
    token = get_current_api_token()
    if token is None:
        return qs
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if token.organization_id != org_id:
        return qs.none()
    return qs.filter(**{team_field: token.team_id}) if token.team_id is not None else qs


def visible_identity_teams(qs, permission=Permission.TEAM_READ):
    return _token_ceiling(
        visible_teams(qs.filter(organization__deleted_at__isnull=True), permission), team_field="pk"
    )


def visible_identity_projects(qs, permission=Permission.PROJECT_READ):
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    return _token_ceiling(
        visible_projects(
            qs.filter(
                organization__deleted_at__isnull=True,
                team__organization_id=org_id,
                team__deleted_at__isnull=True,
            ),
            permission,
        ),
        team_field="team_id",
    )


def visible_identity_bindings(qs):
    """Filter polymorphic binding owners before paging or a bulk revoke."""
    from django.db.models import Q

    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.visibility import visible_registry_apps
    from core.permissions import granted_scopes

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    token = get_current_api_token()
    permission = Permission.ORG_MANAGE_MEMBERS
    if token is not None and token.organization_id != org_id:
        return qs.none()
    scopes = granted_scopes(tenant, permission)
    if scopes.org and (token is None or token.team_id is None):
        # The organization can also clear bindings left on a deleted owner.
        return qs
    teams = visible_identity_teams(Team.objects.filter(organization_id=org_id), permission)
    projects = visible_identity_projects(Project.objects.filter(organization_id=org_id), permission)
    apps = visible_registry_apps(RegisteredApp.objects.filter(organization_id=org_id), permission)
    organization_rows = (
        Q(scope_kind="ORG", scope_id=org_id)
        if scopes.org_only and (token is None or token.team_id is None)
        else Q(pk__in=[])
    )
    return qs.filter(
        organization_rows
        | Q(scope_kind="TEAM", scope_id__in=teams.values("pk"))
        | Q(scope_kind="PROJECT", scope_id__in=projects.values("pk"))
        | Q(scope_kind="APP", scope_id__in=apps.values("pk"))
    )
