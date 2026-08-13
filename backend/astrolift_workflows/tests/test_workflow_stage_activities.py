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
    _load_agent_run_outcome_sync,
    _mark_workflow_run_sync,
    _parent_run_pk,
    _poll_agent_run_status_sync,
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
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-stage")
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
    from astrolift_agents.models import Skill

    org = agent_workload.registered_app.organization
    Skill.objects.create(
        organization=org,
        name="Lint",
        slug="lint",
        content="Lint the proposed change.",
        is_active=True,
    )
    Skill.objects.create(
        organization=org,
        name="Review",
        slug="review",
        content="Review the proposed change.",
        is_active=True,
    )
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
        prompt="Review the trigger and return structured JSON.",
        output_key="review_result",
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
    assert agent_stage["prompt"] == "Review the trigger and return structured JSON."
    assert agent_stage["output_key"] == "review_result"
    assert agent_stage["has_agent_definition"] is True
    # stage_id is the DB pk as a string (the executor keys off it).
    assert agent_stage["stage_id"] == str(_stage(definition, 0).pk)

    gate_stage = stages[1]
    assert gate_stage["kind"] == "human_gate"
    assert gate_stage["has_agent_definition"] is False
    assert gate_stage["output_key"] == "stage_1"


@pytest.mark.django_db
def test_get_workflow_stages_applies_binding_overrides(run, definition, agent_workload):
    from astrolift_registry.models import Workload

    replacement = Workload.objects.create(
        registered_app=agent_workload.registered_app,
        name="Replacement Agent",
        slug="replacement-agent",
        kind=Workload.Kind.AGENT,
    )
    result = _get_workflow_stages_sync(
        definition.slug,
        str(run.pk),
        {
            "0": {
                "agent_workload_id": str(replacement.guid),
                "skill_refs": ["review"],
                "params": {
                    "environment_spec_slug": "special-runtime",
                    "prompt": "Use the customer procedure.",
                    "output_key": "customer_review",
                },
            }
        },
        str(definition.pk),
    )
    stage = result["stages"][0]
    assert stage["agent_definition_id"] == replacement.pk
    assert stage["skill_refs"] == ["review"]
    assert stage["environment_spec_slug"] == "special-runtime"
    assert stage["prompt"] == "Use the customer procedure."
    assert stage["output_key"] == "customer_review"


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

    _update_stage_execution_sync(execution_id, "completed", {"result": "ok"}, None)

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

    aggregated = _aggregate_fan_out_sync(str(run.pk), str(agg_stage.pk), [str(src1.pk), str(src2.pk)])

    assert aggregated["total"] == 2
    assert aggregated["ok_count"] == 1  # only the COMPLETED one counts
    statuses = {item["status"] for item in aggregated["aggregated"]}
    assert statuses == {"completed", "failed"}

    # The aggregation execution links both sources via fan_out_sources.
    agg_exec = WorkflowStageExecution.objects.filter(stage=agg_stage).order_by("-pk").first()
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
def test_dispatch_agent_creates_run_and_links_execution_without_dispatcher(run, definition):
    """With no ACTIVE dispatcher registered, the dispatch still creates the
    AgentRun history row (left PENDING) and links it to the stage execution
    — the workflow's timeout governs the wait, and a push-mode callback can
    still advance the run. Dispatch must not hard-fail on a missing
    dispatcher (registration may be in flight)."""
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    agent_run_id = _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {"trigger": "manual"})

    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    assert agent_run.status == AgentRun.Status.PENDING
    assert agent_run.workload_id == stage.agent_definition_id
    assert agent_run.input["stage_id"] == str(stage.pk)
    assert agent_run.input["skill_refs"] == ["lint", "review"]
    assert agent_run.input["trigger_payload"] == {"trigger": "manual"}

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.agent_run_id == agent_run.pk

    from astrolift_agents.models import AgentTask

    task = AgentTask.objects.get(agent_run=agent_run)
    assert task.dispatch_input == {"trigger": "manual"}
    assert task.brief.context["output_key"] == "review_result"
    assert "Review the trigger and return structured JSON." in task.brief.manifest_snapshot["system_prompt"]


@pytest.mark.django_db
def test_dispatch_uses_resolved_agent_environment_and_packet(run, definition, agent_workload):
    from astrolift_agents.models import AgentEnvironmentSpec, AgentTask
    from astrolift_registry.models import Workload

    replacement = Workload.objects.create(
        registered_app=agent_workload.registered_app,
        name="Replacement Agent",
        slug="replacement-agent-dispatch",
        kind=Workload.Kind.AGENT,
    )
    env = AgentEnvironmentSpec.objects.create(
        organization=run.organization,
        name="Special runtime",
        slug="special-runtime",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    agent_run_id = _dispatch_agent_for_stage_sync(
        str(stage.pk),
        execution_id,
        {"prior": {"answer": 42}},
        {
            "agent_definition_id": replacement.pk,
            "environment_spec_slug": env.slug,
            "skill_refs": ["lint"],
            "prompt": "Use the replacement procedure.",
            "output_key": "replacement_result",
        },
    )
    task = AgentTask.objects.get(agent_run_id=int(agent_run_id))
    assert task.agent_definition_id == replacement.pk
    assert task.environment_spec_id == env.pk
    assert task.brief.context["output_key"] == "replacement_result"
    assert "Use the replacement procedure." in task.brief.manifest_snapshot["system_prompt"]
    assert task.dispatch_input == {"prior": {"answer": 42}}


@pytest.mark.django_db
def test_dispatch_agent_rejects_stage_without_agent_definition(run, definition):
    """A non-AGENT_DISPATCH stage (no agent_definition) can't be dispatched."""
    gate = _stage(definition, 1)  # human_gate, no agent_definition
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)
    with pytest.raises(RuntimeError, match="no agent_definition"):
        _dispatch_agent_for_stage_sync(str(gate.pk), execution_id, {})


def test_parent_run_pk_plain_and_fanout_child():
    """#1017: fan-out children run under '<parent>:fanout:<order>:<idx>' —
    their activities must resolve to the parent run pk, not ValueError on int()."""
    assert _parent_run_pk("38") == 38
    assert _parent_run_pk("38:fanout:0:0") == 38
    assert _parent_run_pk("38:fanout:0:2") == 38


# ---------------------------------------------------------------------------
# AgentTask <-> AgentRun FK linkage (#1217)
# ---------------------------------------------------------------------------


def _dispatch_agent_stage(run, definition, order: int = 0):
    """Dispatch the agent stage at ``order`` (no dispatcher) against ``run``,
    returning the (agent_run, agent_task) it created and linked. The stage's
    execution now carries an ``agent_run``, so a following gate resolves to
    this task."""
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, order)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    agent_run_id = _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {})
    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    task = AgentTask.objects.get(agent_run_id=agent_run.pk)
    return agent_run, task


@pytest.mark.django_db
def test_dispatch_links_agent_task_to_agent_run(run, definition):
    """The dispatch path links the created AgentTask to its AgentRun via the
    explicit FK — no reliance on the (workload, pod) fuzzy join."""
    agent_run, task = _dispatch_agent_stage(run, definition, 0)

    assert task.agent_run_id == agent_run.pk
    # Reverse OneToOne accessor resolves back to the same task.
    assert agent_run.agent_task == task


@pytest.mark.django_db
def test_dispatch_activity_retry_reuses_agent_run_and_task(run, definition):
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    first = _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {"issue": "EMR-1"})
    second = _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {"issue": "EMR-1"})

    assert second == first
    assert AgentRun.objects.filter(pk=int(first)).count() == 1
    assert AgentTask.objects.filter(agent_run_id=int(first)).count() == 1


@pytest.mark.django_db
def test_poll_and_outcome_preserve_callback_result(run, definition):
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    agent_run, task = _dispatch_agent_stage(run, definition, 0)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)
    task.result = {"output": {"findings": ["EMR-123"], "classification": "bug"}}
    task.save(update_fields=["result", "updated_at", "version"])
    task.transition_to(AgentTask.Status.COMPLETED)

    assert _poll_agent_run_status_sync(str(agent_run.pk)) == AgentRun.Status.SUCCEEDED
    outcome = _load_agent_run_outcome_sync(str(agent_run.pk))
    assert outcome["result"] == {"output": {"findings": ["EMR-123"], "classification": "bug"}}
    agent_run.refresh_from_db()
    assert agent_run.output == outcome["result"]


@pytest.mark.django_db
def test_poll_backfills_fk_from_fuzzy_join(run, definition):
    """A pre-FK row linked only by the historical (workload, pod) join gets its
    FK backfilled on the next reconcile (idempotent, only when unset)."""
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    agent_run = AgentRun.objects.create(
        workload=stage.agent_definition,
        status=AgentRun.Status.RUNNING,
        k8s_pod_name="pod-backfill-1",
    )
    # Linked only by the fuzzy join (no FK, no dispatcher → poll backfills the
    # FK then returns early without needing the spawner).
    task = AgentTask.objects.create(
        organization=run.organization,
        agent_definition=stage.agent_definition,
        external_id="pod-backfill-1",
    )
    assert task.agent_run_id is None

    _poll_agent_run_status_sync(str(agent_run.pk))

    task.refresh_from_db()
    assert task.agent_run_id == agent_run.pk


# ---------------------------------------------------------------------------
# Gate interaction capture (#1217)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_gate_open_records_pending_interaction_for_preceding_agent(run, definition):
    """Opening a human-gate execution (reviewers notified) records a pending
    GATE interaction attributed to the preceding agent stage's task."""
    from astrolift_agents.models import AgentInteraction

    _, task = _dispatch_agent_stage(run, definition, 0)
    gate = _stage(definition, 1)

    _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    row = AgentInteraction.objects.get(agent_task=task, kind=AgentInteraction.Kind.GATE)
    assert row.status == "pending"
    assert row.organization_id == run.organization_id
    assert row.detail["stage_order"] == gate.order
    assert row.detail["execution_id"]


@pytest.mark.django_db
def test_gate_approved_records_interaction_attributed_to_agent(run, definition):
    """An approved gate decision records a GATE interaction (status=approved)
    attributed to the preceding agent stage's task, carrying the decider."""
    from astrolift_agents.models import AgentInteraction

    _, task = _dispatch_agent_stage(run, definition, 0)
    gate = _stage(definition, 1)
    gate_exec = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)  # pending

    user = get_user_model().objects.create(username="gatekeeper", email="gk@test")
    _record_human_gate_decision_sync(gate_exec, "approved", user.pk, "ship it")

    rows = list(
        AgentInteraction.objects.filter(agent_task=task, kind=AgentInteraction.Kind.GATE).order_by("id")
    )
    # gate-open (pending) then decision (approved).
    assert [r.status for r in rows] == ["pending", "approved"]
    decided = rows[-1]
    assert decided.detail["decided_by_user_id"] == user.pk
    assert decided.detail["note"] == "ship it"
    assert decided.organization_id == run.organization_id


@pytest.mark.django_db
def test_gate_rejected_records_rejected_interaction(run, definition):
    """A rejected decision (e.g. gate timeout) records a rejected GATE
    interaction."""
    from astrolift_agents.models import AgentInteraction

    _, task = _dispatch_agent_stage(run, definition, 0)
    gate = _stage(definition, 1)
    gate_exec = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    _record_human_gate_decision_sync(gate_exec, "rejected", None, "nope")

    decided = AgentInteraction.objects.filter(
        agent_task=task, kind=AgentInteraction.Kind.GATE, status="rejected"
    ).first()
    assert decided is not None
    assert decided.detail["note"] == "nope"


@pytest.mark.django_db
def test_gate_attributes_to_nearest_preceding_agent_stage(run, agent_workload):
    """With two agent stages before the gate, the gate attributes ONLY to the
    nearest (highest-order) preceding agent stage — not an earlier one. This is
    the core attribution rule (review-loop: gate reviews the stage just before)."""
    from astrolift_agents.models import AgentInteraction

    wd = WorkflowDefinition.objects.create(
        name="Two Agents Gate",
        slug="two-agents-gate",
        model_label="workflows.workflowdefinition",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=MINIMAL_STATES,
        transitions=[],
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        slug="tag-s0",
        definition=wd,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        agent_definition=agent_workload,
        timeout_seconds=120,
    )
    WorkflowStage.objects.create(
        slug="tag-s1",
        definition=wd,
        order=1,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        agent_definition=agent_workload,
        timeout_seconds=120,
    )
    WorkflowStage.objects.create(
        slug="tag-s2",
        definition=wd,
        order=2,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        timeout_seconds=300,
    )

    _, task0 = _dispatch_agent_stage(run, wd, 0)
    _, task1 = _dispatch_agent_stage(run, wd, 1)
    assert task0.pk != task1.pk

    gate = _stage(wd, 2)
    _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    attributed = set(
        AgentInteraction.objects.filter(kind=AgentInteraction.Kind.GATE).values_list(
            "agent_task_id", flat=True
        )
    )
    assert attributed == {task1.pk}


@pytest.mark.django_db
def test_gate_capture_failure_does_not_break_decision(run, definition, monkeypatch):
    """Gate capture is defensive: a failure resolving/recording the interaction
    must never stop the durable decision from being persisted."""
    from astrolift_workflows.activities import workflow_stage_activities as wsa

    _dispatch_agent_stage(run, definition, 0)
    gate = _stage(definition, 1)
    gate_exec = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    def _boom(*args, **kwargs):
        raise RuntimeError("attribution exploded")

    monkeypatch.setattr(wsa, "_resolve_gate_agent_tasks_sync", _boom)

    # Must not raise despite capture blowing up.
    _record_human_gate_decision_sync(gate_exec, "approved", None, "")

    execution = WorkflowStageExecution.objects.get(pk=int(gate_exec))
    assert execution.status == "completed"  # decision still recorded
