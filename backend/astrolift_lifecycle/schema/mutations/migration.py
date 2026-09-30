"""MigrationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import migration_operation
from astrolift_lifecycle.models import (
    AppEnvironment,
    Deployment,
)
from astrolift_lifecycle.schema.mutations.helpers import (
    _DEPLOY_PIPELINE_DISABLED_MSG,
    _actor_from_request,
    _deploy_pipeline_disabled,
    _migrate_workflow_id,
    _record_workflow_run,
)
from astrolift_lifecycle.schema.mutations.types import (
    MigrateAppInputGql,
)
from astrolift_lifecycle.schema.types import (
    AppEnvironmentType,
    app_env_to_type,
)
from astrolift_lifecycle.scopes import environment_app_scope
from astrolift_lifecycle.visibility import live_lifecycle_rows
from astrolift_workflows.client import (
    start_workflow,
)
from astrolift_workflows.inputs import (
    MigrateAppInput,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.run_trigger import request_trigger
from core.tenancy import get_current_tenant


@strawberry.type
class MigrationMutations:
    # ---- App migration -------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.migrate_to_cluster")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=environment_app_scope("input.app_environment_id", permission=Permission.APP_DEPLOY),
        operation=migration_operation,
    )
    @tenant_scoped()
    def migrate_app_to_cluster(
        self, info: Info, input: MigrateAppInputGql
    ) -> MutationResultType[AppEnvironmentType]:
        """Move an ``AppEnvironment`` from its current cluster to a
        target cluster.

        Workflow applies to the target first, validates the rollout,
        then atomically flips the env's binding. ``drainSource=true``
        cleans up the app's resources on the source cluster as a
        best-effort step after the switch. Gated by the same deploy
        pipeline feature flag — migration relies on the same activity
        stack as deploy.
        """
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)

        from astrolift_clusters.models import TenantCluster

        # Org-scope the app-environment lookup to the caller's tenant before
        # the migration workflow side effect. AppEnvironment reaches the org
        # via registered_app. Fails closed (NOT_FOUND) when org_id is
        # None (#1183). The target cluster is org-scoped separately below.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        env = (
            live_lifecycle_rows(AppEnvironment.objects.all())
            .select_related("registered_app", "tenant_cluster")
            .filter(
                guid=str(input.app_environment_id),
                deleted_at__isnull=True,
                registered_app__organization_id=org_id,
            )
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app environment not found")
        # Org-scope the TARGET cluster fetch to the caller's org (or a
        # platform-shared null-org cluster). TenantCluster.organization is a
        # NULLABLE FK, so a bare by-guid fetch would let a caller migrate an
        # app onto another org's private cluster (#1183). Fails closed
        # (NOT_FOUND) for out-of-scope guids, matching the clusters partition.
        target = TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid=str(input.target_cluster_id),
            deleted_at__isnull=True,
            is_active=True,
        ).first()
        if target is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "target cluster not found")
        if env.tenant_cluster_id == target.pk:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "app environment is already bound to the target cluster",
            )
        if target.lifecycle != TenantCluster.Lifecycle.MANAGED.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"target cluster {target.slug!r} is not managed",
            )
        # Pick the latest non-PENDING_APPROVAL deployment as the source
        # of truth for the migration apply — that's the image + config
        # the source cluster is currently running.
        latest = (
            live_lifecycle_rows(Deployment.objects.all())
            .filter(
                registered_app=env.registered_app,
                app_environment=env,
                deleted_at__isnull=True,
            )
            .exclude(status=Deployment.Status.PENDING_APPROVAL.value)
            .order_by("-created_at")
            .first()
        )
        if latest is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "app environment has no deployment to migrate — deploy first",
            )

        actor = _actor_from_request(info)
        handle = start_workflow(
            "MigrateAppWorkflow",
            args=[
                MigrateAppInput(
                    registered_app_id=env.registered_app_id,
                    app_environment_id=env.pk,
                    deployment_id=latest.pk,
                    target_cluster_id=target.pk,
                    drain_source=bool(input.drain_source),
                    actor=actor,
                ),
            ],
            workflow_id=_migrate_workflow_id(str(env.guid)),
        )
        if handle.enqueued:
            _record_workflow_run(
                kind="MigrateAppWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=tenant.organization_id if tenant else None,
                registered_app_id=env.registered_app_id,
                app_environment_id=env.pk,
                actor=actor,
                trigger_kind=request_trigger(),
            )
        return gql_success(app_env_to_type(env))
