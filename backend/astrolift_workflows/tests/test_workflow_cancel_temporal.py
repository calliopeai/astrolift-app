from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.exceptions import CancelledError
from temporalio.worker import Replayer

from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker


async def test_cancel_pending_gate_finalizes_before_temporal_closes(temporal_env):
    opened = asyncio.Event()
    finalized = []

    @activity.defn(name="astrolift.workflow_stage.get_workflow_stages")
    async def get_stages(params):
        return {
            "pattern_kind": "single",
            "definition_id": "1",
            "stages": [
                {
                    "stage_id": "1",
                    "order": 0,
                    "kind": "human_gate",
                    "output_key": "gate",
                    "timeout_seconds": 1800,
                }
            ],
        }

    @activity.defn(name="astrolift.workflow_stage.create_stage_execution")
    async def create_execution(run_id, stage_id, attempt, context=None):
        opened.set()
        return "gate-1"

    @activity.defn(name="astrolift.workflow_stage.mark_workflow_run")
    async def mark_run(run_id, status, result=None, failure=None):
        finalized.append((run_id, status))

    async with temporal_worker(
        temporal_env,
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=[get_stages, create_execution, mark_run],
    ):
        handle = await temporal_env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug="gate",
                workflow_definition_id="1",
                workflow_run_id="1",
                trigger_payload={},
                actor=Actor(kind="system"),
            ),
            id="cancel-gate-regression",
            task_queue="astrolift-test",
        )
        await asyncio.wait_for(opened.wait(), timeout=10)
        # The activity signals opened before its completion reaches the
        # server. Wait for the gate's timer so this cancels the pending gate,
        # rather than racing cancellation against its creation activity.
        for _ in range(100):
            history = await handle.fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in history.events):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("The workflow never reached its pending gate")
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as error:
            await asyncio.wait_for(handle.result(), timeout=15)
        assert isinstance(error.value.cause, CancelledError)
        assert finalized == [("1", "cancelled")]


async def test_pre_finalization_cancellation_history_still_replays():
    # Captured from the unchanged executor at 570675dd with a real Temporal
    # test server (SDK 1.27.2). It has no cancellation-finalization marker or
    # mark_workflow_run activity; the new worker must preserve those commands.
    raw = (Path(__file__).parent / "fixtures" / "legacy-cancelled-workflow.json").read_text()
    history = WorkflowHistory.from_json("cancel-gate-regression", raw)
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)


async def test_pre_propagation_dispatch_cancellation_history_still_replays():
    # Captured from 6ac6c5bb before the propagation fix, with real activities
    # against PostgreSQL. Cancellation incorrectly launched a second dispatch;
    # replay must preserve those commands without scheduling new work.
    raw = (Path(__file__).parent / "fixtures" / "legacy-cancelled-dispatch.json").read_text()
    history = WorkflowHistory.from_json("legacy-cancelled-dispatch", raw)
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)
