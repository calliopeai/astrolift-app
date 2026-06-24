from __future__ import annotations

from typing import Optional

import strawberry
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from graphql import GraphQLError
from strawberry.types import Info

from core.schema.common import MutationResult
from core.schema.common import ValidationError as GQLValidationError
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.schema.types import WorkflowStageType


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
class CreateWorkflowTriggerResult(MutationResult):
    """Inbound webhook trigger for a workflow definition (#1019).
    ``signing_secret`` is the plaintext — shown ONCE, stored only as a hash."""

    slug: Optional[str] = None
    endpoint: Optional[str] = None
    signing_secret: Optional[str] = None


def _require_staff(user):
    """Raise GraphQLError if user is not authenticated staff/superuser."""
    if not user or not user.is_authenticated:
        raise GraphQLError('Authentication required')
    if not (user.is_staff or user.is_superuser):
        raise GraphQLError('Staff or superuser access required')


@strawberry.type
class Mutation:

    @strawberry.mutation(description="Start a workflow for an object.")
    def start_workflow(
        self, info: Info, workflow_slug: str, model_label: str, object_id: int,
    ) -> StartWorkflowResult:
        user = info.context.user
        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug, is_enabled=True).first()
        if not workflow:
            raise GraphQLError(f'Workflow "{workflow_slug}" not found or disabled')

        # Resolve the model
        try:
            parts = model_label.split('.')
            model = apps.get_model(parts[0], parts[-1])
            obj = model.objects.get(pk=object_id)
        except Exception as e:
            raise GraphQLError(f'Object not found: {model_label}:{object_id} — {e}')

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
                        messages=[
                            f'Workflow "{workflow_slug}" has no stages to execute'
                        ],
                    )
                ],
            )

        payload = dict(trigger_payload or {})

        # Imports kept local so this engine app's schema module doesn't pull
        # the Temporal client + operations models at import time (the app is
        # feature-gated).
        from django.utils import timezone

        from astrolift_operations.models import WorkflowRun
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        organization_id = getattr(tenant, "organization_id", None) if tenant else None

        # The WorkflowRun mirror is the executor's source of truth; create
        # it first so the workflow id can key off its pk, then enqueue, then
        # backfill the Temporal run_id.
        run = WorkflowRun.objects.create(
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id="",  # filled in below once the id is known
            run_id="",
            status=WorkflowRun.Status.RUNNING,
            started_at=timezone.now(),
            organization_id=organization_id,
            trigger_actor_user_id=user.pk if getattr(user, "pk", None) else None,
        )

        # A WorkflowInstance keeps the existing instance surface populated.
        # Agent stage workflows don't use the state-machine ``states`` array,
        # so we point the instance at the definition itself and set a plain
        # ``running`` state rather than going through ``WorkflowInstance.start``
        # (which requires an initial state the stage model doesn't declare).
        instance = WorkflowInstance.objects.create(
            workflow=workflow,
            content_type=ContentType.objects.get_for_model(WorkflowDefinition),
            object_id=workflow.pk,
            current_state="running",
            created_by=user,
            updated_by=user,
        )

        workflow_id = f"WorkflowDefinitionRunWorkflow-{run.pk}"
        run.workflow_id = workflow_id
        run.save(update_fields=["workflow_id", "updated_at", "version"])

        handle = start_workflow(
            "WorkflowDefinitionRunWorkflow",
            args=[
                WorkflowDefinitionRunInput(
                    workflow_definition_slug=workflow.slug,
                    workflow_run_id=str(run.pk),
                    trigger_payload=payload,
                    actor=Actor(
                        kind="user",
                        user_id=user.pk if getattr(user, "pk", None) else None,
                        display=getattr(user, "username", "") or "",
                    ),
                )
            ],
            workflow_id=workflow_id,
        )

        if handle.enqueued and handle.run_id:
            run.run_id = handle.run_id
            run.save(update_fields=["run_id", "updated_at", "version"])

        instance.temporal_workflow_id = workflow_id
        instance.save(update_fields=["temporal_workflow_id"])

        return RunWorkflowDefinitionResult(
            ok=True,
            workflow_run_id=str(run.pk),
            temporal_workflow_id=workflow_id,
        )

    @strawberry.mutation(description="Transition a workflow instance to a new state.")
    def transition_workflow(
        self, info: Info, instance_id: strawberry.ID, to_state: str, note: str = '',
    ) -> MutationResult:
        user = info.context.user
        instance = WorkflowInstance.objects.filter(pk=instance_id).first()
        if not instance:
            raise GraphQLError(f'Workflow instance {instance_id} not found')

        if instance.is_completed:
            raise GraphQLError('Workflow is already completed')

        try:
            instance.transition(to_state, user, note)
            return MutationResult.success()
        except ValidationError as e:
            raise GraphQLError(str(e))

    @strawberry.mutation(description="Force a workflow instance to a specific state (admin override).")
    def override_workflow_state(
        self, info: Info, instance_id: strawberry.ID, to_state: str, note: str = '',
    ) -> MutationResult:
        user = info.context.user
        if not user.is_superuser:
            raise GraphQLError('Only superusers can override workflow state')

        instance = WorkflowInstance.objects.filter(pk=instance_id).first()
        if not instance:
            raise GraphQLError(f'Workflow instance {instance_id} not found')

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
            note=f'[ADMIN OVERRIDE] {note}',
        )

        return MutationResult.success()

    @strawberry.mutation(description="Create a new workflow definition (staff only).")
    def create_workflow_definition(
        self, info: Info,
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
                errors=[GQLValidationError(field='pattern_kind', messages=[f'Invalid pattern_kind "{pattern_kind}"'])],
            )

        workflow = WorkflowDefinition(
            name=name,
            slug=slug,
            model_label=model_label,
            states=states,
            transitions=transitions,
            description=description or '',
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
                    errors=[GQLValidationError(field='definition', messages=errors)],
                )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f'Failed to create workflow definition: {e}')

        return MutationResult.success()

    @strawberry.mutation(description="Update an existing workflow definition (staff only).")
    def update_workflow_definition(
        self, info: Info,
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
        _require_staff(user)

        workflow = WorkflowDefinition.objects.filter(slug=slug).first()
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        if pattern_kind is not None:
            valid_patterns = {c[0] for c in WorkflowDefinition.PatternKind.choices}
            if pattern_kind not in valid_patterns:
                return MutationResult(
                    ok=False,
                    errors=[GQLValidationError(field='pattern_kind', messages=[f'Invalid pattern_kind "{pattern_kind}"'])],
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

        errors = workflow.validate_definition()
        if errors:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field='definition', messages=errors)],
            )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f'Failed to update workflow definition: {e}')

        return MutationResult.success()

    @strawberry.mutation(description="Delete a workflow definition by slug (staff only).")
    def delete_workflow_definition(
        self, info: Info,
        slug: str,
    ) -> MutationResult:
        user = info.context.user
        _require_staff(user)

        workflow = WorkflowDefinition.objects.filter(slug=slug).first()
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        if workflow.instances.exists():
            raise GraphQLError(
                f'Cannot delete workflow "{slug}" — it has {workflow.instances.count()} existing instance(s). '
                f'Disable it instead.'
            )

        try:
            workflow.delete()
        except Exception as e:
            raise GraphQLError(f'Failed to delete workflow definition: {e}')

        return MutationResult.success()

    @strawberry.mutation(description="Add a stage to an agent workflow definition (staff only).")
    def create_workflow_stage(
        self,
        info: Info,
        workflow_slug: str,
        order: int,
        kind: str,
        on_failure: str = "fail",
        timeout_seconds: int = 300,
        agent_definition_guid: Optional[str] = None,
        skill_refs: Optional[strawberry.scalars.JSON] = None,
        fan_out_count: Optional[int] = None,
    ) -> CreateWorkflowStageResult:
        user = info.context.user
        _require_staff(user)

        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug).first()
        if not workflow:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field='workflow_slug', messages=[f'Workflow "{workflow_slug}" not found'])],
            )

        # Validate kind
        valid_kinds = {c[0] for c in WorkflowStage.StageKind.choices}
        if kind not in valid_kinds:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field='kind', messages=[f'Invalid stage kind "{kind}"'])],
            )

        # Validate on_failure
        valid_failures = {c[0] for c in WorkflowStage.OnFailure.choices}
        if on_failure not in valid_failures:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field='on_failure', messages=[f'Invalid on_failure "{on_failure}"'])],
            )

        # Resolve optional agent definition
        agent_definition = None
        if agent_definition_guid:
            from astrolift_registry.models import Workload
            agent_definition = Workload.objects.filter(guid=agent_definition_guid).first()
            if agent_definition is None:
                return CreateWorkflowStageResult(
                    ok=False,
                    errors=[GQLValidationError(field='agent_definition_guid', messages=['Workload not found'])],
                )

        stage = WorkflowStage(
            definition=workflow,
            # Stage slug is unique (BaseCoreModel) but the mutation never set
            # it, so every stage defaulted to "none" and the 2nd stage created
            # anywhere collided — making multi-stage workflows impossible via
            # the API. Derive a unique, readable slug from (workflow, order).
            slug=f"{workflow.slug}-stage-{order}",
            order=order,
            kind=kind,
            on_failure=on_failure,
            timeout_seconds=timeout_seconds,
            agent_definition=agent_definition,
            skill_refs=skill_refs or [],
            fan_out_count=fan_out_count,
            created_by=user,
            updated_by=user,
        )

        try:
            stage.save()
        except Exception as e:
            raise GraphQLError(f'Failed to create workflow stage: {e}')

        return CreateWorkflowStageResult(ok=True, stage=stage)

    @strawberry.mutation(description="Create an inbound webhook trigger for a workflow definition (staff only).")
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

        workflow = WorkflowDefinition.objects.filter(
            slug=workflow_slug, deleted_at__isnull=True
        ).first()
        if not workflow:
            return CreateWorkflowTriggerResult(
                ok=False,
                errors=[GQLValidationError(field='workflow_slug', messages=[f'Workflow "{workflow_slug}" not found'])],
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
                errors=[GQLValidationError(field='organization', messages=['no active organization in context'])],
            )

        result = create_webhook_workflow_trigger(workflow, organization=org)
        return CreateWorkflowTriggerResult(
            ok=True,
            slug=result["slug"],
            endpoint=result["endpoint"],
            signing_secret=result["signing_secret"],
        )
