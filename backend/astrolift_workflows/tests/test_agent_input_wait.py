from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio import activity, workflow
from temporalio.worker import Replayer, Worker

from astrolift_agents.models import AgentTask, AgentTaskEvent
from astrolift_agents.services.agent_task_requests import reply_to_request
from astrolift_agents.tests.test_task_timeout import APPROVAL
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.tests import test_workflow_stage_activities as fixtures
from astrolift_workflows.workflows import workflow_definition_run as definition_run

agent_workload = fixtures.agent_workload


@workflow.defn(sandboxed=False)
class InputWaitWorkflow:
    @workflow.run
    async def run(self, run_id: str) -> str:
        return await definition_run.WorkflowDefinitionRunWorkflow()._poll_agent_to_terminal(run_id, 2)


@pytest.mark.django_db(transaction=True)
async def test_workflow_survives_wait_then_expires_after_answer(temporal_env, agent_workload, monkeypatch):
    from astrolift_lifecycle.models import AgentRun

    monkeypatch.setattr(definition_run, "_AGENT_POLL_INTERVAL", timedelta(milliseconds=100))

    def create():
        run = AgentRun.objects.create(workload=agent_workload, status="running")
        task = AgentTask.objects.create(
            organization=agent_workload.registered_app.organization,
            agent_run=run,
            status="running",
            provisioning_at=timezone.now(),
            timeout_seconds=2,
            input_wait_budget_seconds=30,
            dispatch_target={"version": 1, "backend": "local_docker"},
        )
        request = AgentTaskEvent.objects.create(
            organization=task.organization,
            agent_task=task,
            sequence=1,
            turn_id="turn",
            message_id="approval",
            kind="approval_required",
            request=APPROVAL,
        )
        return run, task, request

    run, task, request = await sync_to_async(create)()
    queue = f"input-wait-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[InputWaitWorkflow],
        activities=[activities.poll_agent_run_progress, activities.poll_agent_run_status],
    ):
        handle = await temporal_env.client.start_workflow(
            InputWaitWorkflow.run,
            str(run.pk),
            id=queue,
            task_queue=queue,
        )
        result = asyncio.create_task(handle.result())
        try:
            await asyncio.sleep(3)
            assert not result.done(), "waiting for input spent the stage runtime budget"
            await sync_to_async(task.refresh_from_db)()
            assert task.status == "running"
            await sync_to_async(reply_to_request)(
                task=task, sequence=request.sequence, response={"decision": "allow"}
            )
            assert await asyncio.wait_for(asyncio.shield(result), 15) in {"failed", "timed_out"}
            await sync_to_async(task.refresh_from_db)()
            assert task.status == "timed_out"
            assert task.failure == {"message": "agent exceeded its 2s timeout"}
            await Replayer(workflows=[InputWaitWorkflow]).replay_workflow(await handle.fetch_history())
        finally:
            if not result.done():
                await handle.cancel()
                await asyncio.gather(result, return_exceptions=True)


@workflow.defn(name="InputWaitWorkflow", sandboxed=False)
class LegacyInputWaitWorkflow:
    """The pre-1840 polling commands, for an actual server-produced history."""

    @workflow.run
    async def run(self, run_id: str) -> str:
        deadline = workflow.now() + timedelta(seconds=2)
        while True:
            status = await workflow.execute_activity(
                activities.poll_agent_run_status,
                run_id,
                start_to_close_timeout=definition_run._DB_TIMEOUT,
                retry_policy=definition_run._DB_RETRY,
            )
            if status in ("succeeded", "failed", "cancelled"):
                return status
            if workflow.now() >= deadline:
                return "timed_out"
            await workflow.sleep(min(definition_run._AGENT_POLL_INTERVAL, deadline - workflow.now()))


async def test_pre_input_wait_history_replays_without_replacing_status_activity(temporal_env, monkeypatch):
    monkeypatch.setattr(definition_run, "_AGENT_POLL_INTERVAL", timedelta(milliseconds=100))
    polls = []

    @activity.defn(name="astrolift.workflow_stage.poll_agent_run_status")
    async def status(run_id: str) -> str:
        polls.append(run_id)
        return "succeeded" if len(polls) == 2 else "running"

    queue = f"legacy-input-wait-{uuid4()}"
    async with Worker(
        temporal_env.client, task_queue=queue, workflows=[LegacyInputWaitWorkflow], activities=[status]
    ):
        handle = await temporal_env.client.start_workflow(
            LegacyInputWaitWorkflow.run,
            "legacy-run",
            id=queue,
            task_queue=queue,
        )
        assert await asyncio.wait_for(handle.result(), 15) == "succeeded"
        history = await handle.fetch_history()
    assert polls == ["legacy-run", "legacy-run"]
    await Replayer(workflows=[InputWaitWorkflow]).replay_workflow(history)
