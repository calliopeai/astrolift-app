"""ConfiguredWorkflowScheduleWorkflow: what a configured Workflow's schedule fires (#2053).

`workflows/schedule_sync.py` built a `WorkflowDefinitionRunInput` when the
Workflow was saved and handed it to the schedule as the action's argument.
That input names one `WorkflowRun`, so every fire ran the same row: the first
fire closed it, and every later fire failed at its first stage with "Cannot
open a stage on a closed workflow".

The schedule now carries the Workflow's guid and organization only. This
creates a run per fire and runs the stage executor on it, the split
`PipelineScheduleWorkflow` makes for pipeline triggers (#1614).

Once the child settles, its result is posted to the Workflow's delivery
channels (#2244); a failed run also notifies its owner. Delivery is best
effort: its failure is logged and recorded, never raised into the run.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )
    from astrolift_workflows.activities.workflow_result_delivery import (
        DELIVERY_MAX_ATTEMPTS,
        deliver_workflow_run_result,
    )
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT

# The stage executor's own budget for its thin DB activities.
_DB_TIMEOUT = timedelta(minutes=2)
_DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
# Delivery (#2244) retries a slow or rate-limited channel for a few minutes,
# then gives up; the run's own outcome is already settled by then.
_DELIVERY_TIMEOUT = timedelta(minutes=2)
_DELIVERY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=DELIVERY_MAX_ATTEMPTS,
)
# New step in a deployed workflow: histories that predate it replay without it.
DELIVERY_PATCH = "configured-workflow-result-delivery-v1"


@workflow.defn(name="ConfiguredWorkflowScheduleWorkflow")
class ConfiguredWorkflowScheduleWorkflow:
    @workflow.run
    async def run(self, action: dict[str, Any]) -> dict[str, Any]:
        fire = await workflow.execute_activity(
            create_scheduled_workflow_run,
            action,
            start_to_close_timeout=_DB_TIMEOUT,
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
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
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        try:
            result = await handle
        except ChildWorkflowError:
            if workflow.patched(DELIVERY_PATCH):
                await self._deliver(fire.run_input.workflow_run_id, "failed")
            raise
        if workflow.patched(DELIVERY_PATCH):
            await self._deliver(fire.run_input.workflow_run_id, "completed" if result.ok else "failed")
        return {
            "workflow_run_id": fire.run_input.workflow_run_id,
            "workflow_id": fire.workflow_id,
            "result": result,
        }

    async def _deliver(self, workflow_run_id: str, outcome: str) -> None:
        """Post the result to the workflow's channels. Never fails the run."""
        try:
            await workflow.execute_activity(
                deliver_workflow_run_result,
                {"workflow_run_id": workflow_run_id, "outcome": outcome},
                start_to_close_timeout=_DELIVERY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_DELIVERY_RETRY,
            )
        except ActivityError:
            workflow.logger.warning("result delivery for run %s did not complete", workflow_run_id)
