"""Module constants and helper functions for the mutation package."""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql.errors import MutationErrorType
from astrolift_lifecycle.models import (
    AppEnvironment,
    CustomDomain,
    Deployment,
)
from astrolift_lifecycle.schema.mutations.types import (
    BulkDeploymentResultItem,
)
from astrolift_lifecycle.schema.types import (
    DeploymentType,
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
)
from config.features import Feature, is_enabled
from core.mutations import ErrorCode
from core.tenancy import get_current_tenant

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

            # Emit the app-scoped deploy event so the per-app events
            # feed (which filters on ``registered_app_id``) reflects the
            # deploy (#1111). This is the single funnel every deploy
            # dispatch runs through, so one emit here covers the deploy
            # mutation, approval-quorum start, cron, and force-redeploy
            # paths. Best-effort — the event write is swallowed on
            # failure and must not break the dispatch.
            from core.events import Event

            Event.emit(
                "deploy.started",
                payload={
                    "deployment_guid": str(deployment.guid),
                    "workflow_kind": workflow_kind,
                    "workflow_id": handle.workflow_id,
                    "trigger_kind": deployment.trigger_kind,
                },
                resource_kind="deployment",
                resource_id=str(deployment.guid),
                organization_id=organization_id,
                registered_app_id=registered_app_id,
            )

    transaction.on_commit(_start)


def _resolve_app_env(
    app_slug: str, environment_name: str, *, org_id: int | None
) -> tuple[RegisteredApp, AppEnvironment] | None:
    # Org-scope the app lookup to the caller's tenant — slugs are unique
    # only within an org, so an unscoped fetch lets a caller drive a deploy
    # against a sibling org's app. Fails closed (None) when org_id is None,
    # since organization_id is a non-null FK (#1183).
    app = (
        RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
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


def _manifest_job_agents_only(app: RegisteredApp) -> bool:
    """True when the app's saved manifest declares workloads and ALL are
    Job-family agents — ``kind=agent`` with ``run_family != "service"``
    (#1093, #1027).

    Only a task-family agent renders nothing on the deploy pipeline (it
    is dispatched as a one-shot K8s Job by the agent spine). A
    ``service``-family agent IS deployable — the renderer emits a
    standing Deployment (+Service/HPA) for it, so it must flow through
    startDeployment like any other workload (#1012). Refuse only when
    the manifest would yield zero deployable resources. Best-effort: an
    empty or unparseable manifest falls through to the pre-flight gates,
    which already produce clear errors for those cases."""
    try:
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import parse_raw

        manifest = normalize(parse_raw(app.manifest_raw or ""), defaults=NormalizationDefaults())
    except Exception:  # noqa: BLE001 — unparseable manifest: defer to pre-flight
        return False
    workloads = manifest.workloads
    return bool(workloads) and all(
        getattr(w, "kind", "") == "agent" and getattr(w, "run_family", "task") != "service" for w in workloads
    )


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
                image_tags={"app": deployment.image_tag} if deployment.image_tag else {},
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

    # Org-scope the by-guid lookup to the caller's tenant (already resolved
    # by the bulk resolver). Fails closed (NOT_FOUND) when organization_id
    # is None (#1183).
    deployment = (
        Deployment.objects.select_related("registered_app", "app_environment", "workload")
        .filter(guid=deployment_id, deleted_at__isnull=True, registered_app__organization_id=organization_id)
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
    organization_id: int | None,
) -> BulkDeploymentResultItem:
    """Single-id reject path, callable from the bulk resolver.

    Mirrors :meth:`LifecycleMutation.reject_deployment` (one nay kills
    the deploy → FAILED). Per-id audit entries emitted directly so the
    history panel renders an entry per affected id."""
    from core.mutations import AuditEntry, emit_audit
    from core.mutations import ErrorCode as CoreErrorCode

    # Org-scope the by-guid lookup to the caller's tenant (already resolved
    # by the bulk resolver). Fails closed (NOT_FOUND) when organization_id
    # is None (#1183).
    deployment = (
        Deployment.objects.select_related("registered_app", "app_environment", "workload")
        .filter(guid=deployment_id, deleted_at__isnull=True, registered_app__organization_id=organization_id)
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
