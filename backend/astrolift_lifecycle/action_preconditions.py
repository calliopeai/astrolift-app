"""Lock quick-action targets before comparing versions or calling external systems."""

from contextlib import contextmanager

from astrolift_identity.abac import operation_attributes
from astrolift_identity.operation_context import deployment_approval_count, environment_context
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.visibility import live_app_rows, live_lifecycle_rows
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.scopes import _app_scope
from core.permissions import check_permission
from core.tenancy import get_current_tenant


def _org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


@contextmanager
def locked_deployment(guid):
    from django.db import transaction

    with transaction.atomic():
        target = (
            live_lifecycle_rows(Deployment.objects.all())
            .filter(guid=guid)
            .values("pk", "registered_app_id", "app_environment_id")
            .first()
        )
        if target is None:
            yield None
            return
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .select_for_update(of=("self",))
            .filter(pk=target["registered_app_id"])
            .first()
        )
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_for_update(of=("self",))
            .filter(pk=target["app_environment_id"], registered_app=app)
            .select_related("tenant_cluster", "registered_app")
            .first()
            if app
            else None
        )
        if env is None:
            yield None
            return
        row = (
            live_lifecycle_rows(Deployment.objects.all())
            .select_for_update(of=("self",))
            .filter(pk=target["pk"], registered_app=app, app_environment=env)
            .select_related("registered_app", "app_environment", "workload")
            .first()
        )
        if row is not None:
            row.registered_app, row.app_environment = app, env
        yield row


@contextmanager
def locked_workload(guid, *, environment_guid=None):
    from django.db import transaction

    with transaction.atomic():
        target = (
            Workload.objects.filter(guid=guid, registered_app__organization_id=_org_id())
            .values("pk", "registered_app_id")
            .first()
        )
        if target is None:
            yield None, None
            return
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .select_for_update(of=("self",))
            .filter(pk=target["registered_app_id"])
            .select_related("organization")
            .first()
        )
        if app is None:
            yield None, None
            return
        environments = (
            AppEnvironment.objects.select_for_update(of=("self",))
            .filter(registered_app=app)
            .select_related("tenant_cluster", "registered_app")
        )
        env = (
            environments.filter(guid=environment_guid).first()
            if environment_guid is not None
            else environments.order_by("id").first()
        )
        if env is not None:
            from astrolift_clusters.models import TenantCluster

            cluster = (
                TenantCluster.objects.select_for_update(of=("self",)).filter(pk=env.tenant_cluster_id).first()
            )
            if cluster is not None:
                env.tenant_cluster = cluster
            else:
                env = None
        if env is not None:
            env.registered_app = app
        row = (
            Workload.objects.select_for_update(of=("self",))
            .filter(pk=target["pk"], registered_app=app)
            .first()
        )
        if row is not None:
            row.registered_app = app
        yield row, env


def recheck_action(permission, row, environment, *, deployment=False):
    facts = environment_context(environment, approvals=deployment_approval_count(row) if deployment else None)
    from django.db.models import Q
    from django.utils import timezone

    from astrolift_identity.api_tokens import (
        get_current_api_token,
        reset_current_api_token,
        set_current_api_token,
    )
    from astrolift_identity.models import ApiToken
    from core.permissions import PermissionDenied

    credential = get_current_api_token()
    state = None
    if credential is not None:
        tenant = get_current_tenant()
        fresh = (
            ApiToken.objects.filter(
                pk=getattr(credential, "pk", None),
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
                user__is_active=True,
                is_revoked=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .first()
        )
        if fresh is None:
            raise PermissionDenied(permission, None, "api token is no longer valid")
        state = set_current_api_token(fresh)
    try:
        with operation_attributes(**facts.attributes()):
            scope = _app_scope(pk=row.registered_app_id, permission=permission)
            check_permission(permission, scope=scope)
    finally:
        if state is not None:
            reset_current_api_token(state)
