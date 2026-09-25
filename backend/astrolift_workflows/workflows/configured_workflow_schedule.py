"""ConfiguredWorkflowScheduleWorkflow: what a configured Workflow's schedule fires (#2053).

`workflows/schedule_sync.py` built a `WorkflowDefinitionRunInput` when the
Workflow was saved and handed it to the schedule as the action's argument.
That input names one `WorkflowRun`, so every fire ran the same row: the first
fire closed it, and every later fire failed at its first stage with "Cannot
open a stage on a closed workflow".

The schedule now carries the Workflow's guid and organization only. This
creates a run per fire and runs the stage executor on it, the split
`PipelineScheduleWorkflow` makes for pipeline triggers (#1614).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )

# The stage executor's own budget for its thin DB activities.
_DB_TIMEOUT = timedelta(minutes=2)
_DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)


@workflow.defn(name="ConfiguredWorkflowScheduleWorkflow")
class ConfiguredWorkflowScheduleWorkflow:
    @workflow.run
    async def run(self, action: dict[str, Any]) -> dict[str, Any]:
        fire = await workflow.execute_activity(
            create_scheduled_workflow_run,
            action,
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        if fire.skipped:
            # The Workflow was deleted, disabled or moved off its schedule
            # after the schedule was written. Not a failure: a red run for it
            # would train operators to ignore red runs.
            return {"skipped": fire.skipped}

        # Child rather than a fresh top-level start: the schedule's history
        # should show whether the run it fired actually succeeded, and a
        # detached start would report success for having dispatched. Waiting
        # on the child also keeps the schedule's overlap policy meaningful.
        handle = await workflow.start_child_workflow(
            WorkflowDefinitionRunWorkflow.run,
            fire.run_input,
            id=fire.workflow_id,
        )
        await workflow.execute_activity(
            record_scheduled_workflow_start,
            args=[fire.run_input.workflow_run_id, handle.first_execution_run_id],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        result = await handle
        return {
            "workflow_run_id": fire.run_input.workflow_run_id,
            "workflow_id": fire.workflow_id,
            "result": result,
        }
