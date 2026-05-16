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
    MigrateAppInput,
    RollbackInput,
    TearDownPreviewInput,
)
from config.features import Feature, is_enabled
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
class MigrateAppInputGql:
    app_environment_id: GUID
    target_cluster_id: GUID
    drain_source: bool = True


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


@strawberry.input
class UploadCustomDomainCertificateInput:
    """BYO-cert payload — operator pastes (or pipes via CLI) the full
    PEM chain + private key. Used when the platform can't auto-issue
    a cert: externally-managed AWS zones (ACM can't HTTP-01), zones
    that hit Let's Encrypt rate limits, or air-gapped / custom-CA
    deployments. The renderer reads the stored PEM verbatim — the
    platform never re-issues a BYO cert."""

    id: GUID
    certificate_pem: str
    private_key_pem: str


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


# Standalone capability deprovision (#368) ---------------------------


@strawberry.input
class DeleteAppDnsRecordInput:
    """Drop one DNS record on the app's bound cluster's DnsDriver.

    ``hostname`` is the full FQDN (``api.acme.com``) — the activity
    splits it into ``(name, parent zone)`` and dispatches to the
    DnsDriver. ``recordType`` defaults to CNAME (the most common
    record the platform writes for an app)."""

    app_id: GUID
    hostname: str
    record_type: str = "CNAME"


@strawberry.input
class RevokeAppCertificateInput:
    """Revoke the auto-issued cert tied to a CustomDomain row.

    Resets ``certificate_state`` back to NOT_REQUESTED so the renderer
    stops emitting the Ingress until the operator re-issues or BYO."""

    custom_domain_id: GUID


@strawberry.input
class DeleteAppIdentityRoleInput:
    """Delete the app's cloud IAM/identity role (IRSA / GKE Workload
    Identity / AKS federated cred / projected SA). Subsequent deploys
    re-provision the role on next ``provision_namespace``."""

    app_id: GUID


@strawberry.input
class ArchiveAppRegistryRepoInput:
    """Archive the app's image repo + clear the platform's stored URI.

    The driver's ``archive`` flag controls soft-vs-hard delete on the
    registry side (ECR archives by default). Clearing the URI lets a
    future ``provision_registry_repo`` start cleanly."""

    app_id: GUID
    archive: bool = True


@strawberry.input
class DeleteAppIngressInput:
    """Delete one Ingress (when ``hostname`` given) or every Ingress
    on the app's namespace (when None).

    Per-hostname soft-deletes the matching ``CustomDomain`` row — the
    renderer drops its Ingress on the next deploy. The all-ingresses
    path calls the IngressDriver directly."""

    app_id: GUID
    hostname: str | None = None


@strawberry.type(name="AstroliftCapabilityDeprovisionPayload")
class _CapabilityDeprovisionPayload:
    """Generic envelope for capability-deprovision mutations.

    Carries the cluster slug + a free-form ``detail`` string the UI
    can render. Per-mutation extras (deleted hostnames, role name,
    etc.) live in ``detail`` rather than typed fields — the operator-
    facing display is the same shape across capabilities."""

    app_id: GUID | None
    cluster_slug: str
    detail: str


@strawberry.input
class TriggerDeployWorkflowInput:
    """Input for the rebuild-and-deploy workflow-dispatch mutation (#387).

    ``app_slug`` resolves the RegisteredApp; ``branch`` defaults to
    the app's ``deploy_branch`` (with a ``main`` fallback) when
    None/empty. We accept a slug rather than a guid here for parity
    with ``start_deployment`` — operators tend to script against
    slugs."""

    app_slug: str
    branch: str | None = None


@strawberry.type(name="AstroliftTriggerDeployWorkflowPayload")
class TriggerDeployWorkflowPayload:
    """What the FE renders on a successful workflow dispatch (#387).

    ``run_url`` is the host's runs-page URL for the workflow file —
    GitHub's dispatch endpoint doesn't return a run id, so we link
    to the runs page and the operator watches the new run materialize
    at the top. ``dispatched_branch`` is the branch the dispatch
    actually targeted, after defaulting/normalizing the input."""

    run_url: str
    dispatched_branch: str


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


def _migrate_workflow_id(env_guid: str) -> str:
    return f"MigrateAppWorkflow-{env_guid}"


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
    app = (
        RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
        .select_related("approver_team")
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


def _required_approvals_for(app: RegisteredApp, env: AppEnvironment) -> int:
    """How many approvals does this deploy need?

    Whichever of the env-level (``required_approvals``) or app-level
    (``minimum_approvals`` when ``requires_approval`` is on) policy
    is stricter wins. Returns 0 when neither path gates the deploy.
    """
    env_min = int(env.required_approvals or 0)
    app_min = int(app.minimum_approvals or 0) if app.requires_approval else 0
    return max(env_min, app_min)


def _is_eligible_approver(app: RegisteredApp, *, user_id: int) -> bool:
    """Does ``user_id`` satisfy the app-level approver eligibility?

    When ``requires_approval`` is off, the app-level set imposes no
    constraint (env-level approvals fall back to the holder of
    ``app.approve_deploy`` — that's the permission check at the
    resolver). When on, the user must be either in the
    ``approver_users`` set or a member of ``approver_team``; an
    empty set with ``requires_approval`` on means 'any approver-
    permission holder', which the permission gate already covers.
    """
    if not app.requires_approval:
        return True
    if app.approver_users.filter(pk=user_id).exists():
        return True
    if app.approver_team_id is None:
        # No team and no user set → fall through to permission gate.
        return not app.approver_users.exists()
    from astrolift_identity.models import Member

    return Member.objects.filter(
        user_id=user_id,
        scope_kind=Member.ScopeKind.TEAM,
        scope_id=app.approver_team_id,
        is_active=True,
        deleted_at__isnull=True,
    ).exists()


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
                deployment_id=deployment.pk,
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


_DEPLOY_PIPELINE_DISABLED_MSG = (
    "deploy pipeline is not yet enabled in this environment — the "
    "apply/secrets/dns/rollout activities are still being implemented. "
    "Cluster adoption (bringClusterIntoManagement) is fully wired and "
    "remains usable. Set FEATURE_DEPLOY_PIPELINE=true on the webservice "
    "+ worker once the activity implementations have landed."
)


def _deploy_pipeline_disabled() -> bool:
    """True when the deploy pipeline feature flag is off.

    Used at the top of resolvers whose workflows still depend on stub
    activities (start_deployment, rollback_deployment, tear_down_preview)
    to short-circuit with a clear error envelope.
    """
    return not is_enabled(Feature.DEPLOY_PIPELINE)


def _kick_validate_custom_domain(domain) -> None:
    """Enqueue ``ValidateCustomDomainWorkflow`` for a CustomDomain row.

    Flips the row to ``validating`` so the UI shows the in-flight
    state immediately, then starts the workflow with a deterministic
    id so re-firing the same domain joins the existing run rather
    than spawning a parallel one (matches the cluster bring-into-
    management pattern).
    """
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import (
        Actor,
        ValidateCustomDomainInput,
    )

    domain.validation_status = CustomDomain.ValidationStatus.VALIDATING
    domain.last_validation_error = ""
    domain.save(
        update_fields=[
            "validation_status",
            "last_validation_error",
            "updated_at",
            "version",
        ],
    )
    # Actor is best-effort — the audit middleware captures the human
    # actor on the mutation row separately, but the workflow needs
    # *some* identity for its own audit trail.
    actor = Actor(kind="system", display="custom-domain-handshake")
    start_workflow(
        "ValidateCustomDomainWorkflow",
        args=[
            ValidateCustomDomainInput(
                custom_domain_id=domain.pk,
                actor=actor,
            ),
        ],
        workflow_id=f"ValidateCustomDomainWorkflow-{domain.guid}",
    )


@strawberry.type
class LifecycleMutation:
    @strawberry.field
    @mutation_audit(action="deployment.start")
    @require_permission(Permission.APP_DEPLOY)
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
            approvals_required = _required_approvals_for(app, env)
            initial_status = (
                Deployment.Status.PENDING_APPROVAL if approvals_required > 0 else Deployment.Status.PENDING
            )

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
            deployment = Deployment.objects.create(
                registered_app=app,
                app_environment=env,
                triggered_by_user_id=actor.user_id,
                trigger_kind=input.trigger_kind,
                status=initial_status.value,
                image_tag=input.image_tag,
                image_digest=input.image_digest or "",
                approvals_required=approvals_required,
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
                            deployment_id=deployment.pk,
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

        return gql_success(deployment_to_type(deployment))

    @strawberry.field
    @mutation_audit(action="deployment.reject")
    @require_permission(Permission.APP_APPROVE_DEPLOY)
    @tenant_scoped()
    def reject_deployment(self, info: Info, input: DeploymentByIdInput) -> MutationResultType[DeploymentType]:
        """Reject a pending_approval deploy from in-band.

        Mirrors :meth:`reject_deployment_by_token` but requires an
        authenticated approver. ANY rejection short-circuits to
        FAILED — one nay kills the deploy, matching the quorum
        policy in :mod:`astrolift_lifecycle.approval`.
        """
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
            if deployment.workflow_run_id:
                wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
                if not signal_workflow(wf_id, "abort"):
                    terminate_workflow(wf_id, reason="reject_deployment mutation")
            deployment.transition_to(Deployment.Status.FAILED)

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
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
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
                status=initial_status.value,
                image_tag=source.image_tag,
                image_digest=source.image_digest,
                config_snapshot=source.config_snapshot,
                approvals_required=approvals_required,
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
                            deployment_id=new_deploy.pk,
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
    @mutation_audit(action="environment.pause_ingress")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def pause_app_ingress(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Pause live traffic at the Ingress layer for an env (#378).

        Independent of ``deploys_paused``: the renderer keeps emitting
        the per-CustomDomain Ingress on every deploy, but tagged so
        the controller serves a 503 instead of the app. Lets an
        operator put an app in maintenance ("we'll be right back")
        without freezing the deploy pipeline, and lets them keep
        shipping fixes while traffic stays parked. The flag is
        consulted by ``_render_app_ingresses_and_tls`` on the next
        deploy render — re-deploy or wait for the next CI push for it
        to take effect cluster-side.
        """
        env = (
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if not env.ingress_paused:
            env.ingress_paused = True
            env.save(update_fields=["ingress_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="environment.resume_ingress")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def resume_app_ingress(
        self, info: Info, input: EnvironmentByIdInput
    ) -> MutationResultType[AppEnvironmentType]:
        """Lift the ingress-pause flag — restores normal routing on
        the next render. As with ``pause_app_ingress``, the change is
        picked up by the next deploy."""
        env = (
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster", "managed_domain")
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found")
        if env.ingress_paused:
            env.ingress_paused = False
            env.save(update_fields=["ingress_paused", "updated_at", "version"])
        return gql_success(app_env_to_type(env))

    @strawberry.field
    @mutation_audit(action="preview.tear_down")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def tear_down_preview(
        self, info: Info, input: TearDownPreviewInputGql
    ) -> MutationResultType[DeploymentType]:
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)
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

    # ---- App migration -------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.migrate_to_cluster")
    @require_permission(Permission.APP_DEPLOY)
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

        env = (
            AppEnvironment.objects.select_related("registered_app", "tenant_cluster")
            .filter(guid=str(input.app_environment_id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app environment not found")
        target = TenantCluster.objects.filter(
            guid=str(input.target_cluster_id),
            deleted_at__isnull=True,
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
            Deployment.objects.filter(
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
        tenant = get_current_tenant()
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
            )
        return gql_success(app_env_to_type(env))

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

        # Build the handshake — challenge token + the per-record list
        # the operator must add to their authoritative DNS (or that
        # the platform will create itself when the parent zone is
        # managed). The validation workflow consumes
        # ``required_dns_records`` to know what to probe.
        from astrolift_clusters.models import ManagedDomain
        from astrolift_lifecycle.custom_domain_handshake import (
            build_handshake,
            hostname_parent_zone,
            resolve_cluster_ingress_target,
        )

        parent_zone = hostname_parent_zone(host)
        managed_zone = ManagedDomain.objects.filter(
            zone=parent_zone,
            deleted_at__isnull=True,
        ).first()
        # Best-effort cluster pick: prefer the app's default tenant
        # cluster; fall back to whatever cluster the managed-zone
        # row binds. Either way the operator-facing CNAME target is
        # stable.
        cluster = getattr(app, "default_tenant_cluster", None)
        cluster_slug = cluster.slug if cluster is not None else "default"
        cname_target = resolve_cluster_ingress_target(
            cluster_slug=cluster_slug,
            managed_domain_zone=managed_zone.zone if managed_zone else None,
        )
        handshake = build_handshake(
            hostname=host,
            cluster_ingress_target=cname_target,
            is_platform_managed_zone=managed_zone is not None,
            validation_method=method,
        )

        domain = CustomDomain.objects.create(
            registered_app=app,
            hostname=host,
            validation_method=method,
            txt_challenge_token=handshake.txt_challenge_token,
            expected_cname_target=handshake.expected_cname_target,
            required_dns_records=[
                {
                    "kind": r.kind,
                    "name": r.name,
                    "value": r.value,
                    "ttl": r.ttl,
                    "propagated": r.propagated,
                    "last_checked_at": r.last_checked_at,
                    "message": r.message,
                }
                for r in handshake.required_records
            ],
            is_platform_managed_zone=handshake.is_platform_managed_zone,
        )
        # Fire the validation workflow on creation so platform-managed
        # zones auto-create their records + first DNS probe runs
        # without the operator having to click Recheck.
        _kick_validate_custom_domain(domain)
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
        """Fire ``ValidateCustomDomainWorkflow`` for the row (#397).

        Idempotent: re-firing the same workflow id joins the existing
        run rather than starting a parallel one. The workflow probes
        the authoritative nameservers, updates per-record propagation
        state on ``required_dns_records``, and transitions
        ``validation_status`` to ``validated`` or ``failed``.
        """
        domain = CustomDomain.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
        ).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        _kick_validate_custom_domain(domain)
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.upload_certificate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def upload_custom_domain_certificate(
        self,
        info: Info,
        input: UploadCustomDomainCertificateInput,
    ) -> MutationResultType[AppDomainType]:
        """BYO-cert path for the unhappy-path UX (#397).

        When auto-issuance can't reach the cert (externally-managed
        AWS zone, LE rate-limited zone, custom CA), the operator
        pastes their PEM chain + key here. We store both, flip
        ``certificate_state`` to ``byo``, and the renderer picks up
        the uploaded PEM instead of an issued cert id.

        Basic shape validation only — we accept any PEM-looking
        payload at this layer; the renderer rejects malformed chains
        at apply time and reports back on the deployment status.
        """
        from datetime import datetime as _dt

        domain = CustomDomain.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
        ).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        pem = (input.certificate_pem or "").strip()
        key = (input.private_key_pem or "").strip()
        if "-----BEGIN CERTIFICATE-----" not in pem:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "certificate_pem must contain at least one PEM CERTIFICATE block",
                field="certificatePem",
            )
        if "PRIVATE KEY" not in key:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "private_key_pem must be a PEM-encoded private key",
                field="privateKeyPem",
            )
        # Stash the PEM bundle (chain + key concatenated) onto the
        # row. The renderer reads ``byo_certificate_pem`` and emits a
        # platform-issued Secret/SecretSet to the runtime cluster.
        domain.byo_certificate_pem = pem + "\n" + key
        domain.byo_certificate_uploaded_at = _dt.now(tz=UTC)
        domain.certificate_state = CustomDomain.CertificateState.BYO
        domain.last_certificate_error = ""
        # Clear any auto-issued cert id — the BYO cert supersedes it.
        domain.certificate_id = ""
        domain.save(
            update_fields=[
                "byo_certificate_pem",
                "byo_certificate_uploaded_at",
                "certificate_state",
                "last_certificate_error",
                "certificate_id",
                "updated_at",
                "version",
            ],
        )
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

    # ---- Standalone capability deprovision (#368) ---------------

    @strawberry.field
    @mutation_audit(action="app.dns_record.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def delete_app_dns_record(
        self,
        info: Info,
        input: DeleteAppDnsRecordInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Drop one DNS record on the app's bound cluster's
        ``DnsDriver`` (#368).

        Useful for cleaning up a stale CNAME the renderer once
        emitted but the operator has decommissioned, without dropping
        the whole app's DNS scaffolding. The activity splits the
        FQDN into ``(name, parent zone)`` itself and dispatches.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_dns_record_sync,
        )

        app = RegisteredApp.objects.filter(guid=str(input.app_id), deleted_at__isnull=True).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip()
        if not host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname is required",
                field="hostname",
            )
        try:
            summary = _deprovision_dns_record_sync(
                registered_app_id=app.pk,
                hostname=host,
                record_type=input.record_type or "CNAME",
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=(f"deleted {summary['type']} record {summary['name']!r} in zone {summary['zone']!r}"),
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.certificate.revoke")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def revoke_app_certificate(
        self,
        info: Info,
        input: RevokeAppCertificateInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Revoke the auto-issued cert for a ``CustomDomain`` and
        reset cert state so the renderer stops emitting the Ingress
        (#368).

        The ``CustomDomain`` row is preserved — operators may want to
        re-issue or BYO without re-adding the hostname. Clears
        ``certificate_id``, ``byo_certificate_pem``, and flips state
        to NOT_REQUESTED.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_certificate_sync,
        )

        domain = CustomDomain.objects.filter(
            guid=str(input.custom_domain_id),
            deleted_at__isnull=True,
        ).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "custom domain not found")
        try:
            summary = _deprovision_certificate_sync(custom_domain_id=domain.pk)
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        cert_id = summary["certificate_id"]
        revoked = summary["revoked"]
        if cert_id and revoked:
            detail = f"revoked certificate {cert_id!r}; cert state reset to not_requested"
        elif cert_id:
            detail = f"could not revoke certificate {cert_id!r}; cert state reset to not_requested"
        else:
            detail = "no auto-issued certificate to revoke; cert state reset to not_requested"
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=GUID(str(domain.registered_app.guid)),
                cluster_slug=str(summary["cluster_slug"]),
                detail=detail,
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.identity_role.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def delete_app_identity_role(
        self,
        info: Info,
        input: DeleteAppIdentityRoleInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Delete the cloud IAM/identity role bound to the app's
        ServiceAccount via the cluster's WorkloadIdentityDriver
        (#368).

        Subsequent deploys re-provision the role through the
        canonical onboarding path — pulling the role doesn't break
        the app permanently."""
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_identity_role_sync,
        )

        app = RegisteredApp.objects.filter(guid=str(input.app_id), deleted_at__isnull=True).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        try:
            summary = _deprovision_identity_role_sync(registered_app_id=app.pk)
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=f"deleted identity role {summary['role']!r}",
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.registry_repo.archive")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def archive_app_registry_repo(
        self,
        info: Info,
        input: ArchiveAppRegistryRepoInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Archive the app's image repo on the registry + clear the
        platform's stored URI (#368).

        Default ``archive=True`` matches the registry SDK contract
        (ECR archives images instead of hard-deleting). Clearing the
        URI lets ``provision_registry_repo`` re-create a fresh repo
        on the next provision pass.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_registry_repo_sync,
        )

        app = RegisteredApp.objects.filter(guid=str(input.app_id), deleted_at__isnull=True).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        try:
            summary = _deprovision_registry_repo_sync(
                registered_app_id=app.pk,
                archive=bool(input.archive),
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        verb = "archived" if summary["archived"] else "deleted"
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=f"{verb} registry repo {summary['repo']!r}; registry_repo_uri cleared",
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.ingress.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def delete_app_ingress(
        self,
        info: Info,
        input: DeleteAppIngressInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Delete one or every Ingress on the app's namespace (#368).

        ``hostname`` given → soft-delete the matching CustomDomain;
        the renderer drops its Ingress on the next deploy.
        ``hostname`` None → call ``IngressDriver.delete_ingress`` for
        every CustomDomain on the app's bound cluster, soft-deleting
        each row as it goes. Coordinate with #378 (pause/resume) —
        operators typically pause first, drop the ingress, then
        decide whether to resume on a different host.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_ingress_sync,
        )

        app = RegisteredApp.objects.filter(guid=str(input.app_id), deleted_at__isnull=True).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip() or None
        try:
            summary = _deprovision_ingress_sync(
                registered_app_id=app.pk,
                hostname=host,
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        deleted = summary["deleted_hostnames"]
        if host:
            detail = f"soft-deleted custom domain {host!r}; ingress drops on next deploy"
        else:
            detail = (
                f"deleted {len(deleted)} ingress(es) on namespace "
                f"{summary['namespace']!r}: {', '.join(deleted)}"
                if deleted
                else f"no active ingresses on namespace {summary['namespace']!r}"
            )
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=detail,
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.ci.dispatch")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def trigger_astrolift_deploy_workflow(
        self,
        info: Info,
        input: TriggerDeployWorkflowInput,
    ) -> MutationResultType[TriggerDeployWorkflowPayload]:
        """Rebuild + deploy by firing the source host's workflow-
        dispatch API for the app's CI workflow (#387).

        Does NOT bypass the env's approval policy: if the app gates
        deploys on human approval (``requires_approval=True``) OR any
        env on the app carries ``required_approvals > 0``, the
        mutation refuses with PRECONDITION so the operator falls back
        to the regular ``startDeployment`` + approver flow rather
        than sneaking a build through CI dispatch.
        """
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_scm.services.workflows import (
            WorkflowDispatchError,
            dispatch_astrolift_ci_workflow,
        )

        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        # Approval-gate. Either app-level (requires_approval=True) or
        # env-level (any active env with required_approvals > 0) is
        # enough to bounce this path. The operator gets a clear hint
        # to take the regular deploy path with approvers.
        gates_on_approval = bool(app.requires_approval)
        if not gates_on_approval:
            gates_on_approval = AppEnvironment.objects.filter(
                registered_app=app,
                required_approvals__gt=0,
                deleted_at__isnull=True,
            ).exists()
        if gates_on_approval:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                ("deploy workflow requires approval — " "use startDeployment with approver flow"),
            )

        branch_input = (input.branch or "").strip() or None
        try:
            result = dispatch_astrolift_ci_workflow(app, branch=branch_input)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except WorkflowDispatchError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if not result.ok:
            # WORKFLOW_FILE_MISSING gets surfaced as PRECONDITION so
            # the FE can pivot to the #384 "Sync CI workflow" flow.
            # Other host failures (auth, network, generic API) ride
            # the same envelope; the message carries enough for the
            # toast to be useful.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "workflow dispatch failed",
            )

        dispatched_branch = (branch_input or app.deploy_branch or "main").strip() or "main"
        return gql_success(
            TriggerDeployWorkflowPayload(
                run_url=result.run_url,
                dispatched_branch=dispatched_branch,
            ),
        )

    # ----------------------------------------------------------------
    # Live workload ops (#388): rolling restart + scale replicas
    # ----------------------------------------------------------------
    #
    # First-line incident-response actions. The mutation resolves the
    # Workload row by GUID, dispatches through
    # ``astrolift_lifecycle.services.k8s_ops`` (which speaks to the
    # bound cluster's ClusterDriver), and returns the read-back
    # revision / replica counts. ``app.deploy`` is the gate — these
    # change live runtime state, so we don't expand the permission
    # surface beyond what manual deploys already need.

    @strawberry.field
    @mutation_audit(action="app.workload.restart")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def restart_astrolift_workload(
        self,
        info: Info,
        input: RestartWorkloadInput,
    ) -> MutationResultType[_WorkloadOpPayload]:
        """Trigger a rolling restart on the workload's Deployment.

        Equivalent to ``kubectl rollout restart deployment/<name>``:
        annotates the pod template with a fresh
        ``kubectl.kubernetes.io/restartedAt`` so the Deployment
        controller rolls a new ReplicaSet. No image change, no
        manifest re-render — fastest way to bounce wedged pods or
        propagate a sidecar update.
        """
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
            rollout_restart_workload,
        )
        from astrolift_registry.models import Workload

        workload = (
            Workload.objects.filter(
                guid=str(input.workload_id),
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "workload not found")
        try:
            result = rollout_restart_workload(workload)
        except K8sOpError as exc:
            return gql_failure(exc.code, exc.message)
        return gql_success(
            _WorkloadOpPayload(
                workload_id=input.workload_id,
                new_revision=result.new_revision,
                desired_replicas=None,
                ready_replicas=None,
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.workload.scale")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def scale_astrolift_workload(
        self,
        info: Info,
        input: ScaleWorkloadInput,
    ) -> MutationResultType[_WorkloadOpPayload]:
        """Patch the workload's Deployment ``spec.replicas``.

        Bounds are clamped server-side: ``0 <= replicas <= min(20,
        env.max_replicas)``. Out-of-range scales return
        ``VALIDATION`` with the bounds in the message — the UI surfaces
        the message verbatim so the operator knows the upper they're
        hitting.
        """
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
            scale_workload,
        )
        from astrolift_registry.models import Workload

        workload = (
            Workload.objects.filter(
                guid=str(input.workload_id),
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "workload not found")
        try:
            result = scale_workload(workload, int(input.replicas))
        except K8sOpError as exc:
            return gql_failure(exc.code, exc.message)
        return gql_success(
            _WorkloadOpPayload(
                workload_id=input.workload_id,
                new_revision=None,
                desired_replicas=result.current_replicas,
                ready_replicas=result.ready_replicas,
            ),
        )

    # ----------------------------------------------------------------
    # #385 — one-click source-webhook install
    # ----------------------------------------------------------------
    #
    # Registers (or refreshes) the push-event webhook on the app's
    # configured source repo. ``app.update`` is the gate — this is a
    # repo-config-touching action, not a deploy. The service layer
    # picks the same connection the manifest sync / workflow dispatch
    # paths use so a single identity drives every SCM call for an app.

    @strawberry.field
    @mutation_audit(action="app.ci.install_webhook")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def install_astrolift_source_webhook(
        self,
        info: Info,
        input: InstallSourceWebhookInput,
    ) -> MutationResultType[InstallSourceWebhookPayload]:
        """Install (or refresh) the source-host push-event webhook for
        ``app_slug`` pointing at the platform's receiver URL.

        The receiver URL shape is
        ``{PLATFORM_API_URL}/api/webhooks/github/{app.guid}/`` — keyed
        on the app's GUID so deliveries route without grepping the
        payload. The HMAC secret is rotated on every call (created or
        refreshed) and persisted on the picked SourceConnection's
        ``webhook_secret_*`` columns.

        Status codes:
        * ``created`` — a brand-new hook landed on the host.
        * ``refreshed`` — same URL already registered (or the App's
          own webhook covers this repo); we rotated the secret and
          advanced the ``installed_at`` marker.
        """
        from astrolift_scm.services.webhooks import (
            install_astrolift_source_webhook as install_webhook_service,
        )

        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        try:
            result = install_webhook_service(app)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

        if result.status == "no_connection":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "no source connection available",
            )
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "webhook install failed",
            )

        return gql_success(
            InstallSourceWebhookPayload(
                status=result.status,
                hook_id=result.hook_id,
                receiver_url=result.receiver_url,
            ),
        )

    # ----------------------------------------------------------------
    # CI secrets push (#383): seal + PUT the five ``ASTROLIFT_*``
    # GitHub Actions secrets onto the app's source repo, rotating the
    # deploy token as part of the round-trip.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.push_secrets")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def push_astrolift_ci_secrets_to_repo(
        self,
        info: Info,
        input: PushCiSecretsToRepoInput,
    ) -> MutationResultType[PushCiSecretsPayload]:
        """Push the five CI secrets to the app's source repo (#383).

        Auth is the viewer's *personal* GitHub OAuth connection (per
        #395): we want the secret writes attributed to the human who
        clicked the button on GitHub's audit log, not to a shared
        org-level PAT. The mutation rotates the deploy token as part
        of the push — there's no way to recover the existing plaintext
        from the hash, so a rotation is the only way to deliver a
        sealable value. Live CI keeps working through the rotation
        grace window (the previous hash stays valid for 1h by default).
        """
        from django.conf import settings

        from astrolift_scm.services.secrets import (
            PushSecretsError,
            push_astrolift_ci_secrets,
        )

        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        platform_api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")

        try:
            result = push_astrolift_ci_secrets(
                app,
                viewer_user=viewer,
                platform_api_url=platform_api_url,
            )
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except PushSecretsError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if not result.ok:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "couldn't push CI secrets",
            )

        return gql_success(
            PushCiSecretsPayload(
                secret_names=list(result.secret_names),
                rotated_token_last_4=result.new_token_last_4,
                repo=app.source_repo,
            ),
        )

    # ----------------------------------------------------------------
    # CI workflow push (#384): render the canonical
    # ``.github/workflows/astrolift-ci.yml`` for the app and commit
    # it to the deploy branch. Pairs with the secrets push (#383) so
    # an operator can go from "fresh repo" to "platform-driven deploy"
    # without copy-pasting the reference YAML by hand.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.push_workflow")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def push_astrolift_ci_workflow_to_repo(
        self,
        info: Info,
        input: PushCiWorkflowToRepoInput,
    ) -> MutationResultType[PushCiWorkflowPayload]:
        """Render + reconcile the Astrolift CI workflow file onto the
        app's deploy branch (#384).

        Idempotent on no-op: when the file already matches the rendered
        template byte-for-byte the resolver returns ``status="in_sync"``
        and no commit lands. Protected deploy branches fall through to
        a side-branch + PR flow (``status="pr_opened"``) so the change
        respects the repo's review path.
        """
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            sync_workflow_file_to_repo,
        )

        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None

        try:
            result = sync_workflow_file_to_repo(app, viewer_user=viewer)
        except NotImplementedError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except WorkflowSyncError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message)

        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "couldn't sync CI workflow to repo",
            )

        return gql_success(
            PushCiWorkflowPayload(
                status=result.status,
                commit_sha=result.commit_sha or None,
                pr_url=result.pr_url or None,
            ),
        )


# ---------------------------------------------------------------------------
# #388 input / payload types — defined at module scope so Strawberry
# picks them up alongside the resolver decorators above. Kept at the
# tail of the file so sibling agents appending input/output types
# don't conflict.
# ---------------------------------------------------------------------------


@strawberry.input
class RestartWorkloadInput:
    workload_id: GUID


@strawberry.input
class ScaleWorkloadInput:
    workload_id: GUID
    replicas: int


@strawberry.type(name="AstroliftWorkloadOpPayload")
class _WorkloadOpPayload:
    """Read-back payload for live workload ops.

    ``new_revision`` is the post-restart ``observedGeneration`` (may
    be None when the driver doesn't surface status). ``desired_replicas``
    + ``ready_replicas`` are the post-scale ``spec.replicas`` /
    ``status.readyReplicas`` read-back. Fields irrelevant to the
    specific op are left None — the UI uses the mutation it called
    to decide which fields to render.
    """

    workload_id: GUID
    new_revision: int | None
    desired_replicas: int | None
    ready_replicas: int | None


# ---------------------------------------------------------------------------
# #383 input / payload types — push CI secrets to repo. Kept at module
# scope so Strawberry picks them up; appended at the tail so sibling
# agents touching this file don't collide on the same line range.
# ---------------------------------------------------------------------------


@strawberry.input
class PushCiSecretsToRepoInput:
    """Push the five Astrolift CI secrets to ``app_slug``'s source repo.

    Slug rather than guid so the FE button can call the mutation
    without a separate lookup; matches the contract on every other
    CI-side mutation (#384, #387)."""

    app_slug: str


@strawberry.type(name="AstroliftPushCiSecretsPayload")
class PushCiSecretsPayload:
    """Read-back for a successful CI-secrets push (#383).

    ``secret_names`` is the canonical list of names PUT (rendered
    verbatim in the success toast so the operator knows what just
    landed on the repo). ``rotated_token_last_4`` is the last four
    chars of the freshly-minted deploy-token plaintext — the only
    piece of the new token that ever surfaces to the browser; the
    full plaintext is sealed and handed to GitHub. ``repo`` is the
    ``owner/name`` of the target repo, for the toast's repo-label."""

    secret_names: list[str]
    rotated_token_last_4: str
    repo: str


# ---------------------------------------------------------------------------
# #385 input / payload types — install source-host push-event webhook.
# Kept at the tail so sibling agents touching this file land on
# adjacent line ranges instead of overlapping ones.
# ---------------------------------------------------------------------------


@strawberry.input
class InstallSourceWebhookInput:
    """Install or refresh the push-event webhook on ``app_slug``'s
    source repo. Slug rather than guid so the FE button calls the
    mutation without an extra lookup — same shape every other CI-side
    mutation uses."""

    app_slug: str


@strawberry.type(name="AstroliftInstallSourceWebhookPayload")
class InstallSourceWebhookPayload:
    """Read-back for a successful webhook install / refresh (#385).

    ``status`` is one of:
    * ``created`` — the host registered a brand-new hook.
    * ``refreshed`` — a hook with the same URL already existed (or the
      GitHub App's own webhook covers this repo); we rotated the
      shared HMAC secret and advanced the ``installed_at`` timestamp
      so the operator's click is observable in the UI.

    ``hook_id`` is the host-side identifier (numeric on GitHub,
    stored as string for GitLab / Bitbucket forward-compat). Empty
    for GitHub-App-install connections where the App's own webhook
    routes deliveries.

    ``receiver_url`` is the canonical platform URL the host POSTs
    deliveries to — surfaced in the toast so the operator can paste
    it elsewhere if a manual install is ever needed."""

    status: str
    hook_id: str
    receiver_url: str


# ---------------------------------------------------------------------------
# #384 input / payload types — push CI workflow file to repo. Kept at
# module scope so Strawberry picks them up; appended at the tail so
# sibling agents touching this file don't collide on the same line
# range.
# ---------------------------------------------------------------------------


@strawberry.input
class PushCiWorkflowToRepoInput:
    """Push the rendered Astrolift CI workflow to ``app_slug``'s repo.

    Slug rather than guid so the FE button can call the mutation
    without a separate lookup — matches the contract on the other
    CI-side mutations (#383, #387)."""

    app_slug: str


@strawberry.type(name="AstroliftPushCiWorkflowPayload")
class PushCiWorkflowPayload:
    """Read-back for a successful CI-workflow sync (#384).

    ``status`` discriminates how the change landed:

    * ``created``   — file was missing on the deploy branch; we wrote
                       it. ``commit_sha`` is the commit SHA.
    * ``updated``   — file existed and diverged; we overwrote it.
                       ``commit_sha`` is the new commit SHA.
    * ``in_sync``   — file already matched the rendered template; we
                       did nothing. Both ``commit_sha`` and ``pr_url``
                       are null.
    * ``pr_opened`` — the deploy branch is protected; we landed the
                       file on a side branch and opened a PR back into
                       the deploy branch. ``pr_url`` is the PR URL.

    ``commit_sha`` is null on ``in_sync`` and ``pr_opened`` paths;
    ``pr_url`` is null on ``created`` / ``updated`` / ``in_sync``."""

    status: str
    commit_sha: str | None
    pr_url: str | None
