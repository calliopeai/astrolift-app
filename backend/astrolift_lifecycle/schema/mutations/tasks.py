"""TaskMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
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
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"), operation=named_environment()
    )
    @tenant_scoped()
    def run_task(
        self,
        info: Info,
        input: RunTaskInput,
    ) -> MutationResultType[TaskRunPayloadType]:
        """Trigger a one-shot execution of a ``kind: task`` workload.

        Creates a ``TaskRun`` record in ``pending`` status and returns it.
        The actual K8s Job dispatch happens via the task runner service
        (to be wired in a follow-up); the record is immediately queryable
        so the Tasks fleet page can show the run in the Recent tab.

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
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
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

        env = None
        if (input.environment_name or "").strip():
            env = AppEnvironment.objects.filter(
                registered_app=app,
                name=input.environment_name,
                deleted_at__isnull=True,
            ).first()
            if env is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"environment {input.environment_name!r} not found",
                    field="environmentName",
                )

        actor = info.context.request.user
        run = TaskRun.objects.create(
            workload=workload,
            app_environment=env,
            # A token-authenticated call is an API run, not a manual one (#2152).
            trigger_kind=(
                TaskRun.TriggerKind.API if request_trigger() == RunTrigger.API else TaskRun.TriggerKind.MANUAL
            ),
            triggered_by_user=actor if actor.is_authenticated else None,
            command=input.command or [],
            status=TaskRun.Status.PENDING,
        )
        return gql_success(task_run_to_payload(run))
