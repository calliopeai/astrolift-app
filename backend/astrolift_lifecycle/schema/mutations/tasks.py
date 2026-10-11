"""TaskMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import IntegrityError, transaction
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import named_environment
from astrolift_lifecycle.models import (
    AppEnvironment,
    TaskRun,
)
from astrolift_lifecycle.schema.mutations.types import (
    RunTaskInput,
)
from astrolift_lifecycle.schema.types import (
    TaskRunPayloadType,
    task_run_to_payload,
)
from astrolift_lifecycle.visibility import live_app_rows, live_lifecycle_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.run_trigger import RunTrigger, request_trigger
from core.tenancy import get_current_tenant


@strawberry.type
class TaskMutations:
    # ----------------------------------------------------------------
    # #801 — run_task: operator-initiated execution of a task workload.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="task.run")
    @require_permission(
        Permission.APP_UPDATE,
        scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_UPDATE),
        operation=named_environment(),
    )
    @tenant_scoped()
    def run_task(
        self,
        info: Info,
        input: RunTaskInput,
    ) -> MutationResultType[TaskRunPayloadType]:
        """Trigger a one-shot execution of a ``kind: task`` workload.

        Records a ``TaskRun`` and applies its Kubernetes Job from the
        environment's running deployment (#2330); the run reconciler moves
        it through running to a terminal status. A dispatch failure is
        recorded on the run as ``failed`` with ``failureReason``. Without
        ``environmentName`` the app's only environment is used. A repeated
        ``requestId`` returns the original run instead of starting another.

        The ``command`` field overrides the container's default command
        when provided — useful for one-off script variations without
        requiring a new workload declaration. Omit it to use the
        workload's declared command.
        """
        from astrolift_registry.models import Workload

        workload_slug = (input.workload_slug or "").strip()
        app_slug = (input.app_slug or "").strip()
        if not workload_slug or not app_slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "workload_slug and app_slug are required",
                field="workloadSlug",
            )

        # Org-scope the app lookup to the caller's tenant before creating the
        # TaskRun (which dispatches a one-shot Job). Slugs are unique only
        # within an org. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {app_slug!r} not found",
                field="appSlug",
            )

        workload = Workload.objects.filter(
            slug=workload_slug,
            registered_app=app,
            kind="task",
            deleted_at__isnull=True,
        ).first()
        if workload is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"task workload {workload_slug!r} not found on app {app_slug!r}",
                field="workloadSlug",
            )

        environments = live_lifecycle_rows(AppEnvironment.objects.all()).filter(
            registered_app=app, deleted_at__isnull=True
        )
        env_name = (input.environment_name or "").strip()
        if env_name:
            env = environments.filter(name=env_name).first()
            if env is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"environment {input.environment_name!r} not found",
                    field="environmentName",
                )
        else:
            # A run needs a cluster; without a name only an unambiguous app qualifies.
            candidates = list(environments[:2])
            if len(candidates) != 1:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "environmentName is required when the app has more than one environment",
                    field="environmentName",
                )
            env = candidates[0]
        if env.tenant_cluster_id is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {env.name!r} has no cluster to run on",
                field="environmentName",
            )

        command = input.command or []
        if len(command) > 64 or any(not isinstance(part, str) or len(part) > 4096 for part in command):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "command must be at most 64 strings of at most 4096 characters",
                field="command",
            )
        request_id = (input.request_id or "").strip()
        if len(request_id) > 64:
            return gql_failure(
                ErrorCode.VALIDATION.value, "requestId is at most 64 characters", field="requestId"
            )

        actor = info.context.request.user
        actor_id = actor.pk if actor.is_authenticated else None
        if request_id:
            existing = (
                TaskRun.objects.filter(
                    workload=workload, triggered_by_user_id=actor_id, request_id=request_id
                )
                .select_related("workload__registered_app", "app_environment")
                .first()
            )
            if existing is not None:
                if existing.app_environment_id != env.pk or list(existing.command or []) != list(command):
                    return gql_failure(
                        ErrorCode.CONFLICT.value,
                        "requestId was already used for a different run",
                        field="requestId",
                    )
                return gql_success(task_run_to_payload(existing))
        try:
            with transaction.atomic():
                run = TaskRun.objects.create(
                    workload=workload,
                    app_environment=env,
                    # A token-authenticated call is an API run, not a manual one (#2152).
                    trigger_kind=(
                        TaskRun.TriggerKind.API
                        if request_trigger() == RunTrigger.API
                        else TaskRun.TriggerKind.MANUAL
                    ),
                    triggered_by_user_id=actor_id,
                    command=command,
                    status=TaskRun.Status.PENDING,
                    request_id=request_id,
                )
        except IntegrityError:
            # A concurrent retry with the same key won the insert; return its run.
            existing = TaskRun.objects.filter(
                workload=workload, triggered_by_user_id=actor_id, request_id=request_id
            ).first()
            if existing is None:
                raise
            return gql_success(task_run_to_payload(existing))

        from astrolift_lifecycle.services.task_runner import dispatch_task_run

        run = dispatch_task_run(run)
        return gql_success(task_run_to_payload(run))
