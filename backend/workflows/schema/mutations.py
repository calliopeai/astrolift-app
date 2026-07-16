from __future__ import annotations

from typing import Optional

import strawberry
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from graphql import GraphQLError
from strawberry.types import Info

from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.common import MutationResult
from core.schema.common import ValidationError as GQLValidationError
from core.tenancy import get_current_tenant
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.schema.types import WorkflowDefinitionType, WorkflowStageType


@strawberry.type
class StartWorkflowResult(MutationResult):
    instance_id: Optional[strawberry.ID] = None


@strawberry.type
class RunWorkflowDefinitionResult(MutationResult):
    # WorkflowRun mirror pk (the executor keys stage executions to it).
    workflow_run_id: Optional[strawberry.ID] = None
    # Temporal workflow id, for the viewer / signalling.
    temporal_workflow_id: Optional[str] = None


@strawberry.type
class CreateWorkflowStageResult(MutationResult):
    stage: Optional[WorkflowStageType] = None


@strawberry.type
class CloneWorkflowDefinitionResult(MutationResult):
    """Result of deep-copying a definition into the caller's org (spec 40 §2.1)."""

    slug: Optional[str] = None
    definition: Optional[WorkflowDefinitionType] = None


@strawberry.type
class CreateWorkflowTriggerResult(MutationResult):
    """Inbound webhook trigger for a workflow definition (#1019).
    ``signing_secret`` is the plaintext — shown ONCE, stored only as a hash."""

    slug: Optional[str] = None
    endpoint: Optional[str] = None
    signing_secret: Optional[str] = None


def _require_staff(user):
    """Raise GraphQLError if user is not authenticated staff/superuser."""
    if not user or not user.is_authenticated:
        raise GraphQLError("Authentication required")
    if not (user.is_staff or user.is_superuser):
        raise GraphQLError("Staff or superuser access required")


def _caller_org_pk():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _definition_write_error(user, definition):
    """Deny-by-default write gate for a WorkflowDefinition (spec 40 §2.1).

    Returns ``(field, message)`` describing why ``user`` may not write
    ``definition``, or ``None`` if allowed. Platform-global (null-org)
    templates are read-only to tenants — superuser/staff exempt (the seeding
    path). Org-authored definitions are writable only by their owning org
    (superuser/staff bypass). The org match is the actual scoping (#1042 —
    ``@tenant_scoped`` only asserts a context exists)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return ("permission", "Authentication required")
    if user.is_superuser or user.is_staff:
        return None
    if definition.organization_id is None:
        return ("permission", "PERMISSION_DENIED: platform template — clone to edit")
    if definition.organization_id != _caller_org_pk():
        return ("permission", "PERMISSION_DENIED: not your organization's workflow")
    return None


def _resolve_clone_org(org_id):
    """Resolve ``org_id`` (Organization guid; ``None`` → the caller's active
    org) to an Organization, asserting it matches the caller's active tenant.
    Returns ``(org, None)`` on success or ``(None, failure_result)``. Real org
    scoping per #1042."""
    from astrolift_identity.models import Organization

    user_org = _caller_org_pk()
    if org_id is None:
        org = (
            Organization.objects.filter(pk=user_org, deleted_at__isnull=True).first()
            if user_org is not None
            else None
        )
        if org is None:
            return None, CloneWorkflowDefinitionResult(
                ok=False,
                errors=[GQLValidationError(field="orgId", messages=["no active organization in context"])],
            )
        return org, None
    org = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if org is None:
        return None, CloneWorkflowDefinitionResult(
            ok=False,
            errors=[GQLValidationError(field="orgId", messages=["organization not found"])],
        )
    if user_org is not None and org.pk != user_org:
        return None, CloneWorkflowDefinitionResult(
            ok=False,
            errors=[GQLValidationError(field="orgId", messages=["PERMISSION_DENIED: organization mismatch"])],
        )
    return org, None


def _definition_for_write(slug):
    """Resolve *slug* for a write within the caller's visible scope — the
    org's own definitions UNION platform-global, mirroring
    ``WorkflowDefinition.visible_to_org`` — preferring the org-owned row on
    slug collision; ``_definition_write_error`` then gates. A foreign org's
    slug resolves to nothing (indistinguishable from nonexistent), so no
    cross-tenant existence oracle."""
    org_pk = _caller_org_pk()
    qs = WorkflowDefinition.objects.filter(slug=slug, deleted_at__isnull=True).filter(
        Q(organization_id=org_pk) | Q(organization__isnull=True)
    )
    return qs.filter(organization_id=org_pk).first() or qs.first()


def _unique_clone_slug(base_slug, org):
    """First free slug for ``org`` derived from ``base_slug`` (spec 40 §9 Q2)."""
    candidate = base_slug
    suffix = 0
    while WorkflowDefinition.objects.filter(
        organization=org, slug=candidate, deleted_at__isnull=True
    ).exists():
        suffix += 1
        candidate = f"{base_slug}-copy" if suffix == 1 else f"{base_slug}-copy-{suffix}"
    return candidate


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Start a workflow for an object.")
    @require_permission(Permission.WORKFLOW_TRIGGER)
    @tenant_scoped()
    def start_workflow(
        self,
        info: Info,
        workflow_slug: str,
        model_label: str,
        object_id: int,
    ) -> StartWorkflowResult:
        user = info.context.user
        # Legacy forms state-machine entry (dormant, spec 40 §8) — gated
        # deny-by-default like every mutation. Definition resolution uses the
        # caller-org ∪ global scope so a foreign org's slug is not found.
        workflow = _definition_for_write(workflow_slug)
        if not workflow or not workflow.is_enabled:
            raise GraphQLError(f'Workflow "{workflow_slug}" not found or disabled')

        # Resolve the model. The GFK target is an arbitrary legacy model
        # (forms) with no org FK, so no object-level org filter is possible —
        # the permission + tenant gates above are the scoping.
        try:
            parts = model_label.split(".")
            model = apps.get_model(parts[0], parts[-1])
            obj = model.objects.get(pk=object_id)
        except Exception as e:
            raise GraphQLError(f"Object not found: {model_label}:{object_id} — {e}")

        try:
            instance = WorkflowInstance.start(workflow, obj, user)
            return StartWorkflowResult(ok=True, instance_id=str(instance.pk))
        except ValidationError as e:
            raise GraphQLError(str(e))

    @strawberry.mutation(
        description=(
            "Run an agent WorkflowDefinition's stages durably via Temporal "
            "(WorkflowDefinitionRunWorkflow). Creates the WorkflowInstance + "
            "WorkflowRun mirror rows and enqueues the stage executor."
        )
    )
    def run_workflow_definition(
        self,
        info: Info,
        workflow_slug: str,
        trigger_payload: Optional[strawberry.scalars.JSON] = None,
    ) -> RunWorkflowDefinitionResult:
        user = info.context.user
        _require_staff(user)

        workflow = WorkflowDefinition.objects.filter(
            slug=workflow_slug,
            is_enabled=True,
            deleted_at__isnull=True,
        ).first()
        if not workflow:
            return RunWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug",
                        messages=[f'Workflow "{workflow_slug}" not found or disabled'],
                    )
                ],
            )

        if not workflow.stages.filter(deleted_at__isnull=True).exists():
            return RunWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug",
                        messages=[f'Workflow "{workflow_slug}" has no stages to execute'],
                    )
                ],
            )

        # Imports kept local so this engine app's schema module doesn't pull
        # the Temporal client + operations models at import time (the app is
        # feature-gated).
        from astrolift_workflows.inputs import Actor
        from core.tenancy import get_current_tenant
        from workflows.run_service import start_workflow_definition_run

        tenant = get_current_tenant()
        organization_id = getattr(tenant, "organization_id", None) if tenant else None

        # Start the stage executor via the shared helper (the same path the
        # inbound webhook uses, so both actually run the stages — #1020).
        run, workflow_id = start_workflow_definition_run(
            workflow,
            trigger_payload=trigger_payload,
            organization_id=organization_id,
            actor=Actor(
                kind="user",
                user_id=user.pk if getattr(user, "pk", None) else None,
                display=getattr(user, "username", "") or "",
            ),
        )

        # Keep the existing instance surface populated (UI mirror). Agent stage
        # workflows don't use the state-machine ``states`` array, so point the
        # instance at the definition with a plain ``running`` state rather than
        # ``WorkflowInstance.start`` (which requires an initial state the stage
        # model doesn't declare).
        instance = WorkflowInstance.objects.create(
            workflow=workflow,
            content_type=ContentType.objects.get_for_model(WorkflowDefinition),
            object_id=workflow.pk,
            current_state="running",
            created_by=user,
            updated_by=user,
        )
        instance.temporal_workflow_id = workflow_id
        instance.save(update_fields=["temporal_workflow_id"])

        return RunWorkflowDefinitionResult(
            ok=True,
            workflow_run_id=str(run.pk),
            temporal_workflow_id=workflow_id,
        )

    @strawberry.mutation(description="Transition a workflow instance to a new state.")
    @require_permission(Permission.WORKFLOW_TRIGGER)
    @tenant_scoped()
    def transition_workflow(
        self,
        info: Info,
        instance_id: strawberry.ID,
        to_state: str,
        note: str = "",
    ) -> MutationResult:
        user = info.context.user
        # Caller-org instances plus legacy org-less rows (pre-denormalization
        # forms instances carry organization=NULL — spec 40 §2.3/§8); another
        # org's instance is not found.
        instance = (
            WorkflowInstance.objects.filter(pk=instance_id)
            .filter(Q(organization_id=_caller_org_pk()) | Q(organization__isnull=True))
            .first()
        )
        if not instance:
            raise GraphQLError(f"Workflow instance {instance_id} not found")

        if instance.is_completed:
            raise GraphQLError("Workflow is already completed")

        try:
            instance.transition(to_state, user, note)
            return MutationResult.success()
        except ValidationError as e:
            raise GraphQLError(str(e))

    @strawberry.mutation(description="Force a workflow instance to a specific state (admin override).")
    def override_workflow_state(
        self,
        info: Info,
        instance_id: strawberry.ID,
        to_state: str,
        note: str = "",
    ) -> MutationResult:
        user = info.context.user
        if not user.is_superuser:
            raise GraphQLError("Only superusers can override workflow state")

        instance = WorkflowInstance.objects.filter(pk=instance_id).first()
        if not instance:
            raise GraphQLError(f"Workflow instance {instance_id} not found")

        from workflows.models import TransitionLog

        from_state = instance.current_state
        instance.current_state = to_state
        instance.updated_by = user
        instance.save()

        TransitionLog.objects.create(
            instance=instance,
            from_state=from_state,
            to_state=to_state,
            transitioned_by=user,
            note=f"[ADMIN OVERRIDE] {note}",
        )

        return MutationResult.success()

    @strawberry.mutation(description="Create a new workflow definition (staff only).")
    def create_workflow_definition(
        self,
        info: Info,
        name: str,
        slug: str,
        model_label: str,
        states: strawberry.scalars.JSON,
        transitions: strawberry.scalars.JSON,
        description: Optional[str] = None,
        is_enabled: bool = False,
        pattern_kind: Optional[str] = None,
    ) -> MutationResult:
        user = info.context.user
        _require_staff(user)

        # pattern_kind drives the executor's composition (single / chained /
        # fan_out / ...). It existed on the model but had no creation arg, so
        # every API-created definition was stuck on the default "single" —
        # fan-out workflows were undefinable via the platform.
        valid_patterns = {c[0] for c in WorkflowDefinition.PatternKind.choices}
        if pattern_kind is not None and pattern_kind not in valid_patterns:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="pattern_kind", messages=[f'Invalid pattern_kind "{pattern_kind}"']
                    )
                ],
            )

        workflow = WorkflowDefinition(
            name=name,
            slug=slug,
            model_label=model_label,
            states=states,
            transitions=transitions,
            description=description or "",
            is_enabled=is_enabled,
            pattern_kind=pattern_kind or WorkflowDefinition.PatternKind.SINGLE,
            created_by=user,
            updated_by=user,
        )

        # Only validate if states are provided — empty workflows are valid
        # during creation (user will add states in the builder)
        if states:
            errors = workflow.validate_definition()
            if errors:
                return MutationResult(
                    ok=False,
                    errors=[GQLValidationError(field="definition", messages=errors)],
                )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f"Failed to create workflow definition: {e}")

        return MutationResult.success()

    @strawberry.mutation(description="Update an org-owned workflow definition (globals are read-only).")
    @require_permission(Permission.WORKFLOW_UPDATE)
    @tenant_scoped()
    def update_workflow_definition(
        self,
        info: Info,
        slug: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        model_label: Optional[str] = None,
        states: Optional[strawberry.scalars.JSON] = None,
        transitions: Optional[strawberry.scalars.JSON] = None,
        is_enabled: Optional[bool] = None,
        pattern_kind: Optional[str] = None,
    ) -> MutationResult:
        user = info.context.user

        workflow = _definition_for_write(slug)
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        if pattern_kind is not None:
            valid_patterns = {c[0] for c in WorkflowDefinition.PatternKind.choices}
            if pattern_kind not in valid_patterns:
                return MutationResult(
                    ok=False,
                    errors=[
                        GQLValidationError(
                            field="pattern_kind", messages=[f'Invalid pattern_kind "{pattern_kind}"']
                        )
                    ],
                )
            workflow.pattern_kind = pattern_kind

        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        if model_label is not None:
            workflow.model_label = model_label
        if states is not None:
            workflow.states = states
        if transitions is not None:
            workflow.transitions = transitions
        if is_enabled is not None:
            workflow.is_enabled = is_enabled

        workflow.updated_by = user

        # Agent/stage definitions carry no state machine (states == []) —
        # validate only when states exist, mirroring the create path.
        errors = workflow.validate_definition() if workflow.states else []
        if errors:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field="definition", messages=errors)],
            )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f"Failed to update workflow definition: {e}")

        return MutationResult.success()

    @strawberry.mutation(description="Soft-delete an org-owned workflow definition by slug.")
    @require_permission(Permission.WORKFLOW_DELETE)
    @tenant_scoped()
    def delete_workflow_definition(
        self,
        info: Info,
        slug: str,
    ) -> MutationResult:
        from django.utils import timezone

        user = info.context.user

        workflow = _definition_for_write(slug)
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        # PROTECT semantics for the tier-2 FK: a live configured Workflow
        # still runs off this shape — surface a clean error, not a 500.
        live_workflows = workflow.workflows.filter(deleted_at__isnull=True).count()
        if live_workflows:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="slug",
                        messages=[
                            f'Cannot delete workflow definition "{slug}" — '
                            f"{live_workflows} configured Workflow(s) still use it. "
                            f"Delete those first."
                        ],
                    )
                ],
            )

        # Same PROTECT semantics for webhook triggers (createWorkflowTrigger →
        # astrolift_agents.WorkflowWebhook, consumed by the inbound webhook
        # view). The model is not soft-deletable — rows are append-only and
        # retired via ``enabled`` — so "live" means enabled here.
        live_triggers = workflow.webhooks.filter(enabled=True).count()
        if live_triggers:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="slug",
                        messages=[
                            f'Cannot delete workflow definition "{slug}" — '
                            f"{live_triggers} enabled webhook trigger(s) still reference it. "
                            f"Disable those first."
                        ],
                    )
                ],
            )

        workflow.deleted_at = timezone.now()
        workflow.deleted_by = user
        workflow.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])
        return MutationResult.success()

    @strawberry.mutation(
        description=(
            "Add a stage to a writable workflow definition. order=null appends "
            "after the definition's last stage."
        )
    )
    @require_permission(Permission.WORKFLOW_UPDATE)
    @tenant_scoped()
    def create_workflow_stage(
        self,
        info: Info,
        workflow_slug: str,
        kind: str,
        order: int | None = None,
        role: str | None = None,
        on_failure: str = "fail",
        timeout_seconds: int = 300,
        agent_definition_guid: Optional[str] = None,
        skill_refs: Optional[strawberry.scalars.JSON] = None,
        fan_out_count: Optional[int] = None,
        prompt: str | None = None,
        approvers: strawberry.scalars.JSON | None = None,
    ) -> CreateWorkflowStageResult:
        user = info.context.user

        workflow = _definition_for_write(workflow_slug)
        if not workflow:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug", messages=[f'Workflow "{workflow_slug}" not found']
                    )
                ],
            )

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        # Validate kind
        valid_kinds = {c[0] for c in WorkflowStage.StageKind.choices}
        if kind not in valid_kinds:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field="kind", messages=[f'Invalid stage kind "{kind}"'])],
            )

        # Validate on_failure
        valid_failures = {c[0] for c in WorkflowStage.OnFailure.choices}
        if on_failure not in valid_failures:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[
                    GQLValidationError(field="on_failure", messages=[f'Invalid on_failure "{on_failure}"'])
                ],
            )

        # Resolve optional agent definition — scoped to the caller's org
        # (Workload's org lives via registered_app.organization); a foreign
        # org's workload resolves to not-found, never a cross-tenant binding.
        agent_definition = None
        if agent_definition_guid:
            from astrolift_registry.models import Workload

            agent_definition = Workload.objects.filter(
                guid=agent_definition_guid,
                registered_app__organization_id=_caller_org_pk(),
            ).first()
            if agent_definition is None:
                return CreateWorkflowStageResult(
                    ok=False,
                    errors=[
                        GQLValidationError(field="agent_definition_guid", messages=["Workload not found"])
                    ],
                )

        if order is None:
            # Append after the highest order ever used — soft-deleted stages
            # still occupy the (definition, order) unique constraint.
            from django.db.models import Max

            max_order = workflow.stages.aggregate(Max("order"))["order__max"]
            order = 0 if max_order is None else max_order + 1

        stage = WorkflowStage(
            definition=workflow,
            # Stage slug is unique (BaseCoreModel) but the mutation never set
            # it, so every stage defaulted to "none" and the 2nd stage created
            # anywhere collided — making multi-stage workflows impossible via
            # the API. Derive a unique, readable slug from (workflow, order).
            slug=f"{workflow.slug}-stage-{order}",
            order=order,
            kind=kind,
            role=role or "",
            on_failure=on_failure,
            timeout_seconds=timeout_seconds,
            agent_definition=agent_definition,
            skill_refs=skill_refs or [],
            fan_out_count=fan_out_count,
            prompt=prompt or "",
            approvers=approvers or [],
            created_by=user,
            updated_by=user,
        )

        try:
            stage.save()
        except Exception as e:
            raise GraphQLError(f"Failed to create workflow stage: {e}")

        return CreateWorkflowStageResult(ok=True, stage=stage)

    @strawberry.mutation(
        description="Create an inbound webhook trigger for a workflow definition (staff only)."
    )
    def create_workflow_trigger(self, info: Info, workflow_slug: str) -> CreateWorkflowTriggerResult:
        """Register a ``WorkflowWebhook`` that fires *workflow_slug* on inbound
        POST (#1019). The webhook is owned by the CALLER's active org — a
        WorkflowDefinition carries no org FK, and the org-level endpoint
        (``/api/webhooks/workflow/<org>/<slug>``) resolves + verifies by org —
        so the creator's org is the natural, secure owner. Returns the endpoint
        + plaintext signing secret (shown once). Fire with
        ``POST <endpoint>`` + header ``X-Astrolift-Signature: <secret>``.
        """
        from astrolift_agents.services.workflow_triggers import (
            create_webhook_workflow_trigger,
        )

        from astrolift_identity.models import Organization
        from core.tenancy import get_current_tenant

        user = info.context.user
        _require_staff(user)

        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug, deleted_at__isnull=True).first()
        if not workflow:
            return CreateWorkflowTriggerResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug", messages=[f'Workflow "{workflow_slug}" not found']
                    )
                ],
            )

        # The webhook is owned by the caller's org. WorkflowWebhook.organization
        # is an astrolift_identity.Organization (NOT the profile's
        # organization.Organization — distinct models), so resolve it from the
        # tenant context the same way the agent/managed-service mutations do.
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first() if org_pk else None
        if org is None:
            return CreateWorkflowTriggerResult(
                ok=False,
                errors=[
                    GQLValidationError(field="organization", messages=["no active organization in context"])
                ],
            )

        result = create_webhook_workflow_trigger(workflow, organization=org)
        return CreateWorkflowTriggerResult(
            ok=True,
            slug=result["slug"],
            endpoint=result["endpoint"],
            signing_secret=result["signing_secret"],
        )

    @strawberry.mutation(
        description=(
            "Deep-copy a visible workflow definition + its stages into the "
            "caller's org as a new editable definition (spec 40 §2.1). New slug "
            "on collision; global stages copy with agent_definition cleared."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE)
    @tenant_scoped()
    def clone_workflow_definition(
        self,
        info: Info,
        slug: str,
        org_id: strawberry.ID | None = None,
    ) -> CloneWorkflowDefinitionResult:
        user = info.context.user
        org, err = _resolve_clone_org(org_id)
        if err is not None:
            return err

        # Visible scope = caller's org UNION all platform-global definitions;
        # the org's own wins over a same-slug global (spec 40 §2.1).
        visible = WorkflowDefinition.objects.filter(slug=slug, deleted_at__isnull=True).filter(
            Q(organization_id=org.pk) | Q(organization__isnull=True)
        )
        source = visible.filter(organization_id=org.pk).first() or visible.first()
        if source is None:
            return CloneWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(field="slug", messages=[f'Workflow definition "{slug}" not visible'])
                ],
            )

        new_slug = _unique_clone_slug(source.slug, org)
        same_org = source.organization_id == org.pk
        with transaction.atomic():
            clone = WorkflowDefinition.objects.create(
                organization=org,
                name=source.name,
                slug=new_slug,
                description=source.description or "",
                model_label=source.model_label,
                pattern_kind=source.pattern_kind,
                states=source.states,
                transitions=source.transitions,
                is_enabled=source.is_enabled,
                created_by=user,
                updated_by=user,
            )
            for stage in source.stages.filter(deleted_at__isnull=True).order_by("order"):
                WorkflowStage.objects.create(
                    definition=clone,
                    slug=f"{new_slug}-stage-{stage.order}",
                    order=stage.order,
                    kind=stage.kind,
                    role=stage.role,
                    # Globals carry no org agent — clear unless cloning within
                    # the same org (spec 40 §2.1/§2.4).
                    agent_definition=(stage.agent_definition if same_org else None),
                    skill_refs=list(stage.skill_refs or []),
                    fan_out_count=stage.fan_out_count,
                    fan_out_dynamic=stage.fan_out_dynamic,
                    on_failure=stage.on_failure,
                    timeout_seconds=stage.timeout_seconds,
                    prompt=stage.prompt,
                    approvers=list(stage.approvers or []),
                    created_by=user,
                    updated_by=user,
                )
        return CloneWorkflowDefinitionResult(ok=True, slug=new_slug, definition=clone)
