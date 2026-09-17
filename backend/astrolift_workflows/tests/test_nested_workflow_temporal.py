from __future__ import annotations

import asyncio

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import CancelledError

from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.workflows.workflow_definition_run import (
    WorkflowDefinitionRunWorkflow,
)
from core.testing.temporal import temporal_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["complete", "cancel_parent", "cancel_child"])
async def test_nested_definition_executes_as_linked_temporal_child(temporal_env, action):
    calls: dict[str, list] = {
        "plans": [],
        "children": [],
        "starts": [],
        "updates": [],
        "finalized": [],
    }

    @activity.defn(name="astrolift.workflow_stage.get_workflow_stages")
    async def get_stages(params):
        calls["plans"].append(params)
        if params["workflow_definition_slug"] == "outer":
            return {
                "definition_id": "10",
                "pattern_kind": "chained",
                "stages": [
                    {
                        "stage_id": "101",
                        "order": 0,
                        "kind": "workflow",
                        "on_failure": "skip",
                        "timeout_seconds": 300,
                        "fan_out_count": None,
                        "skill_refs": [],
                        "agent_definition_id": None,
                        "has_agent_definition": False,
                        "environment_spec_slug": "",
                        "prompt": "",
                        "output_key": "inner_result",
                        "workflow_ref": "inner",
                        "nested_definition_id": "20",
                        "nested_definition_slug": "inner",
                    }
                ],
            }
        return {
            "definition_id": "20",
            "pattern_kind": "single",
            "stages": [
                {
                    "stage_id": "201",
                    "order": 0,
                    "kind": "checkpoint" if action == "complete" else "human_gate",
                    "on_failure": "fail",
                    "timeout_seconds": 300,
                    "fan_out_count": None,
                    "skill_refs": [],
                    "agent_definition_id": None,
                    "has_agent_definition": False,
                    "environment_spec_slug": "",
                    "prompt": "",
                    "output_key": "stage_0",
                    "workflow_ref": "",
                    "nested_definition_id": None,
                    "nested_definition_slug": "",
                }
            ],
        }

    @activity.defn(name="astrolift.workflow_stage.create_stage_execution")
    async def create_execution(workflow_run_id, stage_id, attempt_number=1):
        return f"execution-{workflow_run_id}-{stage_id}-{attempt_number}"

    @activity.defn(name="astrolift.workflow_stage.create_nested_workflow_run")
    async def create_child(parent_id, execution_id, child_definition_id):
        calls["children"].append((parent_id, execution_id, child_definition_id))
        return {
            "workflow_run_id": "200",
            "workflow_run_guid": "child-guid",
            "workflow_id": "WorkflowDefinitionRunWorkflow-200",
            "definition_id": "20",
            "definition_slug": "inner",
            "nesting_depth": 1,
        }

    @activity.defn(name="astrolift.workflow_stage.record_nested_workflow_start")
    async def record_start(child_id, temporal_run_id):
        calls["starts"].append((child_id, temporal_run_id))

    @activity.defn(name="astrolift.workflow_stage.snapshot_checkpoint")
    async def checkpoint(workflow_run_id, stage_id, previous_output=None):
        return f"checkpoint-{workflow_run_id}-{stage_id}"

    @activity.defn(name="astrolift.workflow_stage.update_stage_execution")
    async def update_execution(execution_id, status, output=None, error=None):
        calls["updates"].append((execution_id, status, output, error))

    @activity.defn(name="astrolift.workflow_stage.mark_workflow_run")
    async def mark_run(workflow_run_id, status, result=None, failure=None):
        calls["finalized"].append((workflow_run_id, status, result, failure))

    activities = [
        get_stages,
        create_execution,
        create_child,
        record_start,
        checkpoint,
        update_execution,
        mark_run,
    ]
    async with temporal_worker(
        temporal_env,
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=activities,
    ):
        handle = await temporal_env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug="outer",
                workflow_definition_id="10",
                workflow_run_id="100",
                trigger_payload={"issue": "EMR-1"},
                actor=Actor(kind="system"),
            ),
            id="nested-workflow-test",
            task_queue="astrolift-test",
        )
        if action != "complete":
            for _ in range(200):
                if calls["starts"]:
                    child = temporal_env.client.get_workflow_handle(
                        "WorkflowDefinitionRunWorkflow-200", run_id=calls["starts"][0][1]
                    )
                    history = await child.fetch_history()
                    if any(
                        event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in history.events
                    ):
                        break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("The child did not reach its human gate")
            await (handle if action == "cancel_parent" else child).cancel()
        if action == "cancel_parent":
            with pytest.raises(WorkflowFailureError) as error:
                await asyncio.wait_for(handle.result(), 10)
            assert isinstance(error.value.cause, CancelledError)
            assert [(row[0], row[1]) for row in calls["finalized"] if row[0] == "100"] == [
                ("100", "cancelled")
            ]
            assert len(calls["children"]) == 1
            assert calls["updates"] == []
            return
        result = await asyncio.wait_for(handle.result(), 10)

    assert result.ok is True
    if action == "cancel_child":
        assert calls["updates"][-1][1] == "skipped"
        assert calls["finalized"][-1][0:2] == ("100", "completed")
        assert len(calls["children"]) == 1
        return
    assert result.data["final_output"]["issue"] == "EMR-1"
    assert calls["children"] == [("100", "execution-100-101-1", "20")]
    assert calls["starts"][0][0] == "200"
    child_plan = next(params for params in calls["plans"] if params["workflow_definition_slug"] == "inner")
    assert child_plan["workflow_ancestry"] == ["10"]
    assert [(row[0], row[1]) for row in calls["finalized"]] == [
        ("200", "completed"),
        ("100", "completed"),
    ]
    parent_update = calls["updates"][-1]
    assert parent_update[1] == "completed"
    assert parent_update[2]["child_workflow_run_guid"] == "child-guid"
