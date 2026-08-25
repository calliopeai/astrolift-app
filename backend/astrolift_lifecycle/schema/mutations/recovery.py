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
    RerunOnboardingInput,
    RerunOnboardingPayload,
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

    # ----------------------------------------------------------------
    # #1550 — re-run onboarding for an app it never finished
    # ----------------------------------------------------------------
    #
    # The app doctor emits a `fix` verb per failing check and the panel
    # maps each to a mutation. Three of the four verbs already had one;
    # `rerun_onboarding` did not, so the check that fires when an app
    # has no `registry_repo_uri` -- provisioning never completed, the
    # app can never build -- told the frontend to offer a button with
    # nothing behind it. Same shape as the class this sweep has been
    # clearing, arriving from the producer side instead.
    #
    # `OnboardAppWorkflow` reads only `registered_app_id` from its input
    # (verified against the workflow, not the docstring); the other
    # three fields are carried for dataclass stability. Provisioning
    # activities are individually idempotent, so a re-run against a
    # partly-provisioned app fills the gaps rather than duplicating.

    @strawberry.field
    @mutation_audit(
        action="app.rerun_onboarding",
        extras=lambda result: (
            {
                "started": result.data.started,
                "already_running": result.data.already_running,
                "workflow_id": result.data.workflow_id,
            }
            if getattr(result, "ok", False) and getattr(result, "data", None) is not None
            else None
        ),
    )
    @require_permission(Permission.APP_UPDATE, Permission.APP_CREATE)
    @tenant_scoped()
    def rerun_astrolift_onboarding(
        self,
        info: Info,
        input: RerunOnboardingInput,
    ) -> MutationResultType[RerunOnboardingPayload]:
        """Re-run ``OnboardAppWorkflow`` for an app whose provisioning
        never completed (#1550).

        Refuses while a run is already in flight rather than starting a
        second one. That refusal is deliberate and load-bearing: the
        shared ``start_workflow`` submits under
        ``WorkflowIDReusePolicy.TERMINATE_IF_RUNNING``, which is right
        for a deploy (an operator re-deploying wants the new one to win)
        and wrong here -- it would kill a provisioning run halfway
        through and start over, so the impatient second click is the one
        that costs you the most. Two call sites describe the id as a
        dedupe guard that makes a concurrent click safe; it is not, and
        their comments are corrected alongside this.

        Both non-start outcomes come back as ``ok`` with ``started``
        false, because neither is the caller's error: one means the
        repair is already underway, the other that Temporal is switched
        off in this environment. ``detail`` says which.
        """
        from astrolift_workflows.client import (
            describe_workflow_instance,
            start_workflow,
        )
        from astrolift_workflows.inputs import OnboardAppInput

        # Org-scope the lookup before provisioning anything. Slugs are
        # unique only within an org; fails closed (NOT_FOUND) when
        # org_id is None (#1183).
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

        workflow_id = f"OnboardAppWorkflow-{app.guid}"

        # `describe` returns None when Temporal is disabled or the id was
        # never used -- both mean "nothing running", so only an explicit
        # RUNNING blocks. A describe that fails for any other reason also
        # lands here as None; starting is the safe reading, since the
        # alternative is refusing to repair an app on a transient error.
        existing = describe_workflow_instance(workflow_id)
        if existing and existing.get("status") == "RUNNING":
            return gql_success(
                RerunOnboardingPayload(
                    started=False,
                    already_running=True,
                    workflow_id=workflow_id,
                    detail=(
                        "onboarding is already running for this app; "
                        "starting another would terminate it mid-provision"
                    ),
                ),
            )

        try:
            handle = start_workflow(
                "OnboardAppWorkflow",
                args=[
                    OnboardAppInput(
                        registered_app_id=app.pk,
                        # Straight through: the helper already resolves the
                        # system-actor case, and rebuilding an Actor from its
                        # fields would relabel a system caller as a user.
                        actor=_actor_from_request(info),
                        # Legacy input fields the workflow does not read.
                        provider_plugin_id=0,
                        tenant_cluster_id=0,
                    )
                ],
                workflow_id=workflow_id,
            )
        except Exception as exc:  # noqa: BLE001 - a mutation never raises
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"couldn't start onboarding for {app.slug!r}: {exc}",
            )

        if not handle.enqueued:
            return gql_success(
                RerunOnboardingPayload(
                    started=False,
                    already_running=False,
                    workflow_id=workflow_id,
                    detail="Temporal is disabled in this environment; nothing was enqueued",
                ),
            )

        return gql_success(
            RerunOnboardingPayload(
                started=True,
                already_running=False,
                workflow_id=workflow_id,
                detail="onboarding re-run submitted",
            ),
        )
