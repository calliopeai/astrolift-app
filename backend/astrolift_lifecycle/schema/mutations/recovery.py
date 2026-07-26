"""RecoveryMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import (
    AppEnvironment,
)
from astrolift_lifecycle.schema.mutations.helpers import (
    _actor_from_request,
)
from astrolift_lifecycle.schema.mutations.types import (
    ForceRedeployInput,
    ForceRedeployPayload,
    RunJobOnceInput,
    RunJobOncePayload,
)
from astrolift_registry.models import RegisteredApp
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class RecoveryMutations:
    # ----------------------------------------------------------------
    # Force redeploy recovery (#389)
    # ----------------------------------------------------------------
    #
    # Recovery path for wedged apps: cancel in-flight Deployment rows,
    # delete the per-workload k8s objects (Deployment / Service /
    # Ingress / CronJob plus bare-slug fallbacks), then re-dispatch
    # the deploy CI workflow. ``app.deploy`` + ``app.update`` are both
    # required (intentionally restrictive — this drops live traffic).

    @strawberry.field
    @mutation_audit(
        action="app.force_redeploy",
        extras=lambda result: (
            {
                "deployments_cancelled": result.data.deployments_cancelled,
                "k8s_objects_deleted": result.data.k8s_objects_deleted,
                "workflow_dispatched": result.data.workflow_dispatched,
            }
            if getattr(result, "ok", False) and getattr(result, "data", None) is not None
            else None
        ),
    )
    @requires_elevation(action_label="app.force_redeploy")
    @require_permission(Permission.APP_DEPLOY, Permission.APP_UPDATE)
    @tenant_scoped()
    def force_astrolift_redeploy(
        self,
        info: Info,
        input: ForceRedeployInput,
    ) -> MutationResultType[ForceRedeployPayload]:
        """Recover a wedged app by cancelling in-flight deploys,
        deleting orphan k8s objects, and re-firing the CI workflow.

        ``confirm_slug`` must equal the app's slug — muscle-memory
        guard so a stray click on a destructive button doesn't tear
        live traffic on the wrong app.

        ``environment_name`` scopes the recovery to one environment;
        omitting it widens the action to every env on the app (the
        normal case when a rename or namespace migration left objects
        across every env).

        The CI re-dispatch flows through the standard pipeline; envs
        that gate on approval still go through the approver flow.
        Dispatch failures don't roll back the cancellation + delete
        steps — those are surfaced in the payload so the operator
        sees what actually changed.
        """
        from astrolift_workflows.activities.force_redeploy import (
            _cancel_in_flight_deploys_sync,
            _delete_app_k8s_objects_sync,
            _redispatch_ci_workflow_sync,
        )

        # Org-scope the app lookup to the caller's tenant BEFORE the
        # destructive recovery (cancels deploys, deletes k8s objects,
        # re-dispatches CI). Slugs are unique only within an org. Fails
        # closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        # Muscle-memory guard. We compare after the lookup so the
        # error never leaks whether an app slug exists.
        if (input.confirm_slug or "").strip() != app.slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "confirm_slug must match the app's slug",
                field="confirmSlug",
            )

        environment_id: int | None = None
        if input.environment_name:
            env = (
                AppEnvironment.objects.filter(
                    registered_app=app,
                    name=input.environment_name,
                    deleted_at__isnull=True,
                )
                .only("pk")
                .first()
            )
            if env is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"environment {input.environment_name!r} not found on app {app.slug!r}",
                    field="environmentName",
                )
            environment_id = env.pk

        cancelled = _cancel_in_flight_deploys_sync(app.pk, environment_id)
        delete_summary = _delete_app_k8s_objects_sync(app.pk, environment_id)
        dispatch = _redispatch_ci_workflow_sync(app.pk)

        return gql_success(
            ForceRedeployPayload(
                deployments_cancelled=cancelled,
                k8s_objects_deleted=int(delete_summary["deleted"]),
                workflow_dispatched=bool(dispatch["dispatched"]),
                run_url=dispatch["run_url"] or None,
                dispatch_message=dispatch["message"] or None,
            ),
        )

    # ----------------------------------------------------------------
    # Run scheduled job once (#390): spawn an ad-hoc k8s Job from a
    # manifest-declared CronJob without touching the schedule. Direct
    # apply through the cluster driver — no Temporal workflow. Gated on
    # ``app.deploy`` since the action lands a workload on a tenant
    # cluster.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.job.run_once")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def run_astrolift_job_once(
        self,
        info: Info,
        input: RunJobOnceInput,
    ) -> MutationResultType[RunJobOncePayload]:
        """Render a single Job from a CronJob's jobTemplate and apply it.

        The job inherits the cronjob's pod spec verbatim (image, env,
        resource asks) but uses a fresh name ``<job_slug>-manual-<8hex>``
        and ``backoffLimit: 0`` so the manual one-shot is observably
        single-attempt. Recorded as a ``ScheduledJobRun`` with
        ``trigger_kind="manual"`` so the existing jobs surface lists
        the manual run alongside controller-issued runs.
        """
        from astrolift_lifecycle.services.job_runner import (
            JobRunError,
            run_job_once,
        )

        if not (input.job_slug or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "job_slug is required",
                field="jobSlug",
            )
        if not (input.environment_name or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "environment_name is required",
                field="environmentName",
            )

        # Org-scope the app lookup to the caller's tenant before applying a
        # one-shot Job to the cluster. Slugs are unique only within an org.
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        actor = _actor_from_request(info)

        try:
            result = run_job_once(
                app,
                input.environment_name,
                input.job_slug,
                actor_user_id=actor.user_id,
                actor_display=actor.display,
            )
        except JobRunError as exc:
            return gql_failure(exc.code, exc.message)

        if not result.ok:
            return gql_failure(
                ErrorCode.INTERNAL.value,
                result.error or "couldn't apply job manifest",
            )

        return gql_success(
            RunJobOncePayload(
                run_name=result.run_name,
                namespace=result.namespace,
                logs_url=result.logs_url,
            ),
        )
