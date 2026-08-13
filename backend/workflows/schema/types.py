from __future__ import annotations

from datetime import datetime

import strawberry
import strawberry_django
from django.core.exceptions import ObjectDoesNotExist
from strawberry.types import Info

from workflows.models import (
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStage,
    WorkflowStageExecution,
)


@strawberry_django.type(WorkflowStage)
class WorkflowStageType:
    guid: strawberry.ID
    order: int
    kind: str
    role: str
    prompt: str
    agent_ref: str
    workflow_ref: str
    environment_spec_slug: str
    output_key: str
    approvers: strawberry.scalars.JSON
    skill_refs: strawberry.scalars.JSON
    fan_out_count: int | None
    on_failure: str
    timeout_seconds: int
    created_at: datetime

    @strawberry_django.field
    def agent_definition_guid(self) -> str | None:
        if self.agent_definition_id is None:
            return None
        return str(self.agent_definition.guid)

    @strawberry_django.field
    def agent_definition_name(self) -> str | None:
        if self.agent_definition_id is None:
            return None
        return self.agent_definition.name


@strawberry_django.type(WorkflowStageExecution)
class WorkflowStageExecutionType:
    guid: strawberry.ID
    status: str
    attempt_number: int
    started_at: datetime | None
    ended_at: datetime | None
    output: strawberry.scalars.JSON | None
    failure: strawberry.scalars.JSON | None
    error_message: str
    created_at: datetime

    @strawberry_django.field
    def execution_id(self) -> str:
        """The stage-execution pk as a string — the id a ``human_gate_decision``
        / ``escalation_cleared`` signal must target (the executor keys
        ``_gate_decisions`` on this value from ``create_stage_execution``).
        Exposed so an operator can approve a gate via ``signalWorkflowInstance``
        without a DB lookup — without it the approve path wasn't reachable
        through the platform (#1018)."""
        return str(self.pk)

    @strawberry_django.field
    def stage_guid(self) -> str:
        return str(self.stage.guid)

    @strawberry_django.field
    def stage_kind(self) -> str:
        return self.stage.kind

    @strawberry_django.field
    def stage_order(self) -> int:
        return self.stage.order

    @strawberry_django.field
    def agent_run_guid(self) -> str | None:
        if self.agent_run_id is None:
            return None
        return str(self.agent_run.guid)

    @strawberry_django.field
    def child_workflow_run_guid(self) -> str | None:
        try:
            child = self.child_workflow_run
        except ObjectDoesNotExist:
            return None
        return str(child.guid)

    @strawberry_django.field
    def child_workflow_definition_slug(self) -> str | None:
        try:
            child = self.child_workflow_run
        except ObjectDoesNotExist:
            return None
        definition = child.workflow_definition
        return definition.slug if definition is not None else None

    @strawberry_django.field
    def child_workflow_status(self) -> str | None:
        try:
            return self.child_workflow_run.status
        except ObjectDoesNotExist:
            return None


@strawberry_django.type(WorkflowDefinition)
class WorkflowDefinitionType:
    name: str
    slug: str
    description: str | None
    model_label: str
    pattern_kind: str
    states: strawberry.scalars.JSON
    transitions: strawberry.scalars.JSON
    is_enabled: bool
    source_repo: str
    source_path: str
    source_ref: str
    created_at: datetime

    @strawberry_django.field
    def organization_guid(self) -> str | None:
        """Owning org's guid, or null for a platform-global template (spec 40 §2.1)."""
        if self.organization_id is None:
            return None
        return str(self.organization.guid)

    @strawberry_django.field
    def project_guid(self) -> str | None:
        if self.project_id is None:
            return None
        return str(self.project.guid)

    @strawberry_django.field
    def project_slug(self) -> str:
        return self.project.slug if self.project_id is not None else ""

    @strawberry_django.field
    def instance_count(self) -> int:
        return self.instances.count()

    @strawberry_django.field
    def active_instance_count(self) -> int:
        return self.instances.filter(completed_at__isnull=True).count()

    @strawberry_django.field
    def workflow_stages(self) -> list[WorkflowStageType]:
        return self.stages.select_related("agent_definition").order_by("order")


@strawberry.type
class AvailableTransition:
    from_state: str
    to_state: str
    label: str
    conditions_met: bool


@strawberry.type
class TransitionLogEntry:
    from_state: str
    to_state: str
    note: str
    timestamp: datetime
    username: str | None


@strawberry_django.type(WorkflowInstance)
class WorkflowInstanceType:
    current_state: str
    started_at: datetime
    completed_at: datetime | None
    object_id: int | None

    @strawberry_django.field
    def workflow_name(self) -> str:
        return self.workflow.name

    @strawberry_django.field
    def workflow_slug(self) -> str:
        return self.workflow.slug

    @strawberry_django.field
    def is_completed(self) -> bool:
        return self.completed_at is not None

    @strawberry_django.field
    def state_label(self) -> str:
        return self.workflow.get_state_label(self.current_state)

    @strawberry_django.field
    def available_transitions(self, info: Info) -> list[AvailableTransition]:
        user = info.context.user
        transitions = self.get_available_transitions(user)
        return [
            AvailableTransition(
                from_state=t["from_state"],
                to_state=t["to_state"],
                label=t.get("label", ""),
                conditions_met=t.get("conditions_met", True),
            )
            for t in transitions
        ]

    @strawberry_django.field
    def history(self) -> list[TransitionLogEntry]:
        return [
            TransitionLogEntry(
                from_state=log.from_state,
                to_state=log.to_state,
                note=log.note,
                timestamp=log.timestamp,
                username=log.transitioned_by.username if log.transitioned_by else None,
            )
            for log in self.transition_logs.select_related("transitioned_by").all()[:50]
        ]
