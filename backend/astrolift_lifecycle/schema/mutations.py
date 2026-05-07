"""
Deployment control plane mutations.

This is the GraphQL surface for the workflow control plane. Each
mutation:

  1. Validates inputs and resolves the target row.
  2. Mutates DB state through ``Deployment.transition_to`` (no direct
     status writes — the state machine is the only path).
  3. Submits or signals a Temporal workflow via
     ``astrolift_workflows.client``.
  4. Records a ``WorkflowRun`` mirror row when a new workflow starts.

Single-flight per (app, env) is enforced through the workflow id
``DeployAppWorkflow-<app-guid>-<env-guid>``. A duplicate start collides
on the workflow id and Temporal rejects it.

Approve / abort / rollback / redeploy / tear-down all share the same
pattern; the differences are in the input payload, target workflow,
and post-condition state.
"""

from __future__ import annotations

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    AppEnvironment,
    Deployment,
    PreviewEnvironment,
)
from astrolift_lifecycle.schema.types import DeploymentType, deployment_to_type
from astrolift_operations.models import WorkflowRun
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.client import (
    signal_workflow,
    start_workflow,
    terminate_workflow,
)
from astrolift_workflows.inputs import (
    Actor,
    DeployAppInput,
    RollbackInput,
    TearDownPreviewInput,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@strawberry.input
class StartDeploymentInput:
    app_slug: str
    environment_name: str
    image_tag: str
    image_digest: str | None = None
    workload_slug: str | None = None
    trigger_kind: str = "manual"


@strawberry.input
class DeploymentByIdInput:
    id: GUID


@strawberry.input
class TearDownPreviewInputGql:
    id: GUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_TRIGGER_KINDS = {k.value for k in Deployment.TriggerKind}


def _actor_from_request(info: Info) -> Actor:
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        return Actor(
            kind="user",
            user_id=user.pk,
            display=getattr(user, "username", "") or "",
        )
    tenant = get_current_tenant()
    if tenant and tenant.actor_user_id:
        return Actor(kind="user", user_id=tenant.actor_user_id, display="")
    return Actor(kind="system", display="system")


def _deploy_workflow_id(app_guid: str, env_guid: str) -> str:
    return f"DeployAppWorkflow-{app_guid}-{env_guid}"


def _rollback_workflow_id(deploy_guid: str) -> str:
    return f"RollbackDeploymentWorkflow-{deploy_guid}"


def _teardown_workflow_id(preview_guid: str) -> str:
    return f"TearDownPreviewWorkflow-{preview_guid}"


def _record_workflow_run(
    *,
    kind: str,
    workflow_id: str,
    run_id: str,
    organization_id: int | None,
    registered_app_id: int | None,
    app_environment_id: int | None,
    actor: Actor,
) -> WorkflowRun:
    return WorkflowRun.objects.create(
        workflow_kind=kind,
        workflow_id=workflow_id,
        run_id=run_id or "",
        status=WorkflowRun.Status.RUNNING,
        started_at=timezone.now(),
        organization_id=organization_id,
        registered_app_id=registered_app_id,
        app_environment_id=app_environment_id,
        trigger_actor_user_id=actor.user_id,
        trigger_actor_token_kind="",
        trigger_actor_token_id=None,
    )


def _resolve_app_env(
    app_slug: str, environment_name: str
) -> tuple[RegisteredApp, AppEnvironment] | None:
    app = (
        RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return None
    env = (
        AppEnvironment.objects.filter(
            registered_app=app,
            name=environment_name,
            deleted_at__isnull=True,
        )
        .select_related("registered_app", "tenant_cluster", "managed_domain")
        .first()
    )
    if env is None:
        return None
    return app, env


# ---------------------------------------------------------------------------
# Root mutation type
# ---------------------------------------------------------------------------


@strawberry.type
class LifecycleMutation:
    @strawberry.field
    @mutation_audit(action="deployment.start")
    @require_permission(Permission.APP_DEPLOY)
    def start_deployment(
        self, info: Info, input: StartDeploymentInput
    ) -> MutationResultType[DeploymentType]:
        if input.trigger_kind not in _VALID_TRIGGER_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown trigger_kind: {input.trigger_kind}",
                field="triggerKind",
            )
        if not input.image_tag:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "image_tag is required",
                field="imageTag",
            )

        resolved = _resolve_app_env(input.app_slug, input.environment_name)
        if resolved is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} environment {input.environment_name!r} not found",
            )
        app, env = resolved

        if env.deploys_paused:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {env.name!r} has deploys paused",
                field="environmentName",
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()

        with transaction.atomic():
            initial_status = (
                Deployment.Status.PENDING_APPROVAL
                if env.required_approvals > 0
                else Deployment.Status.PENDING
            )
            deployment = Deployment.objects.create(
                registered_app=app,
                app_environment=env,
                triggered_by_user_id=actor.user_id,
                trigger_kind=input.trigger_kind,
                status=initial_status.value,
                image_tag=input.image_tag,
                image_digest=input.image_digest or "",
                approvals_required=env.required_approvals,
                approvals_received=0,
            )

            if initial_status is Deployment.Status.PENDING:
                wf_id = _deploy_workflow_id(str(app.guid), str(env.guid))
                handle = start_workflow(
                    "DeployAppWorkflow",
                    args=[
                        DeployAppInput(
                            registered_app_id=app.pk,
                            app_environment_id=env.pk,
                            image_tags={"app": input.image_tag},
                            trigger_kind=input.trigger_kind,
                            actor=actor,
                        )
                    ],
                    workflow_id=wf_id,
                )
                if handle.enqueued:
                    run = _record_workflow_run(
                        kind="DeployAppWorkflow",
                        workflow_id=handle.workflow_id,
                        run_id=handle.run_id,
                        organization_id=tenant.organization_id if tenant else None,
                        registered_app_id=app.pk,
                        app_environment_id=env.pk,
                        actor=actor,
                    )
                    deployment.workflow_run = run
                    deployment.save(update_fields=["workflow_run", "updated_at", "version"])

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.approve")
    @require_permission(Permission.APP_APPROVE_DEPLOY)
    def approve_deployment(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        deployment = (
            Deployment.objects.select_related(
                "registered_app", "app_environment", "workload"
            )
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if deployment is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")
        if deployment.status != Deployment.Status.PENDING_APPROVAL.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"deployment is in status {deployment.status}, expected pending_approval",
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()
        if actor.user_id and deployment.triggered_by_user_id == actor.user_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot approve your own deployment",
            )

        with transaction.atomic():
            deployment.approvals_received += 1
            if deployment.approvals_received >= deployment.approvals_required:
                deployment.transition_to(Deployment.Status.PENDING)
                app = deployment.registered_app
                env = deployment.app_environment
                wf_id = _deploy_workflow_id(str(app.guid), str(env.guid))
                handle = start_workflow(
                    "DeployAppWorkflow",
                    args=[
                        DeployAppInput(
                            registered_app_id=app.pk,
                            app_environment_id=env.pk,
                            image_tags={"app": deployment.image_tag},
                            trigger_kind=deployment.trigger_kind,
                            actor=actor,
                        )
                    ],
                    workflow_id=wf_id,
                )
                if handle.enqueued:
                    run = _record_workflow_run(
                        kind="DeployAppWorkflow",
                        workflow_id=handle.workflow_id,
                        run_id=handle.run_id,
                        organization_id=tenant.organization_id if tenant else None,
                        registered_app_id=app.pk,
                        app_environment_id=env.pk,
                        actor=actor,
                    )
                    deployment.workflow_run = run
                    deployment.save(update_fields=["workflow_run", "updated_at", "version"])
            else:
                deployment.save(
                    update_fields=["approvals_received", "updated_at", "version"]
                )

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.abort")
    @require_permission(Permission.APP_DEPLOY)
    def abort_deployment(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        deployment = (
            Deployment.objects.select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if deployment is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")

        in_flight = {
            Deployment.Status.PENDING_APPROVAL.value,
            Deployment.Status.PENDING.value,
            Deployment.Status.DEPLOYING.value,
            Deployment.Status.REDEPLOYING.value,
        }
        if deployment.status not in in_flight:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"deployment in status {deployment.status} is not in-flight",
            )

        if deployment.workflow_run_id:
            wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
            # Try a graceful signal first; fall back to terminate.
            if not signal_workflow(wf_id, "abort"):
                terminate_workflow(wf_id, reason="abort_deployment mutation")

        with transaction.atomic():
            deployment.transition_to(Deployment.Status.FAILED)

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.rollback")
    @require_permission(Permission.APP_ROLLBACK)
    def rollback_deployment(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        deployment = (
            Deployment.objects.select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if deployment is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")
        if deployment.status != Deployment.Status.RUNNING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"only running deployments are rollback targets (status={deployment.status})",
            )

        prior = (
            Deployment.objects.filter(
                registered_app_id=deployment.registered_app_id,
                app_environment_id=deployment.app_environment_id,
                status=Deployment.Status.SUPERSEDED.value,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if prior is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "no prior superseded deployment to roll back to",
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()

        with transaction.atomic():
            deployment.transition_to(Deployment.Status.ROLLED_BACK)
            new_deploy = Deployment.objects.create(
                registered_app_id=deployment.registered_app_id,
                app_environment_id=deployment.app_environment_id,
                workload_id=deployment.workload_id,
                triggered_by_user_id=actor.user_id,
                trigger_kind=Deployment.TriggerKind.ROLLBACK.value,
                status=Deployment.Status.PENDING.value,
                image_tag=prior.image_tag,
                image_digest=prior.image_digest,
                config_snapshot=prior.config_snapshot,
                approvals_required=0,
                approvals_received=0,
                promoted_from=prior,
            )
            handle = start_workflow(
                "RollbackDeploymentWorkflow",
                args=[
                    RollbackInput(
                        deployment_id=new_deploy.pk,
                        actor=actor,
                    )
                ],
                workflow_id=_rollback_workflow_id(str(new_deploy.guid)),
            )
            if handle.enqueued:
                run = _record_workflow_run(
                    kind="RollbackDeploymentWorkflow",
                    workflow_id=handle.workflow_id,
                    run_id=handle.run_id,
                    organization_id=tenant.organization_id if tenant else None,
                    registered_app_id=new_deploy.registered_app_id,
                    app_environment_id=new_deploy.app_environment_id,
                    actor=actor,
                )
                new_deploy.workflow_run = run
                new_deploy.save(update_fields=["workflow_run", "updated_at", "version"])

        return gql_success(deployment_to_type(new_deploy))

    @strawberry.field
    @mutation_audit(action="deployment.redeploy")
    @require_permission(Permission.APP_DEPLOY)
    def redeploy_app(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        source = (
            Deployment.objects.select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if source is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")

        actor = _actor_from_request(info)
        tenant = get_current_tenant()
        env = source.app_environment

        if env.deploys_paused:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {env.name!r} has deploys paused",
            )

        initial_status = (
            Deployment.Status.PENDING_APPROVAL
            if env.required_approvals > 0
            else Deployment.Status.PENDING
        )

        with transaction.atomic():
            new_deploy = Deployment.objects.create(
                registered_app_id=source.registered_app_id,
                app_environment_id=source.app_environment_id,
                workload_id=source.workload_id,
                triggered_by_user_id=actor.user_id,
                trigger_kind=Deployment.TriggerKind.MANUAL.value,
                status=initial_status.value,
                image_tag=source.image_tag,
                image_digest=source.image_digest,
                config_snapshot=source.config_snapshot,
                approvals_required=env.required_approvals,
                approvals_received=0,
                promoted_from=source,
            )

            if initial_status is Deployment.Status.PENDING:
                wf_id = _deploy_workflow_id(
                    str(source.registered_app.guid), str(env.guid)
                )
                handle = start_workflow(
                    "DeployAppWorkflow",
                    args=[
                        DeployAppInput(
                            registered_app_id=source.registered_app_id,
                            app_environment_id=source.app_environment_id,
                            image_tags={"app": source.image_tag},
                            trigger_kind=Deployment.TriggerKind.MANUAL.value,
                            actor=actor,
                        )
                    ],
                    workflow_id=wf_id,
                )
                if handle.enqueued:
                    run = _record_workflow_run(
                        kind="DeployAppWorkflow",
                        workflow_id=handle.workflow_id,
                        run_id=handle.run_id,
                        organization_id=tenant.organization_id if tenant else None,
                        registered_app_id=source.registered_app_id,
                        app_environment_id=source.app_environment_id,
                        actor=actor,
                    )
                    new_deploy.workflow_run = run
                    new_deploy.save(update_fields=["workflow_run", "updated_at", "version"])

        return gql_success(deployment_to_type(new_deploy))

    @strawberry.field
    @mutation_audit(action="preview.tear_down")
    @require_permission(Permission.APP_DEPLOY)
    def tear_down_preview(
        self, info: Info, input: TearDownPreviewInputGql
    ) -> MutationResultType[DeploymentType]:
        preview = (
            PreviewEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if preview is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "preview environment not found")

        actor = _actor_from_request(info)
        tenant = get_current_tenant()

        handle = start_workflow(
            "TearDownPreviewWorkflow",
            args=[
                TearDownPreviewInput(
                    preview_environment_id=preview.pk,
                    actor=actor,
                )
            ],
            workflow_id=_teardown_workflow_id(str(preview.guid)),
        )
        if handle.enqueued:
            _record_workflow_run(
                kind="TearDownPreviewWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=tenant.organization_id if tenant else None,
                registered_app_id=preview.registered_app_id,
                app_environment_id=None,
                actor=actor,
            )

        latest = (
            Deployment.objects.filter(
                registered_app_id=preview.registered_app_id,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if latest is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "no deployment exists for this app yet",
            )
        return gql_success(deployment_to_type(latest))
