"""Production worker activities, PostgreSQL and a real disposable Temporal server."""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from temporalio.api.enums.v1 import EventType
from temporalio.worker import Replayer, Worker

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.workflows.workflow_definition_run import (
    WorkflowDefinitionRunWorkflow,
)
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]
REGISTERED = [
    activities.get_workflow_stages,
    activities.create_stage_execution,
    activities.snapshot_checkpoint,
    activities.record_human_gate_decision,
    activities.update_stage_execution,
    activities.mark_workflow_run,
]


def create_plan(*, max_rounds=3, exhausted="fail", output_test=False):
    org = Organization.objects.create(
        name="Bounded loop integration", slug="bounded-loop-integration"
    )
    definition = WorkflowDefinition.objects.create(
        name="Review",
        slug="review",
        organization=org,
        model_label="",
        pattern_kind="chained" if output_test else "review_loop",
    )
    WorkflowStage.objects.create(
        definition=definition,
        slug="draft",
        order=0,
        kind="checkpoint",
        output_key="draft",
    )
    stage = WorkflowStage.objects.create(
        definition=definition,
        slug="review-stage",
        order=1,
        kind="checkpoint" if output_test else "human_gate",
        output_key="review",
        timeout_seconds=1800,
        back_edge={
            "to": "draft",
            "when": "output_equals" if output_test else "gate_rejected",
            "max_rounds": max_rounds,
            "on_exhausted": exhausted,
            **({"path": "tests.passed", "value": False} if output_test else {}),
        },
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"bounded-loop-{uuid.uuid4()}",
        run_id="",
        status="running",
    )
    return definition.pk, run.pk, run.workflow_id, stage.pk


async def start(temporal_env, plan, payload=None):
    definition_id, run_id, workflow_id, _ = plan
    return await temporal_env.client.start_workflow(
        WorkflowDefinitionRunWorkflow.run,
        WorkflowDefinitionRunInput(
            workflow_definition_slug="review",
            workflow_definition_id=str(definition_id),
            workflow_run_id=str(run_id),
            trigger_payload=payload or {"draft": "initial"},
            actor=Actor(kind="system"),
        ),
        id=workflow_id,
        task_queue="astrolift-test",
    )


async def wait_execution(run_id, stage_id, *, round_number=1, status="running"):
    for _ in range(300):
        row = await sync_to_async(
            lambda: WorkflowStageExecution.objects.filter(
                workflow_run_id=run_id,
                stage_id=stage_id,
                round_number=round_number,
                status=status,
            )
            .order_by("pk")
            .first()
        )()
        if row:
            return row
        await asyncio.sleep(0.02)
    pytest.fail(
        f"The real worker did not open the expected round {round_number} {status} execution"
    )


async def wait_gate_acknowledged(handle, execution_id):
    """SQL commit precedes the activity acknowledgement; wait for its timer."""
    for _ in range(300):
        history = await handle.fetch_history()
        completed = None
        for event in history.events:
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                payloads = (
                    event.activity_task_completed_event_attributes.result.payloads
                )
                if (
                    payloads
                    and payloads[0].metadata.get("encoding") == b"json/plain"
                    and json.loads(payloads[0].data) == str(execution_id)
                ):
                    completed = event.event_id
            if (
                completed is not None
                and event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                and event.event_id > completed
            ):
                return
        await asyncio.sleep(0.02)
    pytest.fail("The real engine did not acknowledge its pending gate before restart")


async def test_rejection_reenters_producer_and_survives_worker_restart(temporal_env):
    plan = await sync_to_async(create_plan)()
    _, run_id, _, stage_id = plan

    def replacement_worker():
        # Disable sticky caching in the disposable Java test server so the
        # replacement must replay durable history, rather than an old cache.
        return Worker(
            temporal_env.client,
            task_queue="astrolift-test",
            workflows=[WorkflowDefinitionRunWorkflow],
            activities=REGISTERED,
            max_cached_workflows=0,
        )

    async with replacement_worker():
        handle = await start(temporal_env, plan)
        first = await wait_execution(run_id, stage_id)
        await handle.signal(
            "human_gate_decision",
            {
                "execution_id": str(first.pk),
                "decision": "rejected",
                "note": "Revise the draft",
            },
        )
        second = await wait_execution(run_id, stage_id, round_number=2)
        await wait_gate_acknowledged(handle, second.pk)
    async with replacement_worker():
        await handle.signal(
            "human_gate_decision",
            {
                "execution_id": str(second.pk),
                "decision": "approved",
                "note": "Approved revision",
            },
        )
        result = await asyncio.wait_for(handle.result(), 45)
        history = await handle.fetch_history()
    assert result.ok
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run_id=run_id)
        .order_by("pk")
        .values_list(
            "stage__output_key", "round_number", "attempt_number", "status", "caused_by"
        )
    )
    assert [(key, rnd, attempt, status) for key, rnd, attempt, status, _ in rows] == [
        ("draft", 1, 1, "completed"),
        ("review", 1, 1, "failed"),
        ("draft", 2, 1, "completed"),
        ("review", 2, 1, "completed"),
    ]
    assert all(
        cause
        == {
            "edge": "review->draft",
            "reason": "gate_rejected",
            "max_rounds": 3,
            "edge_round": 2,
        }
        for *_, cause in rows[2:]
    )
    assert result.data["final_output"]["human_gate"] == "approved"
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)


@pytest.mark.parametrize("exhausted", ["fail", "escalate"])
async def test_rejections_stop_at_declared_round_cap(temporal_env, exhausted):
    plan = await sync_to_async(create_plan)(max_rounds=2, exhausted=exhausted)
    _, run_id, _, stage_id = plan
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        for rnd in (1, 2):
            gate = await wait_execution(run_id, stage_id, round_number=rnd)
            await handle.signal(
                "human_gate_decision",
                {
                    "execution_id": str(gate.pk),
                    "decision": "rejected",
                    "note": f"Reject round {rnd}",
                },
            )
        if exhausted == "escalate":
            escalation = await wait_execution(
                run_id, stage_id, round_number=2, status="escalated"
            )
            assert escalation.caused_by["reason"] == "max_rounds_exhausted"
            await handle.signal(
                "escalation_cleared",
                {
                    "execution_id": str(escalation.pk),
                    "note": "Operator accepts the outcome",
                },
            )
        result = await asyncio.wait_for(handle.result(), 20)
    assert result.ok is (exhausted == "escalate")
    assert (
        await sync_to_async(
            lambda: WorkflowStageExecution.objects.filter(
                workflow_run_id=run_id, round_number__gt=2
            ).count()
        )()
        == 0
    )
    assert (
        await sync_to_async(
            lambda: WorkflowStageExecution.objects.filter(
                workflow_run_id=run_id, stage__output_key="draft"
            ).count()
        )()
        == 2
    )
    if exhausted == "fail":
        assert result.data["status"] == "incomplete"
        assert result.data["loop"]["max_rounds"] == 2


@pytest.mark.parametrize(
    "payload, expected",
    [({"tests": {"passed": False}}, "incomplete"), ({"tests": {}}, "unavailable")],
)
async def test_output_condition_is_bounded_or_explicitly_unavailable(
    temporal_env, payload, expected
):
    plan = await sync_to_async(create_plan)(max_rounds=2, output_test=True)
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, payload)
        result = await asyncio.wait_for(handle.result(), 20)
    assert not result.ok
    assert result.data["status"] == expected
    counts = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run_id=plan[1])
        .order_by("pk")
        .values_list("round_number", flat=True)
    )
    assert counts == ([1, 1, 2, 2] if expected == "incomplete" else [1, 1])


async def test_final_child_failure_returns_after_attempts_and_passes_actual_feedback(
    temporal_env,
):
    def plan_rows():
        plan = create_plan(max_rounds=2)
        definition = WorkflowDefinition.objects.get(pk=plan[0])
        definition.pattern_kind = "chained"
        definition.save(update_fields=["pattern_kind"])
        child = WorkflowDefinition.objects.create(
            organization=definition.organization,
            name="Failed child",
            slug="failed-child",
            model_label="",
        )
        WorkflowStage.objects.create(
            definition=child, order=0, kind="human_gate", timeout_seconds=1
        )
        stage = WorkflowStage.objects.get(pk=plan[3])
        stage.kind = "workflow"
        stage.workflow_ref = "failed-child"
        stage.timeout_seconds = 30
        stage.on_failure = "retry"
        stage.max_attempts = 2
        stage.back_edge = {"to": "draft", "when": "stage_failed", "max_rounds": 2}
        stage.save()
        return plan

    plan = await sync_to_async(plan_rows)()
    registered = REGISTERED + [
        activities.create_nested_workflow_run,
        activities.record_nested_workflow_start,
    ]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok and result.data["status"] == "incomplete"
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run_id=plan[1]).order_by("pk")
    )
    assert [
        (row.round_number, row.attempt_number)
        for row in rows
        if row.stage_id == plan[3]
    ] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert rows[3].caused_by["reason"] == "stage_failed"
    children_inputs = [
        json.loads(
            event.start_child_workflow_execution_initiated_event_attributes.input.payloads[
                0
            ].data
        )
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]
    loop = children_inputs[2]["trigger_payload"]["_astrolift_workflow"]["loop"]
    assert loop["feedback"]["failure"]["message"]
    assert loop["edge_round"] == 2 and loop["round_number"] == 2
    assert (
        await sync_to_async(
            lambda: WorkflowRun.objects.filter(parent_run_id=plan[1]).count()
        )()
        == 4
    )
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)


async def test_real_unconfigured_agent_fanout_records_owned_branches_and_incomplete_outcome(
    temporal_env,
):
    def plan_rows():
        plan = create_plan(max_rounds=2)
        definition = WorkflowDefinition.objects.get(pk=plan[0])
        definition.pattern_kind = "chained"
        definition.save(update_fields=["pattern_kind"])
        stage = WorkflowStage.objects.get(pk=plan[3])
        from astrolift_identity.models import Project, Team
        from astrolift_registry.models import RegisteredApp, Workload

        team = Team.objects.create(
            organization=definition.organization, name="Disposable", slug="disposable"
        )
        project = Project.objects.create(
            organization=definition.organization,
            team=team,
            name="Disposable",
            slug="disposable",
        )
        app = RegisteredApp.objects.create(
            organization=definition.organization,
            team=team,
            project=project,
            name="Disposable agent app",
            slug="disposable-agent-app",
        )
        agent = Workload.objects.create(
            registered_app=app,
            name="Unconfigured agent",
            slug="unconfigured-agent",
            kind="agent",
        )
        stage.agent_definition = agent
        stage.kind = "agent_dispatch"
        stage.fan_out_count = 2
        stage.back_edge = {}
        stage.save()
        return plan

    plan = await sync_to_async(plan_rows)()
    registered = REGISTERED + [
        activities.dispatch_agent_for_stage,
        activities.load_agent_run_outcome,
    ]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok and result.data["status"] == "incomplete"
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(
            workflow_run_id=plan[1], stage_id=plan[3]
        ).order_by("pk")
    )
    parent = next(row for row in rows if row.fanout_parent_execution_id is None)
    branches = [row for row in rows if row.fanout_parent_execution_id == parent.pk]
    assert parent.status == "failed"
    assert len(branches) == 2
    assert {row.fanout_index for row in branches} == {0, 1}
    assert all(row.round_number == 1 and row.status == "failed" for row in branches)
    assert parent.output["count"] == 2 and parent.output["complete"] is False
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)


@pytest.mark.parametrize(
    "cap,fallback", [(1, "Bounded import done"), (2, None), (5, ""), (2, "__absent__")]
)
async def test_actual_flowise_import_roundtrip_executes_and_preserves_cap_output(
    temporal_env, cap, fallback
):
    from workflows.importers.flowise import FlowiseImporter
    from workflows.manifest import (
        create_definition_from_manifest,
        emit_workflow_manifest,
        parse_workflow_manifest,
    )
    from workflows.tests.test_flowise_bounded_loops_2156 import source_loop

    def prepare():
        source = source_loop(cap=cap, fallback=fallback)
        if fallback == "__absent__":
            source["nodes"][2]["data"]["inputs"].pop("fallbackMessage")
        imported = FlowiseImporter().import_flow(source)
        parsed = parse_workflow_manifest(emit_workflow_manifest(imported.manifest))
        org = Organization.objects.create(
            name="Imported loop integration", slug="imported-loop-integration"
        )
        definition = create_definition_from_manifest(
            parsed, organization=org, is_enabled=True
        )
        run = WorkflowRun.objects.create(
            organization=org,
            workflow_definition=definition,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id=f"imported-loop-{uuid.uuid4()}",
            run_id="",
            status="running",
        )
        gate = definition.stages.get(kind="human_gate")
        return definition.pk, run.pk, run.workflow_id, gate.pk

    plan = await sync_to_async(prepare)()
    _, run_id, _, stage_id = plan
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        for round_number in range(1, cap + 1):
            row = await wait_execution(run_id, stage_id, round_number=round_number)
            await handle.signal(
                "human_gate_decision",
                {
                    "execution_id": str(row.pk),
                    "decision": "approved",
                    "note": "Continue",
                },
            )
        result = await asyncio.wait_for(handle.result(), 45)
        history = await handle.fetch_history()
    assert result.ok
    expected = {
        "nodeID": "humanInputAgentflow_0",
        "maxLoopCount": cap,
        "fallbackMessage": fallback,
        "content": fallback
        or f"Loop completed after reaching maximum iteration count of {cap}.",
    }
    if fallback == "__absent__":
        expected.pop("fallbackMessage")
        expected["content"] = (
            f"Loop completed after reaching maximum iteration count of {cap}."
        )
    assert result.data["final_output"] == expected
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run_id=run_id)
        .order_by("pk")
        .values("stage__kind", "round_number", "status", "caused_by", "output")
    )
    assert [
        (row["stage__kind"], row["round_number"], row["status"]) for row in rows
    ] == [
        (kind, round_number, "completed")
        for round_number in range(1, cap + 1)
        for kind in ("human_gate", "checkpoint")
    ]
    assert rows[-1]["output"] == {"checkpoint": expected}
    assert all(
        row["output"]["checkpoint"]["content"]
        == "Loop back to Continue this round? (humanInputAgentflow_0)"
        for row in rows[:-1]
        if row["stage__kind"] == "checkpoint"
    )
    assert all(row["caused_by"].get("reason") == "always" for row in rows[2:])
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)
