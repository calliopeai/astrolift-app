"""DeploymentMutations — split from the monolithic mutations module."""

from __future__ import annotations

from typing import cast

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import (
    deployment_operation,
    named_environment,
)
from astrolift_lifecycle.action_preconditions import locked_deployment, recheck_action
from astrolift_lifecycle.approval import mint_magic_link
from astrolift_lifecycle.deployment_identity_origin import (
    DeploymentOriginError,
    capture_deployment_origin,
    persist_deployment_origin,
)
from astrolift_lifecycle.models import (
    AppEnvironment,
    Deployment,
    DeploymentLog,
)
from astrolift_lifecycle.promotion import (
    DeploymentRef,
    PromotionError,
    PromotionTarget,
    plan_promotion,
)
from astrolift_lifecycle.schema.mutations.helpers import (
    _BULK_APPROVE_REJECT_CAP,
    _DEPLOY_PIPELINE_DISABLED_MSG,
    _VALID_TRIGGER_KINDS,
    _WEBHOOK_TRIGGER_KINDS,
    _abort_extras,
    _actor_from_request,
    _deploy_pipeline_disabled,
    _deploy_workflow_id,
    _deployment_target_from_input,
    _is_eligible_approver,
    _lookup_deployment_by_token,
    _manifest_job_agents_only,
    _process_bulk_approve_one,
    _process_bulk_reject_one,
    _record_approval_vote_and_maybe_start,
    _required_approvals_for,
    _resolve_app_env,
    _rollback_workflow_id,
    _self_approve_allowed,
    _start_deploy_workflow_on_commit,
    _start_extras,
)
from astrolift_lifecycle.schema.mutations.types import (
    AbortDeploymentInput,
    ApproveByTokenInput,
    BulkApproveDeploymentsInput,
    BulkDeploymentResultData,
    BulkDeploymentResultItem,
    BulkRejectDeploymentsInput,
    DeploymentByIdInput,
    PromoteDeploymentInput,
    RejectByTokenInput,
    StartDeploymentInput,
)
from astrolift_lifecycle.schema.types import (
    DeploymentType,
    deployment_to_type,
)
from astrolift_lifecycle.scopes import deployment_app_scope
from astrolift_lifecycle.visibility import live_app_rows, live_lifecycle_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_services.capability_projection import check_promotion
from astrolift_workflows.client import (
    signal_workflow,
    terminate_workflow,
)
from astrolift_workflows.inputs import (
    Actor,
    DeployAppInput,
    RollbackInput,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class DeploymentMutations:
    @strawberry.field
    @mutation_audit(
        action="deployment.start",
        extras=lambda result: _start_extras(result),
    )
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_DEPLOY),
        operation=named_environment(),
    )
    @tenant_scoped()
    def start_deployment(self, info: Info, input: StartDeploymentInput) -> MutationResultType[DeploymentType]:
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
        if input.trigger_kind not in _VALID_TRIGGER_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown trigger_kind: {input.trigger_kind}",
                field="triggerKind",
            )
        # Resolve the caller's tenant up front — this mutation creates a
        # Deployment and fires a workflow, so the org gate must precede any
        # side effect. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        resolved = _resolve_app_env(input.app_slug, input.environment_name, org_id=org_id)
        if resolved is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} environment {input.environment_name!r} not found",
            )
        app, env = resolved
        try:
            origin = capture_deployment_origin(getattr(info.context, "request", None), app, env)
        except DeploymentOriginError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

        # #1093: an app whose manifest yields zero deployable resources
        # (all workloads are Job-family agents) has nothing the deploy
        # pipeline can roll out — refuse here with a pointer at the right
        # surface instead of letting the workflow async-fail in pre-flight.
        # Service-family agents deploy normally (#1012/#1027).
        if _manifest_job_agents_only(app):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} declares only Job-family (run_family=task) agent "
                "workloads, which render no deployable resources — they dispatch via "
                "the agent spine (registerAgentRepo); nothing to deploy",
                field="appSlug",
            )

        if env.deploys_paused:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {env.name!r} has deploys paused",
                field="environmentName",
            )

        # App-global webhook-deploy pause (#399). Only blocks
        # webhook-shaped triggers (push / ci / scheduled). Manual
        # deploys from the UI / CLI continue to flow so the operator
        # can still ship a fix while the storm is stopped.
        if app.webhook_deploys_paused and input.trigger_kind in _WEBHOOK_TRIGGER_KINDS:
            reason = (app.webhook_deploys_pause_reason or "").strip()
            suffix = f" Reason: {reason}" if reason else ""
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has webhook-fired deploys paused.{suffix}",
                field="triggerKind",
            )

        from astrolift_lifecycle.source_revision import resolve_deployment_source
        from astrolift_scm.providers import ProviderError

        try:
            source = resolve_deployment_source(
                app,
                image_tag=input.image_tag,
                commit_sha=input.commit_sha,
                branch=input.branch,
                source_ref=input.source_ref,
            )
        except ProviderError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value if exc.code == "VALIDATION" else ErrorCode.PRECONDITION.value,
                exc.message,
                field="imageTag"
                if app.build_mode == RegisteredApp.BuildMode.CI_PUSHED and not input.image_tag
                else "sourceRef",
            )

        actor = _actor_from_request(info)

        with transaction.atomic():
            approvals_required = _required_approvals_for(app, env)
            initial_status = (
                Deployment.Status.PENDING_APPROVAL if approvals_required > 0 else Deployment.Status.PENDING
            )
            # #1536: a new deploy supersedes any in-flight one for this
            # (app, env) — abort the live workflow, fail out dead-workflow
            # rows. Before the new row exists so it can't self-match.
            if initial_status is Deployment.Status.PENDING:
                from astrolift_lifecycle.supersede import supersede_in_flight_deploys

                supersede_in_flight_deploys(app, env)

            # Mint an emailed-approval magic link when the deploy gates
            # on human approvals. Plaintext is returned in the
            # published lifecycle event (operators wire that to
            # email/Slack); only the hash + expiry persist on the row.
            approval_token_plaintext: str | None = None
            approval_token_hash = ""
            approval_token_expires_at = None
            if initial_status is Deployment.Status.PENDING_APPROVAL:
                issued = mint_magic_link(now=timezone.now())
                approval_token_plaintext = issued.plaintext_token
                approval_token_hash = issued.token_hash
                approval_token_expires_at = issued.expires_at
            # #736 — clamp strategy to known choices; treat anything
            # else (including "") as "rolling" so the FE always renders
            # a meaningful pill.  The workflow can override later if it
            # decides on canary / blue-green.
            strategy_in = (input.strategy or "rolling").strip().lower()
            if strategy_in not in {s.value for s in Deployment.Strategy}:
                strategy_in = "rolling"

            deployment = Deployment.objects.create(
                registered_app=app,
                app_environment=env,
                triggered_by_user_id=actor.user_id,
                trigger_kind=input.trigger_kind,
                strategy=strategy_in,
                status=initial_status.value,
                image_tag=source.image_tag,
                image_digest=input.image_digest or "",
                approvals_required=approvals_required,
                approvals_received=0,
                approval_token_hash=approval_token_hash,
                approval_token_expires_at=approval_token_expires_at,
                ci_actor_kind=(input.ci_actor_kind or "").strip(),
                commit_sha=source.commit_sha,
                branch=source.branch,
                ci_run_url=(input.ci_run_url or "").strip(),
                ci_provider=(input.ci_provider or "").strip(),
                commit_message=(input.commit_message or "").strip(),
                commit_author=(input.commit_author or "").strip(),
                pr_number=int(input.pr_number or 0),
                commit_author_avatar_url=(input.commit_author_avatar_url or "").strip(),
            )

            persist_deployment_origin(deployment, origin)

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
                _start_deploy_workflow_on_commit(
                    deployment=deployment,
                    workflow_kind="DeployAppWorkflow",
                    workflow_id=wf_id,
                    args=[
                        DeployAppInput(
                            registered_app_id=app.pk,
                            app_environment_id=env.pk,
                            deployment_id=deployment.pk,
                            image_tags={"app": source.image_tag} if source.image_tag else {},
                            trigger_kind=input.trigger_kind,
                            actor=actor,
                            commit_sha=source.commit_sha,
                        )
                    ],
                    organization_id=tenant.organization_id if tenant else None,
                    registered_app_id=app.pk,
                    app_environment_id=env.pk,
                    actor=actor,
                )

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(
        action="deployment.approve",
        target=_deployment_target_from_input,
    )
    @require_permission(
        Permission.APP_APPROVE_DEPLOY,
        scope=deployment_app_scope("input.id", permission=Permission.APP_APPROVE_DEPLOY),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def approve_deployment(
        self, info: Info, input: DeploymentByIdInput
    ) -> MutationResultType[DeploymentType]:
        # Org-scope the by-guid lookup to the caller's tenant before the
        # approval side effect (which starts the deploy workflow). Deployment
        # reaches the org via registered_app; @tenant_scoped only asserts a
        # tenant. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        deployment = (
            live_lifecycle_rows(Deployment.objects.all())
            .select_related("registered_app", "app_environment", "workload")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
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
        if actor.user_id and deployment.triggered_by_user_id == actor.user_id and not _self_approve_allowed():
            # Self-approval blocked (#419). The FE primarily hides the
            # approve CTA based on the Deployment.triggered_by_me flag
            # from the query; this resolver guard is the backstop so
            # an out-of-date page or scripted client can't slip through.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot approve your own deployment — another approver required",
            )

        if actor.user_id and not _is_eligible_approver(
            deployment.registered_app,
            user_id=actor.user_id,
        ):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's approver set",
            )

        with transaction.atomic():
            _record_approval_vote_and_maybe_start(
                deployment,
                actor,
                organization_id=tenant.organization_id if tenant else None,
            )

        return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

    @strawberry.field
    @mutation_audit(
        action="deployment.reject",
        target=_deployment_target_from_input,
        extras=lambda result: _abort_extras(result),
    )
    @require_permission(
        Permission.APP_APPROVE_DEPLOY,
        scope=deployment_app_scope("input.id", permission=Permission.APP_APPROVE_DEPLOY),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def reject_deployment(
        self, info: Info, input: AbortDeploymentInput
    ) -> MutationResultType[DeploymentType]:
        """Reject a pending_approval deploy from in-band.

        Mirrors :meth:`reject_deployment_by_token` but requires an
        authenticated approver. ANY rejection short-circuits to
        FAILED — one nay kills the deploy, matching the quorum
        policy in :mod:`astrolift_lifecycle.approval`.

        ``reason`` is required + non-empty (#419) so every rejection
        leaves an auditable explanation. The reason is persisted on
        the deployment row (``aborted_reason``) so the history sidebar
        can render it without an extra join.
        """
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )
        # Org-scope the by-guid lookup to the caller's tenant before the
        # reject side effect (which terminates the deploy workflow).
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        deployment = (
            live_lifecycle_rows(Deployment.objects.all())
            .select_related("registered_app", "app_environment", "workload")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
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
        if actor.user_id and deployment.triggered_by_user_id == actor.user_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot reject your own deployment",
            )
        if actor.user_id and not _is_eligible_approver(
            deployment.registered_app,
            user_id=actor.user_id,
        ):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's approver set",
            )

        with transaction.atomic():
            deployment.aborted_reason = reason
            deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
            if deployment.workflow_run_id:
                wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
                if not signal_workflow(wf_id, "abort"):
                    terminate_workflow(wf_id, reason=f"reject_deployment: {reason}")
            deployment.transition_to(Deployment.Status.FAILED)

        return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

    # ---- Bulk approve / reject (#420) ---------------------------------
    #
    # Operators clearing a backlog of pending deploys want a single
    # action that processes every selection without the per-row round-
    # trip. Each id is processed independently: a per-id permission /
    # self-trigger / eligibility failure surfaces in the result item
    # rather than aborting the batch. Per-id audit rows are emitted by
    # the underlying single-id call paths so the timeline reads the
    # same as if the operator had clicked through one at a time.

    @strawberry.field
    @require_permission(Permission.APP_APPROVE_DEPLOY, any_scope=True)
    @tenant_scoped()
    def bulk_approve_deployments(
        self, info: Info, input: BulkApproveDeploymentsInput
    ) -> MutationResultType[BulkDeploymentResultData]:
        ids = list(input.deployment_ids or [])
        if not ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "deploymentIds is required",
                field="deploymentIds",
            )
        if len(ids) > _BULK_APPROVE_REJECT_CAP:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"too many deployments (cap is {_BULK_APPROVE_REJECT_CAP})",
                field="deploymentIds",
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()
        organization_id = tenant.organization_id if tenant else None

        results: list[BulkDeploymentResultItem] = []
        succeeded = 0
        failed = 0
        for deployment_id in ids:
            item = _process_bulk_approve_one(
                deployment_id=str(deployment_id),
                actor=actor,
                viewer_user_id=actor.user_id,
                organization_id=organization_id,
            )
            results.append(item)
            if item.ok:
                succeeded += 1
            else:
                failed += 1
        return gql_success(
            BulkDeploymentResultData(
                results=results,
                succeeded_count=succeeded,
                failed_count=failed,
            )
        )

    @strawberry.field
    @require_permission(Permission.APP_APPROVE_DEPLOY, any_scope=True)
    @tenant_scoped()
    def bulk_reject_deployments(
        self, info: Info, input: BulkRejectDeploymentsInput
    ) -> MutationResultType[BulkDeploymentResultData]:
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )
        ids = list(input.deployment_ids or [])
        if not ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "deploymentIds is required",
                field="deploymentIds",
            )
        if len(ids) > _BULK_APPROVE_REJECT_CAP:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"too many deployments (cap is {_BULK_APPROVE_REJECT_CAP})",
                field="deploymentIds",
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()
        organization_id = tenant.organization_id if tenant else None

        results: list[BulkDeploymentResultItem] = []
        succeeded = 0
        failed = 0
        for deployment_id in ids:
            item = _process_bulk_reject_one(
                deployment_id=str(deployment_id),
                reason=reason,
                actor=actor,
                viewer_user_id=actor.user_id,
                organization_id=organization_id,
            )
            results.append(item)
            if item.ok:
                succeeded += 1
            else:
                failed += 1
        return gql_success(
            BulkDeploymentResultData(
                results=results,
                succeeded_count=succeeded,
                failed_count=failed,
            )
        )

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
    @mutation_audit(
        action="deployment.abort",
        target=_deployment_target_from_input,
        extras=lambda result: _abort_extras(result),
    )
    @require_permission(
        Permission.APP_DEPLOY,
        scope=deployment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def abort_deployment(self, info: Info, input: AbortDeploymentInput) -> MutationResultType[DeploymentType]:
        """Abort an in-flight deploy.

        ``reason`` is required + non-empty (#419) so every abort leaves
        an auditable explanation — matches the rejection-reason flow.
        Persisted on the deployment row so the history sidebar can
        render it without an extra audit-log join.
        """
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )

        # Org-scope the by-guid lookup to the caller's tenant before the
        # abort side effect (which terminates the deploy workflow).
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        deployment = (
            live_lifecycle_rows(Deployment.objects.all())
            .select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if deployment is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")

        in_flight = {
            Deployment.Status.PENDING_APPROVAL.value,
            Deployment.Status.PENDING.value,
            Deployment.Status.DEPLOYING.value,
            Deployment.Status.REDEPLOYING.value,
            # A failed deploy is abortable too — there's no workflow left
            # to stop, the operator is just dismissing the record so it
            # drops off the lists. Handled below by a soft-delete rather
            # than a state-machine transition (FAILED is terminal in
            # _TRANSITIONS, so transition_to would reject it).
            Deployment.Status.FAILED.value,
        }
        if deployment.status not in in_flight:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"deployment in status {deployment.status} is not in-flight",
            )

        actor = _actor_from_request(info)

        if deployment.status == Deployment.Status.FAILED.value:
            # Nothing is running, so skip the Temporal cancellation. Mark
            # the reason, then soft-delete to dismiss the record (FAILED
            # is terminal — there's no legal forward transition).
            with transaction.atomic():
                deployment.aborted_reason = reason
                deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
                deployment.soft_delete()
            return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

        if deployment.workflow_run_id:
            wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
            # Try a graceful signal first; fall back to terminate.
            if not signal_workflow(wf_id, "abort"):
                terminate_workflow(wf_id, reason=f"abort_deployment: {reason}")

        with transaction.atomic():
            deployment.aborted_reason = reason
            deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
            deployment.transition_to(Deployment.Status.FAILED)

        return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

    @strawberry.field
    @mutation_audit(
        action="deployment.delete",
        target=_deployment_target_from_input,
    )
    @require_permission(
        Permission.APP_DEPLOY,
        scope=deployment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def delete_deployment(self, info: Info, input: DeploymentByIdInput) -> MutationResultType[DeploymentType]:
        """Dismiss / delete a deployment the operator is done with.

        Two shapes, both gated on ``app.deploy`` (same as abort):

        * Terminal rows (``failed`` / ``superseded`` / ``rolled_back``)
          are soft-deleted so they drop off every list. There is nothing
          running to tear down.
        * A ``running`` row is *superseded* (a legal state-machine
          transition) and a log note is written. We do NOT tear down the
          k8s resources here — that's a separate teardown workflow; this
          mutation only retires the record from the active rollout slot.
        """
        # Org-scope the by-guid lookup to the caller's tenant before the
        # supersede / soft-delete side effect. Fails closed (NOT_FOUND) when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        deployment = (
            live_lifecycle_rows(Deployment.objects.all())
            .select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id)
            .first()
        )
        if deployment is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")

        terminal = {
            Deployment.Status.FAILED.value,
            Deployment.Status.SUPERSEDED.value,
            Deployment.Status.ROLLED_BACK.value,
        }
        deletable = terminal | {Deployment.Status.RUNNING.value}
        if deployment.status not in deletable:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"deployment in status {deployment.status} cannot be deleted "
                "(abort it first if it is still in flight)",
            )

        actor = _actor_from_request(info)

        if deployment.status == Deployment.Status.RUNNING.value:
            # Retire the active rollout: supersede + leave a paper trail.
            # No k8s teardown here — that's a dedicated workflow.
            with transaction.atomic():
                deployment.transition_to(Deployment.Status.SUPERSEDED)
                DeploymentLog.objects.create(
                    deployment=deployment,
                    status=Deployment.Status.SUPERSEDED.value,
                    message="superseded via delete_deployment",
                    by_user_id=actor.user_id,
                )
            return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

        # Terminal row — soft-delete so it disappears from the lists.
        deployment.soft_delete()
        return gql_success(deployment_to_type(deployment, viewer_user_id=actor.user_id))

    @strawberry.field
    @mutation_audit(action="deployment.rollback")
    @require_permission(
        Permission.APP_ROLLBACK,
        scope=deployment_app_scope("input.id", permission=Permission.APP_ROLLBACK),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def rollback_deployment(
        self, info: Info, input: DeploymentByIdInput, if_match_version: int | None = None
    ) -> MutationResultType[DeploymentType]:
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
        # Org-scope the by-guid lookup to the caller's tenant before the
        # rollback side effect (which creates a new deploy + fires a
        # workflow). Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        with locked_deployment(str(input.id)) as deployment:
            if deployment is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")
            mismatch = check_version_match(deployment, if_match_version=if_match_version, kind="Deployment")
            if mismatch is not None:
                return mismatch
            recheck_action(Permission.APP_ROLLBACK, deployment, deployment.app_environment, deployment=True)
            from astrolift_lifecycle.deployment_identity_origin import native_origin_required

            if native_origin_required(deployment.registered_app, deployment.app_environment):
                return gql_failure(ErrorCode.PRECONDITION.value, "NATIVE_HUMAN_ORIGIN_REQUIRED")

            if deployment.status != Deployment.Status.RUNNING.value:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    f"only running deployments are rollback targets (status={deployment.status})",
                )

            current = (
                live_lifecycle_rows(Deployment.objects.all())
                .filter(
                    app_environment_id=deployment.app_environment_id, status=str(Deployment.Status.RUNNING)
                )
                .order_by("-created_at", "-guid")
                .values_list("pk", flat=True)
                .first()
            )
            if current != deployment.pk:
                return cast(
                    MutationResultType[DeploymentType],
                    gql_failure(
                        ErrorCode.PRECONDITION.value,
                        "only the current running deployment is a rollback target",
                    ),
                )

            prior = (
                live_lifecycle_rows(Deployment.objects.all())
                .select_for_update(of=("self",))
                .filter(
                    registered_app_id=deployment.registered_app_id,
                    app_environment_id=deployment.app_environment_id,
                    status=Deployment.Status.SUPERSEDED.value,
                    created_at__lt=deployment.created_at,
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

            with transaction.atomic():
                deployment.transition_to(Deployment.Status.ROLLED_BACK)
                new_deploy = Deployment.objects.create(
                    registered_app_id=deployment.registered_app_id,
                    app_environment_id=deployment.app_environment_id,
                    workload_id=deployment.workload_id,
                    triggered_by_user_id=actor.user_id,
                    trigger_kind=Deployment.TriggerKind.ROLLBACK.value,
                    # #736 — rollback inherits the prior known-good
                    # deploy's strategy so the operator gets the same kind
                    # of rollout they last had working.
                    strategy=(prior.strategy or "rolling"),
                    status=Deployment.Status.PENDING.value,
                    image_tag=prior.image_tag,
                    image_digest=prior.image_digest,
                    config_snapshot=prior.config_snapshot,
                    approvals_required=0,
                    approvals_received=0,
                    promoted_from=prior,
                )
                _start_deploy_workflow_on_commit(
                    deployment=new_deploy,
                    workflow_kind="RollbackDeploymentWorkflow",
                    workflow_id=_rollback_workflow_id(str(new_deploy.guid)),
                    args=[
                        RollbackInput(
                            deployment_id=new_deploy.pk,
                            actor=actor,
                        )
                    ],
                    organization_id=tenant.organization_id if tenant else None,
                    registered_app_id=new_deploy.registered_app_id,
                    app_environment_id=new_deploy.app_environment_id,
                    actor=actor,
                )

            return gql_success(deployment_to_type(new_deploy))

    @strawberry.field
    @mutation_audit(action="deployment.redeploy")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=deployment_app_scope("input.id", permission=Permission.APP_DEPLOY),
        operation=deployment_operation("input.id"),
    )
    @tenant_scoped()
    def redeploy_app(
        self, info: Info, input: DeploymentByIdInput, if_match_version: int | None = None
    ) -> MutationResultType[DeploymentType]:
        # Org-scope the by-guid lookup to the caller's tenant before the
        # redeploy side effect (creates a new deploy + fires a workflow).
        # Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        with locked_deployment(str(input.id)) as source:
            if source is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "deployment not found")
            mismatch = check_version_match(source, if_match_version=if_match_version, kind="Deployment")
            if mismatch is not None:
                return mismatch
            recheck_action(Permission.APP_DEPLOY, source, source.app_environment, deployment=True)

            actor = _actor_from_request(info)
            env = source.app_environment
            try:
                origin = capture_deployment_origin(
                    getattr(info.context, "request", None), source.registered_app, env
                )
            except DeploymentOriginError as exc:
                return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

            if env.deploys_paused:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    f"environment {env.name!r} has deploys paused",
                )

            approvals_required = _required_approvals_for(source.registered_app, env)
            initial_status = (
                Deployment.Status.PENDING_APPROVAL if approvals_required > 0 else Deployment.Status.PENDING
            )

            with transaction.atomic():
                new_deploy = Deployment.objects.create(
                    registered_app_id=source.registered_app_id,
                    app_environment_id=source.app_environment_id,
                    workload_id=source.workload_id,
                    triggered_by_user_id=actor.user_id,
                    trigger_kind=Deployment.TriggerKind.MANUAL.value,
                    # #736 — redeploy keeps the source deploy's strategy.
                    strategy=(source.strategy or "rolling"),
                    status=initial_status.value,
                    image_tag=source.image_tag,
                    image_digest=source.image_digest,
                    config_snapshot=source.config_snapshot,
                    approvals_required=approvals_required,
                    approvals_received=0,
                    promoted_from=source,
                )

                persist_deployment_origin(new_deploy, origin)

                if initial_status is Deployment.Status.PENDING:
                    wf_id = _deploy_workflow_id(str(source.registered_app.guid), str(env.guid))
                    _start_deploy_workflow_on_commit(
                        deployment=new_deploy,
                        workflow_kind="DeployAppWorkflow",
                        workflow_id=wf_id,
                        args=[
                            DeployAppInput(
                                registered_app_id=source.registered_app_id,
                                app_environment_id=source.app_environment_id,
                                deployment_id=new_deploy.pk,
                                image_tags={"app": source.image_tag},
                                trigger_kind=Deployment.TriggerKind.MANUAL.value,
                                actor=actor,
                            )
                        ],
                        organization_id=tenant.organization_id if tenant else None,
                        registered_app_id=source.registered_app_id,
                        app_environment_id=source.app_environment_id,
                        actor=actor,
                    )

            return gql_success(deployment_to_type(new_deploy))

    @strawberry.field
    @mutation_audit(action="deployment.promote")
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_DEPLOY),
        operation=named_environment(environment_field="input.target_environment_name"),
    )
    @tenant_scoped()
    def promote_deployment(
        self, info: Info, input: PromoteDeploymentInput
    ) -> MutationResultType[DeploymentType]:
        """Promote the source env's current running deployment — its exact
        image + config — into the target env (#1041, astrolift-cli#40).

        Reuses the promotion policy in :mod:`astrolift_lifecycle.promotion`
        (``plan_promotion`` / ``validate_promotion``) to gate the move and
        stamps ``promoted_from`` for lineage (#63). The new row runs the
        standard ``DeployAppWorkflow`` apply path — identical to the tail of
        ``PromoteDeploymentWorkflow`` — honouring the target env's approval
        gate the same way ``startDeployment`` / ``redeployApp`` do.
        """
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)

        # Deny-by-default org scoping: resolve the app + envs against the
        # caller's org so a cross-org slug can never be promoted (#1042).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"app {input.app_slug!r} not found")

        app = (
            live_app_rows(RegisteredApp.objects.all())
            .filter(
                slug=input.app_slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("approver_team")
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"app {input.app_slug!r} not found")

        def _env(name: str) -> AppEnvironment | None:
            return (
                live_lifecycle_rows(AppEnvironment.objects.all())
                .filter(
                    registered_app=app,
                    name=name,
                    deleted_at__isnull=True,
                )
                .select_related(
                    "registered_app",
                    "tenant_cluster__provider_plugin",
                    "managed_domain",
                )
                .first()
            )

        source_env = _env(input.source_environment_name)
        if source_env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"source environment {input.source_environment_name!r} not found",
                field="sourceEnvironmentName",
            )
        target_env = _env(input.target_environment_name)
        if target_env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"target environment {input.target_environment_name!r} not found",
                field="targetEnvironmentName",
            )

        try:
            origin = capture_deployment_origin(getattr(info.context, "request", None), app, target_env)
        except DeploymentOriginError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

        if target_env.deploys_paused:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {target_env.name!r} has deploys paused",
                field="targetEnvironmentName",
            )

        source = (
            live_lifecycle_rows(Deployment.objects.all())
            .filter(
                registered_app=app,
                app_environment=source_env,
                status=Deployment.Status.RUNNING.value,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if source is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"no running deployment in environment {source_env.name!r} to promote",
                field="sourceEnvironmentName",
            )

        # A promotion moves an image between environments that may sit on
        # different clusters, and the source's managed services resolved
        # against the *source* cluster's plugin catalogue. Verify the
        # target can expose every one of those variants before we accept
        # the promotion: without this the operator gets a green mutation
        # and an audit entry, and the mismatch only surfaces mid-apply in
        # DeployAppWorkflow (#59).
        capability = check_promotion(source_env=source_env, target_env=target_env)
        if not capability.ok:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "; ".join(issue.detail for issue in capability.issues),
                field="targetEnvironmentName",
            )

        approvals_required = _required_approvals_for(app, target_env)
        try:
            plan = plan_promotion(
                source=DeploymentRef(
                    deployment_id=source.pk,
                    app_id=app.pk,
                    environment_id=source_env.pk,
                    image_digest=source.image_digest,
                    promoted_from_id=source.promoted_from_id,
                ),
                target=PromotionTarget(
                    environment_id=target_env.pk,
                    app_id=app.pk,
                    requires_approval=approvals_required > 0,
                ),
            )
        except PromotionError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))

        actor = _actor_from_request(info)
        initial_status = (
            Deployment.Status.PENDING_APPROVAL if plan.needs_approval else Deployment.Status.PENDING
        )

        with transaction.atomic():
            new_deploy = Deployment.objects.create(
                registered_app=app,
                app_environment=target_env,
                workload_id=source.workload_id,
                triggered_by_user_id=actor.user_id,
                trigger_kind=Deployment.TriggerKind.PROMOTION.value,
                strategy=(source.strategy or "rolling"),
                status=initial_status.value,
                image_tag=source.image_tag,
                image_digest=plan.image_digest,
                config_snapshot=source.config_snapshot,
                approvals_required=approvals_required,
                approvals_received=0,
                promoted_from_id=plan.promoted_from_id,
            )

            persist_deployment_origin(new_deploy, origin)

            if initial_status is Deployment.Status.PENDING:
                wf_id = _deploy_workflow_id(str(app.guid), str(target_env.guid))
                _start_deploy_workflow_on_commit(
                    deployment=new_deploy,
                    workflow_kind="DeployAppWorkflow",
                    workflow_id=wf_id,
                    args=[
                        DeployAppInput(
                            registered_app_id=app.pk,
                            app_environment_id=target_env.pk,
                            deployment_id=new_deploy.pk,
                            image_tags={"app": source.image_tag},
                            trigger_kind=Deployment.TriggerKind.PROMOTION.value,
                            actor=actor,
                        )
                    ],
                    organization_id=org_id,
                    registered_app_id=app.pk,
                    app_environment_id=target_env.pk,
                    actor=actor,
                )

        return gql_success(deployment_to_type(new_deploy))
