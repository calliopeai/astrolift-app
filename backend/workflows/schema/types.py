from __future__ import annotations

from datetime import datetime

import strawberry
import strawberry_django
from django.core.exceptions import ObjectDoesNotExist
from strawberry.types import Info

from astrolift_workflows.schema.stage_temporal_types import (
    WorkflowStageTemporalExecution,
    stage_temporal_execution,
)
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
    max_attempts: int
    iteration: strawberry.scalars.JSON
    back_edge: strawberry.scalars.JSON
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


def _gate_payload(output) -> dict:
    """The ``human_gate`` sub-object a decision is recorded under, or ``{}``.

    ``record_human_gate_decision`` writes
    ``{"human_gate": {"decision", "decided_by_user_id", "note"}}`` onto the
    execution's ``output``. That column is free-form JSON every other stage
    kind also writes to (a chained stage's output can be a list, a scalar,
    or a dict whose ``human_gate`` key is the string an approved gate
    returns downstream), so every access is guarded.
    """
    if not isinstance(output, dict):
        return {}
    payload = output.get("human_gate")
    return payload if isinstance(payload, dict) else {}


@strawberry.type
class WorkflowLoopCause:
    edge: str
    reason: str
    max_rounds: int
    edge_round: int


@strawberry_django.type(WorkflowStageExecution)
class WorkflowStageExecutionType:
    guid: strawberry.ID
    status: str
    attempt_number: int
    round_number: int
    collection_index: int | None
    fanout_index: int | None
    started_at: datetime | None
    ended_at: datetime | None
    output: strawberry.scalars.JSON | None
    failure: strawberry.scalars.JSON | None
    error_message: str
    created_at: datetime

    @strawberry_django.field
    def temporal_execution(self) -> WorkflowStageTemporalExecution | None:
        return stage_temporal_execution(self)

    @strawberry_django.field
    def caused_by(self) -> WorkflowLoopCause | None:
        cause = self.caused_by
        if not isinstance(cause, dict) or not {"edge", "reason", "max_rounds", "edge_round"} <= cause.keys():
            return None
        if not isinstance(cause["edge"], str) or not isinstance(cause["reason"], str):
            return None
        if type(cause["max_rounds"]) is not int or type(cause["edge_round"]) is not int:
            return None
        if not 1 <= cause["edge_round"] <= cause["max_rounds"] <= 20:
            return None
        return WorkflowLoopCause(edge=cause["edge"], reason=cause["reason"], max_rounds=cause["max_rounds"], edge_round=cause["edge_round"])

    @strawberry_django.field
    def fanout_stage_id(self) -> str | None:
        parent = self.fanout_parent_execution
        return (
            str(parent.stage.guid)
            if parent is not None and parent.workflow_run_id == self.workflow_run_id and parent.stage_id == self.stage_id
            else None
        )

    @strawberry_django.field
    def fanout_parent_execution_guid(self) -> str | None:
        parent = self.fanout_parent_execution
        return (
            str(parent.guid)
            if parent is not None and parent.workflow_run_id == self.workflow_run_id and parent.stage_id == self.stage_id
            else None
        )

    @strawberry_django.field
    def collection_stage_id(self) -> str | None:
        parent = self.collection_parent_execution
        return str(parent.stage.guid) if parent is not None and parent.workflow_run_id == self.workflow_run_id and parent.stage.definition_id == self.stage.definition_id else None

    @strawberry_django.field
    def collection_parent_execution_guid(self) -> str | None:
        parent = self.collection_parent_execution
        return str(parent.guid) if parent is not None and parent.workflow_run_id == self.workflow_run_id and parent.stage.definition_id == self.stage.definition_id else None

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
    def stage_role(self) -> str:
        """The stage's role label — the human-readable "who does this" that
        pairs with ``stage_kind`` when rendering a run's stage list."""
        return self.stage.role or ""

    @strawberry_django.field
    def stage_approvers(self) -> list[str]:
        """The stage's declared approvers (team / role slugs) — who a gate in
        ``human_gate_state: pending`` is waiting on. Empty for a stage that
        declares none, and for every non-gate kind."""
        approvers = self.stage.approvers
        if not isinstance(approvers, list):
            return []
        return [str(a) for a in approvers if str(a).strip()]

    @strawberry_django.field
    def human_gate_state(self) -> str:
        """Read-only gate state for a ``human_gate`` stage; ``""`` for every
        other stage kind.

        There is no separate gate table — this execution row IS the durable
        record (``record_human_gate_decision`` writes the outcome onto
        ``output`` and closes the row), so the state is derived from it:

        * ``pending`` — the gate is open and no decision has been recorded.
          This describes the STAGE, so a caller rendering "waiting on
          approval" should also check the run's own state: legacy runs can
          retain open rows until their execution records are reconciled.
        * ``approved`` / ``rejected`` — the recorded decision. A gate that
          runs out its timeout is recorded as ``rejected`` with
          ``human_gate_note`` ``"gate timed out"``.
        * ``closed`` — the row reached a terminal status with no decision
          recorded.
        """
        if self.stage.kind != WorkflowStage.StageKind.HUMAN_GATE:
            return ""
        decision = _gate_payload(self.output).get("decision")
        if decision in ("approved", "rejected"):
            return str(decision)
        return "closed" if self.is_terminal else "pending"

    @strawberry_django.field
    def human_gate_note(self) -> str:
        """The note recorded alongside a gate decision (``"gate timed out"``
        for an expired gate). Empty until a decision lands."""
        return str(_gate_payload(self.output).get("note") or "")

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
