"""PipelineScheduleWorkflow — what a cron trigger actually fires (#1614).

`astrolift_pipelines/schedule_sync.py` has created Temporal schedules
targeting `"PipelineScheduleWorkflow"` since pipeline triggers shipped, and
the workflow was defined nowhere. Two of the import ratchet's remaining
entries were the same call site failing earlier, on
`get_temporal_client` -- so the schedule was never created either, and the
missing workflow was never reached. Fixing only the import would have
produced schedules whose every fire failed to start.

The wrapper exists because `PipelineRunWorkflow.run` takes an integer
`PipelineRun` primary key. A schedule cannot supply one: the row does not
exist until the fire happens. So this creates the run, then hands its pk on
-- which is exactly what `schedule_sync`'s own comment says this workflow is
for ("a thin wrapper that creates a PipelineRun and dispatches
PipelineRunWorkflow").
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.pipeline_schedule_fire import create_scheduled_pipeline_run

_CREATE_TIMEOUT = timedelta(seconds=60)


@workflow.defn(name="PipelineScheduleWorkflow")
class PipelineScheduleWorkflow:
    @workflow.run
    async def run(self, action: dict[str, Any]) -> dict[str, Any]:
        created = await workflow.execute_activity(
            create_scheduled_pipeline_run,
            action,
            start_to_close_timeout=_CREATE_TIMEOUT,
        )
        if created.get("skipped"):
            # A trigger disabled or deleted between the schedule being
            # written and this fire. Not a failure: the schedule outliving
            # its trigger by one tick is ordinary, and a red workflow for it
            # would train operators to ignore red workflows.
            return created

        # Child rather than a fresh top-level start: the schedule's history
        # should show whether the run it fired actually succeeded, and a
        # detached start would report success for having dispatched.
        result = await workflow.execute_child_workflow(
            "PipelineRunWorkflow",
            created["pipeline_run_id"],
            id=f"pipeline-run-{created['pipeline_run_id']}",
        )
        return {"pipeline_run_id": created["pipeline_run_id"], "result": result}
