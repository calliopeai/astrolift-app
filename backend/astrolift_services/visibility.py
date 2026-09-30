"""Live resource rows confined to granted owners and bearer credentials."""

from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.models import Project
from astrolift_identity.scope_visibility import visible_projects
from astrolift_registry.models import RegisteredApp
from astrolift_registry.visibility import visible_registry_apps
from astrolift_services.scopes import _org_id, live_managed_services, live_projects, live_secret_bundles
from core.permissions import Permission, granted_scopes
from core.tenancy import get_current_tenant


def credential_managed_services(qs, *, app_permission=Permission.APP_READ):
    from django.db.models import Q

    token = get_current_api_token()
    qs = live_managed_services(qs)
    if token is None:
        return qs
    if token.organization_id != _org_id():
        return qs.none()
    if token.team_id is None:
        return qs
    apps = visible_registry_apps(RegisteredApp.objects.all(), app_permission)
    projects = live_projects(Project.objects.filter(team_id=token.team_id))
    return qs.filter(Q(registered_app__in=apps) | Q(project__in=projects))


def visible_secret_bundles(qs, permission=Permission.APP_READ):
    from django.db.models import Q

    qs = live_secret_bundles(qs)
    scopes = granted_scopes(get_current_tenant(), permission)
    if not scopes.org:
        projects = visible_projects(live_projects(Project.objects.all()), permission)
        covered = Q(project__in=projects) | Q(
            project_id__isnull=True, team_id__in=scopes.team_ids | scopes.exact_team_ids
        )
        if scopes.org_only:
            covered |= Q(project_id__isnull=True, team_id__isnull=True)
        qs = qs.filter(covered)
    token = get_current_api_token()
    if token is None:
        return qs
    if token.organization_id != _org_id():
        return qs.none()
    if token.team_id is None:
        return qs
    return qs.filter(Q(project__team_id=token.team_id) | Q(project_id__isnull=True, team_id=token.team_id))
