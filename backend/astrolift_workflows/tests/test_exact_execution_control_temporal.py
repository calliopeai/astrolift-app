from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from temporalio import workflow
from temporalio.client import WorkflowFailureError

from astrolift_workflows import client
from core.testing.temporal import temporal_worker


@workflow.defn(sandboxed=False)
class HoldingExactExecution:
    def __init__(self):
        self.finished = False

    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: self.finished)

    @workflow.signal
    def finish(self):
        self.finished = True


@pytest.mark.parametrize("operation", ["cancel", "terminate"])
async def test_control_pins_execution_when_temporal_workflow_id_is_reused(
    temporal_env, monkeypatch, operation
):
    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", get_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    control = client.cancel_workflow if operation == "cancel" else client.terminate_workflow
    extra_args = [] if operation == "cancel" else ["disposable verification"]
    async with temporal_worker(temporal_env, workflows=[HoldingExactExecution]):
        original = await temporal_env.client.start_workflow(
            HoldingExactExecution.run, id="reused-control-id", task_queue="astrolift-test"
        )
        await original.signal(HoldingExactExecution.finish)
        await asyncio.wait_for(original.result(), 10)
        neighbor = await temporal_env.client.start_workflow(
            HoldingExactExecution.run, id="reused-control-id", task_queue="astrolift-test"
        )
        try:
            description = await sync_to_async(client.describe_workflow_instance)(
                original.id, run_id=original.first_execution_run_id
            )
            assert description["status"] == "COMPLETED"
            assert description["run_id"] == original.first_execution_run_id
            # A delayed Stop for the finished incarnation must not reach the
            # new one, whether Temporal accepts an idempotent request or refuses it.
            await sync_to_async(control)(original.id, *extra_args, run_id=original.first_execution_run_id)
            old_record = temporal_env.client.get_workflow_handle(
                original.id, run_id=original.first_execution_run_id
            )
            assert (await old_record.describe()).status.name == "COMPLETED"
            assert (await neighbor.describe()).status.name == "RUNNING"
            assert await sync_to_async(control)(
                neighbor.id, *extra_args, run_id=neighbor.first_execution_run_id
            )
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(neighbor.result(), 10)
            assert (await neighbor.describe()).status.name == (
                "CANCELED" if operation == "cancel" else "TERMINATED"
            )
        finally:
            if (await neighbor.describe()).status.name == "RUNNING":
                await neighbor.terminate(reason="test cleanup")


@pytest.mark.parametrize("operation", ["cancel", "terminate"])
@pytest.mark.parametrize("run_id", ["", " "])
def test_explicit_empty_execution_id_never_falls_back_to_latest(monkeypatch, operation, run_id):
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    method = "_cancel_async" if operation == "cancel" else "_terminate"
    monkeypatch.setattr(client, method, lambda *a, **kw: pytest.fail("unqualified control request sent"))
    control = client.cancel_workflow if operation == "cancel" else client.terminate_workflow
    extra_args = [] if operation == "cancel" else ["disposable verification"]
    assert not control("shared-workflow", *extra_args, run_id=run_id)


@pytest.mark.parametrize("run_id", ["", " "])
def test_explicit_empty_execution_id_never_describes_latest(monkeypatch, run_id):
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(
        client, "_describe_instance_async", lambda *a, **kw: pytest.fail("unqualified observation sent")
    )
    assert client.describe_workflow_instance("shared-workflow", run_id=run_id) is None
