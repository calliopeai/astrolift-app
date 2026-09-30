"""Lifecycle rows retain coherent live owners before reads, counts and writes."""

from django.db.models import F, Q

from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_registry.visibility import visible_registry_apps
from core.tenancy import TenantContext, get_current_tenant, tenant_context

_CURRENT_ORG = object()


def _org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def live_app_rows(qs, *, org_id=_CURRENT_ORG):
    org_id = _org_id() if org_id is _CURRENT_ORG else org_id
    # Internal provider readers may receive an already-authorized organization
    # explicitly. Build the canonical owner subquery under that organization.
    with tenant_context(TenantContext(organization_id=org_id)):
        return live_app_owners(
            qs.filter(organization_id=org_id, organization__deleted_at__isnull=True, deleted_at__isnull=True)
        )


def cluster_owned_and_live(cluster, org_id):
    return (
        cluster is not None
        and cluster.deleted_at is None
        and cluster.is_active
        and cluster.organization_id in (None, org_id)
    )


def visible_apps(qs, permission):
    return visible_registry_apps(live_app_rows(qs), permission)


def historical_deployment_rows(qs):
    """Organization-confined snapshots remain readable after app/environment teardown."""
    org_id = _org_id()
    apps = RegisteredApp.all_objects.filter(
        organization_id=org_id, organization__deleted_at__isnull=True
    ).filter(
        Q(team_id__isnull=True) | Q(team__organization_id=org_id),
        Q(project_id__isnull=True)
        | Q(project__organization_id=org_id, project__team__organization_id=org_id),
        Q(team_id__isnull=True) | Q(project_id__isnull=True) | Q(team_id=F("project__team_id")),
    )
    return qs.filter(deleted_at__isnull=True, registered_app__in=apps).filter(
        Q(workload_id__isnull=True) | Q(workload__registered_app_id=F("registered_app_id")),
        Q(app_environment_id__isnull=True)
        | (
            Q(app_environment__registered_app_id=F("registered_app_id"))
            & (
                Q(app_environment__tenant_cluster__organization_id=org_id)
                | Q(app_environment__tenant_cluster__organization_id__isnull=True)
            )
        ),
    )


def live_lifecycle_rows(qs, *, app_path=None, org_id=_CURRENT_ORG):
    """Every populated environment/workload must belong to the row's actual app."""
    org_id = _org_id() if org_id is _CURRENT_ORG else org_id
    fields = {field.name for field in qs.model._meta.fields}
    if app_path is None:
        app_path = "registered_app" if "registered_app" in fields else "workload__registered_app"
    apps = live_app_rows(RegisteredApp.objects.all(), org_id=org_id)
    qs = qs.filter(deleted_at__isnull=True, **{f"{app_path}__in": apps})
    environment_path = "" if qs.model._meta.model_name == "appenvironment" else "app_environment__"
    if not environment_path or "app_environment" in fields:
        valid = Q(
            **{
                f"{environment_path}deleted_at__isnull": True,
                f"{environment_path}registered_app_id": F(f"{app_path}_id"),
                f"{environment_path}tenant_cluster__deleted_at__isnull": True,
                f"{environment_path}tenant_cluster__is_active": True,
            }
        ) & (
            Q(**{f"{environment_path}tenant_cluster__organization_id": org_id})
            | Q(**{f"{environment_path}tenant_cluster__organization_id__isnull": True})
        )
        if environment_path:
            valid |= Q(app_environment_id__isnull=True)
        qs = qs.filter(valid)
    if "workload" in fields:
        qs = qs.filter(
            Q(workload_id__isnull=True)
            | Q(workload__deleted_at__isnull=True, workload__registered_app_id=F(f"{app_path}_id"))
        )
    return qs


def visible_operation_rows(qs, permission, *, app_path="registered_app", historical=False, **kwargs):
    """Intersect bearer ownership before the shared ABAC operation row filter."""
    from astrolift_identity.operation_visibility import visible_operation_rows as operation_rows

    qs = historical_deployment_rows(qs) if historical else live_lifecycle_rows(qs, app_path=app_path)
    apps = visible_registry_apps(live_app_rows(RegisteredApp.objects.all()), permission)
    qs = qs.filter(**{f"{app_path}__in": apps})
    return operation_rows(qs, permission, app_path=app_path, **kwargs)
