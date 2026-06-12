"""Real-DB tests for the WorkflowDefinition stage-executor activities.

The Temporal activity wrappers are thin (``activity.heartbeat()`` +
``sync_to_async`` of a ``_xxx_sync`` helper), so these exercise the sync
helpers directly against the database — the same way the platform's other
activity tests verify the DB-touching half without standing up a Temporal
environment.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.workflow_stage_activities import (
    _aggregate_fan_out_sync,
    _create_stage_execution_sync,
    _dispatch_agent_for_stage_sync,
    _get_workflow_stages_sync,
    _mark_workflow_run_sync,
    _record_human_gate_decision_sync,
    _snapshot_checkpoint_sync,
    _update_stage_execution_sync,
)
from workflows.models import (
    WorkflowDefinition,
    WorkflowStage,
    WorkflowStageExecution,
)

MINIMAL_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Stage Org", slug="stage-org-test")


@pytest.fixture
def agent_workload(db, org):
    """A minimal agent-kind Workload to act as a stage's agent_definition."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=org, name="T", slug="t-stage")
    project = Project.objects.create(
        organization=org, team=team, name="P", slug="p-stage"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Agent Host",
        slug="agent-host",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name="Reviewer Agent",
        slug="reviewer-agent",
        kind=Workload.Kind.AGENT,
    )


@pytest.fixture
def definition(db, agent_workload):
    wd = WorkflowDefinition.objects.create(
        name="Stage Pipeline",
        slug="stage-pipeline",
        model_label="workflows.workflowdefinition",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=MINIMAL_STATES,
        transitions=[],
        is_enabled=True,
    )
    # WorkflowStage extends the slugged core.models.common.BaseCoreModel —
    # its slug is unique and defaults to the literal "none" when unset, so
    # each stage needs its own slug to avoid a collision.
    WorkflowStage.objects.create(
        slug="stage-pipeline-s0",
        definition=wd,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        on_failure=WorkflowStage.OnFailure.RETRY,
        timeout_seconds=120,
        agent_definition=agent_workload,
        skill_refs=["lint", "review"],
    )
    WorkflowStage.objects.create(
        slug="stage-pipeline-s1",
        definition=wd,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        on_failure=WorkflowStage.OnFailure.FAIL,
        timeout_seconds=300,
    )
    WorkflowStage.objects.create(
        slug="stage-pipeline-s2",
        definition=wd,
        order=2,
        kind=WorkflowStage.StageKind.CHECKPOINT,
    )
    return wd


@pytest.fixture
def run(db, org, definition):
    # WorkflowRun extends the non-slugged core.models.base.BaseCoreModel —
    # it has no name/slug fields.
    return WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-1",
        run_id="",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
    )


def _stage(definition: WorkflowDefinition, order: int) -> WorkflowStage:
    return definition.stages.get(order=order)


# ---------------------------------------------------------------------------
# get_workflow_stages
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_workflow_stages_returns_ordered_dicts(definition):
    result = _get_workflow_stages_sync(definition.slug)
    assert result["pattern_kind"] == "chained"
    stages = result["stages"]
    assert [s["order"] for s in stages] == [0, 1, 2]

    agent_stage = stages[0]
    assert agent_stage["kind"] == "agent_dispatch"
    assert agent_stage["on_failure"] == "retry"
    assert agent_stage["timeout_seconds"] == 120
    assert agent_stage["skill_refs"] == ["lint", "review"]
    assert agent_stage["has_agent_definition"] is True
    # stage_id is the DB pk as a string (the executor keys off it).
    assert agent_stage["stage_id"] == str(_stage(definition, 0).pk)

    gate_stage = stages[1]
    assert gate_stage["kind"] == "human_gate"
    assert gate_stage["has_agent_definition"] is False


@pytest.mark.django_db
def test_get_workflow_stages_missing_definition_raises():
    with pytest.raises(RuntimeError, match="not found"):
        _get_workflow_stages_sync("does-not-exist")


@pytest.mark.django_db
def test_get_workflow_stages_disabled_definition_raises(definition):
    definition.is_enabled = False
    definition.save(update_fields=["is_enabled", "updated_at", "version"])
    with pytest.raises(RuntimeError):
        _get_workflow_stages_sync(definition.slug)


@pytest.mark.django_db
def test_get_workflow_stages_excludes_soft_deleted_stages(definition):
    from django.utils import timezone

    stage = _stage(definition, 2)
    stage.deleted_at = timezone.now()
    stage.save(update_fields=["deleted_at", "updated_at", "version"])

    orders = [s["order"] for s in _get_workflow_stages_sync(definition.slug)["stages"]]
    assert orders == [0, 1]


# ---------------------------------------------------------------------------
# create_stage_execution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_stage_execution_opens_running_row_and_points_run(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == WorkflowStageExecution.Status.RUNNING
    assert execution.attempt_number == 1
    assert execution.started_at is not None
    assert execution.workflow_run_id == run.pk

    run.refresh_from_db()
    assert run.current_stage_execution_id == execution.pk


@pytest.mark.django_db
def test_create_stage_execution_tracks_attempt_number(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 3)
    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.attempt_number == 3


# ---------------------------------------------------------------------------
# update_stage_execution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_update_stage_execution_completes_with_output(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    _update_stage_execution_sync(
        execution_id, "completed", {"result": "ok"}, None
    )

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "completed"
    assert execution.output == {"result": "ok"}
    assert execution.ended_at is not None
    assert execution.is_terminal


@pytest.mark.django_db
def test_update_stage_execution_records_failure(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    _update_stage_execution_sync(execution_id, "failed", None, "boom")

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "failed"
    assert execution.error_message == "boom"
    assert execution.failure == {"message": "boom"}
    assert execution.ended_at is not None


@pytest.mark.django_db
def test_update_stage_execution_refuses_to_resurrect_terminal_row(run, definition):
    """A row that already reached a terminal status must not be reopened —
    the executions table is append-only after termination, so a duplicate
    or late activity delivery is a no-op."""
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    _update_stage_execution_sync(execution_id, "completed", {"r": 1}, None)

    # Attempt to flip a completed row back to running — ignored.
    _update_stage_execution_sync(execution_id, "running", {"r": 2}, None)

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "completed"
    assert execution.output == {"r": 1}


@pytest.mark.django_db
def test_update_stage_execution_invalid_status_raises(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    with pytest.raises(ValueError, match="invalid stage execution status"):
        _update_stage_execution_sync(execution_id, "bogus", None, None)


# ---------------------------------------------------------------------------
# record_human_gate_decision
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_human_gate_approved_completes_execution(run, definition):
    gate = _stage(definition, 1)
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    user = get_user_model().objects.create(username="approver", email="a@test")
    _record_human_gate_decision_sync(execution_id, "approved", user.pk, "looks good")

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "completed"
    assert execution.output["human_gate"]["decision"] == "approved"
    assert execution.output["human_gate"]["decided_by_user_id"] == user.pk
    assert execution.output["human_gate"]["note"] == "looks good"
    assert execution.ended_at is not None


@pytest.mark.django_db
def test_human_gate_rejected_fails_execution(run, definition):
    gate = _stage(definition, 1)
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    _record_human_gate_decision_sync(execution_id, "rejected", None, "nope")

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "failed"
    assert execution.error_message == "human gate rejected"
    assert execution.output["human_gate"]["decision"] == "rejected"


@pytest.mark.django_db
def test_human_gate_invalid_decision_raises(run, definition):
    gate = _stage(definition, 1)
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)
    with pytest.raises(ValueError, match="invalid human-gate decision"):
        _record_human_gate_decision_sync(execution_id, "maybe", None, "")


@pytest.mark.django_db
def test_human_gate_decision_on_terminal_row_is_noop(run, definition):
    gate = _stage(definition, 1)
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)
    _record_human_gate_decision_sync(execution_id, "approved", None, "")
    # A second, conflicting decision after termination is ignored.
    _record_human_gate_decision_sync(execution_id, "rejected", None, "late")
    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "completed"


# ---------------------------------------------------------------------------
# snapshot_checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_checkpoint_completes_immediately(run, definition):
    checkpoint = _stage(definition, 2)
    execution_id = _snapshot_checkpoint_sync(
        str(run.pk), str(checkpoint.pk), {"agent_run_status": "succeeded"}
    )

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "completed"
    assert execution.started_at is not None
    assert execution.ended_at is not None
    assert execution.output == {"checkpoint": {"agent_run_status": "succeeded"}}

    run.refresh_from_db()
    assert run.current_stage_execution_id == execution.pk


@pytest.mark.django_db
def test_snapshot_checkpoint_handles_no_previous_output(run, definition):
    checkpoint = _stage(definition, 2)
    execution_id = _snapshot_checkpoint_sync(str(run.pk), str(checkpoint.pk), None)
    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.output == {"checkpoint": {}}


# ---------------------------------------------------------------------------
# aggregate_fan_out
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregate_fan_out_merges_sources_and_links_them(run, definition):
    # Build an AGGREGATION stage + two completed fan-out source executions.
    agg_stage = WorkflowStage.objects.create(
        slug="stage-pipeline-agg",
        definition=definition,
        order=3,
        kind=WorkflowStage.StageKind.AGGREGATION,
    )
    fan_stage = _stage(definition, 0)
    src1 = WorkflowStageExecution.objects.create(
        slug="src-exec-1",
        workflow_run=run,
        stage=fan_stage,
        status=WorkflowStageExecution.Status.COMPLETED,
        output={"n": 1},
    )
    src2 = WorkflowStageExecution.objects.create(
        slug="src-exec-2",
        workflow_run=run,
        stage=fan_stage,
        status=WorkflowStageExecution.Status.FAILED,
        output=None,
    )

    aggregated = _aggregate_fan_out_sync(
        str(run.pk), str(agg_stage.pk), [str(src1.pk), str(src2.pk)]
    )

    assert aggregated["total"] == 2
    assert aggregated["ok_count"] == 1  # only the COMPLETED one counts
    statuses = {item["status"] for item in aggregated["aggregated"]}
    assert statuses == {"completed", "failed"}

    # The aggregation execution links both sources via fan_out_sources.
    agg_exec = (
        WorkflowStageExecution.objects.filter(stage=agg_stage).order_by("-pk").first()
    )
    assert agg_exec.status == "completed"
    assert set(agg_exec.fan_out_sources.values_list("pk", flat=True)) == {
        src1.pk,
        src2.pk,
    }


@pytest.mark.django_db
def test_aggregate_fan_out_empty_sources(run, definition):
    agg_stage = WorkflowStage.objects.create(
        slug="stage-pipeline-agg",
        definition=definition,
        order=3,
        kind=WorkflowStage.StageKind.AGGREGATION,
    )
    aggregated = _aggregate_fan_out_sync(str(run.pk), str(agg_stage.pk), [])
    assert aggregated == {"aggregated": [], "ok_count": 0, "total": 0}


# ---------------------------------------------------------------------------
# mark_workflow_run
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_mark_workflow_run_completed_clears_current_stage(run, definition):
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    run.refresh_from_db()
    assert run.current_stage_execution_id == int(execution_id)

    _mark_workflow_run_sync(str(run.pk), "completed", {"final": "ok"}, None)

    run.refresh_from_db()
    assert run.status == "completed"
    assert run.result == {"final": "ok"}
    assert run.ended_at is not None
    assert run.current_stage_execution_id is None


@pytest.mark.django_db
def test_mark_workflow_run_failed_records_failure(run):
    _mark_workflow_run_sync(str(run.pk), "failed", None, {"message": "stage 1 failed"})
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.failure == {"message": "stage 1 failed"}
    assert run.ended_at is not None


@pytest.mark.django_db
def test_mark_workflow_run_invalid_status_raises(run):
    with pytest.raises(ValueError, match="invalid workflow run status"):
        _mark_workflow_run_sync(str(run.pk), "exploded", None, None)


# ---------------------------------------------------------------------------
# dispatch_agent_for_stage — resilient no-dispatcher path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dispatch_agent_creates_run_and_links_execution_without_dispatcher(
    run, definition
):
    """With no ACTIVE dispatcher registered, the dispatch still creates the
    AgentRun history row (left PENDING) and links it to the stage execution
    — the workflow's timeout governs the wait, and a push-mode callback can
    still advance the run. Dispatch must not hard-fail on a missing
    dispatcher (registration may be in flight)."""
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    agent_run_id = _dispatch_agent_for_stage_sync(
        str(stage.pk), execution_id, {"trigger": "manual"}
    )

    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    assert agent_run.status == AgentRun.Status.PENDING
    assert agent_run.workload_id == stage.agent_definition_id
    assert agent_run.input["stage_id"] == str(stage.pk)
    assert agent_run.input["skill_refs"] == ["lint", "review"]
    assert agent_run.input["trigger_payload"] == {"trigger": "manual"}

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.agent_run_id == agent_run.pk


@pytest.mark.django_db
def test_dispatch_agent_rejects_stage_without_agent_definition(run, definition):
    """A non-AGENT_DISPATCH stage (no agent_definition) can't be dispatched."""
    gate = _stage(definition, 1)  # human_gate, no agent_definition
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)
    with pytest.raises(RuntimeError, match="no agent_definition"):
        _dispatch_agent_for_stage_sync(str(gate.pk), execution_id, {})
