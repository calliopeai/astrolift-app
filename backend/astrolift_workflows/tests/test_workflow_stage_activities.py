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
    _create_nested_workflow_run_sync,
    _create_stage_execution_sync,
    _dispatch_agent_for_stage_sync,
    _get_workflow_stages_sync,
    _load_agent_run_outcome_sync,
    _mark_workflow_run_sync,
    _parent_run_pk,
    _poll_agent_run_status_sync,
    _record_human_gate_decision_sync,
    _record_nested_workflow_start_sync,
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
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization

    organization = Organization.objects.create(name="Stage Org", slug="stage-org-test")
    # Agent dispatch resolves the org's managed cluster (#1704). Without one
    # an org cannot run an agent at all, by any path, so a fixture without
    # one is not a useful stand-in for an install.
    plugin = ProviderPlugin(
        name="K8s",
        slug="k8s-stage",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    TenantCluster.objects.create(
        organization=organization,
        name="agents",
        slug="agents-stage",
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-stage"),
        provider_config={},
        endpoint="https://k8s.invalid",
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    return organization


class _FakeSpawner:
    """Stands in for the K8s Job backend.

    ``status`` reports the job as still running: these tests drive terminal
    state through callbacks and explicit transitions, and a spawner that
    reported "succeeded" would settle every task behind their backs.
    """

    def __init__(self, *args, **kwargs):
        pass

    def spawn(self, task):
        from astrolift_dispatch.spawners.base import SpawnResult

        return SpawnResult(external_id=f"job-{task.pk}", ok=True)

    def status(self, external_id):
        from astrolift_dispatch.spawners.base import TaskStatus

        return TaskStatus(running=True)

    def stop(self, external_id, **kwargs):
        return None

    def confirm_stopped(self, external_id):
        return True


@pytest.fixture(autouse=True)
def _fake_spawner(monkeypatch):
    from astrolift_dispatch.spawners import registry

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: _FakeSpawner())


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
def test_nested_stage_resolves_child_and_creates_linked_run(org, agent_workload):
    project = agent_workload.registered_app.project
    child = WorkflowDefinition.objects.create(
        organization=org,
        project=project,
        name="Nested Child",
        slug="nested-child",
        pattern_kind=WorkflowDefinition.PatternKind.SINGLE,
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        slug="nested-child-stage-0",
        definition=child,
        order=0,
        kind=WorkflowStage.StageKind.CHECKPOINT,
    )
    parent = WorkflowDefinition.objects.create(
        organization=org,
        project=project,
        name="Nested Parent",
        slug="nested-parent",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        is_enabled=True,
    )
    nested_stage = WorkflowStage.objects.create(
        slug="nested-parent-stage-0",
        definition=parent,
        order=0,
        kind=WorkflowStage.StageKind.WORKFLOW,
        workflow_ref=child.slug,
        output_key="child_result",
    )
    parent_run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_definition=parent,
        workflow_id="WorkflowDefinitionRunWorkflow-nested-parent-test",
        run_id="",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
        nesting_depth=0,
    )

    plan = _get_workflow_stages_sync(
        parent.slug,
        str(parent_run.pk),
        {},
        str(parent.pk),
        [],
    )
    assert plan["definition_id"] == str(parent.pk)
    assert plan["stages"][0]["nested_definition_id"] == str(child.pk)
    assert plan["stages"][0]["nested_definition_slug"] == child.slug

    execution_id = _create_stage_execution_sync(
        str(parent_run.pk),
        str(nested_stage.pk),
        1,
    )
    result = _create_nested_workflow_run_sync(
        str(parent_run.pk),
        execution_id,
        str(child.pk),
    )
    child_run = WorkflowRun.objects.get(pk=int(result["workflow_run_id"]))
    assert child_run.parent_run == parent_run
    assert child_run.parent_stage_execution_id == int(execution_id)
    assert child_run.workflow_definition == child
    assert child_run.nesting_depth == 1
    assert child_run.trigger_kind == "parent"  # (#2152)
    # Activity retry is idempotent because one parent execution owns one child.
    repeated = _create_nested_workflow_run_sync(
        str(parent_run.pk),
        execution_id,
        str(child.pk),
    )
    assert repeated["workflow_run_id"] == result["workflow_run_id"]
    _record_nested_workflow_start_sync(result["workflow_run_id"], "temporal-child-run-id")
    child_run.refresh_from_db()
    assert child_run.run_id == "temporal-child-run-id"


@pytest.mark.django_db
def test_nested_stage_runtime_rejects_ancestry_cycle(org, agent_workload):
    definition = WorkflowDefinition.objects.create(
        organization=org,
        project=agent_workload.registered_app.project,
        name="Runtime Cycle",
        slug="runtime-cycle",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        is_enabled=True,
    )
    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_definition=definition,
        workflow_id="WorkflowDefinitionRunWorkflow-runtime-cycle-test",
        run_id="",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
    )
    with pytest.raises(RuntimeError, match="cycle"):
        _get_workflow_stages_sync(
            definition.slug,
            str(run.pk),
            {},
            str(definition.pk),
            [str(definition.pk)],
        )


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
def test_dispatch_without_a_dispatcher_spawns_through_the_direct_path(run, definition):
    """Nothing on an install registers a DispatcherInstance -- the endpoint
    exists for a Dispatch Service to register itself, and no component runs
    one -- so requiring it meant every workflow stage hung in ``running``
    forever while ``astro agent dispatch`` worked fine (#1704). With none
    registered the stage spawns the way direct dispatch always has: the
    k8s_job backend against the org's managed cluster."""
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    agent_run_id = _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {"trigger": "manual"})

    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    assert agent_run.status == AgentRun.Status.RUNNING
    assert agent_run.workload_id == stage.agent_definition_id
    assert agent_run.input["stage_id"] == str(stage.pk)
    assert agent_run.input["skill_refs"] == ["lint", "review"]
    assert agent_run.input["trigger_payload"] == {"trigger": "manual"}

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.agent_run_id == agent_run.pk

    task = AgentTask.objects.get(agent_run=agent_run)
    assert task.status == AgentTask.Status.RUNNING
    assert task.external_id
    assert task.dispatcher_id is None
    assert task.team_id == stage.agent_definition.registered_app.team_id
    assert task.project_id == stage.agent_definition.registered_app.project_id
    assert task.dispatch_input == {"trigger": "manual"}
    assert task.brief.context["output_key"] == "review_result"
    assert "Review the trigger and return structured JSON." in task.brief.manifest_snapshot["system_prompt"]


@pytest.mark.django_db
def test_a_stage_with_no_dispatch_target_fails_the_run_with_the_reason(run, definition):
    """The old behaviour left the AgentRun PENDING on the theory that a
    dispatcher registration might be in flight. On an install where none is
    ever registered that read as the run hanging forever, with the only
    trace a WARNING in the worker log. A stage that cannot dispatch settles
    FAILED and carries why."""
    from astrolift_agents.models import AgentTask
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AgentRun

    TenantCluster.objects.all().delete()
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    with pytest.raises(RuntimeError, match="no dispatch target"):
        _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {})

    agent_run = AgentRun.objects.get(workload=stage.agent_definition)
    assert agent_run.status == AgentRun.Status.FAILED
    assert agent_run.output["dispatch_error"]
    task = AgentTask.objects.get(agent_run=agent_run)
    assert task.status == AgentTask.Status.FAILED
    assert "no dispatch target" in task.failure["message"]


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
    # A stage started it, for whoever started the run (#2152).
    assert task.trigger_kind == "parent"
    assert task.triggered_by_user_id == run.trigger_actor_user_id
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
    # The dispatch spawned, so the task is already RUNNING.
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
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


@pytest.mark.django_db
def test_poll_uses_the_namespace_frozen_at_stage_spawn(run, definition, monkeypatch):
    """A stage Job lives in its per-org namespace, never ``default``.

    Polling the registry default makes the provider report the live Job as
    missing/failed. That terminalizes the task and revokes its callback token
    while the pod is still running, so every subsequent report gets a 401.
    """
    from astrolift_agents.models import AgentTask, DispatcherInstance
    from astrolift_dispatch.spawners import registry as spawner_registry
    from astrolift_dispatch.spawners.base import TaskStatus
    from astrolift_lifecycle.models import AgentRun

    stage = _stage(definition, 0)
    dispatcher = DispatcherInstance.objects.create(
        organization=run.organization,
        name="Stage dispatcher",
        slug="stage-poll-namespace-dispatcher",
        endpoint="https://dispatch.example.test/",
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )
    agent_run = AgentRun.objects.create(
        workload=stage.agent_definition,
        status=AgentRun.Status.RUNNING,
        k8s_pod_name="agent-task-stage-namespace",
    )
    namespace = "astrolift-agents-stage-org-test"
    task = AgentTask.objects.create(
        organization=run.organization,
        agent_definition=stage.agent_definition,
        agent_run=agent_run,
        dispatcher=dispatcher,
        external_id=agent_run.k8s_pod_name,
        namespace=namespace,
        status=AgentTask.Status.RUNNING,
        callback_token_hash="a" * 64,
    )
    captured: dict[str, object] = {}

    class _RunningSpawner:
        def status(self, external_id: str) -> TaskStatus:
            captured["external_id"] = external_id
            return TaskStatus(running=True)

    def _get_spawner(backend: str, *, cluster=None, namespace: str = "default"):
        captured.update(backend=backend, cluster=cluster, namespace=namespace)
        return _RunningSpawner()

    monkeypatch.setattr(spawner_registry, "get_spawner", _get_spawner)

    assert _poll_agent_run_status_sync(str(agent_run.pk)) == AgentRun.Status.RUNNING
    assert captured["namespace"] == namespace
    assert captured["external_id"] == task.external_id
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.callback_token_hash == "a" * 64


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


# ---------------------------------------------------------------------------
# Gate reviewer notification (#59)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_gate_open_emails_the_approver_named_on_the_stage(run, definition):
    """Opening a gate notifies its reviewer: the stage's address-shaped
    approver receives the review-request email."""
    from django.core import mail

    gate = _stage(definition, 1)
    gate.approvers = ["team:reviewers", "reviewer@example.test"]
    gate.save()

    _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["reviewer@example.test"]
    assert "stage-pipeline-s1" in message.subject


@pytest.mark.django_db
def test_gate_open_emits_notified_event_with_delivery_outcome(run, definition):
    """The gate notification rides the platform event bus, so the activity
    feed / outbound webhooks see the gate open and which channels delivered."""
    from astrolift_operations.models import Event

    gate = _stage(definition, 1)
    gate.approvers = ["reviewer@example.test"]
    gate.save()

    _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    event = Event.objects.get(event_type="workflow.human_gate.notified")
    assert event.organization_id == run.organization_id
    assert event.resource_kind == "workflow_run"
    assert event.resource_id == str(run.guid)
    assert event.payload["stage_name"] == gate.slug
    assert event.payload["delivery"] == {"email": True}


@pytest.mark.django_db
def test_gate_open_falls_back_to_org_admin_when_no_approver_address(run, definition):
    """Team / role approver slugs are not addresses, so a gate declaring only
    those falls through to the service's org-admin recipient."""
    from django.core import mail

    gate = _stage(definition, 1)
    gate.approvers = ["team:reviewers"]
    gate.save()

    _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    admin_emails = set(
        get_user_model().objects.filter(is_superuser=True, is_active=True).values_list("email", flat=True)
    )
    assert admin_emails  # the platform ships a superuser to fall back to
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to[0] in admin_emails


@pytest.mark.django_db
def test_non_gate_stage_open_does_not_notify(run, definition):
    """Only human gates notify — an agent stage opening RUNNING must not mail
    anyone, even when a recipient is resolvable."""
    from django.core import mail

    from astrolift_operations.models import Event

    # A fallback recipient exists, so silence here can only come from the
    # kind check and not from an unresolvable reviewer.
    assert get_user_model().objects.filter(is_superuser=True, is_active=True).exists()

    _create_stage_execution_sync(str(run.pk), str(_stage(definition, 0).pk), 1)

    assert mail.outbox == []
    assert not Event.objects.filter(event_type="workflow.human_gate.notified").exists()


@pytest.mark.django_db
def test_gate_notification_failure_does_not_break_stage_creation(run, definition, monkeypatch):
    """Notification is side-effect-only: a broken mail / Slack path must not
    fail the activity and stall the run at the gate."""
    from astrolift_agents.services import human_gate as human_gate_service

    def _boom(*args, **kwargs):
        raise RuntimeError("notification exploded")

    monkeypatch.setattr(human_gate_service, "notify_human_gate", _boom)

    gate = _stage(definition, 1)
    execution_id = _create_stage_execution_sync(str(run.pk), str(gate.pk), 1)

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    assert execution.status == "running"


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "terminated", "timed_out"])
def test_terminal_run_updates_only_its_exact_configured_instance(run, org, status):
    from astrolift_identity.models import Organization
    from workflows.models import WorkflowInstance

    run.run_id = "exact-incarnation"
    run.save(update_fields=["run_id"])
    own = WorkflowInstance.objects.create(
        organization=org,
        current_state="running",
        temporal_workflow_id=run.workflow_id,
        temporal_run_id=run.run_id,
    )
    different_run = WorkflowInstance.objects.create(
        organization=org,
        current_state="running",
        temporal_workflow_id=run.workflow_id,
        temporal_run_id="another-incarnation",
    )
    different_org = WorkflowInstance.objects.create(
        organization=Organization.objects.create(name="Other", slug="other-terminal-org"),
        current_state="running",
        temporal_workflow_id=run.workflow_id,
        temporal_run_id=run.run_id,
    )
    _mark_workflow_run_sync(str(run.pk), status, None, None)
    own.refresh_from_db()
    assert own.current_state == status
    assert own.completed_at is not None
    for other in (different_run, different_org):
        other.refresh_from_db()
        assert other.current_state == "running"
        assert other.completed_at is None


@pytest.mark.django_db
def test_cancelled_run_closes_gate_without_rewriting_completed_stages(run, definition):
    from django.utils import timezone

    completed = WorkflowStageExecution.objects.create(
        workflow_run=run,
        stage=_stage(definition, 0),
        status="completed",
        ended_at=timezone.now(),
        output={"keep": "answer"},
    )
    gate_id = _create_stage_execution_sync(str(run.pk), str(_stage(definition, 1).pk), 1)
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    gate = WorkflowStageExecution.objects.get(pk=gate_id)
    assert gate.status == "cancelled"
    assert gate.ended_at is not None
    completed.refresh_from_db()
    assert completed.status == "completed"
    assert completed.output == {"keep": "answer"}


@pytest.mark.django_db
def test_fanout_child_cannot_finalize_parent_run(run):
    _mark_workflow_run_sync(f"{run.pk}:fanout:0:1", "completed", {"child": "answer"}, None)
    run.refresh_from_db()
    assert run.status == "running"
    assert run.ended_at is None
    assert run.result is None


@pytest.mark.django_db
def test_configured_instance_created_after_executor_finishes_starts_terminal(run, org, definition):
    from workflows.models import Workflow, WorkflowInstance

    run.run_id = "fast-execution"
    run.save(update_fields=["run_id"])
    _mark_workflow_run_sync(str(run.pk), "completed", {"answer": "done"}, None)
    run.refresh_from_db()
    configured = Workflow.objects.create(
        organization=org,
        definition=definition,
        name="Fast",
        slug="fast-completion",
    )
    instance = WorkflowInstance.start(
        configured_workflow=configured,
        temporal_workflow_id=run.workflow_id,
        temporal_run_id=run.run_id,
    )
    assert instance.current_state == "completed"
    assert instance.completed_at == run.ended_at


@pytest.mark.django_db
def test_terminal_finalization_retry_preserves_outcome_and_timestamp(run):
    _mark_workflow_run_sync(str(run.pk), "completed", {"answer": "keep"}, None)
    run.refresh_from_db()
    ended_at, version = run.ended_at, run.version
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, {"message": "late delivery"})
    run.refresh_from_db()
    assert run.status == "completed"
    assert run.result == {"answer": "keep"}
    assert run.failure is None
    assert run.ended_at == ended_at
    assert run.version == version


@pytest.mark.django_db
def test_cancelled_workflow_stops_only_its_owned_agent_task(run, definition, monkeypatch):
    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners import registry
    from astrolift_lifecycle.models import AgentRun

    _, own_task = _dispatch_agent_stage(run, definition, 0)
    neighbor = WorkflowRun.objects.create(
        organization=run.organization,
        workflow_kind=run.workflow_kind,
        workflow_id=run.workflow_id,
        run_id="neighbor-incarnation",
    )
    _, other_task = _dispatch_agent_stage(neighbor, definition, 0)
    stopped = []

    class StopSpawner(_FakeSpawner):
        def stop(self, external_id, **kwargs):
            stopped.append(external_id)

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: StopSpawner())
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)

    own_task.refresh_from_db()
    other_task.refresh_from_db()
    assert stopped == [own_task.external_id]
    assert own_task.status == AgentTask.Status.CANCELLED
    assert other_task.status == AgentTask.Status.RUNNING
    assert AgentRun.objects.get(pk=own_task.agent_run_id).status == AgentRun.Status.CANCELLED


@pytest.mark.django_db
def test_workflow_cleanup_failure_is_visible_and_retryable(run, definition, monkeypatch):
    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners import registry

    _, task = _dispatch_agent_stage(run, definition, 0)
    stopped = []
    fail = True

    class StopSpawner(_FakeSpawner):
        def stop(self, external_id, **kwargs):
            stopped.append(external_id)
            if fail:
                raise RuntimeError("delete denied")

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: StopSpawner())
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, {"message": "cancelled by request"})
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert run.failure["task_cleanup"]["status"] == "failed"
    assert run.failure["message"] == "cancelled by request"
    closed_at = run.ended_at

    fail = False
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert stopped == [task.external_id, task.external_id]
    assert task.status == AgentTask.Status.CANCELLED
    assert run.failure["task_cleanup"]["status"] == "completed"
    assert run.ended_at == closed_at


@pytest.mark.django_db
def test_closed_workflow_refuses_delayed_agent_dispatch(run, definition):
    from astrolift_agents.models import AgentTask

    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    before = AgentTask.objects.count()
    with pytest.raises(RuntimeError, match="closed|terminal|cancelled"):
        _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {})
    assert AgentTask.objects.count() == before


@pytest.mark.django_db
def test_stage_task_stop_uses_the_backend_that_spawned_it(run, definition, monkeypatch):
    from astrolift_agents.models import DispatcherInstance
    from astrolift_dispatch.spawners import registry
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    monkeypatch.setattr("astrolift_agents.services.task_target.docker_daemon_id", lambda: "test-daemon")
    DispatcherInstance.objects.create(
        organization=run.organization,
        name="Local workflow dispatcher",
        slug="local-workflow-dispatcher",
        endpoint="http://localhost:9000",
        cloud=DispatcherInstance.Cloud.LOCAL,
        backend=DispatcherInstance.Backend.LOCAL_DOCKER,
        status=DispatcherInstance.Status.ACTIVE,
    )
    calls = []

    def spawner(backend, *, cluster=None, namespace="default"):
        calls.append(backend)
        return _FakeSpawner()

    monkeypatch.setattr(registry, "get_spawner", spawner)
    _, task = _dispatch_agent_stage(run, definition, 0)
    assert calls == ["local_docker"]
    result = _cancel_agent_task_sync(str(task.guid))
    assert result["ok"]
    assert calls == ["local_docker", "local_docker"]


@pytest.mark.django_db
@pytest.mark.parametrize("violation", ["foreign_org", "shared_agent_run", "unknown_target"])
def test_workflow_cleanup_refuses_unproven_ownership(run, definition, monkeypatch, violation):
    from astrolift_dispatch.spawners import registry
    from astrolift_identity.models import Organization

    _, task = _dispatch_agent_stage(run, definition, 0)
    if violation == "foreign_org":
        task.organization = Organization.objects.create(name="Foreign", slug="foreign-cleanup")
        task.save(update_fields=["organization", "updated_at", "version"])
    elif violation == "shared_agent_run":
        neighbor = WorkflowRun.objects.create(
            organization=run.organization,
            workflow_kind=run.workflow_kind,
            workflow_id="other-workflow",
            run_id="other-execution",
        )
        WorkflowStageExecution.objects.create(
            workflow_run=neighbor,
            stage=_stage(definition, 0),
            agent_run_id=task.agent_run_id,
            slug="ambiguous-agent-run",
            status="running",
        )
    else:
        task.dispatch_target = {}
        task.save(update_fields=["dispatch_target", "updated_at", "version"])
    version = task.version
    stopped = []

    class StopSpawner(_FakeSpawner):
        def stop(self, external_id, **kwargs):
            stopped.append(external_id)

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: StopSpawner())
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert stopped == []
    assert task.status == "running"
    assert task.version == version
    assert run.failure["task_cleanup"]["status"] == "failed"
    assert run.failure["task_cleanup"]["remaining"] == 1


@pytest.mark.django_db
def test_cleanup_uses_saved_cluster_and_namespace(run, definition, monkeypatch):
    from astrolift_dispatch.spawners import registry

    _, task = _dispatch_agent_stage(run, definition, 0)
    target = dict(task.dispatch_target)
    task.namespace = "changed-default"
    task.save(update_fields=["namespace", "updated_at", "version"])
    observed = []

    def spawner(backend, *, cluster=None, namespace="default"):
        observed.append((backend, str(cluster.guid), namespace))
        return _FakeSpawner()

    monkeypatch.setattr(registry, "get_spawner", spawner)
    monkeypatch.setattr(
        "astrolift_workflows.activities.agent_stage._resolve_managed_cluster",
        lambda organization: pytest.fail("cleanup tried the current default cluster"),
    )
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    assert observed == [("k8s_job", target["cluster_guid"], target["namespace"])]
    task.refresh_from_db()
    assert task.status == "cancelled"


@pytest.mark.django_db
def test_cleanup_refuses_replaced_cluster_endpoint(run, definition):
    from astrolift_clusters.models import TenantCluster

    _, task = _dispatch_agent_stage(run, definition, 0)
    TenantCluster.objects.filter(guid=task.dispatch_target["cluster_guid"]).update(
        endpoint="https://replacement.invalid"
    )
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == "running"
    assert "endpoint changed" in run.failure["task_cleanup"]["errors"][0]["message"]


@pytest.mark.django_db
def test_cleanup_recovers_resource_created_before_external_id_was_saved(run, definition, monkeypatch):
    from astrolift_dispatch.spawners import registry

    _, task = _dispatch_agent_stage(run, definition, 0)
    task.external_id = ""
    task.status = "provisioning"
    task.save(update_fields=["external_id", "status", "updated_at", "version"])
    stopped = []

    class StopSpawner(_FakeSpawner):
        def stop(self, external_id, **kwargs):
            stopped.append((external_id, kwargs["expected_task_guid"]))

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: StopSpawner())
    _mark_workflow_run_sync(str(run.pk), "terminated", None, None)
    task.refresh_from_db()
    assert stopped == [(task.dispatch_target["planned_external_id"], str(task.guid))]
    assert task.status == "cancelled"


@pytest.mark.django_db
def test_cleanup_waits_for_confirmed_deletion(run, definition, monkeypatch):
    from astrolift_dispatch.spawners import registry

    _, task = _dispatch_agent_stage(run, definition, 0)
    deleted = False

    class DeletingSpawner(_FakeSpawner):
        def confirm_stopped(self, external_id):
            return deleted

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: DeletingSpawner())
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == "running"
    assert run.failure["task_cleanup"]["status"] == "pending"
    assert run.failure["task_cleanup"]["remaining"] == 1
    deleted = True
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == "cancelled"
    assert run.failure["task_cleanup"]["status"] == "completed"


@pytest.mark.django_db
def test_cleanup_rotates_past_failed_tasks_and_preserves_completed_resources(run, definition, monkeypatch):
    from django.utils import timezone

    from astrolift_agents.services.workflow_task_cleanup import cleanup_workflow_tasks
    from astrolift_dispatch.spawners import registry

    tasks = [_dispatch_agent_stage(run, definition, 0)[1] for _ in range(3)]
    tasks[2].transition_to("completed")
    run.status, run.ended_at = "cancelled", timezone.now()
    run.save(update_fields=["status", "ended_at", "updated_at", "version"])
    stopped = []
    failing = True

    class StopSpawner(_FakeSpawner):
        def stop(self, external_id, **kwargs):
            stopped.append(external_id)
            if failing and external_id == tasks[0].external_id:
                raise RuntimeError("delete denied")

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: StopSpawner())
    cleanup_workflow_tasks(run.pk, limit=1)
    cleanup_workflow_tasks(run.pk, limit=1)
    assert stopped == [tasks[0].external_id, tasks[1].external_id]
    failing = False
    cleanup_workflow_tasks(run.pk, limit=1)
    run.refresh_from_db()
    assert stopped == [tasks[0].external_id, tasks[1].external_id, tasks[0].external_id]
    assert run.failure["task_cleanup"]["status"] == "completed"
    tasks[2].refresh_from_db()
    assert tasks[2].status == "completed"


@pytest.mark.django_db
def test_cleanup_refuses_replacement_docker_daemon(run, definition, monkeypatch):
    from astrolift_dispatch.spawners import registry

    _, task = _dispatch_agent_stage(run, definition, 0)
    task.dispatch_target.update(backend="local_docker", docker_daemon_id="original-daemon")
    task.save(update_fields=["dispatch_target", "updated_at", "version"])
    monkeypatch.setattr("astrolift_agents.services.task_target.docker_daemon_id", lambda: "replacement")
    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: pytest.fail("wrong daemon contacted"))
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    task.refresh_from_db()
    run.refresh_from_db()
    assert task.status == "running"
    assert "different Docker daemon" in run.failure["task_cleanup"]["errors"][0]["message"]


@pytest.mark.django_db
def test_closed_workflow_refuses_delayed_stage_creation(run, definition):
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    with pytest.raises(RuntimeError, match="closed workflow"):
        _create_stage_execution_sync(str(run.pk), str(_stage(definition, 0).pk), 1)
    assert not WorkflowStageExecution.objects.filter(workflow_run=run).exists()


@pytest.mark.django_db(transaction=True)
def test_cancellation_during_dispatch_waits_for_durable_spawn_identity(run, definition, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections

    from astrolift_agents.models import AgentTask
    from astrolift_agents.services.workflow_task_cleanup import cleanup_workflow_tasks
    from astrolift_dispatch.spawners import registry

    entered, release = Event(), Event()
    stopped = []

    class BlockingSpawner(_FakeSpawner):
        def spawn(self, task):
            entered.set()
            assert release.wait(15), "test did not release the dispatch"
            return super().spawn(task)

        def stop(self, external_id, **kwargs):
            stopped.append(external_id)

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: BlockingSpawner())
    stage = _stage(definition, 0)
    execution_id = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1)

    def dispatch():
        close_old_connections()
        try:
            return _dispatch_agent_for_stage_sync(str(stage.pk), execution_id, {})
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch)
        try:
            assert entered.wait(10)
            task = AgentTask.objects.get(agent_run__stage_executions__pk=execution_id)
            assert task.dispatch_target["planned_external_id"]
            _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
            run.refresh_from_db()
            task.refresh_from_db()
            assert run.failure["task_cleanup"]["status"] == "pending"
            assert task.status == "provisioning"
            assert stopped == []
        finally:
            release.set()
        future.result(timeout=10)
    cleanup_workflow_tasks(run.pk)
    task.refresh_from_db()
    run.refresh_from_db()
    assert stopped == [task.external_id]
    assert task.status == "cancelled"
    assert run.failure["task_cleanup"]["status"] == "completed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["cancel", "terminate"])
async def test_temporal_closure_during_dispatch_cleans_up_after_spawn(
    run, definition, monkeypatch, temporal_env, action
):
    import asyncio
    from threading import Event

    from asgiref.sync import sync_to_async
    from temporalio.client import WorkflowFailureError

    from astrolift_agents.models import AgentTask
    from astrolift_agents.services.workflow_task_cleanup import cleanup_workflow_tasks
    from astrolift_dispatch.spawners import registry
    from astrolift_workflows.activities.workflow_run_reconcile import reconcile_workflow_runs
    from astrolift_workflows.activities.workflow_stage_activities import (
        create_stage_execution,
        dispatch_agent_for_stage,
        get_workflow_stages,
        mark_workflow_run,
        poll_agent_run_status,
        update_stage_execution,
    )
    from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
    from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
    from core.testing.temporal import temporal_worker

    entered, release = Event(), Event()
    stopped = []

    class BlockingSpawner(_FakeSpawner):
        def spawn(self, task):
            entered.set()
            assert release.wait(20), "test did not release the dispatch"
            return super().spawn(task)

        def stop(self, external_id, **kwargs):
            stopped.append(external_id)

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: BlockingSpawner())

    async def client():
        return temporal_env.client

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)

    @sync_to_async
    def task_state():
        task = AgentTask.objects.get(agent_run__stage_executions__workflow_run_id=run.pk)
        return task.pk, task.status, task.external_id

    async with temporal_worker(
        temporal_env,
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=[
            get_workflow_stages,
            create_stage_execution,
            dispatch_agent_for_stage,
            mark_workflow_run,
            poll_agent_run_status,
            update_stage_execution,
        ],
    ):
        handle = await temporal_env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug=definition.slug,
                workflow_definition_id=str(definition.pk),
                workflow_run_id=str(run.pk),
                trigger_payload={},
                actor=Actor(kind="system"),
            ),
            id=run.workflow_id,
            task_queue="astrolift-test",
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            await sync_to_async(run.refresh_from_db)()
            run.run_id = handle.first_execution_run_id
            await sync_to_async(run.save)(update_fields=["run_id", "updated_at", "version"])
            await getattr(handle, action)()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), 10)
            assert (await handle.describe()).status.name == (
                "CANCELED" if action == "cancel" else "TERMINATED"
            )
            assert stopped == []
            if action == "cancel":
                await sync_to_async(run.refresh_from_db)()
                assert run.failure["task_cleanup"]["status"] == "pending"
        finally:
            release.set()
        for _ in range(100):
            _, status, external_id = await task_state()
            if status == "running":
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("The guarded dispatch did not finish after being released")
        if action == "terminate":
            summary = await reconcile_workflow_runs()
            assert summary.errors == 0
            assert summary.repaired == 1
        else:
            await sync_to_async(cleanup_workflow_tasks)(run.pk)
        _, status, _ = await task_state()
        assert status == "cancelled"
        assert stopped == [external_id]
        await sync_to_async(run.refresh_from_db)()
        assert run.failure["task_cleanup"]["status"] == "completed"
