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

from datetime import UTC

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.approval import mint_magic_link
from astrolift_lifecycle.models import (
    AppEnvironment,
    CustomDomain,
    Deployment,
    DeployToken,
    PreviewEnvironment,
)
from astrolift_lifecycle.schema.types import (
    AppDomainType,
    AppEnvironmentType,
    DeploymentType,
    DeployTokenType,
    app_domain_to_type,
    app_env_to_type,
    deploy_token_to_type,
    deployment_to_type,
)
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
from core.decorators import tenant_scoped
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
    # CI / VCS provenance (#166). Optional at the GraphQL boundary so
    # manual deploys from the UI work without specifying these; the
    # webhook path always populates them.
    ci_actor_kind: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    ci_run_url: str | None = None
    ci_provider: str | None = None


@strawberry.input
class DeploymentByIdInput:
    id: GUID


@strawberry.input
class ApproveByTokenInput:
    """Public mutation input — the token is the auth proof.

    No tenant context, no permission check: the SHA-256 of ``token``
    must match an active, unconsumed, unexpired
    ``Deployment.approval_token_hash``."""

    token: str


@strawberry.input
class RejectByTokenInput:
    """Public mutation input — same auth model as ``ApproveByTokenInput``.

    ``reason`` is recorded on the deployment's lifecycle event for the
    audit trail."""

    token: str
    reason: str | None = None


@strawberry.input
class TearDownPreviewInputGql:
    id: GUID


@strawberry.input
class EnvironmentByIdInput:
    id: GUID


# Custom domain CRUD (#281) ------------------------------------------


@strawberry.input
class AddAppDomainInput:
    app_slug: str
    hostname: str
    validation_method: str | None = None
    """dns_txt | http_01 | dns_01 (default: dns_txt)"""


@strawberry.input
class RemoveAppDomainInput:
    id: GUID


@strawberry.input
class RecheckDomainValidationInput:
    id: GUID


@strawberry.type
class _AppDomainRemovedPayload:
    id: GUID
    deleted: bool


# Deploy token CRUD (#281) -------------------------------------------


@strawberry.input
class CreateDeployTokenInput:
    app_slug: str
    name: str
    scopes: list[str] | None = None
    expires_at_iso: str | None = None
    """ISO-8601; if absent the token defaults to the platform's
    1-year TTL."""


@strawberry.input
class RotateDeployTokenInput:
    id: GUID


@strawberry.input
class RevokeDeployTokenInput:
    id: GUID


@strawberry.type
class DeployTokenSecretReveal:
    """Returned exactly once on creation/rotation; the plaintext
    token never lives in DB."""

    token: DeployTokenType
    plaintext_secret: str


@strawberry.type
class _DeployTokenRevokedPayload:
    id: GUID
    revoked: bool


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


def _resolve_app_env(app_slug: str, environment_name: str) -> tuple[RegisteredApp, AppEnvironment] | None:
    app = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True).first()
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


def _lookup_deployment_by_token(
    presented_plaintext: str,
) -> tuple[Deployment | None, MutationResultType[DeploymentType] | None]:
    """Find the deployment whose ``approval_token_hash`` matches the
    presented plaintext, or return the appropriate failure envelope.

    Single error message across every failure path so callers can't
    distinguish "no such token" from "expired" from "already used" via
    timing.
    """
    import hashlib

    presented = (presented_plaintext or "").strip()
    INVALID = gql_failure(
        ErrorCode.PERMISSION_DENIED.value,
        "approval token invalid",
        field="token",
    )
    if not presented:
        return None, INVALID

    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    deployment = (
        Deployment.objects.select_related("registered_app", "app_environment", "workload")
        .filter(
            approval_token_hash=digest,
            deleted_at__isnull=True,
        )
        .first()
    )
    if deployment is None:
        return None, INVALID
    if deployment.approval_token_used_at is not None:
        return None, INVALID
    if deployment.approval_token_expires_at is None or deployment.approval_token_expires_at <= timezone.now():
        return None, INVALID
    if deployment.status != Deployment.Status.PENDING_APPROVAL.value:
        return None, INVALID
    return deployment, None


def _record_approval_vote_and_maybe_start(
    deployment: Deployment,
    actor: Actor,
    organization_id: int | None,
) -> None:
    """Increment ``approvals_received`` and, if quorum is now met,
    transition the deploy to PENDING + enqueue the DeployAppWorkflow.

    Shared by :meth:`approve_deployment` (auth-required, called by an
    operator) and :meth:`approve_deployment_by_token` (public, called
    by the holder of an emailed magic link). Callers wrap this in
    ``transaction.atomic`` and persist the deployment row themselves
    when no transition fires.
    """
    deployment.approvals_received += 1
    if deployment.approvals_received < deployment.approvals_required:
        deployment.save(update_fields=["approvals_received", "updated_at", "version"])
        return

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
            organization_id=organization_id,
            registered_app_id=app.pk,
            app_environment_id=env.pk,
            actor=actor,
        )
        deployment.workflow_run = run
        deployment.save(update_fields=["workflow_run", "updated_at", "version"])


# ---------------------------------------------------------------------------
# Root mutation type
# ---------------------------------------------------------------------------


@strawberry.type
class LifecycleMutation:
    @strawberry.field
    @mutation_audit(action="deployment.start")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def start_deployment(self, info: Info, input: StartDeploymentInput) -> MutationResultType[DeploymentType]:
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

            # Mint an emailed-approval magic link when the env gates on
            # human approvals. Plaintext is returned in the published
            # lifecycle event (operators wire that to email/Slack);
            # only the hash + expiry persist on the row.
            approval_token_plaintext: str | None = None
            approval_token_hash = ""
            approval_token_expires_at = None
            if initial_status is Deployment.Status.PENDING_APPROVAL:
                issued = mint_magic_link(now=timezone.now())
                approval_token_plaintext = issued.plaintext_token
                approval_token_hash = issued.token_hash
                approval_token_expires_at = issued.expires_at
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
                approval_token_hash=approval_token_hash,
                approval_token_expires_at=approval_token_expires_at,
                ci_actor_kind=(input.ci_actor_kind or "").strip(),
                commit_sha=(input.commit_sha or "").strip(),
                branch=(input.branch or "").strip(),
                ci_run_url=(input.ci_run_url or "").strip(),
                ci_provider=(input.ci_provider or "").strip(),
            )

            if approval_token_plaintext:
                # Surface the plaintext exactly once, on the
                # deploy.approval_token.minted lifecycle event.
                # Notification/webhook fan-out picks this up.
                try:
                    from core.pubsub import publish_sync

                    publish_sync(
                        f"deployment.approval_token.minted.{app.organization_id}",
                        {
                            "deployment_id": str(deployment.guid),
                            "registered_app_slug": app.slug,
                            "environment_name": env.name,
                            "approval_token": approval_token_plaintext,
                            "expires_at": (
                                approval_token_expires_at.isoformat() if approval_token_expires_at else ""
                            ),
                        },
                    )
                except Exception:
                    import logging

                    logging.getLogger(__name__).warning("approval token publish failed", exc_info=True)

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
    @tenant_scoped()
    def approve_deployment(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        deployment = (
            Deployment.objects.select_related("registered_app", "app_environment", "workload")
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
            _record_approval_vote_and_maybe_start(
                deployment,
                actor,
                organization_id=tenant.organization_id if tenant else None,
            )

        return gql_success(deployment_to_type(deployment))

    # ---- Public token-based approve / reject (#125, spec 06 §4.6) ----
    #
    # These two resolvers are **public** by design: no auth, no
    # @tenant_scoped, no @require_permission. The token itself is the
    # auth proof — operators wire ``deployment.approval_token.minted``
    # events to email/Slack, the recipient clicks a link with the
    # token in the URL, and the UI calls these mutations. The hash-
    # at-rest pattern means a leaked DB never leaks usable tokens.
    #
    # Listed in the tenancy guardrail's EXEMPT set with this rationale.

    @strawberry.field
    @mutation_audit(action="deployment.approve_by_token")
    def approve_deployment_by_token(
        self, info: Info, input: ApproveByTokenInput
    ) -> MutationResultType[DeploymentType]:
        deployment, err = _lookup_deployment_by_token(input.token)
        if err is not None:
            return err

        # Self-approval guard doesn't apply here: the token issuer
        # would have to leak it to the deployer for self-approve, and
        # the *issuance* is what gates approval policy. Token mint is
        # done at start_deployment time inside the platform.
        actor = Actor(kind="token", display="approval_token")

        with transaction.atomic():
            deployment.approval_token_used_at = timezone.now()
            deployment.save(
                update_fields=[
                    "approval_token_used_at",
                    "updated_at",
                    "version",
                ]
            )
            _record_approval_vote_and_maybe_start(
                deployment,
                actor,
                organization_id=deployment.registered_app.organization_id,
            )

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.reject_by_token")
    def reject_deployment_by_token(
        self, info: Info, input: RejectByTokenInput
    ) -> MutationResultType[DeploymentType]:
        deployment, err = _lookup_deployment_by_token(input.token)
        if err is not None:
            return err

        reason = (input.reason or "rejected via approval token").strip()

        with transaction.atomic():
            deployment.approval_token_used_at = timezone.now()
            deployment.save(
                update_fields=[
                    "approval_token_used_at",
                    "updated_at",
                    "version",
                ]
            )
            # Terminal: reject moves pending_approval → failed and
            # stops any workflow that might already be running (none
            # should be at this state, but defense in depth).
            if deployment.workflow_run_id:
                wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
                if not signal_workflow(wf_id, "abort"):
                    terminate_workflow(wf_id, reason=f"reject_by_token: {reason}")
            deployment.transition_to(Deployment.Status.FAILED)

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.abort")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def abort_deployment(self, info: Info, input: DeploymentByIdInput) -> MutationResultType[DeploymentType]:
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
    @tenant_scoped()
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
    @tenant_scoped()
    def redeploy_app(self, info: Info, input: DeploymentByIdInput) -> MutationResultType[DeploymentType]:
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
            Deployment.Status.PENDING_APPROVAL if env.required_approvals > 0 else Deployment.Status.PENDING
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
                wf_id = _deploy_workflow_id(str(source.registered_app.guid), str(env.guid))
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
    @mutation_audit(action="environment.pause")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def pause_environment(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Pause reconciliation for an environment.

        While paused, ``startDeployment`` rejects with PRECONDITION
        (the same gate the deploy mutation already checks against
        ``env.deploys_paused``). Operators use this when an env is
        misconfigured or under maintenance and we don't want CI or
        push triggers to land deploys mid-investigation.
        """
        env = (
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if not env.deploys_paused:
            env.deploys_paused = True
            env.save(update_fields=["deploys_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.resume")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def resume_environment(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Lift the pause flag — does NOT replay queued deploys; the
        next CI/push trigger or manual ``startDeployment`` proceeds
        as usual."""
        env = (
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if env.deploys_paused:
            env.deploys_paused = False
            env.save(update_fields=["deploys_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="preview.tear_down")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
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

    # ---- Custom domain (#281) -----------------------------------

    @strawberry.field
    @mutation_audit(action="app.domain.add")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def add_app_domain(
        self,
        info: Info,
        input: AddAppDomainInput,
    ) -> MutationResultType[AppDomainType]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip().lower()
        if not host or "." not in host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be a fully-qualified domain",
                field="hostname",
            )
        method = (input.validation_method or "dns_txt").lower()
        valid_methods = {"dns_txt", "http_01", "dns_01"}
        if method not in valid_methods:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"validation_method must be one of {sorted(valid_methods)}",
                field="validationMethod",
            )
        # Idempotent re-add: an active row with the same hostname is
        # treated as success rather than a 409.
        existing = CustomDomain.objects.filter(hostname=host, deleted_at__isnull=True).first()
        if existing is not None:
            if existing.registered_app_id != app.id:
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"domain {host!r} already bound to another app",
                    field="hostname",
                )
            return gql_success(app_domain_to_type(existing))
        domain = CustomDomain.objects.create(
            registered_app=app,
            hostname=host,
            validation_method=method,
        )
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.remove")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def remove_app_domain(
        self,
        info: Info,
        input: RemoveAppDomainInput,
    ) -> MutationResultType[_AppDomainRemovedPayload]:
        domain = CustomDomain.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        domain.soft_delete()
        return gql_success(
            _AppDomainRemovedPayload(
                id=input.id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.domain.recheck")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def recheck_domain_validation(
        self,
        info: Info,
        input: RecheckDomainValidationInput,
    ) -> MutationResultType[AppDomainType]:
        """Trigger a re-check of cert / DNS validation. The DNS
        polling loop runs out-of-band; this mutation just bumps
        ``updated_at`` so the UI can show 'last_checked_at' moved
        forward and pick up state changes from the polling loop."""
        domain = CustomDomain.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        domain.save(update_fields=["updated_at", "version"])
        return gql_success(app_domain_to_type(domain))

    # ---- Deploy tokens (#281) ------------------------------------

    @strawberry.field
    @mutation_audit(action="app.deploy_token.create")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_deploy_token(
        self,
        info: Info,
        input: CreateDeployTokenInput,
    ) -> MutationResultType[DeployTokenSecretReveal]:
        import hashlib
        import secrets as secrets_lib
        from datetime import datetime, timedelta

        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        plaintext = "alfdt_" + secrets_lib.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        expires_at = None
        if input.expires_at_iso:
            try:
                expires_at = datetime.fromisoformat(
                    input.expires_at_iso,
                )
            except ValueError:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "expires_at_iso must be ISO-8601",
                    field="expiresAtIso",
                )
        else:
            expires_at = datetime.now(tz=UTC) + timedelta(days=365)
        token = DeployToken.objects.create(
            registered_app=app,
            name=input.name,
            token_hash=digest,
            token_last_4=plaintext[-4:],
            scopes=list(input.scopes or ["app.deploy"]),
            expires_at=expires_at,
        )
        return gql_success(
            DeployTokenSecretReveal(
                token=deploy_token_to_type(token),
                plaintext_secret=plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.deploy_token.rotate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def rotate_deploy_token(
        self,
        info: Info,
        input: RotateDeployTokenInput,
    ) -> MutationResultType[DeployTokenSecretReveal]:
        import hashlib
        import secrets as secrets_lib
        from datetime import datetime, timedelta

        token = DeployToken.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if token is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "deploy token not found",
            )
        if token.is_revoked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot rotate a revoked token; create a new one",
            )
        plaintext = "alfdt_" + secrets_lib.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        # Park the previous hash for a 24h grace window so CI
        # runners holding the old token keep working until they're
        # updated (matches the model's documented rotation flow).
        token.previous_token_hash = token.token_hash
        token.previous_token_expires_at = datetime.now(tz=UTC) + timedelta(hours=24)
        token.token_hash = digest
        token.token_last_4 = plaintext[-4:]
        token.last_rotated_at = datetime.now(tz=UTC)
        token.save(
            update_fields=[
                "previous_token_hash",
                "previous_token_expires_at",
                "token_hash",
                "token_last_4",
                "last_rotated_at",
                "updated_at",
                "version",
            ]
        )
        return gql_success(
            DeployTokenSecretReveal(
                token=deploy_token_to_type(token),
                plaintext_secret=plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.deploy_token.revoke")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def revoke_deploy_token(
        self,
        info: Info,
        input: RevokeDeployTokenInput,
    ) -> MutationResultType[_DeployTokenRevokedPayload]:
        token = DeployToken.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if token is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "deploy token not found",
            )
        if not token.is_revoked:
            token.is_revoked = True
            token.save(
                update_fields=[
                    "is_revoked",
                    "updated_at",
                    "version",
                ]
            )
        return gql_success(
            _DeployTokenRevokedPayload(
                id=input.id,
                revoked=True,
            )
        )
