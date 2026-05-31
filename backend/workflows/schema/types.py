from __future__ import annotations

from datetime import datetime
from typing import Optional

import strawberry
import strawberry_django
from strawberry.types import Info

from workflows.models import (
    TransitionLog,
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
    skill_refs: strawberry.scalars.JSON
    fan_out_count: Optional[int]
    on_failure: str
    timeout_seconds: int
    created_at: datetime

    @strawberry_django.field
    def agent_definition_guid(self) -> Optional[str]:
        if self.agent_definition_id is None:
            return None
        return str(self.agent_definition.guid)

    @strawberry_django.field
    def agent_definition_name(self) -> Optional[str]:
        if self.agent_definition_id is None:
            return None
        return self.agent_definition.name


@strawberry_django.type(WorkflowStageExecution)
class WorkflowStageExecutionType:
    guid: strawberry.ID
    status: str
    attempt_number: int
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    output: Optional[strawberry.scalars.JSON]
    failure: Optional[strawberry.scalars.JSON]
    error_message: str
    created_at: datetime

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
    def agent_run_guid(self) -> Optional[str]:
        if self.agent_run_id is None:
            return None
        return str(self.agent_run.guid)


@strawberry_django.type(WorkflowDefinition)
class WorkflowDefinitionType:
    name: str
    slug: str
    description: Optional[str]
    model_label: str
    pattern_kind: str
    states: strawberry.scalars.JSON
    transitions: strawberry.scalars.JSON
    is_enabled: bool
    created_at: datetime

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
    username: Optional[str]


@strawberry_django.type(WorkflowInstance)
class WorkflowInstanceType:
    current_state: str
    started_at: datetime
    completed_at: Optional[datetime]
    object_id: int

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
                from_state=t['from_state'],
                to_state=t['to_state'],
                label=t.get('label', ''),
                conditions_met=t.get('conditions_met', True),
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
            for log in self.transition_logs.select_related('transitioned_by').all()[:50]
        ]
