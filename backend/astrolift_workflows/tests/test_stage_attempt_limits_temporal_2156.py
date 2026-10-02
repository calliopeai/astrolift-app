"""Real PostgreSQL mirrors and real Temporal execute the production activities."""

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Replayer

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def create_plan(cap):
    org = Organization.objects.create(name="Stage cap integration", slug="stage-cap-integration")
    child = WorkflowDefinition.objects.create(name="Child", slug="child", organization=org, model_label="")
    WorkflowStage.objects.create(
        definition=child,
        slug="child-gate",
        order=0,
        kind="human_gate",
        timeout_seconds=1,
    )
    outer = WorkflowDefinition.objects.create(name="Parent", slug="parent", organization=org, model_label="")
    stage = WorkflowStage.objects.create(
        definition=outer,
        slug="parent-child",
        order=0,
        kind="workflow",
        workflow_ref="child",
        on_failure="retry",
        max_attempts=cap,
        timeout_seconds=30,
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=outer,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"stage-cap-{uuid.uuid4()}",
        run_id="",
        status="running",
    )
    return outer.pk, run.pk, run.workflow_id, stage.pk


@pytest.mark.parametrize("cap", [1, 4])
async def test_real_child_failures_stop_at_configured_attempt_cap_and_replay(temporal_env, cap):
    definition_id, run_id, workflow_id, stage_id = await sync_to_async(create_plan)(cap)
    registered = [
        activities.get_workflow_stages,
        activities.create_stage_execution,
        activities.create_nested_workflow_run,
        activities.record_nested_workflow_start,
        activities.record_human_gate_decision,
        activities.update_stage_execution,
        activities.mark_workflow_run,
    ]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        handle = await temporal_env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug="parent",
                workflow_definition_id=str(definition_id),
                workflow_run_id=str(run_id),
                trigger_payload={},
                actor=Actor(kind="system"),
            ),
            id=workflow_id,
            task_queue="astrolift-test",
        )
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok is False
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(
            workflow_run_id=run_id,
            stage_id=stage_id,
        )
        .order_by("attempt_number")
        .values_list("attempt_number", "status")
    )
    assert rows == [(attempt, "failed") for attempt in range(1, cap + 1)]
    children = await sync_to_async(list)(
        WorkflowRun.objects.filter(parent_run_id=run_id).values_list("status", flat=True)
    )
    assert children == ["failed"] * cap
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=run_id).status)() == "failed"
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)
