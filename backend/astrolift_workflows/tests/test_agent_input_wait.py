from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio import workflow
from temporalio.worker import Worker

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
        finally:
            if not result.done():
                await handle.cancel()
                await asyncio.gather(result, return_exceptions=True)
