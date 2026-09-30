"""Filter operations collections to live owners before aggregation or paging."""

from django.db.models import CharField, Count, Q
from django.db.models.functions import Cast

from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.models import Project, Team
from astrolift_identity.operation_visibility import visible_operation_rows
from astrolift_identity.scope_visibility import visible_projects, visible_teams
from astrolift_operations.scopes import apps, org_id
from astrolift_registry.visibility import visible_registry_apps
from astrolift_services.scopes import live_projects
from core.permissions import granted_scopes
from core.tenancy import get_current_tenant


def owners(permission):
    scopes = granted_scopes(get_current_tenant(), permission)
    app_rows = visible_registry_apps(apps(), permission)
    projects = visible_projects(live_projects(Project.objects.all()), permission)
    teams = visible_teams(Team.objects.filter(organization_id=org_id(), deleted_at__isnull=True), permission)
    organization = scopes.org or scopes.org_only
    token = get_current_api_token()
    if token is not None:
        if token.organization_id != org_id():
            return app_rows.none(), projects.none(), teams.none(), False
        if token.team_id is not None:
            projects, teams = projects.filter(team_id=token.team_id), teams.filter(pk=token.team_id)
            organization = False
    return app_rows, projects, teams, organization


def visible_events(qs, permission):
    app_rows, projects, teams, organization = owners(permission)
    covered = (
        Q(registered_app__in=app_rows)
        | Q(registered_app__isnull=True, project__in=projects)
        | Q(registered_app__isnull=True, project__isnull=True, team__in=teams)
    )
    if organization:
        covered |= Q(registered_app__isnull=True, project__isnull=True, team__isnull=True)
    return qs.filter(covered, organization_id=org_id())


def visible_webhooks(qs, permission):
    app_rows, _projects, teams, organization = owners(permission)
    covered = Q(registered_app__in=app_rows) | Q(registered_app__isnull=True, team__in=teams)
    if organization:
        covered |= Q(registered_app__isnull=True, team__isnull=True)
    return qs.filter(
        covered,
        Q(team_id__isnull=True) | Q(team__organization_id=org_id(), team__deleted_at__isnull=True),
        organization_id=org_id(),
        deleted_at__isnull=True,
    )


def _ids(qs):
    return qs.annotate(_guid_text=Cast("guid", CharField())).values("_guid_text")


def _unique_slugs(qs, field="slug"):
    return (
        qs.exclude(**{field: ""})
        .order_by()
        .values(field)
        .annotate(_matches=Count("pk"))
        .filter(_matches=1)
        .values(field)
    )


def visible_rules(qs, permission):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload
    from astrolift_services.models import ManagedService
    from astrolift_services.visibility import credential_managed_services

    app_rows, projects, _teams, organization = owners(permission)
    if organization and granted_scopes(get_current_tenant(), permission).org:
        return qs.filter(organization_id=org_id(), deleted_at__isnull=True)
    environments = visible_operation_rows(
        AppEnvironment.objects.filter(registered_app__in=app_rows, deleted_at__isnull=True),
        permission,
        environment_path="self",
    )
    workloads = Workload.objects.filter(registered_app__in=app_rows, deleted_at__isnull=True)
    services = credential_managed_services(ManagedService.objects.all(), app_permission=permission).filter(
        Q(registered_app__in=app_rows) | Q(project__in=projects)
    )
    covered = (
        Q(managed_service__in=services)
        | Q(managed_service__isnull=True, target="app", target_id__in=app_rows.values("slug"))
        | Q(managed_service__isnull=True, target="app", target_id__in=_ids(app_rows))
        | Q(managed_service__isnull=True, target="env", target_id__in=_ids(environments))
        | Q(managed_service__isnull=True, target="workload", target_id__in=_ids(workloads))
        | Q(
            managed_service__isnull=True,
            target="env",
            target_id__in=environments.filter(
                name__in=_unique_slugs(
                    AppEnvironment.objects.filter(registered_app__in=apps(), deleted_at__isnull=True), "name"
                )
            ).values("name"),
        )
        | Q(
            managed_service__isnull=True,
            target="workload",
            target_id__in=workloads.filter(
                slug__in=_unique_slugs(
                    Workload.objects.filter(registered_app__in=apps(), deleted_at__isnull=True)
                )
            ).values("slug"),
        )
    )
    if organization:
        covered |= Q(managed_service__isnull=True, target="global", target_id="")
    return _rule_operations(qs.filter(covered, organization_id=org_id(), deleted_at__isnull=True), permission)


def _rule_operations(qs, permission):
    from astrolift_identity.operation_context import OperationContext, environment_context
    from astrolift_identity.operation_visibility import _evaluator, _operation_policies
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload
    from astrolift_services.models import ManagedService
    from astrolift_services.scopes import live_managed_services

    tenant = get_current_tenant()
    if not _operation_policies(tenant, permission):
        return qs
    app_map = {}
    for app in apps().only("pk", "guid", "slug"):
        app_map[app.slug] = app_map[str(app.guid)] = app.pk
    envs = list(
        AppEnvironment.objects.filter(registered_app__in=apps(), deleted_at__isnull=True)
        .select_related("tenant_cluster")
        .order_by("pk")
    )
    env_map = {str(env.guid): env for env in envs}
    for env in envs:
        if env.name:
            env_map[env.name] = None if env.name in env_map else env
    app_envs = {}
    for env in envs:
        app_envs.setdefault(env.registered_app_id, []).append(env)
    workloads = Workload.objects.filter(registered_app__in=apps(), deleted_at__isnull=True)
    workload_map = {}
    for guid, slug, app_id in workloads.values_list("guid", "slug", "registered_app_id"):
        workload_map[str(guid)] = app_id
        if slug:
            workload_map[slug] = None if slug in workload_map else app_id
    services = {
        service.pk: service
        for service in live_managed_services(ManagedService.objects.all()).select_related(
            "tenant_cluster", "app_environment__tenant_cluster"
        )
    }
    allows, ids = _evaluator(tenant, permission), []
    unknown = OperationContext()
    for pk, target, ident, service_id in qs.order_by().values_list(
        "pk", "target", "target_id", "managed_service_id"
    ):
        point, contexts = ("ORG", org_id()), (unknown,)
        if service_id:
            service = services.get(service_id)
            if service is None:
                continue
            if service.registered_app_id:
                point, contexts = (
                    ("APP", service.registered_app_id),
                    (environment_context(service.app_environment),),
                )
            else:
                point = ("PROJECT", service.project_id)
                contexts = (
                    OperationContext(
                        environment=service.effective_environment_name,
                        region=service.tenant_cluster.region or None,
                        approvals=0,
                    ),
                )
        elif target == "app" and ident in app_map:
            point = ("APP", app_map[ident])
            contexts = tuple(environment_context(env) for env in app_envs.get(point[1], ())) or (unknown,)
        elif target == "env" and env_map.get(ident) is not None:
            env = env_map[ident]
            point, contexts = ("APP", env.registered_app_id), (environment_context(env),)
        elif target == "workload" and workload_map.get(ident) is not None:
            point = ("APP", workload_map[ident])
            env = next(iter(app_envs.get(point[1], ())), None)
            contexts = (environment_context(env),)
        if all(allows(*point, context) for context in contexts):
            ids.append(pk)
    return qs.filter(pk__in=ids)
