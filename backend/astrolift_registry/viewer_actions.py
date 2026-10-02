"""Batched advisory permission decisions for workload restart and scale."""

from dataclasses import dataclass

from astrolift_identity.abac import (
    RequestAttributes,
    current_attributes,
    operation_attributes,
    request_attributes,
)
from astrolift_identity.operation_context import environment_context
from astrolift_identity.permission_resolver import (
    Decision,
    _app_scope_chains,
    _decide_from_grants,
    _is_superuser,
    _live_grants,
    _share_grants,
    actor_groups,
)
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.visibility import cluster_owned_and_live, live_app_rows
from astrolift_lifecycle.workload_targets import WorkloadActionTarget, action_target
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.scopes import _credential_app_ids, _credential_scope
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    _check_permission_decision,
)
from core.tenancy import TenantContext, get_current_tenant


@dataclass(frozen=True)
class ActionPermission:
    allowed: bool
    code: str = ""
    reason: str = ""
    target: WorkloadActionTarget | None = None


def workload_viewer_permissions(workloads) -> dict[int, ActionPermission]:
    """One page preload; no authority is cached across requests or mutations."""
    ids = {workload.pk for workload in workloads}
    if not ids:
        return {}
    tenant = get_current_tenant() or TenantContext()
    with request_attributes(current_attributes() or RequestAttributes()):
        return _workload_viewer_permissions(ids, tenant)


def _workload_viewer_permissions(ids, tenant):
    if tenant.actor_user_id is None:
        return {pk: ActionPermission(False, "PERMISSION_DENIED", "no actor") for pk in ids}
    result = {
        pk: ActionPermission(False, "PERMISSION_DENIED", "workload has no coherent live owner") for pk in ids
    }
    apps = live_app_rows(RegisteredApp.objects.all())
    rows = list(
        Workload.objects.filter(pk__in=ids, registered_app__in=apps).select_related(
            "registered_app__project", "registered_app__organization"
        )
    )
    app_ids = {row.registered_app_id for row in rows}
    chains = _app_scope_chains(tenant, app_ids)
    groups = actor_groups(tenant)
    grants = tuple(_live_grants(tenant, {link for chain in chains.values() for link in chain}))
    shares = _share_grants(tenant, app_ids)
    superuser = tenant.actor_user_id is not None and _is_superuser(tenant.actor_user_id)
    attrs = current_attributes()
    for row in rows:
        app = row.registered_app
        attrs.cache[("slug", "APP", app.pk)] = app.slug
        if app.project_id and ("PROJECT", app.project_id) in chains.get(app.pk, ()):
            attrs.cache[("slug", "PROJECT", app.project_id)] = app.project.slug
    allowed_app_ids = _credential_app_ids(app_ids, Permission.APP_DEPLOY)
    environments = {}
    for environment in (
        AppEnvironment.objects.filter(registered_app_id__in=app_ids)
        .select_related("tenant_cluster")
        .order_by("id")
    ):
        environments.setdefault(environment.registered_app_id, environment)

    for row in rows:
        app_id = row.registered_app_id
        environment = environments.get(app_id)
        if environment is None or not cluster_owned_and_live(
            environment.tenant_cluster, tenant.organization_id
        ):
            result[row.pk] = ActionPermission(
                False, "PRECONDITION", "workload has no coherent live primary environment"
            )
            continue
        target = action_target(row, environment)
        scope = PermissionScope(ScopeKind.APP, app_id)
        try:
            _credential_scope(scope, Permission.APP_DEPLOY, allowed_app_ids=allowed_app_ids)

            def decide(app_id=app_id):
                if tenant.actor_user_id is None:
                    return False, "no actor"
                if superuser:
                    return True, "django superuser"
                chain = chains.get(app_id)
                decision = (
                    _decide_from_grants(
                        tenant,
                        Permission.APP_DEPLOY,
                        chain,
                        grants,
                        tuple(shares.get(app_id, ())),
                        groups,
                    )
                    if chain
                    else Decision(False, "no scope")
                )
                return decision.as_tuple()

            # Both the entry gate's zero approvals and the locked recheck's
            # unknown approvals must permit the action.
            for approvals in (0, None):
                with operation_attributes(
                    **environment_context(environment, approvals=approvals).attributes()
                ):
                    _check_permission_decision(Permission.APP_DEPLOY, scope, decide)
        except PermissionDenied as exc:
            result[row.pk] = ActionPermission(False, "PERMISSION_DENIED", exc.reason, target=target)
        else:
            result[row.pk] = ActionPermission(True, target=target)
    return result


def exact_workload_viewer_permission(workload, environment) -> ActionPermission:
    from core.permissions import check_permission

    target = action_target(workload, environment)
    if target is None:
        return ActionPermission(False, "PRECONDITION", "workload environment target is unavailable")
    from astrolift_registry.scopes import _app_scope

    try:
        for approvals in (0, None):
            with operation_attributes(**environment_context(environment, approvals=approvals).attributes()):
                check_permission(
                    Permission.APP_DEPLOY,
                    scope=_app_scope(pk=workload.registered_app_id, permission=Permission.APP_DEPLOY),
                )
    except PermissionDenied as exc:
        return ActionPermission(False, "PERMISSION_DENIED", exc.reason, target=target)
    return ActionPermission(True, target=target)
