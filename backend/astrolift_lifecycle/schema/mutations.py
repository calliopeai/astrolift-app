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

import re
from datetime import UTC

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_graphql.errors import MutationErrorType
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.approval import mint_magic_link
from astrolift_lifecycle.models import (
    AppEnvironment,
    CustomDomain,
    Deployment,
    DeploymentLog,
    DeployToken,
    DomainPathRoute,
    DomainRedirectRule,
    EnvironmentSetting,
    PreviewEnvironment,
    TaskRun,
)
from astrolift_lifecycle.promotion import (
    DeploymentRef,
    PromotionError,
    PromotionTarget,
    plan_promotion,
)
from astrolift_lifecycle.schema.types import (
    AppDomainType,
    AppEnvironmentType,
    DeploymentType,
    DeployTokenType,
    EnvironmentSettingType,
    PreviewEnvironmentType,
    TaskRunPayloadType,
    app_domain_to_type,
    app_env_to_type,
    deploy_token_to_type,
    deployment_to_type,
    env_setting_to_type,
    preview_to_type,
    task_run_to_payload,
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
    BuildPreviewInput,
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
    # Commit-message provenance (#419) — surfaced on the approval card
    # so approvers can see what they're greenlighting. Optional so the
    # manual-UI path keeps working.
    commit_message: str | None = None
    commit_author: str | None = None
    # GitHub PR + avatar provenance (#722) — populated by the push
    # webhook when the deploy was triggered from a PR; manual UI
    # deploys leave these unset (pr_number defaults to 0, FE renders
    # a dash).
    pr_number: int | None = None
    commit_author_avatar_url: str | None = None
    # Rollout strategy (#736).  The Temporal workflow's rollout-policy
    # step decides this; manual deploys default to "rolling" (the
    # safest k8s default).  Accepted values mirror Deployment.Strategy.
    strategy: str | None = None


@strawberry.input
class PromoteDeploymentInput:
    app_slug: str
    source_environment_name: str
    target_environment_name: str


@strawberry.input
class DeploymentByIdInput:
    id: GUID


@strawberry.input
class AbortDeploymentInput:
    """``reason`` is required + non-empty (#419) so every rejection
    leaves a paper trail. Validated at the resolver boundary; we
    persist it on the row + the audit-log extras payload so the
    history sidebar can render it without an extra join."""

    id: GUID
    reason: str


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
class BulkApproveDeploymentsInput:
    """Bulk-approve N pending deploys in one call (#420).

    ``deployment_ids`` is capped at 50 — defensive ceiling so a runaway
    selection doesn't fan into a single huge audit-write burst.
    ``reason`` is optional and carried onto the audit-extras payload
    for every approved id."""

    deployment_ids: list[GUID]
    reason: str | None = None


@strawberry.input
class BulkRejectDeploymentsInput:
    """Bulk-reject N pending deploys in one call (#420).

    Same shape as :class:`BulkApproveDeploymentsInput` but ``reason``
    is required + non-empty (mirrors the single-id reject contract)
    so every rejected deploy carries an auditable explanation."""

    deployment_ids: list[GUID]
    reason: str


@strawberry.type(name="AstroliftBulkDeploymentResultItem")
class BulkDeploymentResultItem:
    """Per-id outcome from a bulk approve / reject pass.

    ``ok`` is True when the per-id resolver succeeded; ``deployment``
    carries the post-mutation row in that case. On failure ``errors``
    is non-empty and ``deployment`` is null — the FE renders the
    failure inline alongside the still-pending row so the operator can
    retry just the failing entries."""

    deployment_id: GUID
    ok: bool
    errors: list[MutationErrorType]
    deployment: DeploymentType | None


@strawberry.type(name="AstroliftBulkDeploymentResultData")
class BulkDeploymentResultData:
    """Container for the per-id breakdown returned in
    :class:`AstroliftBulkDeploymentMutationResult.data`. Wrapping the
    list in a dedicated type keeps the standard ``MutationResult``
    envelope shape so callers reuse their existing ``ok / errors /
    data`` handling pattern."""

    results: list[BulkDeploymentResultItem]
    succeeded_count: int
    failed_count: int


@strawberry.input
class TearDownPreviewInputGql:
    id: GUID


@strawberry.input
class ExtendPreviewTtlInputGql:
    """Push a preview's auto-teardown out by N days (#431).

    Allowed ``days`` values are locked to ``{1, 7, 30}`` — the resolver
    rejects anything else. The model layer enforces the +30d ceiling
    from now so an extension on a preview that's already 25 days in
    can't sneak past the cap.
    """

    id: GUID
    days: int


@strawberry.input
class CreatePreviewEnvironmentInput:
    """Manually spin up a preview from a branch — no PR required (#751).

    The auto path (PR-opened webhook → BuildPreviewWorkflow) keys on
    ``(registered_app, pr_number)``; this manual path keys on
    ``(registered_app, branch)`` so re-running the mutation on the same
    branch returns the existing preview rather than racing two
    namespaces in.

    ``environment_name`` is the AppEnvironment name carved out for the
    preview deploy. Defaults to ``preview-<branch-slug>`` when the
    caller doesn't pin a value; explicit names let operators run
    parallel previews with distinct deploy_config overrides on the same
    branch (e.g., a perf-tuned variant vs. baseline).
    """

    app_slug: str
    branch: str
    environment_name: str = ""


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
class AddWildcardDomainInput:
    """Add a ``*.hostname`` wildcard domain (#753).

    ``hostname`` is the apex (e.g. ``example.com``); the platform
    issues a cert covering both the apex and ``*.<hostname>`` SANs.
    Wildcard issuance requires DNS-01 by CA policy — HTTP-01 / DNS-TXT
    can't authorize wildcards — so ``validation_method`` is restricted
    to ``dns_01`` (defaulted; rejected at the resolver if overridden
    to anything else).

    ``sni_cert_ref`` lets the operator pin a provider-specific cert
    identifier for SNI on this hostname. Empty defaults to the
    renderer's auto-pick; populated for multi-cert SNI scenarios
    (e.g., EV on apex + wildcard on subdomains).
    """

    app_slug: str
    hostname: str
    validation_method: str = "dns_01"
    sni_cert_ref: str = ""


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


# Domain redirect rules (#742) ---------------------------------------


@strawberry.input
class DomainRedirectRuleInput:
    """One redirect rule on the replace-all ``setDomainRedirects``
    payload (#742). Mirrors ``DomainRedirectRule``'s persisted shape;
    defaults match the model defaults so the FE only needs to specify
    ``kind`` for the well-known kinds (http_to_https / apex_to_www /
    www_to_apex)."""

    kind: str
    source_pattern: str = ""
    destination_url: str = ""
    http_status: int = 301
    preserve_query_string: bool = True
    priority: int = 0


@strawberry.input
class SetDomainRedirectsInput:
    domain_id: GUID
    rules: list[DomainRedirectRuleInput]


# Domain path routes (#740) ------------------------------------------


@strawberry.input
class DomainPathRouteInput:
    """One path-prefix routing rule on the replace-all
    ``setDomainPathRoutes`` payload (#740)."""

    path_prefix: str
    target_workload_slug: str
    target_port: int
    strip_prefix: bool = False
    priority: int = 0


@strawberry.input
class SetDomainPathRoutesInput:
    domain_id: GUID
    routes: list[DomainPathRouteInput]


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
    token never lives in DB.

    ``rotation_grace_seconds`` is the live Constance-tunable grace
    window (``DEPLOY_TOKEN_ROTATION_GRACE_SECONDS``, default 24h)
    the previous secret stays valid for after a rotate. ``0`` on
    creation (no previous secret to honour). Surfaced so the rotate
    ConfirmDialog can display the *actual* grace operators are
    committing to instead of a hard-coded number that may have
    drifted (#425).
    """

    token: DeployTokenType
    plaintext_secret: str
    rotation_grace_seconds: int = 0


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

# Trigger kinds that the app-global ``webhook_deploys_paused`` gate
# (#399) blocks. Webhook-shaped triggers (push / ci / scheduled) come
# from automation and are exactly what the operator wants to stop
# during a deploy storm. Manual / rollback / promotion are operator-
# initiated and bypass the gate — that's the on-call escape valve
# the issue calls out explicitly.
_WEBHOOK_TRIGGER_KINDS = frozenset(
    {
        Deployment.TriggerKind.PUSH.value,
        Deployment.TriggerKind.CI.value,
        Deployment.TriggerKind.SCHEDULED.value,
    }
)


def _self_approve_allowed() -> bool:
    """Self-approval policy gate (#419).

    Default: False — the same person can't push and approve.
    Operators flip the Constance flag ``ALLOW_SELF_APPROVE_DEPLOYS``
    at runtime to override (e.g. single-engineer dev orgs that still
    want the audit trail). Constance unavailable (DB not migrated,
    plugin disabled) falls back to the safe default."""
    try:
        from constance import config as constance_config

        return bool(getattr(constance_config, "ALLOW_SELF_APPROVE_DEPLOYS", False))
    except Exception:
        return False


def _abort_extras(result) -> dict | None:
    """Audit-extras hook for abort/reject mutations.

    Stuffs the deployment's ``aborted_reason`` onto ``AuditEntry.extra``
    so the approval-history query can render it even when the
    deployment row has been pruned by a later force-redeploy."""
    if not getattr(result, "ok", False):
        return None
    data = getattr(result, "data", None)
    reason = getattr(data, "aborted_reason", "") if data is not None else ""
    if not reason:
        return None
    return {"reason": reason}


def _deployment_target_from_input(*_args, **kwargs) -> tuple[str, str] | None:
    """Audit ``target`` hook for deployment lifecycle mutations whose
    input carries the deployment guid (approve / reject / abort /
    rollback / redeploy / approve_by_token / reject_by_token).

    Returns ``("Deployment", "<guid>")`` so the audit row carries the
    deployment-scoped key the history query (#419) filters on. We
    swallow KeyErrors / AttributeErrors so an unexpected input shape
    never breaks the mutation — the row just won't carry a target.
    """
    try:
        payload = kwargs.get("input")
        if payload is None:
            return None
        identifier = getattr(payload, "id", None)
        if identifier is None:
            return None
        return ("Deployment", str(identifier))
    except (AttributeError, KeyError):
        return None


def _start_extras(result) -> dict | None:
    """Audit-extras hook for ``start_deployment``: stamps the
    freshly-created deployment guid onto the audit row's data
    payload so the approval-history query can correlate the
    lifecycle-start event back to the same deployment id the
    later approve/reject/abort rows carry on ``target_id``."""
    if not getattr(result, "ok", False):
        return None
    data = getattr(result, "data", None)
    deployment_id = getattr(data, "id", None) if data is not None else None
    if deployment_id is None:
        return None
    return {"deployment_id": str(deployment_id)}


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


_BRANCH_SLUG_BAD_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify_branch(branch: str) -> str:
    """Squash a branch name into a k8s-namespace-safe label fragment.

    Lowercases, replaces any run of non-``[a-z0-9]`` with ``-``, trims
    leading/trailing ``-``. Truncates to 40 chars to leave room for the
    ``<org>-<app>-`` prefix when composing the full namespace (k8s caps
    namespaces at 63 chars).  Returns empty string when the branch
    contains nothing slugifiable — the resolver rejects that.
    """
    slug = _BRANCH_SLUG_BAD_CHARS.sub("-", branch.lower()).strip("-")
    return slug[:40].strip("-")


def _manual_preview_namespace(*, org_slug: str, app_slug: str, branch_slug: str) -> str:
    """Compose ``<org>-<app>-<branch_slug>`` and truncate to fit k8s'
    63-char namespace ceiling. Mirrors the auto-preview namer
    (``<org>-<app>-pr-<n>``) so namespace audits group the two paths
    under one shape.
    """
    raw = f"{org_slug}-{app_slug}-{branch_slug}"
    if len(raw) <= 63:
        return raw
    # Truncate the app segment first since org tends to be stable.
    suffix = f"-{branch_slug}"
    budget = 63 - len(org_slug) - 1 - len(suffix)
    if budget <= 0:
        # org_slug + branch_slug already > 63; clip the branch instead.
        room_for_branch = 63 - len(org_slug) - 1 - len(app_slug) - 1
        clipped_branch = branch_slug[: max(1, room_for_branch)].strip("-") or "preview"
        return f"{org_slug}-{app_slug}-{clipped_branch}"
    truncated_app = app_slug[:budget].rstrip("-")
    return f"{org_slug}-{truncated_app}{suffix}"


def _build_preview_workflow_id(preview_guid: str) -> str:
    """Workflow id for the manual ``createPreviewEnvironment`` path
    (#751).

    Keyed on the preview row's guid (which is stable for the lifetime
    of the row) so re-firing the mutation against an existing manual
    preview joins the in-flight build rather than starting a parallel
    namespace.  Distinct ID-shape from the SCM-webhook dispatch
    (``build-preview-<repo>-<pr>-<sha>``) so the two paths can't
    collide on Temporal's workflow-id index even when the same app has
    both a PR-triggered and a manual preview running."""
    return f"BuildPreviewWorkflow-{preview_guid}"


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


def _start_deploy_workflow_on_commit(
    *,
    deployment: Deployment,
    workflow_kind: str,
    workflow_id: str,
    args: list,
    organization_id: int | None,
    registered_app_id: int | None,
    app_environment_id: int | None,
    actor: Actor,
) -> None:
    """Defer the Temporal workflow start until the surrounding transaction
    commits (#1025).

    Starting the workflow inside ``transaction.atomic()`` lets a worker pick
    the run up and read the deployment row before that row is committed, so a
    freshly-registered app's first deploy can sit ``pending`` forever (the
    worker saw nothing to act on). Registering the start via ``on_commit``
    guarantees the row is visible before the workflow runs. The deployment row
    itself is written by the caller inside the atomic block; only the start and
    the ``WorkflowRun`` mirror link happen post-commit.
    """

    def _start() -> None:
        handle = start_workflow(workflow_kind, args=args, workflow_id=workflow_id)
        if handle.enqueued:
            run = _record_workflow_run(
                kind=workflow_kind,
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=organization_id,
                registered_app_id=registered_app_id,
                app_environment_id=app_environment_id,
                actor=actor,
            )
            deployment.workflow_run = run
            deployment.save(update_fields=["workflow_run", "updated_at", "version"])

    transaction.on_commit(_start)


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
    _start_deploy_workflow_on_commit(
        deployment=deployment,
        workflow_kind="DeployAppWorkflow",
        workflow_id=wf_id,
        args=[
            DeployAppInput(
                registered_app_id=app.pk,
                app_environment_id=env.pk,
                deployment_id=deployment.pk,
                image_tags={"app": deployment.image_tag},
                trigger_kind=deployment.trigger_kind,
                actor=actor,
                commit_sha=deployment.commit_sha,
            )
        ],
        organization_id=organization_id,
        registered_app_id=app.pk,
        app_environment_id=env.pk,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Bulk approve / reject helpers (#420)
# ---------------------------------------------------------------------------


# Defensive ceiling. Past 50, the right shape is server-side scheduling
# (a workflow that drains a queue) rather than a synchronous mutation
# that holds a single request open for N rows.
_BULK_APPROVE_REJECT_CAP = 50


def _bulk_item_failure(deployment_id: str, code: str, message: str, *, field: str | None = None):
    """Per-id failure envelope shared across bulk approve / reject."""
    return BulkDeploymentResultItem(
        deployment_id=GUID(deployment_id),
        ok=False,
        errors=[MutationErrorType(code=code, message=message, field=field)],
        deployment=None,
    )


def _process_bulk_approve_one(
    *,
    deployment_id: str,
    actor: Actor,
    viewer_user_id: int | None,
    organization_id: int | None,
) -> BulkDeploymentResultItem:
    """Single-id approve path, callable from the bulk resolver.

    Mirrors :meth:`LifecycleMutation.approve_deployment` minus the
    @mutation_audit decorator (we emit per-id audit entries
    ourselves below) and minus the @require_permission decorator (the
    outer bulk resolver already gated on the org-scope permission;
    the per-id eligibility check still runs)."""
    from core.mutations import AuditEntry, emit_audit
    from core.mutations import ErrorCode as CoreErrorCode

    deployment = (
        Deployment.objects.select_related("registered_app", "app_environment", "workload")
        .filter(guid=deployment_id, deleted_at__isnull=True)
        .first()
    )
    if deployment is None:
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.NOT_FOUND.value,
            "deployment not found",
        )
    if deployment.status != Deployment.Status.PENDING_APPROVAL.value:
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PRECONDITION.value,
            f"deployment is in status {deployment.status}, expected pending_approval",
        )
    if actor.user_id and deployment.triggered_by_user_id == actor.user_id and not _self_approve_allowed():
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PRECONDITION.value,
            "cannot approve your own deployment — another approver required",
        )
    if actor.user_id and not _is_eligible_approver(deployment.registered_app, user_id=actor.user_id):
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PERMISSION_DENIED.value,
            "you are not in this app's approver set",
        )

    try:
        with transaction.atomic():
            _record_approval_vote_and_maybe_start(
                deployment,
                actor,
                organization_id=organization_id,
            )
    except Exception as exc:  # noqa: BLE001 — per-id failure shouldn't abort the batch
        emit_audit(
            AuditEntry(
                actor_user_id=actor.user_id,
                organization_id=organization_id,
                action="deployment.bulk_approve",
                decision="DENY",
                target_kind="Deployment",
                target_id=deployment_id,
                duration_ms=0,
                permissions=("app.approve_deploy",),
                error_code=CoreErrorCode.INTERNAL.value,
                error_message=str(exc),
            )
        )
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.INTERNAL.value,
            str(exc) or "approve failed",
        )

    emit_audit(
        AuditEntry(
            actor_user_id=actor.user_id,
            organization_id=organization_id,
            action="deployment.approve",
            decision="ALLOW",
            target_kind="Deployment",
            target_id=deployment_id,
            duration_ms=0,
            permissions=("app.approve_deploy",),
            extra={"bulk": True},
        )
    )
    return BulkDeploymentResultItem(
        deployment_id=GUID(deployment_id),
        ok=True,
        errors=[],
        deployment=deployment_to_type(deployment, viewer_user_id=viewer_user_id),
    )


def _process_bulk_reject_one(
    *,
    deployment_id: str,
    reason: str,
    actor: Actor,
    viewer_user_id: int | None,
) -> BulkDeploymentResultItem:
    """Single-id reject path, callable from the bulk resolver.

    Mirrors :meth:`LifecycleMutation.reject_deployment` (one nay kills
    the deploy → FAILED). Per-id audit entries emitted directly so the
    history panel renders an entry per affected id."""
    from core.mutations import AuditEntry, emit_audit
    from core.mutations import ErrorCode as CoreErrorCode

    deployment = (
        Deployment.objects.select_related("registered_app", "app_environment", "workload")
        .filter(guid=deployment_id, deleted_at__isnull=True)
        .first()
    )
    if deployment is None:
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.NOT_FOUND.value,
            "deployment not found",
        )
    if deployment.status != Deployment.Status.PENDING_APPROVAL.value:
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PRECONDITION.value,
            f"deployment is in status {deployment.status}, expected pending_approval",
        )
    if actor.user_id and deployment.triggered_by_user_id == actor.user_id:
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PRECONDITION.value,
            "cannot reject your own deployment",
        )
    if actor.user_id and not _is_eligible_approver(deployment.registered_app, user_id=actor.user_id):
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.PERMISSION_DENIED.value,
            "you are not in this app's approver set",
        )

    organization_id = deployment.registered_app.organization_id
    try:
        with transaction.atomic():
            deployment.aborted_reason = reason
            deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
            if deployment.workflow_run_id:
                wf_id = deployment.workflow_run.workflow_id  # type: ignore[union-attr]
                if not signal_workflow(wf_id, "abort"):
                    terminate_workflow(wf_id, reason=f"bulk_reject_deployment: {reason}")
            deployment.transition_to(Deployment.Status.FAILED)
    except Exception as exc:  # noqa: BLE001
        emit_audit(
            AuditEntry(
                actor_user_id=actor.user_id,
                organization_id=organization_id,
                action="deployment.bulk_reject",
                decision="DENY",
                target_kind="Deployment",
                target_id=deployment_id,
                duration_ms=0,
                permissions=("app.approve_deploy",),
                error_code=CoreErrorCode.INTERNAL.value,
                error_message=str(exc),
            )
        )
        return _bulk_item_failure(
            deployment_id,
            ErrorCode.INTERNAL.value,
            str(exc) or "reject failed",
        )

    emit_audit(
        AuditEntry(
            actor_user_id=actor.user_id,
            organization_id=organization_id,
            action="deployment.reject",
            decision="ALLOW",
            target_kind="Deployment",
            target_id=deployment_id,
            duration_ms=0,
            permissions=("app.approve_deploy",),
            extra={"reason": reason, "bulk": True},
        )
    )
    return BulkDeploymentResultItem(
        deployment_id=GUID(deployment_id),
        ok=True,
        errors=[],
        deployment=deployment_to_type(deployment, viewer_user_id=viewer_user_id),
    )


# ---------------------------------------------------------------------------
# Root mutation type
# ---------------------------------------------------------------------------


_DEPLOY_PIPELINE_DISABLED_MSG = (
    "deploy pipeline is disabled in this environment. Cluster adoption "
    "(bringClusterIntoManagement) is unaffected and remains usable. "
    "Flip DEPLOY_PIPELINE_ENABLED to True in /app/admin/constance/ to "
    "re-enable rollouts at runtime, or seed the default via the "
    "FEATURE_DEPLOY_PIPELINE env var on the webservice + worker."
)


def _deploy_pipeline_disabled() -> bool:
    """True when the deploy pipeline gate is off.

    Used at the top of resolvers whose workflows depend on the deploy
    pipeline (start_deployment, promote, rollback, tear_down_preview)
    to short-circuit with a clear error envelope.

    Precedence (mirrors ``_temporal_enabled`` in
    ``astrolift_workflows.client``): the Constance row wins so an
    operator can pause rollouts from the admin without a redeploy;
    the ``FEATURE_DEPLOY_PIPELINE`` env var seeds the Constance
    default on first boot. If Constance is unavailable (DB not
    migrated, plugin disabled) we fall back to the env-backed
    default via ``config.features.is_enabled``.
    """
    try:
        from constance import config as constance_config

        return not bool(getattr(constance_config, "DEPLOY_PIPELINE_ENABLED", True))
    except Exception:
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


@strawberry.input
class SetEnvironmentSettingInput:
    environment_id: GUID
    key: str
    value: str


@strawberry.input
class ClearEnvironmentSettingInput:
    environment_id: GUID
    key: str


@strawberry.type
class LifecycleMutation:
    @strawberry.field
    @mutation_audit(
        action="deployment.start",
        extras=lambda result: _start_extras(result),
    )
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
                commit_message=(input.commit_message or "").strip(),
                commit_author=(input.commit_author or "").strip(),
                pr_number=int(input.pr_number or 0),
                commit_author_avatar_url=(input.commit_author_avatar_url or "").strip(),
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
                _start_deploy_workflow_on_commit(
                    deployment=deployment,
                    workflow_kind="DeployAppWorkflow",
                    workflow_id=wf_id,
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
    @require_permission(Permission.APP_APPROVE_DEPLOY)
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
    @require_permission(Permission.APP_APPROVE_DEPLOY)
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
    @require_permission(Permission.APP_APPROVE_DEPLOY)
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

        results: list[BulkDeploymentResultItem] = []
        succeeded = 0
        failed = 0
        for deployment_id in ids:
            item = _process_bulk_reject_one(
                deployment_id=str(deployment_id),
                reason=reason,
                actor=actor,
                viewer_user_id=actor.user_id,
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
    @require_permission(Permission.APP_DEPLOY)
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
    @require_permission(Permission.APP_DEPLOY)
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
        deployment = (
            Deployment.objects.select_related("registered_app", "app_environment")
            .filter(guid=str(input.id), deleted_at__isnull=True)
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
    @require_permission(Permission.APP_DEPLOY)
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
            RegisteredApp.objects.filter(
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
                AppEnvironment.objects.filter(
                    registered_app=app,
                    name=name,
                    deleted_at__isnull=True,
                )
                .select_related("registered_app", "tenant_cluster", "managed_domain")
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

        if target_env.deploys_paused:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"environment {target_env.name!r} has deploys paused",
                field="targetEnvironmentName",
            )

        source = (
            Deployment.objects.filter(
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

    @strawberry.field
    @mutation_audit(action="preview.extend_ttl")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def extend_preview_ttl(
        self, info: Info, input: ExtendPreviewTtlInputGql
    ) -> MutationResultType[PreviewEnvironmentType]:
        """Push the preview's auto-teardown out by ``days`` (#431).

        ``days`` must be one of ``{1, 7, 30}``. The new ``ttl_until``
        is capped at +30 days from now even on a chained set of
        extensions — see :meth:`PreviewEnvironment.extend_ttl`.

        Returns the updated preview type with the new ``ttl_until``
        echoed back so the UI can refresh the countdown without a
        second round-trip. No workflow is started — the GC sweep
        consults ``ttl_until`` on its own cadence.
        """
        from astrolift_lifecycle.models.preview_environment import (
            PREVIEW_TTL_EXTEND_DAYS,
        )

        if input.days not in PREVIEW_TTL_EXTEND_DAYS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"days must be one of {list(PREVIEW_TTL_EXTEND_DAYS)}; got {input.days}",
            )
        preview = (
            PreviewEnvironment.objects.select_related(
                "registered_app",
                "registered_app__organization",
                "registered_app__default_tenant_cluster",
                "app_environment__tenant_cluster",
            )
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if preview is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "preview environment not found")
        if preview.status == PreviewEnvironment.Status.TORN_DOWN.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot extend a torn-down preview",
            )

        try:
            preview.extend_ttl(days=input.days)
        except ValueError as exc:
            # Model layer mirrors the resolver's validation — keep
            # both so programmatic callers (workflows, fixtures) hit
            # the same guard.
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))

        preview.save(update_fields=["ttl_until", "updated_at", "version"])
        return gql_success(preview_to_type(preview))

    @strawberry.field
    @mutation_audit(action="preview.create_manual")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def create_preview_environment(
        self,
        info: Info,
        input: CreatePreviewEnvironmentInput,
    ) -> MutationResultType[PreviewEnvironmentType]:
        """Manually spin up a preview environment from a branch (#751).

        Companion to the auto path (PR webhook → BuildPreviewWorkflow).
        Operators hit this from the Previews page when they want to
        burn a preview for a long-lived branch, a force-pushed fork, or
        a draft PR the webhook isn't watching.

        Idempotent on ``(app, branch)``: a re-fire when an active
        manual preview already exists for the branch returns that row
        as success (matches ``addAppDomain``'s idempotent re-add).

        Refuses to start when:

        * The app's ``preview_enabled`` flag is off (operator already
          opted out of previews at the app level).
        * The deploy pipeline feature is gated off.
        * The app isn't bound to a tenant cluster (no place to land
          the namespace).
        * ``branch`` doesn't shake out to a non-empty RFC 1123 label
          (k8s namespace constraint).
        """
        if _deploy_pipeline_disabled():
            return gql_failure(ErrorCode.PRECONDITION.value, _DEPLOY_PIPELINE_DISABLED_MSG)

        app = (
            RegisteredApp.objects.filter(slug=input.app_slug, deleted_at__isnull=True)
            .select_related("organization", "default_tenant_cluster")
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        if not app.preview_enabled:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has preview environments disabled",
            )

        branch = (input.branch or "").strip()
        if not branch:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "branch is required",
                field="branch",
            )

        branch_slug = _slugify_branch(branch)
        if not branch_slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"branch {branch!r} does not contain any RFC 1123 label characters",
                field="branch",
            )

        cluster = app.default_tenant_cluster
        if cluster is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has no default tenant cluster bound — provision one first",
            )

        # Idempotent re-fire: existing active manual preview for the
        # same (app, branch) returns success rather than racing a
        # second namespace through the unique index.
        existing = (
            PreviewEnvironment.objects.select_related("registered_app")
            .filter(
                registered_app=app,
                branch=branch,
                is_manual=True,
                deleted_at__isnull=True,
            )
            .first()
        )
        if existing is not None:
            return gql_success(preview_to_type(existing))

        environment_name = (input.environment_name or "").strip() or f"preview-{branch_slug}"
        org_slug = (
            getattr(app.organization, "slug", None) or getattr(app.organization, "name", "") or "org"
        ).lower()
        namespace = _manual_preview_namespace(org_slug=org_slug, app_slug=app.slug, branch_slug=branch_slug)
        # Hostname follows the platform's preview wildcard convention
        # but keyed on the branch slug (no PR number). The cluster's
        # ingress-target resolution happens at apply time in the
        # BuildPreviewWorkflow; here we just record the stable name
        # the operator-facing surfaces (#751 FE, audit log) cite.
        from astrolift_clusters.models import resolve_managed_domain

        _managed_domain = resolve_managed_domain(app.organization, for_preview=True)
        # ``preview-<branch>.<app>.<org>`` plus the install's managed zone
        # when one exists; without a zone, stop at the org slug rather than
        # repeating it (the old ``... or org_slug`` fallback doubled it).
        _base = f"preview-{branch_slug}.{app.slug}.{org_slug}"
        _zone = getattr(_managed_domain, "zone", None)
        hostname = (f"{_base}.{_zone}" if _zone else _base).lower()

        with transaction.atomic():
            env = AppEnvironment.objects.create(
                registered_app=app,
                tenant_cluster=cluster,
                name=environment_name,
                url=f"https://{hostname}",
                managed_domain=_managed_domain,
                required_approvals=0,
            )
            preview = PreviewEnvironment.objects.create(
                registered_app=app,
                pr_number=None,
                branch=branch,
                is_manual=True,
                status=PreviewEnvironment.Status.BUILDING,
                hostname=hostname,
                namespace=namespace,
                app_environment=env,
            )

        actor = _actor_from_request(info)
        tenant = get_current_tenant()
        handle = start_workflow(
            "BuildPreviewWorkflow",
            args=[
                BuildPreviewInput(
                    preview_environment_id=preview.pk,
                    actor=actor,
                ),
            ],
            workflow_id=_build_preview_workflow_id(str(preview.guid)),
        )
        if handle.enqueued:
            _record_workflow_run(
                kind="BuildPreviewWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id,
                organization_id=tenant.organization_id if tenant else None,
                registered_app_id=preview.registered_app_id,
                app_environment_id=env.pk,
                actor=actor,
            )

        return gql_success(preview_to_type(preview))

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
    @mutation_audit(action="app.domain.add_wildcard")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def add_wildcard_domain(
        self,
        info: Info,
        input: AddWildcardDomainInput,
    ) -> MutationResultType[AppDomainType]:
        """Add a wildcard custom domain (``*.hostname``) for an app (#753).

        Same handshake shape as ``addAppDomain`` but pins the row as
        ``is_wildcard=True``, forces ``validation_method=dns_01`` (CA
        policy on wildcards), and persists an optional
        ``sni_cert_ref`` so the renderer can pick a specific cert
        for SNI on this hostname.

        Idempotent on hostname: an existing active row for the same
        apex bound to the same app is returned as success (matches
        ``addAppDomain``). A row bound to a different app returns
        CONFLICT — wildcard ownership is exclusive per apex.
        """
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        host = (input.hostname or "").strip().lower()
        if not host or "." not in host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be a fully-qualified domain (apex; the platform adds the ``*.`` prefix)",
                field="hostname",
            )
        # Defensive: refuse a leading ``*.`` — the apex is what we
        # store, and the wildcard SAN is implied. Otherwise we'd end
        # up persisting ``*.example.com`` as the hostname and the
        # renderer would emit ``*.*.example.com`` SANs.
        if host.startswith("*."):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be the apex (e.g. ``example.com``); the wildcard ``*.`` prefix is implied",
                field="hostname",
            )

        method = (input.validation_method or "dns_01").lower()
        if method != "dns_01":
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "wildcard domains must use dns_01 validation (CA policy); HTTP-01 / DNS-TXT can't authorize wildcards",
                field="validationMethod",
            )

        sni_cert_ref = (input.sni_cert_ref or "").strip()

        existing = CustomDomain.objects.filter(hostname=host, deleted_at__isnull=True).first()
        if existing is not None:
            if existing.registered_app_id != app.id:
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"domain {host!r} already bound to another app",
                    field="hostname",
                )
            # Idempotent re-add: same app, same hostname. Promote to
            # wildcard if the existing row was a single-host (so a
            # caller upgrading from ``addAppDomain`` → ``addWildcard...``
            # converges on the wildcard contract). sni_cert_ref is
            # rewritten when the caller passes a non-empty value;
            # empty leaves the previous pin untouched (the resolver
            # has no surface for clearing a pin — that's intentional;
            # operators clear via ``removeAppDomain`` + re-add).
            update_fields: list[str] = []
            if not existing.is_wildcard:
                existing.is_wildcard = True
                update_fields.append("is_wildcard")
            if existing.validation_method != method:
                existing.validation_method = method
                update_fields.append("validation_method")
            if sni_cert_ref and existing.sni_cert_ref != sni_cert_ref:
                existing.sni_cert_ref = sni_cert_ref
                update_fields.append("sni_cert_ref")
            if update_fields:
                update_fields.extend(["updated_at", "version"])
                existing.save(update_fields=update_fields)
            return gql_success(app_domain_to_type(existing))

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
            is_wildcard=True,
            sni_cert_ref=sni_cert_ref,
        )
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

    @strawberry.field
    @mutation_audit(action="app.domain.redirects_updated")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_domain_redirects(
        self,
        info: Info,
        input: SetDomainRedirectsInput,
    ) -> MutationResultType[AppDomainType]:
        """Replace the redirect-rule set on a custom domain (#742).

        The FE renders the rules table as a single editable list; this
        mutation accepts the new full set and atomically:

        1. Soft-deletes every active row on the domain (preserves the
           audit trail via ``deleted_at``).
        2. Bulk-creates the new rows from ``input.rules``.

        The renderer reads the live set the next time the cluster's
        ingress is reconciled — the per-driver wiring (nginx
        ``server-snippet``, traefik middleware, ALB redirect-action)
        translates each rule into the cloud's redirect primitive.
        """
        valid_kinds = {k.value for k in DomainRedirectRule.Kind}
        valid_statuses = {s.value for s in DomainRedirectRule.HttpStatus}
        for idx, row in enumerate(input.rules):
            if row.kind not in valid_kinds:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].kind must be one of {sorted(valid_kinds)}",
                    field="rules",
                )
            if int(row.http_status) not in valid_statuses:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].httpStatus must be one of {sorted(valid_statuses)}",
                    field="rules",
                )
            # ``custom`` and ``alias`` rules need an explicit destination
            # — the renderer has nothing to derive from for these kinds.
            # ``http_to_https`` / ``apex_to_www`` / ``www_to_apex`` are
            # well-known and the renderer derives the target from the
            # parent domain's hostname.
            if (
                row.kind
                in (
                    DomainRedirectRule.Kind.CUSTOM.value,
                    DomainRedirectRule.Kind.ALIAS.value,
                )
                and not (row.destination_url or "").strip()
            ):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].destinationUrl is required for kind={row.kind!r}",
                    field="rules",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain_qs = CustomDomain.objects.filter(
            guid=str(input.domain_id),
            deleted_at__isnull=True,
        )
        if org_id is not None:
            domain_qs = domain_qs.filter(registered_app__organization_id=org_id)
        domain = domain_qs.select_related("registered_app").first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )

        now = timezone.now()
        with transaction.atomic():
            DomainRedirectRule.objects.filter(
                custom_domain=domain,
                deleted_at__isnull=True,
            ).update(
                deleted_at=now,
                updated_at=now,
            )
            DomainRedirectRule.objects.bulk_create(
                [
                    DomainRedirectRule(
                        custom_domain=domain,
                        kind=row.kind,
                        source_pattern=(row.source_pattern or "").strip(),
                        destination_url=(row.destination_url or "").strip(),
                        http_status=int(row.http_status),
                        preserve_query_string=bool(row.preserve_query_string),
                        priority=int(row.priority),
                    )
                    for row in input.rules
                ],
            )
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.path_routes_updated")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_domain_path_routes(
        self,
        info: Info,
        input: SetDomainPathRoutesInput,
    ) -> MutationResultType[AppDomainType]:
        """Replace the path-routing rule set on a custom domain (#740).

        Accepts the new full route set and atomically:

        1. Soft-deletes every active row on the domain.
        2. Bulk-creates the new rows from ``input.routes``.

        ``path_prefix`` must start with ``/``.  ``target_port`` must be
        in [1, 65535].  ``target_workload_slug`` is validated for
        non-emptiness here; workload existence is checked by the renderer
        at reconcile time.
        """
        for idx, row in enumerate(input.routes):
            if not (row.path_prefix or "").startswith("/"):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].pathPrefix must start with '/'",
                    field="routes",
                )
            if not (1 <= int(row.target_port) <= 65535):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].targetPort must be in [1, 65535]",
                    field="routes",
                )
            if not (row.target_workload_slug or "").strip():
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].targetWorkloadSlug is required",
                    field="routes",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain_qs = CustomDomain.objects.filter(
            guid=str(input.domain_id),
            deleted_at__isnull=True,
        )
        if org_id is not None:
            domain_qs = domain_qs.filter(registered_app__organization_id=org_id)
        domain = domain_qs.select_related("registered_app").first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")

        now = timezone.now()
        with transaction.atomic():
            DomainPathRoute.objects.filter(
                custom_domain=domain,
                deleted_at__isnull=True,
            ).update(
                deleted_at=now,
                updated_at=now,
            )
            DomainPathRoute.objects.bulk_create(
                [
                    DomainPathRoute(
                        custom_domain=domain,
                        path_prefix=row.path_prefix.strip(),
                        target_workload_slug=row.target_workload_slug.strip(),
                        target_port=int(row.target_port),
                        strip_prefix=bool(row.strip_prefix),
                        priority=int(row.priority),
                    )
                    for row in input.routes
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

        from astrolift_lifecycle.deploy_tokens import PLAINTEXT_PREFIX

        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        # #449: mint with the canonical ``alft_dt_`` prefix so
        # ``verify_token`` + ``DeployTokenAuthMiddleware`` accept the
        # round-trip. Previously hard-coded ``alfdt_`` mismatched the
        # verifier and silently broke CI runners.
        plaintext = PLAINTEXT_PREFIX + secrets_lib.token_urlsafe(32)
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
    @requires_elevation(action_label="app.deploy_token.rotate")
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

        from astrolift_lifecycle.deploy_tokens import (
            PLAINTEXT_PREFIX,
            rotation_grace_seconds_from_constance,
        )

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
        # #449: canonical ``alft_dt_`` prefix — see ``create_deploy_token``.
        plaintext = PLAINTEXT_PREFIX + secrets_lib.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        # Park the previous hash for the Constance-tunable grace
        # window so CI runners holding the old token keep working
        # until they're updated. The UI surfaces this exact value
        # on the rotate-confirm dialog (#425).
        grace_seconds = rotation_grace_seconds_from_constance()
        now = datetime.now(tz=UTC)
        token.previous_token_hash = token.token_hash
        token.previous_token_expires_at = now + timedelta(seconds=grace_seconds)
        token.token_hash = digest
        token.token_last_4 = plaintext[-4:]
        token.last_rotated_at = now
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
                rotation_grace_seconds=grace_seconds,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.deploy_token.revoke")
    @requires_elevation(action_label="app.deploy_token.revoke")
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
                ("deploy workflow requires approval — use startDeployment with approver flow"),
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
        sealable value. The rotation is *immediate* (no grace window):
        the operator clicked an explicit ``Push & rotate`` affordance
        and was warned in the confirm dialog, so the old hash is
        revoked in the same transaction as the new plaintext lands in
        GitHub. In-flight CI runs holding the previous token will
        need to be re-kicked.
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
    # CI secrets validate (#693): read-only probe of the app's source
    # repo Actions secrets, reporting which of the canonical five are
    # set + whether they look 'current' relative to the platform's last
    # push.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.ci.validate_secrets")
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def validate_astrolift_ci_secrets(
        self,
        info: Info,
        input: ValidateAstroliftCiSecretsInput,
    ) -> MutationResultType[ValidateAstroliftCiSecretsPayload]:
        """Probe the app's source repo for the five Astrolift CI secrets
        (#693).

        Read-only — never writes; permission gate is ``app.read`` because
        an operator who can see the app should be allowed to verify the
        CI setup is healthy, even if they couldn't push it.  Auth is the
        viewer's personal GitHub OAuth connection (per #395) — same
        scoping as the push side so the validate call doesn't surface
        secrets visible only to an org-level PAT.

        Surfaces non-GitHub source kinds + missing connections through
        the same PRECONDITION envelope shape the push side returns;
        ``UNSUPPORTED_SOURCE`` is the explicit code for non-GitHub.
        """
        from astrolift_scm.services.secrets import validate_astrolift_ci_secrets

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

        result = validate_astrolift_ci_secrets(app, viewer_user=viewer)
        if not result.ok:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error_message or "validate failed",
            )
        return gql_success(
            ValidateAstroliftCiSecretsPayload(
                repo=app.source_repo,
                results=[
                    CiSecretValidationType(
                        secret_name=r.secret_name,
                        is_set=r.is_set,
                        is_current=r.is_current,
                        updated_at=r.updated_at,
                    )
                    for r in result.results
                ],
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

    # ----------------------------------------------------------------
    # Danger-zone hard deregister (#392)
    # ----------------------------------------------------------------
    #
    # Fires ``DeregisterAppWorkflow`` (#392) with a deterministic
    # workflow id (``DeregisterAppWorkflow-<app-guid>``) so re-firing
    # the mutation joins the existing run rather than starting a
    # parallel teardown — partial failures are resumable. The
    # ``confirm_name`` field is a muscle-memory guard: the operator
    # must type the app's name verbatim to enable the destructive
    # click in the FE modal.

    @strawberry.field
    @mutation_audit(action="app.deregister")
    @requires_elevation(action_label="app.deregister")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def deregister_astrolift_app(
        self,
        info: Info,
        input: DeregisterAppInput,
    ) -> MutationResultType[DeregisterAppPayload]:
        """Hard-deregister an app and tear down every per-app cloud
        resource (#392).

        Validates the typed-confirmation guard (``confirm_name`` must
        equal the app's ``name``) before kicking the workflow off.
        Returns the workflow id immediately; the workflow does the
        per-resource teardown async and reports each step's outcome
        on its result envelope.

        Re-firing the same mutation joins the existing workflow run
        via Temporal de-dup on the workflow id — operators retry on
        partial failure by clicking Deregister again."""
        # NOTE: ``start_workflow`` is imported at module scope so the
        # ``temporal_recorder`` fixture's monkeypatch on
        # ``astrolift_lifecycle.schema.mutations.start_workflow``
        # actually intercepts the call. Local re-imports would
        # silently bypass the recorder.
        from astrolift_workflows.inputs import (
            Actor as _Actor,
        )
        from astrolift_workflows.inputs import (
            DeregisterAppInput as _DeregisterInput,
        )

        slug = (input.app_slug or "").strip()
        if not slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "app_slug is required",
                field="appSlug",
            )

        app = (
            RegisteredApp.objects.filter(slug=slug, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {slug!r} not found",
                field="appSlug",
            )

        confirm = (input.confirm_name or "").strip()
        if confirm != app.name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "confirm_name must match the app's name exactly",
                field="confirmName",
            )

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        actor = _Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(
                getattr(user, "email", "") or getattr(user, "username", ""),
            ),
        )

        workflow_id = f"DeregisterAppWorkflow-{app.guid}"
        # Deterministic workflow id makes re-firing the mutation
        # converge on the existing run; Temporal de-dups by id.
        start_workflow(
            "DeregisterAppWorkflow",
            args=[
                _DeregisterInput(
                    registered_app_id=app.pk,
                    actor=actor,
                    # Operator opt-in (#1000); defaults keep + snapshot.
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=workflow_id,
        )

        return gql_success(
            DeregisterAppPayload(
                workflow_id=workflow_id,
                still_live_resources=[],
            ),
        )

    # ----------------------------------------------------------------
    # Grace-period teardown cancel (#436 B)
    # ----------------------------------------------------------------
    #
    # Sends the ``cancel_teardown`` signal to a running
    # ``DeregisterAppWorkflow``. If the signal lands within the 5-min
    # grace window before the workflow's first destructive activity
    # fires, the teardown short-circuits and no per-app resource is
    # touched. After the window elapses the signal is a no-op — the
    # destructive activities are monotonic by design (a half-applied
    # teardown is not rolled back; the operator retries instead).
    # Gated on ``app.delete`` — same permission the deregister
    # mutation requires.

    @strawberry.field
    @mutation_audit(action="app.deregister.cancel")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def cancel_astrolift_deregister(
        self,
        info: Info,
        input: CancelDeregisterInput,
    ) -> MutationResultType[CancelDeregisterPayload]:
        """Cancel a pending deregister within the 5-min grace window."""
        wf_id = (input.workflow_id or "").strip()
        if not wf_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "workflow_id is required",
                field="workflowId",
            )
        # Defence-in-depth: the deterministic deregister workflow id
        # carries the app guid; tenant scoping on the resolver entry
        # would catch a cross-org request, but pin the id shape here so
        # an operator can't accidentally cancel an unrelated workflow.
        if not wf_id.startswith("DeregisterAppWorkflow-"):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "workflow_id must reference a DeregisterAppWorkflow run",
                field="workflowId",
            )
        # Resolve + scope-check the app guid embedded in the workflow id.
        # The mutation surface is tenant-scoped, so a sibling-org guid
        # short-circuits to NOT_FOUND rather than leaking row counts.
        app_guid = wf_id[len("DeregisterAppWorkflow-") :]
        app = RegisteredApp.objects.filter(guid=app_guid).select_related("organization").first()
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no deregister workflow found for {wf_id!r}",
                field="workflowId",
            )

        reason = (input.reason or "").strip()
        delivered = signal_workflow(wf_id, "cancel_teardown", reason)
        # Best-effort: signal failure (workflow not running, Temporal
        # disabled in dev, etc.) is surfaced as ok=True with
        # ``signal_delivered=False`` so the FE can render the
        # "couldn't reach" copy instead of a generic error toast.
        return gql_success(
            CancelDeregisterPayload(
                workflow_id=wf_id,
                signal_delivered=bool(delivered),
            ),
        )

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
    # Environment-level key/value settings overrides (#744)
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.env.setting.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_environment_setting(
        self,
        info: Info,
        input: SetEnvironmentSettingInput,
    ) -> MutationResultType[EnvironmentSettingType]:
        tenant = get_current_tenant()
        env = (
            AppEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.environment_id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")
        if env.registered_app.organization_id != tenant.organization_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")

        key = (input.key or "").strip()
        if not key:
            return gql_failure(ErrorCode.VALIDATION.value, "key must not be empty", field="key")

        actor = info.context.request.user
        existing = EnvironmentSetting.objects.filter(
            app_environment=env, key=key, deleted_at__isnull=True
        ).first()
        if existing is not None:
            existing.value = input.value
            existing.updated_by = actor
            existing.save(update_fields=["value", "updated_by", "updated_at", "version"])
            return gql_success(env_setting_to_type(existing))

        setting = EnvironmentSetting.objects.create(
            app_environment=env,
            key=key,
            value=input.value,
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(env_setting_to_type(setting))

    @strawberry.field
    @mutation_audit(action="app.env.setting.clear")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def clear_environment_setting(
        self,
        info: Info,
        input: ClearEnvironmentSettingInput,
    ) -> MutationResultType[EnvironmentSettingType]:
        from django.utils import timezone

        tenant = get_current_tenant()
        env = (
            AppEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.environment_id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")
        if env.registered_app.organization_id != tenant.organization_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")

        key = (input.key or "").strip()
        if not key:
            return gql_failure(ErrorCode.VALIDATION.value, "key must not be empty", field="key")

        actor = info.context.request.user
        setting = EnvironmentSetting.objects.filter(
            app_environment=env, key=key, deleted_at__isnull=True
        ).first()
        if setting is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"no active setting for key {key!r}", field="key")

        setting.deleted_at = timezone.now()
        setting.deleted_by = actor
        setting.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])
        return gql_success(env_setting_to_type(setting))

    # ----------------------------------------------------------------
    # #801 — run_task: operator-initiated execution of a task workload.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="task.run")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def run_task(
        self,
        info: Info,
        input: "RunTaskInput",
    ) -> "MutationResultType[TaskRunPayloadType]":
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

        app = (
            RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
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
            trigger_kind=TaskRun.TriggerKind.MANUAL,
            triggered_by_user=actor if actor.is_authenticated else None,
            command=input.command or [],
            status=TaskRun.Status.PENDING,
        )
        return gql_success(task_run_to_payload(run))

    @strawberry.field
    @mutation_audit(
        action="orphan.reap",
        target=lambda root, info, input: ("orphan", input.reap_key),
    )
    @require_permission(Permission.CLUSTER_MANAGE)
    def reap_cloud_orphan(
        self, info: Info, input: ReapCloudOrphanInput
    ) -> MutationResultType[ReapCloudOrphanPayload]:
        """Reap one detected cloud orphan THROUGH the provider drivers (#995).

        Install-wide operator action (orphans have no org owner) — gated on
        ``CLUSTER_MANAGE`` and deliberately NOT ``@tenant_scoped``. The reaper
        re-checks ownership and refuses anything that still has a live owner;
        only detection-proven orphans are reapable. Idempotent: an already-gone
        resource returns success so a re-run converges. ``force_destroy``
        carries through to the driver for deletion-protected resources.

        Reaping never issues a raw cloud API delete — it reuses the same
        idempotent driver deprovision path teardown uses (#1034).
        """
        from astrolift_operations.services.orphan_reaper import ALL_KINDS, reap_orphan

        kind = (input.kind or "").strip()
        reap_key = (input.reap_key or "").strip()
        if kind not in ALL_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown orphan kind {kind!r}",
                field="kind",
            )
        if not reap_key:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reap_key is required",
                field="reapKey",
            )

        result = reap_orphan(
            kind=kind,
            reap_key=reap_key,
            cluster_slug=(input.cluster_slug or "").strip(),
            force_destroy=bool(input.force_destroy),
        )
        if not result.ok:
            # A refusal (live owner) is a precondition failure, not a crash.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.message,
                field="reapKey",
            )
        return gql_success(
            ReapCloudOrphanPayload(
                kind=result.kind,
                identifier=result.identifier,
                reaped=not result.already_gone,
                already_gone=result.already_gone,
                message=result.message,
            )
        )


# ---------------------------------------------------------------------------
# #389 input / payload types — force-redeploy recovery. Defined at
# module scope so Strawberry picks them up; appended at the tail so
# sibling agents touching this file land on adjacent line ranges
# instead of overlapping ones.
# ---------------------------------------------------------------------------


@strawberry.input
class ForceRedeployInput:
    """Input for the force-redeploy recovery mutation (#389).

    ``app_slug`` resolves the RegisteredApp; ``environment_name``
    scopes the recovery to one env (None → every env on the app).
    ``confirm_slug`` must equal ``app_slug`` — muscle-memory guard
    against accidental destructive clicks. The FE confirmation modal
    binds the typed-slug field to this argument."""

    app_slug: str
    environment_name: str | None = None
    confirm_slug: str


@strawberry.type(name="AstroliftForceRedeployPayload")
class ForceRedeployPayload:
    """Read-back for a force-redeploy attempt (#389).

    ``deployments_cancelled`` is the count of ``Deployment`` rows
    transitioned out of an in-flight status (PENDING_APPROVAL /
    PENDING / DEPLOYING / REDEPLOYING) into FAILED.

    ``k8s_objects_deleted`` is the count of stub manifests the cluster
    driver accepted on the delete pass. The driver treats not-found
    as ok per the SDK contract — this is the count of stubs that did
    not surface an error, not necessarily the count of objects that
    actually existed.

    ``workflow_dispatched`` is True when the CI workflow-dispatch call
    succeeded. ``run_url`` is the host's runs-page URL when present;
    ``dispatch_message`` carries the failure message when False so
    the FE toast surfaces the partial-success shape (cancellation +
    delete counts are still applied)."""

    deployments_cancelled: int
    k8s_objects_deleted: int
    workflow_dispatched: bool
    run_url: str | None
    dispatch_message: str | None


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


# ---------------------------------------------------------------------------
# #390 input / payload types — run-scheduled-job-once. Kept at the
# tail so sibling agents touching this file land on adjacent line
# ranges instead of overlapping ones.
# ---------------------------------------------------------------------------


@strawberry.input
class RunJobOnceInput:
    """Input for the manual one-shot run of a manifest-declared CronJob
    (#390).

    ``app_slug`` resolves the RegisteredApp; ``environment_name`` picks
    the target env (which carries the tenant cluster the Job lands on);
    ``job_slug`` is the cronjob workload's slug from
    ``astrolift.toml`` — slug rather than guid so the FE button calls
    the mutation without an extra lookup."""

    app_slug: str
    environment_name: str
    job_slug: str


@strawberry.type(name="AstroliftRunJobOncePayload")
class RunJobOncePayload:
    """Read-back for a successful manual job-run dispatch (#390).

    ``run_name`` is the freshly-applied k8s Job name
    (``<job_slug>-manual-<8hex>``); operators paste it verbatim into
    ``kubectl logs job/<name>`` when diagnosing outside the UI.
    ``namespace`` is the per-app namespace the Job landed in.
    ``logs_url`` points at the existing scheduled-job-runs surface
    where the run materializes once the cluster reports it."""

    run_name: str
    namespace: str
    logs_url: str | None


# ---------------------------------------------------------------------------
# #392 input / payload types — danger-zone hard deregister. Defined at
# module scope so Strawberry picks them up; appended at the tail so
# sibling agents touching this file land on adjacent line ranges
# instead of overlapping ones.
# ---------------------------------------------------------------------------


@strawberry.input
class DeregisterAppInput:
    """Input for the hard-deregister + full teardown mutation (#392).

    ``app_slug`` resolves the RegisteredApp; ``confirm_name`` must
    equal the app's ``name`` verbatim — a muscle-memory guard against
    accidental destructive clicks. The FE confirmation modal binds
    the typed-name field to this argument so the destructive button
    stays disabled until the operator types the exact name.

    Keep-vs-wipe is an explicit operator choice, defaulting to the SAFE
    corner (#1000): with both flags False (the default) the child
    managed-service deprovisions take an RDS final snapshot and retain
    S3 contents — recoverable. ``delete_data=True`` + ``force_destroy=True``
    is the danger-zone four-corner that irreversibly wipes persistent
    state. ``force_destroy`` without ``delete_data`` is a no-op per the
    driver matrix."""

    app_slug: str
    confirm_name: str
    delete_data: bool = False
    force_destroy: bool = False


@strawberry.type(name="AstroliftDeregisterAppPayload")
class DeregisterAppPayload:
    """Read-back for a deregister kickoff or resume (#392).

    ``workflow_id`` is the deterministic Temporal id
    (``DeregisterAppWorkflow-<app-guid>``) the mutation fired. Re-
    firing the mutation joins the existing run via Temporal de-dup
    on this id so partial-failure resume is a one-click retry.

    ``still_live_resources`` is empty on the initial kickoff (the
    workflow runs async, so the mutation returns before any
    teardown step has completed). The list is populated when an
    operator polls the workflow result on a resume path — the
    workflow's ``WorkflowResult.data["still_live_resources"]``
    carries the resource keys that failed on the prior pass."""

    workflow_id: str
    still_live_resources: list[str]


# ---------------------------------------------------------------------------
# #436 B — grace-period cancel for a pending deregister teardown.
# Defined at module scope so Strawberry resolves the types alongside the
# resolver. Appended after the existing deregister types so sibling
# agents touching this region land on adjacent ranges.
# ---------------------------------------------------------------------------


@strawberry.input
class CancelDeregisterInput:
    """Input for the grace-period cancel mutation (#436 B).

    ``workflow_id`` is the deterministic id the deregister mutation
    returned. ``reason`` is a free-form audit note (the operator's
    why-cancelled context) — surfaces in the workflow log + the
    mutation_audit event extras."""

    workflow_id: str
    reason: str | None = None


@strawberry.type(name="AstroliftCancelDeregisterPayload")
class CancelDeregisterPayload:
    """Read-back for a cancel-deregister attempt (#436 B).

    ``signal_delivered`` is False when the Temporal client couldn't
    reach the workflow (workflow already finished, Temporal disabled
    in dev, transport error). The FE renders the "couldn't reach"
    copy in that case so the operator knows whether their cancel
    landed."""

    workflow_id: str
    signal_delivered: bool


# ---------------------------------------------------------------------------
# #693 input / payload types — validate the five Astrolift CI secrets on
# the app's source repo.
# ---------------------------------------------------------------------------


@strawberry.input
class ValidateAstroliftCiSecretsInput:
    """Probe the app's source repo's Actions secrets and report which of
    the five canonical Astrolift names are set (#693).

    Slug rather than guid for parity with ``pushAstroliftCiSecretsToRepo``."""

    app_slug: str


@strawberry.type(name="AstroliftCiSecretValidation")
class CiSecretValidationType:
    """One row of the validate-CI-secrets report (#693).

    The FE renders one chip per row — green check on ``isSet=True &&
    isCurrent in (True, None)``, amber on ``isSet=True && isCurrent=False``
    (set but stale), red X on ``isSet=False``.

    ``updatedAt`` is the GitHub-provided ISO-8601 timestamp; the FE
    formats it as 'updated 2 days ago' next to the chip."""

    secret_name: str
    is_set: bool
    is_current: bool | None
    """None means 'unknown' — the platform has either never pushed
    secrets to this repo (no baseline to compare) or GitHub returned
    no usable updated_at on the row.  FE renders these as a green
    check with no freshness annotation."""

    updated_at: str


@strawberry.type(name="AstroliftValidateCiSecretsPayload")
class ValidateAstroliftCiSecretsPayload:
    """Read-back for ``validateAstroliftCiSecrets`` (#693).

    ``repo`` is the ``owner/name`` of the target repo, mirroring the
    push payload for the FE's repo-label affordance."""

    repo: str
    results: list[CiSecretValidationType]


# ---------------------------------------------------------------------------
# #801 RunTaskInput — operator-initiated one-shot task execution.
# Defined at module scope so Strawberry discovers it before the mutation
# method references it via the forward-reference string.
# ---------------------------------------------------------------------------


@strawberry.input
class RunTaskInput:
    """Input for the ``runTask`` mutation (#801).

    ``app_slug`` + ``workload_slug`` identify the ``kind: task``
    workload to execute.  ``environment_name`` picks the target
    cluster; when omitted the app's default environment is used.
    ``command`` overrides the container command so the same workload
    can run one-off variations (e.g. ``["python", "manage.py",
    "migrate", "--database", "secondary"]``) without a manifest edit.
    Pass an empty list (the default) to run the declared command as-is.
    """

    app_slug: str
    workload_slug: str
    environment_name: str | None = None
    command: list[str] = strawberry.field(default_factory=list)


# ---------------------------------------------------------------------------
# #995 input / payload types — orphan reaper. Defined at module scope so
# Strawberry discovers them before the mutation method's forward-reference
# string resolves them; appended at the tail per the file convention.
# ---------------------------------------------------------------------------


@strawberry.input
class ReapCloudOrphanInput:
    """Input for ``reapCloudOrphan`` (#995).

    ``kind`` + ``reap_key`` come straight off a ``scanCloudOrphans`` result
    row (``CloudOrphanType.kind`` / ``.reap_key``). ``cluster_slug`` is the
    optional hint of which managed cluster's driver should reap a
    cloud-enumerated resource (IAM role); the reaper falls back to any
    capable managed cluster when omitted. ``force_destroy`` removes
    deletion-protection where a resource has it.
    """

    kind: str
    reap_key: str
    cluster_slug: str | None = None
    force_destroy: bool = False


@strawberry.type
class ReapCloudOrphanPayload:
    """Read-back for a reaped orphan (#995).

    ``reaped`` is True when the driver actually deprovisioned the resource;
    ``already_gone`` is True when the resource was found to be already absent
    (an idempotent no-op) — both are successful outcomes.
    """

    kind: str
    identifier: str
    reaped: bool
    already_gone: bool
    message: str
