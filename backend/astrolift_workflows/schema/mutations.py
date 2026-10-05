"""Admin mutations for the Temporal workflow viewer (#437) + the
configured-Workflow write surface (spec 40 §3/§6, #967/#968).

Cancel / terminate / signal are gated per run ownership (the tier-2
re-gate). Two paths are valid. The platform operator (an active
superuser holding ``AUDIT_LOG_READ`` + ``ADMIN_ELEVATE``, so a bearer
token also needs the admin scope) reaches every run (own-org,
foreign-org, legacy org-less) and acts fleet-wide. Otherwise the tenant
path applies: ``WORKFLOW_TRIGGER`` at the run's own scope (#1965), with
everything outside the caller's org (a foreign org's run, a legacy
org-less run, a nonexistent id) answered by one identical not-found
envelope (oracle closure).
"""

from __future__ import annotations

import strawberry
from django.db import transaction
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_identity.operation_context import agent_region_operation, execution_operation
from astrolift_workflows.client import (
    cancel_workflow,
    signal_workflow,
    terminate_workflow,
)
from astrolift_workflows.configuration_targets import (
    configuration_definition_scope,
    configured_workflow_scope,
    find_configuration_definition,
    find_configured_workflow,
)
from astrolift_workflows.schedule_operations import (
    authorize_schedule,
    schedule_workflow,
    validate_schedule_configuration,
    workflow_schedule_scope,
)
from astrolift_workflows.schema.execution_types import WorkflowExecutionControlResult, WorkflowExecutionType
from astrolift_workflows.schema.schedule_types import (
    WorkflowScheduleResult,
    WorkflowScheduleState,
    schedule_to_type,
)
from astrolift_workflows.schema.workflow_config_types import (
    ConfiguredWorkflowType,
    workflow_to_type,
)
from core.decorators import TenantRequired, tenant_scoped
from core.permissions import (
    Permission,
    PermissionDenied,
    check_permission,
    is_platform_operator,
    require_permission,
)
from core.schema.common import MutationResult, ValidationError
from core.tenancy import get_current_tenant
from workflows.scopes import (
    definition_scope_by_slug,
    definition_scope_by_stage_guid,
    execution_scope_by_id,
    reviewed_definition_scope,
    workflow_run_scope,
    workflow_scope_by_guid,
)

JSON = strawberry.scalars.JSON


def _failure(field: str, msg: str) -> MutationResult:
    return MutationResult(ok=False, errors=[ValidationError(field=field, messages=[msg])])


def _caller_org_pk() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _resolve_caller_org(org_id: str | None):
    """Resolve the caller's active org and confirm an ``org_id`` arg (guid)
    refers to it. Returns ``(organization, None)`` or ``(None, failure)``.
    Deny-by-default real org scoping per #1042 — the decorator only asserts
    a context exists."""
    from astrolift_identity.models import Organization

    caller = _caller_org_pk()
    if caller is None:
        return None, _failure("organization", "no active organization in context")
    org = Organization.objects.filter(pk=caller, deleted_at__isnull=True).first()
    if org is None:
        return None, _failure("organization", "active organization not found")
    if org_id is not None and str(org.guid) != str(org_id):
        return None, _failure("orgId", "PERMISSION_DENIED: organization mismatch")
    return org, None


def _run_owner_org_id(workflow_id: str) -> int | None:
    """Owning org pk for a Temporal workflow id, when a mirror row carries
    one (the tier-3 ``WorkflowInstance`` or the operations ``WorkflowRun``);
    ``None`` for legacy org-less runs."""
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowInstance

    org_id = (
        WorkflowInstance.objects.filter(
            temporal_workflow_id=workflow_id,
            organization__isnull=False,
            deleted_at__isnull=True,
        )
        .values_list("organization_id", flat=True)
        .first()
    )
    if org_id is not None:
        return org_id
    return (
        WorkflowRun.objects.filter(
            workflow_id=workflow_id,
            organization__isnull=False,
            deleted_at__isnull=True,
        )
        .order_by("-pk")
        .values_list("organization_id", flat=True)
        .first()
    )


def _has_elevated_viewer_access(user) -> bool:
    """True for the platform operator, who reads and acts fleet-wide.

    The permission pair alone is not enough: ``org_owner`` and
    ``org_admin`` carry the whole permission enum, ``ADMIN_ELEVATE``
    included, so on its own it would hand every org admin every other
    org's runs (#1965). Checking the pair on top keeps the bearer-token
    ceiling: a token reaches ``ADMIN_ELEVATE`` only with the admin scope.
    """
    if not is_platform_operator(user):
        return False
    try:
        check_permission(Permission.AUDIT_LOG_READ)
        check_permission(Permission.ADMIN_ELEVATE)
    except PermissionDenied:
        return False
    return True


def _gate_instance_op(user, workflow_id: str) -> MutationResult | None:
    """Data-dependent permission gate for cancel / terminate / signal.

    The branch depends on the looked-up run, so it cannot live in a static
    ``@require_permission`` stack. The platform operator reaches every
    run: own-org, foreign-org and legacy org-less. Otherwise the tenant path
    applies: ``WORKFLOW_TRIGGER`` at the run's own scope (its app, else its
    definition's project, else the org; never the selected team or
    project), reaching only the caller org's runs. Anything else (a
    foreign org's run, an org-less run, a nonexistent id) is answered
    with one identical not-found envelope, never a forbidden that
    confirms the id exists (oracle closure). Raises ``PermissionDenied``
    / ``TenantRequired`` exactly like the decorator stack; returns a
    failure envelope only for the not-found case."""
    if _has_elevated_viewer_access(user):
        return None
    caller = _caller_org_pk()
    from astrolift_identity.abac import operation_attributes
    from astrolift_identity.operation_context import workflow_operation

    with operation_attributes(**workflow_operation(workflow_id).attributes()):
        check_permission(Permission.WORKFLOW_TRIGGER, scope=workflow_run_scope(workflow_id, caller))
    if caller is None:
        raise TenantRequired("instance ops on org-owned runs require a resolved tenant context")
    if _run_owner_org_id(workflow_id) != caller:
        return _failure("workflow_id", "workflow instance not found")
    return None


@strawberry.type
class CreateWorkflowResult(MutationResult):
    workflow: ConfiguredWorkflowType | None = None
    configuration_saved: bool = False
    schedule: WorkflowScheduleState | None = None


@strawberry.type
class RunWorkflowResult(MutationResult):
    """Result of starting a configured Workflow's run (spec 40 §3)."""

    # The Temporal workflow id of the started run (the run id callers track).
    run_id: str | None = None
    # The astrolift_operations.WorkflowRun mirror pk (stage executions key on it).
    workflow_run_id: str | None = None
    # The tier-3 WorkflowInstance guid.
    instance_id: str | None = None


@strawberry.type
class TemporalWorkflowsMutation:
    @strawberry.mutation
    def cancel_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
        run_id: str | None = None,
    ) -> MutationResult:
        """Cooperative cancel — Temporal signals the workflow which can
        run cleanup before exiting. Use this for workflows that own
        external resources (deploys, migrations) so they teardown
        cleanly. Returns ``ok=False`` with a non-empty errors list when
        Temporal is disabled or the handle is missing."""
        gate = _gate_instance_op(info.context.user, workflow_id)
        if gate is not None:
            return gate
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        if run_id is not None and not run_id.strip():
            return _failure("run_id", "run_id must not be empty")
        delivered = cancel_workflow(workflow_id, **({"run_id": run_id} if run_id is not None else {}))
        if not delivered:
            return _failure("workflow_id", "cancel could not be delivered")
        return MutationResult.success()

    @strawberry.mutation
    def terminate_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
        reason: str,
        run_id: str | None = None,
    ) -> MutationResult:
        """Hard terminate — Temporal kills the workflow immediately,
        no cleanup runs. Reserve for wedged workflows that the
        cooperative cancel can't unstick. ``reason`` is required and
        stored on the Temporal record so the next operator sees why."""
        gate = _gate_instance_op(info.context.user, workflow_id)
        if gate is not None:
            return gate
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        if run_id is not None and not run_id.strip():
            return _failure("run_id", "run_id must not be empty")
        reason = (reason or "").strip()
        if not reason:
            return _failure("reason", "reason is required")
        delivered = terminate_workflow(
            workflow_id, reason, **({"run_id": run_id} if run_id is not None else {})
        )
        if not delivered:
            return _failure("workflow_id", "terminate could not be delivered")
        return MutationResult.success()

    @strawberry.mutation
    def signal_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
        signal_name: str,
        payload: JSON | None = None,
    ) -> MutationResult:
        """Send an arbitrary signal to a running workflow.

        Power-user escape hatch for workflows that expose custom
        signals (``abort``, ``cancel_teardown``, etc.). Payload is
        passed through as the single signal argument; pass ``null`` for
        signals that take no args. Returns ``ok=False`` when Temporal
        is disabled or the signal couldn't be delivered (workflow
        already complete, handle missing)."""
        gate = _gate_instance_op(info.context.user, workflow_id)
        if gate is not None:
            return gate
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        signal_name = (signal_name or "").strip()
        if not signal_name:
            return _failure("signal_name", "signal_name is required")
        if signal_name in _EXECUTION_SIGNALS and isinstance(payload, dict):
            resolved = _resolve_execution_id(payload.get("execution_id"), workflow_id)
            if resolved is None:
                return _failure(
                    "payload",
                    "execution_id does not name a stage execution; pass the executionId "
                    "or guid from workflowStageExecutions",
                )
            payload = {**payload, "execution_id": resolved}
            if signal_name == "human_gate_decision":
                # The approver is who is calling, never a user id the caller
                # supplies; and a gate that names its approvers by address is
                # decided only by one of them (or the platform operator)
                # (#1982).
                refusal = _gate_approver_refusal(info.context.user, resolved, workflow_id)
                if refusal is not None:
                    return _failure("payload", refusal)
                payload = {**payload, "decided_by_user_id": getattr(info.context.user, "pk", None)}
        target_workflow_id = workflow_id
        if signal_name in _EXECUTION_SIGNALS and isinstance(payload, dict):
            target_workflow_id = _execution_signal_target(
                payload["execution_id"], workflow_id, organization_id=_run_owner_org_id(workflow_id)
            )
            if target_workflow_id is None:
                return _failure("payload", "stage execution signal target is unavailable")
        args: list = [payload] if payload is not None else []
        delivered = signal_workflow(target_workflow_id, signal_name, *args)
        if not delivered:
            return _failure("signal_name", "signal could not be delivered")
        return MutationResult.success()


# Signals the run workflow keys on a stage execution. The workflow stores the
# decision under ``str(execution.pk)`` (what ``executionId`` exposes), so a
# signal carrying the execution's ``guid`` was accepted by Temporal and then
# ignored forever (#1786). Resolve either spelling here and refuse the rest.
_EXECUTION_SIGNALS = frozenset({"human_gate_decision", "escalation_cleared"})


def _execution_signal_target(
    execution_id: str, workflow_id: str, *, organization_id: int | None
) -> str | None:
    from workflows.collections import collection_binding
    from workflows.models import WorkflowStageExecution

    row = (
        WorkflowStageExecution.objects.select_related(
            "collection_parent_execution__stage", "workflow_run", "stage"
        )
        .filter(
            pk=execution_id,
            workflow_run__workflow_id=workflow_id,
            workflow_run__organization_id=organization_id,
        )
        .first()
    )
    if row is None:
        return None
    if row.collection_parent_execution_id is None:
        return workflow_id if not row.collection_workflow_id else None
    parent = row.collection_parent_execution
    binding = collection_binding(parent.output)
    if (
        binding is None
        or type(row.collection_index) is not int
        or not 0 <= row.collection_index < binding["item_count"]
    ):
        return None
    expected = f"WorkflowDefinitionRunWorkflow-{row.workflow_run_id}:collection:{binding.get('owner_order')}:{row.collection_index}-parent-{parent.pk}"
    if (
        parent.status != "running"
        or parent.stage.kind != "collection"
        or parent.workflow_run_id != row.workflow_run_id
        or parent.stage.definition_id != row.stage.definition_id
        or str(row.stage_id) not in binding.get("body_stage_ids", [])
        or row.collection_workflow_id != expected
    ):
        return None
    return expected


def _gate_approver_refusal(user, execution_id: str, workflow_id: str) -> str | None:
    """Why ``user`` may not decide the human gate ``execution_id``, or ``None``.

    ``WorkflowStage.approvers`` holds approver references: addresses, or team
    / role slugs the notification service resolves. When the stage names
    addresses, only a caller whose email is one of them may decide; slug
    references are not resolved here (anyone with ``workflow.trigger`` at the
    run's scope, as before). The platform operator may always decide. The
    predicate itself is shared with the org-wide pending-gates list
    (``workflows.scopes.may_decide_human_gate``, #1820) so the two never
    disagree on who may act.
    """
    from workflows.models import WorkflowStageExecution
    from workflows.scopes import may_decide_human_gate

    # tenancy: confined to the signalled run, as in ``_resolve_execution_id``.
    row = (
        WorkflowStageExecution.objects.select_related("stage")
        .filter(workflow_run__workflow_id=workflow_id, pk=int(execution_id))
        .first()
    )
    approvers = getattr(getattr(row, "stage", None), "approvers", None)
    if may_decide_human_gate(user, approvers):
        return None
    return "only one of this gate's named approvers may decide it"


def _resolve_execution_id(raw: object, workflow_id: str) -> str | None:
    from workflows.models import WorkflowStageExecution

    value = str(raw or "").strip()
    if not value:
        return None
    # tenancy: confined to the signalled run, whose ownership the gate
    # already checked; any other run's execution, in any org, is refused.
    rows = WorkflowStageExecution.objects.filter(workflow_run__workflow_id=workflow_id)
    row = rows.filter(pk=int(value)).first() if value.isdigit() else rows.filter(guid=value).first()
    return str(row.pk) if row is not None else None


@strawberry.type
class WorkflowsMutation:
    """Configured-Workflow write surface (spec 40 §3/§6). Every resolver is
    ``WORKFLOW_*``-gated + ``@tenant_scoped`` and applies a real caller-org
    filter in the body (#1042). Definition + stage CREATE / clone + the
    forms paths stay on the #966 ``workflows.schema`` surface (reused); this
    class owns tier-2 Workflows, the run mapping, and the stage
    update/delete/reorder #966 did not build."""

    @strawberry.mutation(description="Cancel, terminate, or retry cleanup for an exact owned execution.")
    @require_permission(
        Permission.WORKFLOW_TRIGGER,
        scope=execution_scope_by_id(permission=Permission.WORKFLOW_TRIGGER),
        operation=execution_operation,
    )
    @tenant_scoped()
    def control_workflow_execution(
        self,
        info: Info,
        execution_id: strawberry.ID,
        workflow_id: str,
        run_id: str,
        action: str,
        reason: str = "",
    ) -> WorkflowExecutionControlResult:
        from astrolift_workflows.execution_controls import control_execution, find_execution

        run = find_execution(_caller_org_pk(), str(execution_id))
        if run is None:
            return WorkflowExecutionControlResult(
                ok=False, errors=[ValidationError(field="executionId", messages=["Execution not found"])]
            )
        try:
            state = control_execution(
                run, workflow_id=workflow_id, run_id=run_id, action=action, reason=reason
            )
        except ValueError as exc:
            return WorkflowExecutionControlResult(
                ok=False, errors=[ValidationError(field="executionId", messages=[str(exc)])]
            )
        return WorkflowExecutionControlResult(
            ok=True, requested=True, execution=WorkflowExecutionType(**state)
        )

    @strawberry.mutation(
        description="Reconcile the exact reviewed configuration version with its native Temporal schedule. Does not start an immediate run. Active reconciliation requires workflow.trigger; deleted workflow cleanup also requires workflow.delete."
    )
    @require_permission(Permission.WORKFLOW_UPDATE, scope=workflow_schedule_scope(Permission.WORKFLOW_UPDATE))
    @tenant_scoped()
    def reconcile_workflow_schedule(
        self,
        info: Info,
        workflow_id: GUID,  # type: ignore[valid-type]
        expected_version: int,
        expected_active: bool,
    ) -> WorkflowScheduleResult:
        from workflows.schedule_sync import reconcile_workflow_schedule

        workflow = schedule_workflow(workflow_id)
        if workflow is None:
            return WorkflowScheduleResult(
                ok=False, errors=[ValidationError(field="workflow_id", messages=["Workflow not found"])]
            )
        result = reconcile_workflow_schedule(
            workflow,
            expected_version=expected_version,
            expected_active=expected_active,
            authorize=authorize_schedule,
        )
        return WorkflowScheduleResult(
            ok=result.confirmed,
            schedule=schedule_to_type(result),
            errors=[]
            if result.confirmed
            else [
                ValidationError(
                    field="schedule",
                    messages=[result.message or "Schedule does not match the reviewed configuration."],
                )
            ],
        )

    # ── tier-2 Workflow CRUD ────────────────────────────────────────────

    @strawberry.mutation(
        description=(
            "Create a configured Workflow from an exact definitionId or legacy definitionSlug. "
            "When both are supplied the slug must match the ID. Set isEnabled=false to prepare "
            "configuration without activation; the legacy default is true."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE, scope=configuration_definition_scope)
    @tenant_scoped()
    def create_workflow(
        self,
        info: Info,
        name: str,
        definition_slug: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        stage_bindings: JSON | None = None,
        inputs: JSON | None = None,
        trigger_kind: str = "manual",
        schedule_cron: str | None = None,
        org_id: strawberry.ID | None = None,
        definition_id: GUID | None = None,  # type: ignore[valid-type]
        is_enabled: bool = True,
    ) -> CreateWorkflowResult:
        from django.core.exceptions import ValidationError as DjangoValidationError

        from workflows.models import Workflow

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return CreateWorkflowResult(ok=err.ok, errors=err.errors)

        valid_triggers = {c[0] for c in Workflow.TriggerKind.choices}
        if trigger_kind not in valid_triggers:
            return CreateWorkflowResult(
                ok=False,
                errors=[
                    ValidationError(field="trigger_kind", messages=[f'Invalid trigger_kind "{trigger_kind}"'])
                ],
            )

        with transaction.atomic():
            definition = find_configuration_definition(
                org.pk,
                definition_id=definition_id,
                definition_slug=definition_slug,
                lock=True,
            )
            if definition is None:
                return CreateWorkflowResult(
                    ok=False,
                    errors=[
                        ValidationError(
                            field="definition_id" if definition_id is not None else "definition_slug",
                            messages=["Workflow definition not visible or identity assertion does not match"],
                        )
                    ],
                )

            check_permission(
                Permission.WORKFLOW_CREATE,
                scope=reviewed_definition_scope(definition, org.pk, permission=Permission.WORKFLOW_CREATE),
            )

            from workflows.back_edges import SUPPORTED_EXECUTOR_PATTERNS

            if definition.pattern_kind not in SUPPORTED_EXECUTOR_PATTERNS:
                return CreateWorkflowResult(
                    ok=False,
                    errors=[
                        ValidationError(
                            field="definition_slug",
                            messages=["This workflow pattern has no supported executor"],
                        )
                    ],
                )

            user = info.context.user
            wf = Workflow(
                organization=org,
                definition=definition,
                name=name,
                slug=slug or "",
                description=description or "",
                stage_bindings=stage_bindings or {},
                inputs=inputs or {},
                trigger_kind=trigger_kind,
                is_enabled=is_enabled,
                schedule_cron=(schedule_cron or None),
                created_by=user,
                updated_by=user,
            )
            schedule_error = validate_schedule_configuration(wf)
            if schedule_error is not None:
                return CreateWorkflowResult(ok=False, errors=[schedule_error])
            from workflows.schedule_sync import schedule_inactive_reason

            wf.schedule_managed = schedule_inactive_reason(wf) is None
            if wf.schedule_managed:
                authorize_schedule(wf, Permission.WORKFLOW_CREATE)
            try:
                # Model.save validates bindings (every agent_dispatch stage resolves).
                wf.save()
            except DjangoValidationError as e:
                return CreateWorkflowResult(
                    ok=False,
                    errors=[ValidationError(field="stage_bindings", messages=list(e.messages))],
                )
            except Exception as e:  # noqa: BLE001 — IntegrityError on (org, slug), etc.
                return CreateWorkflowResult(
                    ok=False, errors=[ValidationError(field="slug", messages=[str(e)])]
                )

        return _configured_schedule_result(wf, Permission.WORKFLOW_CREATE)

    @strawberry.mutation(
        description=(
            "Update a configured Workflow by exact workflowId or legacy slug (bindings / inputs / trigger / enabled). "
            "A supplied slug must match its ID. definitionId or definitionSlug repoints it at another visible definition: the fallback "
            "for a versioned importWorkflowManifest(replace: true) (#1822), or any "
            "manual repoint. The existing stage_bindings must still validate against "
            "the new definition's stages, or the update is refused."
        )
    )
    @require_permission(
        Permission.WORKFLOW_UPDATE, scope=configured_workflow_scope(Permission.WORKFLOW_UPDATE)
    )
    @tenant_scoped()
    def update_workflow(
        self,
        info: Info,
        slug: str | None = None,
        name: str | None = None,
        description: str | None = None,
        stage_bindings: JSON | None = None,
        inputs: JSON | None = None,
        trigger_kind: str | None = None,
        schedule_cron: str | None = None,
        is_enabled: bool | None = None,
        definition_slug: str | None = None,
        org_id: strawberry.ID | None = None,
        workflow_id: GUID | None = None,  # type: ignore[valid-type]
        definition_id: GUID | None = None,  # type: ignore[valid-type]
    ) -> CreateWorkflowResult:
        from django.core.exceptions import ValidationError as DjangoValidationError

        from workflows.models import Workflow

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return CreateWorkflowResult(ok=err.ok, errors=err.errors)

        with transaction.atomic():
            # Manifest replacement locks the definition before configured rows.
            # Keep that order when repointing to avoid a lock inversion.
            new_definition = None
            if definition_slug is not None or definition_id is not None:
                new_definition = find_configuration_definition(
                    org.pk,
                    definition_id=definition_id,
                    definition_slug=definition_slug,
                    lock=True,
                )
                if new_definition is None:
                    return CreateWorkflowResult(
                        ok=False,
                        errors=[
                            ValidationError(
                                field="definition_id" if definition_id is not None else "definition_slug",
                                messages=[
                                    "Workflow definition not visible or identity assertion does not match"
                                ],
                            )
                        ],
                    )
                # Same permission createWorkflow requires to bind a Workflow to
                # this definition in the first place.
                check_permission(
                    Permission.WORKFLOW_CREATE,
                    scope=reviewed_definition_scope(
                        new_definition, org.pk, permission=Permission.WORKFLOW_CREATE
                    ),
                )

            wf = find_configured_workflow(org.pk, workflow_id=workflow_id, slug=slug, lock=True)
            if wf is None:
                return CreateWorkflowResult(
                    ok=False,
                    errors=[
                        ValidationError(
                            field="workflow_id" if workflow_id is not None else "slug",
                            messages=["Workflow not found or identity assertion does not match"],
                        )
                    ],
                )

            check_permission(
                Permission.WORKFLOW_UPDATE,
                scope=reviewed_definition_scope(wf.definition, org.pk, permission=Permission.WORKFLOW_UPDATE),
            )
            from workflows.schedule_sync import schedule_inactive_reason

            previously_active = schedule_inactive_reason(wf) is None
            if new_definition is not None:
                wf.definition = new_definition

            if trigger_kind is not None:
                valid_triggers = {c[0] for c in Workflow.TriggerKind.choices}
                if trigger_kind not in valid_triggers:
                    return CreateWorkflowResult(
                        ok=False,
                        errors=[
                            ValidationError(
                                field="trigger_kind", messages=[f'Invalid trigger_kind "{trigger_kind}"']
                            )
                        ],
                    )
                wf.trigger_kind = trigger_kind
            if name is not None:
                wf.name = name
            if description is not None:
                wf.description = description
            if stage_bindings is not None:
                wf.stage_bindings = stage_bindings
            if inputs is not None:
                wf.inputs = inputs
            if schedule_cron is not None:
                wf.schedule_cron = schedule_cron or None
            if is_enabled is not None:
                wf.is_enabled = is_enabled
            wf.updated_by = info.context.user

            schedule_error = validate_schedule_configuration(wf)
            if schedule_error is not None:
                return CreateWorkflowResult(ok=False, errors=[schedule_error])
            active = schedule_inactive_reason(wf) is None
            wf.schedule_managed = wf.schedule_managed or previously_active or active
            if active:
                authorize_schedule(wf)
            try:
                wf.save()
            except DjangoValidationError as e:
                return CreateWorkflowResult(
                    ok=False, errors=[ValidationError(field="stage_bindings", messages=list(e.messages))]
                )

        return _configured_schedule_result(wf, Permission.WORKFLOW_UPDATE)

    @strawberry.mutation(
        description="Soft-delete a configured Workflow by exact workflowId or legacy slug and request schedule removal. A supplied slug must match its ID."
    )
    @require_permission(
        Permission.WORKFLOW_DELETE, scope=configured_workflow_scope(Permission.WORKFLOW_DELETE)
    )
    @tenant_scoped()
    def delete_workflow(
        self,
        info: Info,
        slug: str | None = None,
        org_id: strawberry.ID | None = None,
        workflow_id: GUID | None = None,  # type: ignore[valid-type]
    ) -> WorkflowScheduleResult:
        from django.utils import timezone

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return WorkflowScheduleResult(ok=err.ok, errors=err.errors)

        with transaction.atomic():
            wf = find_configured_workflow(org.pk, workflow_id=workflow_id, slug=slug, lock=True)
            if wf is None:
                return WorkflowScheduleResult(
                    ok=False,
                    errors=[
                        ValidationError(
                            field="workflow_id",
                            messages=["Workflow not found or identity assertion does not match"],
                        )
                    ],
                )

            check_permission(
                Permission.WORKFLOW_DELETE,
                scope=reviewed_definition_scope(wf.definition, org.pk, permission=Permission.WORKFLOW_DELETE),
            )
            from workflows.schedule_sync import schedule_inactive_reason

            wf.schedule_managed = wf.schedule_managed or schedule_inactive_reason(wf) is None
            wf.deleted_at = timezone.now()
            wf.deleted_by = info.context.user
            wf.save(
                update_fields=["deleted_at", "deleted_by", "updated_at", "version", "schedule_managed"],
                skip_binding_validation=True,
            )
        result = _configured_schedule_result(wf, Permission.WORKFLOW_DELETE)
        return WorkflowScheduleResult(
            ok=result.ok, errors=result.errors, configuration_saved=True, schedule=result.schedule
        )

    # ── run mapping (spec 40 §3) ────────────────────────────────────────

    @strawberry.mutation(description="Run a configured Workflow now via Temporal (spec 40 §3).")
    @require_permission(
        Permission.WORKFLOW_TRIGGER,
        scope=workflow_scope_by_guid("workflow_id"),
        operation=agent_region_operation,
    )
    @tenant_scoped()
    def run_workflow(
        self,
        info: Info,
        workflow_id: strawberry.ID,
        inputs: JSON | None = None,
        org_id: strawberry.ID | None = None,
    ) -> RunWorkflowResult:
        from django.core.exceptions import ValidationError as DjangoValidationError

        from astrolift_workflows.inputs import Actor
        from core.run_trigger import request_trigger
        from workflows.models import Workflow, WorkflowInstance
        from workflows.run_service import start_workflow_definition_run

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return RunWorkflowResult(ok=err.ok, errors=err.errors)

        wf = (
            Workflow.objects.filter(guid=str(workflow_id), organization=org, deleted_at__isnull=True)
            .select_related("definition")
            .first()
        )
        if wf is None:
            return RunWorkflowResult(
                ok=False, errors=[ValidationError(field="workflow_id", messages=["Workflow not found"])]
            )
        if not wf.is_enabled:
            return RunWorkflowResult(
                ok=False, errors=[ValidationError(field="workflow_id", messages=["Workflow is disabled"])]
            )
        if not wf.definition.is_enabled:
            return RunWorkflowResult(
                ok=False,
                errors=[ValidationError(field="definition", messages=["Workflow definition is disabled"])],
            )
        if not wf.definition.stages.filter(deleted_at__isnull=True).exists():
            return RunWorkflowResult(
                ok=False,
                errors=[ValidationError(field="definition", messages=["Workflow definition has no stages"])],
            )

        # Validate bindings: every agent_dispatch stage resolves (§2.2).
        try:
            wf.validate_bindings()
        except DjangoValidationError as e:
            return RunWorkflowResult(
                ok=False, errors=[ValidationError(field="stage_bindings", messages=list(e.messages))]
            )

        # Merge workflow-level inputs ⊕ call inputs (call wins).
        merged_inputs = {**(wf.inputs or {}), **(inputs or {})}
        user = info.context.user

        run, temporal_workflow_id = start_workflow_definition_run(
            wf.definition,
            trigger_payload=merged_inputs,
            organization_id=org.pk,
            actor=Actor(
                kind="user",
                user_id=user.pk if getattr(user, "pk", None) else None,
                display=getattr(user, "username", "") or "",
            ),
            stage_bindings=wf.stage_bindings,
            trigger_kind=request_trigger(),
        )

        # Tier-3 run record linked to the Workflow (denormalized org, §2.3).
        # Persist the Temporal run_id so a historical run's DAG overlays without
        # a live Temporal describe (#1180).
        instance = WorkflowInstance.start(
            configured_workflow=wf,
            user=user,
            temporal_workflow_id=temporal_workflow_id,
            temporal_run_id=run.run_id or None,
        )

        return RunWorkflowResult(
            ok=True,
            run_id=temporal_workflow_id,
            workflow_run_id=str(run.pk),
            instance_id=str(instance.pk),
        )

    # ── stage update / delete / reorder (spec 40 §6; #966 built create) ──

    @strawberry.mutation(description="Update a stage's fields on a writable definition (spec 40 §6).")
    @require_permission(Permission.WORKFLOW_UPDATE, scope=definition_scope_by_stage_guid("stage_guid"))
    @tenant_scoped()
    def update_workflow_stage(
        self,
        info: Info,
        stage_guid: strawberry.ID,
        kind: str | None = None,
        role: str | None = None,
        on_failure: str | None = None,
        timeout_seconds: int | None = None,
        max_attempts: int | None = None,
        back_edge: JSON | None = None,
        iteration: JSON | None = None,
        agent_definition_guid: str | None = None,
        agent_ref: str | None = None,
        workflow_ref: str | None = None,
        environment_spec_slug: str | None = None,
        skill_refs: JSON | None = None,
        fan_out_count: int | None = None,
        prompt: str | None = None,
        output_key: str | None = None,
        approvers: JSON | None = None,
    ) -> MutationResult:
        from workflows.models import WorkflowStage
        from workflows.schema.mutations import _definition_write_error

        stage = (
            WorkflowStage.objects.filter(guid=str(stage_guid), deleted_at__isnull=True)
            .select_related("definition")
            .first()
        )
        if stage is None:
            return _failure("stage_guid", "Stage not found")

        write_err = _definition_write_error(info.context.user, stage.definition)
        if write_err is not None:
            return _failure(write_err[0], write_err[1])

        if kind is not None:
            if kind not in {c[0] for c in WorkflowStage.StageKind.choices}:
                return _failure("kind", f'Invalid stage kind "{kind}"')
            stage.kind = kind
        if on_failure is not None:
            if on_failure not in {c[0] for c in WorkflowStage.OnFailure.choices}:
                return _failure("on_failure", f'Invalid on_failure "{on_failure}"')
            stage.on_failure = on_failure
        if max_attempts is not None:
            from workflows.stage_limits import validate_stage_attempts

            try:
                stage.max_attempts = validate_stage_attempts(max_attempts)
            except ValueError as exc:
                return _failure("max_attempts", str(exc))
        if iteration is not None:
            from workflows.collections import validate_iteration

            try:
                stage.iteration = validate_iteration(iteration, kind=stage.kind)
            except ValueError as exc:
                return _failure("iteration", str(exc))
        if back_edge is not None:
            from workflows.back_edges import validate_back_edge

            try:
                stage.back_edge = validate_back_edge(back_edge, kind=stage.kind)
            except ValueError as exc:
                return _failure("back_edge", str(exc))
        if role is not None:
            stage.role = role
        if timeout_seconds is not None:
            stage.timeout_seconds = timeout_seconds
        if agent_ref is not None:
            stage.agent_ref = agent_ref.strip()
        if workflow_ref is not None:
            stage.workflow_ref = workflow_ref.strip()
        if environment_spec_slug is not None:
            stage.environment_spec_slug = environment_spec_slug.strip()
        if skill_refs is not None:
            stage.skill_refs = skill_refs
        if fan_out_count is not None:
            stage.fan_out_count = fan_out_count
        if prompt is not None:
            stage.prompt = prompt
        if output_key is not None:
            stage.output_key = output_key.strip()
        if approvers is not None:
            stage.approvers = approvers
        if agent_definition_guid is not None:
            from astrolift_registry.models import Workload

            # Scoped to the caller's org (via registered_app.organization) —
            # a foreign org's workload is not-found, never a cross-tenant bind.
            workload = (
                Workload.objects.filter(
                    guid=agent_definition_guid,
                    registered_app__organization_id=_caller_org_pk(),
                ).first()
                if agent_definition_guid
                else None
            )
            if agent_definition_guid and workload is None:
                return _failure("agent_definition_guid", "Workload not found")
            stage.agent_definition = workload
            if workload is not None:
                stage.agent_ref = workload.slug
        stage.updated_by = info.context.user
        try:
            with transaction.atomic():
                stage.save()
                from workflows.composition import validate_workflow_composition

                validate_workflow_composition(stage.definition)
        except ValueError as exc:
            return _failure("workflow_ref", str(exc))
        return MutationResult.success()

    @strawberry.mutation(description="Soft-delete a stage from a writable definition (spec 40 §6).")
    @require_permission(Permission.WORKFLOW_UPDATE, scope=definition_scope_by_stage_guid("stage_guid"))
    @tenant_scoped()
    def delete_workflow_stage(self, info: Info, stage_guid: strawberry.ID) -> MutationResult:
        from django.utils import timezone

        from workflows.models import WorkflowStage
        from workflows.schema.mutations import _definition_write_error

        stage = (
            WorkflowStage.objects.filter(guid=str(stage_guid), deleted_at__isnull=True)
            .select_related("definition")
            .first()
        )
        if stage is None:
            return _failure("stage_guid", "Stage not found")

        write_err = _definition_write_error(info.context.user, stage.definition)
        if write_err is not None:
            return _failure(write_err[0], write_err[1])

        stage.deleted_at = timezone.now()
        stage.deleted_by = info.context.user
        try:
            with transaction.atomic():
                stage.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])
                from workflows.composition import validate_workflow_composition

                validate_workflow_composition(stage.definition)
        except ValueError as exc:
            return _failure("back_edge", str(exc))
        return MutationResult.success()

    @strawberry.mutation(
        description="Reorder a definition's stages (spec 40 §6). Pass stage guids in the new order."
    )
    @require_permission(Permission.WORKFLOW_UPDATE, scope=definition_scope_by_slug("definition_slug"))
    @tenant_scoped()
    def reorder_workflow_stages(
        self, info: Info, definition_slug: str, stage_guids: list[strawberry.ID]
    ) -> MutationResult:
        from django.db import transaction

        from workflows.models import WorkflowDefinition
        from workflows.schema.mutations import _definition_write_error

        org = _caller_org_pk()
        definition = (
            WorkflowDefinition.visible_to_org(org)
            .filter(slug=definition_slug, deleted_at__isnull=True)
            .first()
        )
        if definition is None:
            return _failure("definition_slug", f'Workflow definition "{definition_slug}" not visible')

        write_err = _definition_write_error(info.context.user, definition)
        if write_err is not None:
            return _failure(write_err[0], write_err[1])

        stages = {str(s.guid): s for s in definition.stages.filter(deleted_at__isnull=True)}
        requested = [str(g) for g in stage_guids]
        if len(requested) != len(stages) or set(requested) != set(stages):
            return _failure("stage_guids", "stage_guids must list exactly the definition's stages")

        from workflows.back_edges import validate_loop_plan

        try:
            validate_loop_plan(
                [
                    {
                        "order": index,
                        "kind": stages[guid].kind,
                        "output_key": stages[guid].output_key,
                        "max_attempts": stages[guid].max_attempts,
                        "fan_out_count": stages[guid].fan_out_count,
                        "fan_out_dynamic": stages[guid].fan_out_dynamic,
                        "back_edge": stages[guid].back_edge,
                        "iteration": stages[guid].iteration,
                    }
                    for index, guid in enumerate(requested)
                ],
                pattern_kind=definition.pattern_kind,
                require_review_loop=False,
            )
        except ValueError as exc:
            return _failure("back_edge", str(exc))

        # Two-phase to dodge the (definition, order) unique constraint: park
        # stages at negative orders, then assign final positions.
        with transaction.atomic():
            for offset, guid in enumerate(requested):
                s = stages[guid]
                s.order = -(offset + 1)
                s.save(update_fields=["order", "updated_at", "version"])
            for new_order, guid in enumerate(requested):
                s = stages[guid]
                s.order = new_order
                s.updated_by = info.context.user
                s.save(update_fields=["order", "updated_by", "updated_at", "version"])
        return MutationResult.success()


def _configured_schedule_result(workflow, permission):
    from workflows.schedule_sync import sync_workflow_schedule, unrequested_schedule

    if workflow.schedule_managed:
        observed = sync_workflow_schedule(
            workflow, authorize=lambda current: authorize_schedule(current, permission)
        )
    else:
        observed = unrequested_schedule(workflow)
    ok = observed.confirmed or observed.observed_state == "not_requested"
    return CreateWorkflowResult(
        ok=ok,
        configuration_saved=True,
        workflow=workflow_to_type(workflow, with_runs=True),
        schedule=schedule_to_type(observed),
        errors=[]
        if ok
        else [
            ValidationError(
                field="schedule",
                messages=[
                    "Configuration was saved, but schedule state is unconfirmed. Inspect and reconcile this workflow ID."
                ],
            )
        ],
    )
