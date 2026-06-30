"""Tests for agent workflow patterns + stage execution tracking (#45, #46)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]

MINIMAL_TRANSITIONS = [
    {"from_state": "pending", "to_state": "done", "label": "Complete"},
]


@pytest.fixture
def workflow_def(db):
    return WorkflowDefinition.objects.create(
        name="Fan-out pipeline",
        slug="fan-out-pipeline",
        model_label="workflows.workflowdefinition",
        pattern_kind=WorkflowDefinition.PatternKind.FAN_OUT,
        states=MINIMAL_STATES,
        transitions=MINIMAL_TRANSITIONS,
        is_enabled=True,
    )


@pytest.fixture
def workflow_run(db, workflow_def):
    """Minimal WorkflowRun row — no Temporal needed, FK is all we need."""
    from astrolift_operations.models import WorkflowRun

    # WorkflowRun is identified by workflow_kind / workflow_id / run_id —
    # it has no name/slug (not a NamedBaseCoreModel).
    return WorkflowRun.objects.create(
        workflow_kind="FanOutAgentWorkflow",
        workflow_id="test-workflow-001",
        run_id="test-run-001",
        status=WorkflowRun.Status.RUNNING,
    )


# ---------------------------------------------------------------------------
# #45 — PatternKind on WorkflowDefinition
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_workflow_definition_pattern_kind_fan_out(workflow_def):
    """WorkflowDefinition can be created with pattern_kind='fan_out'."""
    assert workflow_def.pattern_kind == WorkflowDefinition.PatternKind.FAN_OUT
    assert workflow_def.pattern_kind == "fan_out"


@pytest.mark.django_db
def test_workflow_definition_pattern_kind_default(db):
    """Default pattern_kind is 'single'."""
    wd = WorkflowDefinition.objects.create(
        name="Simple single",
        slug="simple-single",
        model_label="workflows.workflowdefinition",
        states=MINIMAL_STATES,
        transitions=MINIMAL_TRANSITIONS,
    )
    assert wd.pattern_kind == WorkflowDefinition.PatternKind.SINGLE


@pytest.mark.django_db
def test_all_pattern_kinds_valid(db):
    """All PatternKind values are accepted by the model."""
    for kind in WorkflowDefinition.PatternKind:
        wd = WorkflowDefinition.objects.create(
            name=f"Pattern {kind}",
            slug=f"pattern-{kind}",
            model_label="workflows.workflowdefinition",
            pattern_kind=kind,
            states=MINIMAL_STATES,
            transitions=MINIMAL_TRANSITIONS,
        )
        assert wd.pattern_kind == kind


# ---------------------------------------------------------------------------
# #45 — WorkflowStage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_workflow_stage_can_be_created(workflow_def):
    """WorkflowStage can be created with kind='agent_dispatch'."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        timeout_seconds=120,
    )
    assert stage.kind == "agent_dispatch"
    assert stage.order == 0
    assert stage.definition == workflow_def
    assert stage.on_failure == WorkflowStage.OnFailure.FAIL
    assert stage.timeout_seconds == 120
    assert stage.skill_refs == []
    assert stage.fan_out_count is None


@pytest.mark.django_db
def test_workflow_stage_fan_out_count(workflow_def):
    """fan_out_count is stored and retrieved correctly."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        fan_out_count=8,
    )
    stage.refresh_from_db()
    assert stage.fan_out_count == 8


@pytest.mark.django_db
def test_workflow_stage_human_gate(workflow_def):
    """HUMAN_GATE stage needs no agent_definition."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        on_failure=WorkflowStage.OnFailure.ESCALATE,
    )
    assert stage.kind == "human_gate"
    assert stage.agent_definition is None
    assert stage.on_failure == "escalate"


@pytest.mark.django_db
def test_workflow_stage_ordering(workflow_def):
    """Stages are returned ordered by (definition, order)."""
    # Stage slug is unique (BaseCoreModel); mirror the mutation and give
    # each a per-(definition, order) slug, else all default to "none" and
    # the second insert collides.
    WorkflowStage.objects.create(definition=workflow_def, order=2, kind="checkpoint", slug="stage-ord-2")
    WorkflowStage.objects.create(definition=workflow_def, order=0, kind="agent_dispatch", slug="stage-ord-0")
    WorkflowStage.objects.create(definition=workflow_def, order=1, kind="human_gate", slug="stage-ord-1")

    orders = list(WorkflowStage.objects.filter(definition=workflow_def).values_list("order", flat=True))
    assert orders == [0, 1, 2]


@pytest.mark.django_db
def test_workflow_stage_unique_constraint(workflow_def):
    """Two stages at the same order in the same definition are rejected."""
    from django.db import IntegrityError

    WorkflowStage.objects.create(definition=workflow_def, order=0, kind="agent_dispatch")
    with pytest.raises(IntegrityError):
        WorkflowStage.objects.create(definition=workflow_def, order=0, kind="checkpoint")


# ---------------------------------------------------------------------------
# #46 — WorkflowStageExecution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_workflow_stage_run_tracks_status(workflow_def, workflow_run):
    """WorkflowStageExecution tracks a stage's execution status."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
    )
    execution = WorkflowStageExecution.objects.create(
        workflow_run=workflow_run,
        stage=stage,
        status=WorkflowStageExecution.Status.PENDING,
    )
    assert execution.status == "pending"
    assert execution.workflow_run == workflow_run
    assert execution.stage == stage
    assert execution.agent_run is None
    assert execution.started_at is None
    assert execution.ended_at is None
    assert execution.attempt_number == 1
    assert not execution.is_terminal


@pytest.mark.django_db
def test_status_transitions_pending_running_completed(workflow_def, workflow_run):
    """Status transitions: pending → running → completed."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
    )
    execution = WorkflowStageExecution.objects.create(
        workflow_run=workflow_run,
        stage=stage,
        status=WorkflowStageExecution.Status.PENDING,
    )
    assert execution.status == WorkflowStageExecution.Status.PENDING
    assert not execution.is_terminal

    # → running
    execution.status = WorkflowStageExecution.Status.RUNNING
    execution.started_at = timezone.now()
    execution.save()
    execution.refresh_from_db()
    assert execution.status == "running"
    assert execution.started_at is not None
    assert not execution.is_terminal

    # → completed
    execution.status = WorkflowStageExecution.Status.COMPLETED
    execution.ended_at = timezone.now()
    execution.output = {"result": "ok"}
    execution.save()
    execution.refresh_from_db()
    assert execution.status == "completed"
    assert execution.ended_at is not None
    assert execution.output == {"result": "ok"}
    assert execution.is_terminal


@pytest.mark.django_db
def test_terminal_statuses_are_terminal(workflow_def, workflow_run):
    """All terminal status values are correctly identified."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
    )
    terminal_statuses = [
        WorkflowStageExecution.Status.COMPLETED,
        WorkflowStageExecution.Status.FAILED,
        WorkflowStageExecution.Status.SKIPPED,
        WorkflowStageExecution.Status.ESCALATED,
        WorkflowStageExecution.Status.CANCELLED,
    ]
    for i, status in enumerate(terminal_statuses):
        # Execution slug is unique (BaseCoreModel); without an explicit
        # slug every row defaults to "none" and the loop's 2nd insert
        # collides. Mirror the activity layer's _unique_slug pattern.
        execution = WorkflowStageExecution.objects.create(
            slug=f"wfse-term-{i}",
            workflow_run=workflow_run,
            stage=stage,
            status=status,
            attempt_number=i + 1,
        )
        assert execution.is_terminal, f"{status} should be terminal"


@pytest.mark.django_db
def test_pending_and_running_are_not_terminal(workflow_def, workflow_run):
    """PENDING and RUNNING are not terminal."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
    )
    for status in (WorkflowStageExecution.Status.PENDING, WorkflowStageExecution.Status.RUNNING):
        # Unique slug per row — see test_terminal_statuses_are_terminal.
        execution = WorkflowStageExecution.objects.create(
            slug=f"wfse-{status}",
            workflow_run=workflow_run,
            stage=stage,
            status=status,
        )
        assert not execution.is_terminal, f"{status} should not be terminal"


@pytest.mark.django_db
def test_workflow_run_current_stage_execution(workflow_def, workflow_run):
    """WorkflowRun.current_stage_execution FK can be set and followed."""
    stage = WorkflowStage.objects.create(
        definition=workflow_def,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
    )
    execution = WorkflowStageExecution.objects.create(
        workflow_run=workflow_run,
        stage=stage,
        status=WorkflowStageExecution.Status.RUNNING,
        started_at=timezone.now(),
    )
    workflow_run.current_stage_execution = execution
    workflow_run.save()
    workflow_run.refresh_from_db()
    assert workflow_run.current_stage_execution_id == execution.pk
