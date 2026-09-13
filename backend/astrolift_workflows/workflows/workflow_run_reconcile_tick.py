"""Periodically reconcile a bounded batch of exact Temporal executions."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.workflow_run_reconcile import (
        WorkflowRunReconcileSummary,
        reconcile_workflow_runs_tick,
    )


@workflow.defn(name="WorkflowRunReconcileTickWorkflow")
class WorkflowRunReconcileTickWorkflow:
    @workflow.run
    async def run(self) -> WorkflowRunReconcileSummary:
        return await workflow.execute_activity(
            reconcile_workflow_runs_tick,
            start_to_close_timeout=timedelta(seconds=50),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
