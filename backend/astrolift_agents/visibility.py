"""Permission and bearer-token boundaries for agent workloads and tasks."""

from django.db.models import Q

from astrolift_identity.api_tokens import get_current_api_token, token_scope_allows_permission
from astrolift_identity.models import Project, Team
from astrolift_identity.scope_visibility import visible_apps, visible_projects, visible_teams
from astrolift_registry.models import AppTeamAccess, RegisteredApp, Workload
from core.permissions import Permission, PermissionScope, ScopeKind, granted_scopes
from core.tenancy import get_current_tenant

_READ_PERMISSIONS = {Permission.AGENT_READ, Permission.APP_READ, Permission.AGENT_TASK_WATCH}


def _shares(team_ids, permission):
    return Q(
        team_accesses__team_id__in=team_ids,
        team_accesses__deleted_at__isnull=True,
        team_accesses__access_level__in=_share_levels(permission),
    )


def _share_levels(permission):
    levels = [AppTeamAccess.AccessLevel.DEPLOYER, AppTeamAccess.AccessLevel.OWNER]
    return [AppTeamAccess.AccessLevel.VIEWER, *levels] if permission in _READ_PERMISSIONS else levels


def _permitted_apps(qs, permission):
    scopes = granted_scopes(get_current_tenant(), permission)
    if scopes.org:
        return qs
    primary = visible_apps(qs, permission)
    return qs.filter(Q(pk__in=primary.values("pk")) | _shares(scopes.team_ids, permission)).distinct()


def app_permission_scope(app, permission):
    """App ancestry, or a team whose explicit share delegates this action."""
    scopes = granted_scopes(get_current_tenant(), permission)
    if (
        scopes.org
        or app.pk in scopes.app_ids
        or app.project_id in scopes.project_ids
        or app.team_id in scopes.team_ids
    ):
        return PermissionScope(kind=ScopeKind.APP, id=app.pk)
    shares = AppTeamAccess.objects.filter(
        registered_app=app, team_id__in=scopes.team_ids, access_level__in=_share_levels(permission)
    )
    team_id = shares.values_list("team_id", flat=True).first()
    return PermissionScope(kind=ScopeKind.TEAM, id=team_id) if team_id is not None else None


def _token_allows_org(org_id, permission):
    from django.contrib.auth import get_user_model

    tenant = get_current_tenant()
    if tenant is None or org_id is None:
        return False
    if (
        tenant.organization_id != org_id
        and not get_user_model()
        .objects.filter(pk=tenant.actor_user_id, is_active=True, is_superuser=True)
        .exists()
    ):
        return False
    token = get_current_api_token()
    if token is None:
        return True
    if token.organization_id != org_id or not token_scope_allows_permission(token, permission.value):
        return False
    return token.team_id is None or Team.objects.filter(pk=token.team_id, organization_id=org_id).exists()


def _token_apps(qs, permission):
    token = get_current_api_token()
    if token is None or token.team_id is None:
        return qs
    return qs.filter(Q(team_id=token.team_id) | _shares([token.team_id], permission)).distinct()


def workload_rows(org_id, permission):
    """Live workloads covered by RBAC and the token's team/share ceiling."""
    qs = Workload.objects.filter(
        registered_app__organization_id=org_id,
        registered_app__organization__deleted_at__isnull=True,
        registered_app__deleted_at__isnull=True,
    )
    if not _token_allows_org(org_id, permission):
        return qs.none()
    apps = RegisteredApp.objects.filter(organization_id=org_id)
    apps = _token_apps(_permitted_apps(apps, permission), permission)
    return qs.filter(registered_app_id__in=apps.values("pk"))


def agent_workloads(org_id, permission=Permission.AGENT_READ):
    return workload_rows(org_id, permission).filter(kind=Workload.Kind.AGENT)


def dispatchable_agent_workloads(org_id):
    """Agent workloads ``runAstroliftAgent`` would actually accept from the
    caller (#2071): gated on ``agent.dispatch``, not the coarser
    ``agent.read`` the plain agent list uses, through the same token
    team/share ceiling every other org-scoped agent read applies, and
    narrowed to the Task run family -- ``dispatch_registered_agent``
    refuses a Service-family agent regardless of RBAC. A caller who can
    read an agent but not dispatch it, or whose bearer token is scoped to
    a team that does not hold the dispatch share, sees it absent here even
    though ``agent_workloads`` still lists it."""
    return agent_workloads(org_id, Permission.AGENT_DISPATCH).filter(run_family=Workload.RunFamily.TASK)


def agent_by_slug(org_id, slug, permission):
    """Resolve one authorized workload; callers still validate its kind."""
    matches = list(
        workload_rows(org_id, permission)
        .filter(slug=slug)
        .select_related("registered_app", "registered_app__organization")[:2]
    )
    return matches[0] if len(matches) == 1 else None


def agent_tasks(org_id, permission):
    """Task ownership is project, else team, else registered-agent app.

    Invalid or absent ownership is org-only. In particular, a stale
    project/team reference never falls through to a different app owner.
    The same priority is used by ``agent_task_scope`` for object gates.
    """
    from astrolift_agents.models import AgentTask

    qs = AgentTask.objects.filter(
        organization_id=org_id, organization__deleted_at__isnull=True
    ).select_related("project", "team", "agent_definition__registered_app")
    return _owned_rows(qs, org_id, permission)


def agent_boxes(org_id, permission):
    """Live boxes the caller reaches at ``permission`` (#1866).

    A box is owned like a task: a recorded project, else a recorded team,
    else its agent's app, else the org alone, behind the same bearer-token
    ceiling. ``agent_box_scope`` applies the same priority to object gates.
    """
    from astrolift_agents.models import AgentBox

    qs = AgentBox.objects.filter(
        organization_id=org_id, organization__deleted_at__isnull=True
    ).select_related(
        "organization", "project", "team", "agent_definition__registered_app", "environment_spec"
    )
    return _owned_rows(qs, org_id, permission)


def _owned_rows(qs, org_id, permission):
    """Narrow task or box rows to the ones ``permission`` reaches."""
    if not _token_allows_org(org_id, permission):
        return qs.none()

    scopes = granted_scopes(get_current_tenant(), permission)
    projects = Project.objects.filter(organization_id=org_id)
    teams = Team.objects.filter(organization_id=org_id)
    apps = RegisteredApp.objects.filter(organization_id=org_id)
    if not scopes.org:
        projects = visible_projects(projects, permission)
        teams = visible_teams(teams, permission)
        apps = _permitted_apps(apps, permission)

    token = get_current_api_token()
    if token is not None and token.team_id is not None:
        projects = projects.filter(team_id=token.team_id)
        teams = teams.filter(pk=token.team_id)
        apps = _token_apps(apps, permission)
    elif scopes.org:
        return qs

    definitions = Workload.objects.filter(kind=Workload.Kind.AGENT, registered_app_id__in=apps.values("pk"))
    return qs.filter(
        Q(project_id__in=projects.values("pk"))
        | Q(project_id__isnull=True, team_id__in=teams.values("pk"))
        | Q(project_id__isnull=True, team_id__isnull=True, agent_definition_id__in=definitions.values("pk"))
    )


def environment_specs(org_id, permission):
    """Live environment specs the caller reaches at ``permission`` (#1866).

    An org-shared spec (no team, no project) reaches every holder of
    ``permission`` in the org: it is the org's to share. An owned spec
    reaches holders covering its project, else its team; an owner that is
    deleted or in another org leaves the spec to org-level grants, never to
    whichever team is selected. A team-scoped bearer token sees the
    org-shared specs and its own team's.
    """
    from astrolift_agents.models import AgentEnvironmentSpec

    qs = AgentEnvironmentSpec.objects.filter(
        organization_id=org_id,
        organization__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).select_related("organization", "team", "project")
    if not _token_allows_org(org_id, permission):
        return qs.none()
    scopes = granted_scopes(get_current_tenant(), permission)
    if not scopes:
        return qs.none()
    token = get_current_api_token()
    token_team_id = token.team_id if token is not None else None
    if scopes.org and token_team_id is None:
        return qs
    projects = Project.objects.filter(organization_id=org_id)
    teams = Team.objects.filter(organization_id=org_id)
    if not scopes.org:
        projects = visible_projects(projects, permission)
        teams = visible_teams(teams, permission)
    if token_team_id is not None:
        projects = projects.filter(team_id=token_team_id)
        teams = teams.filter(pk=token_team_id)
    return qs.filter(
        Q(team__isnull=True, project__isnull=True)
        | Q(project_id__in=projects.values("pk"))
        | Q(project__isnull=True, team_id__in=teams.values("pk"))
    )


def spec_owner_scope(spec) -> PermissionScope:
    """The scope a spec's writes are checked at (#1866).

    Its live project, else its live team. An org-shared spec, or one whose
    owner is deleted or foreign, takes an explicit org scope: a factory
    miss must not inherit the selected team or project (#1745).
    """
    org_id = spec.organization_id
    if spec.project_id is not None:
        project = spec.project
        if project.organization_id == org_id and project.deleted_at is None:
            return PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
    elif spec.team_id is not None:
        team = spec.team
        if team.organization_id == org_id and team.deleted_at is None:
            return PermissionScope(kind=ScopeKind.TEAM, id=team.pk)
    return PermissionScope(kind=ScopeKind.ORG, id=org_id)


def spec_usable_by_app(spec, app) -> bool:
    """Whether ``app``'s agents may run with ``spec`` (#1866).

    An env spec's secrets are usable only by agents in scopes that may read
    it: an org-shared spec serves every app in its org, a project's spec
    that project's apps, and a team's spec (with no project) that team's
    apps. A spec whose owner is gone serves nobody.
    """
    if spec is None or app is None or spec.organization_id != app.organization_id:
        return False
    if spec.project_id is None and spec.team_id is None:
        return True
    if spec_owner_scope(spec).kind == ScopeKind.ORG:
        return False
    if spec.project_id is not None:
        return spec.project_id == app.project_id
    return spec.team_id == app.team_id


def watchable_task_ids(tasks):
    """Batch authorization before any framebuffer URLs are minted."""
    from astrolift_agents.models import AgentTask

    ids_by_org: dict[int, list[int]] = {}
    for task in tasks:
        if task.status == AgentTask.Status.RUNNING and task.vnc_enabled:
            ids_by_org.setdefault(task.organization_id, []).append(task.pk)
    allowed = set()
    for org_id, task_ids in ids_by_org.items():
        allowed.update(
            agent_tasks(org_id, Permission.AGENT_TASK_WATCH)
            .filter(pk__in=task_ids)
            .values_list("pk", flat=True)
        )
    return allowed
