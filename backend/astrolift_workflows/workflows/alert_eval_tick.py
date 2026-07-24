"""AlertEvalTickWorkflow — one tick of the alert-rule evaluation loop.

Runs via the platform schedule registry (~every 60 s). Thin Temporal
entry point around :func:`astrolift_workflows.activities.alert_eval.
evaluate_alerts_tick` so the sweep has durable retries + a single-flight
workflow id, and each sweep is its own run in the Temporal UI.

Coalescing tick model (mirrors the uptime + cron ticks): a stuck sweep
can't overlap the next — the activity timeout is bounded under the
interval.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.alert_eval import (
        AlertEvalSummary,
        evaluate_alerts_tick,
    )


_TICK_TIMEOUT = timedelta(seconds=50)
"""Under the 60 s interval so a slow sweep can't overlap the next tick."""


@workflow.defn(name="AlertEvalTickWorkflow")
class AlertEvalTickWorkflow:
    @workflow.run
    async def run(self) -> AlertEvalSummary:
        return await workflow.execute_activity(
            evaluate_alerts_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
