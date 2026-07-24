"""RunStatusReconcileTickWorkflow — one tick of the run-status reconciler.

Runs via the platform schedule registry (~every 60 s). Thin Temporal
entry point around :func:`astrolift_workflows.activities.
run_status_reconcile.reconcile_runs_tick` so the sweep has durable
retries + a single-flight workflow id, and each sweep is its own run in
the Temporal UI.

Coalescing tick model (mirrors the alert-eval + uptime ticks): a stuck
sweep can't overlap the next — the activity timeout is bounded under the
interval.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.run_status_reconcile import (
        RunStatusReconcileSummary,
        reconcile_runs_tick,
    )


_TICK_TIMEOUT = timedelta(seconds=50)
"""Under the 60 s interval so a slow sweep can't overlap the next tick.
Sequential reads suffice at current run counts; parallelize inside the
activity before this bound is at risk."""


@workflow.defn(name="RunStatusReconcileTickWorkflow")
class RunStatusReconcileTickWorkflow:
    @workflow.run
    async def run(self) -> RunStatusReconcileSummary:
        return await workflow.execute_activity(
            reconcile_runs_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
