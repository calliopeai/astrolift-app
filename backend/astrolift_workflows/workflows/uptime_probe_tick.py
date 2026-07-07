"""UptimeProbeTickWorkflow — one tick of the synthetic uptime prober.

Runs via the platform schedule registry (~every 2 min). Thin Temporal
entry point around :func:`astrolift_workflows.activities.uptime.
probe_uptime_tick` so the sweep has durable retries + a single-flight
workflow id, and each sweep is its own run in the Temporal UI.

Coalescing tick model (mirrors the cron ticks): a stuck sweep can't
overlap the next — the activity timeout is bounded under the interval.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.uptime import (
        UptimeProbeSummary,
        probe_uptime_tick,
    )


_TICK_TIMEOUT = timedelta(seconds=100)
"""Under the 120 s interval so a slow sweep can't overlap the next tick.
Sequential probing suffices at current app counts; parallelize the sweep
inside the activity before this bound is at risk."""


@workflow.defn(name="UptimeProbeTickWorkflow")
class UptimeProbeTickWorkflow:
    @workflow.run
    async def run(self) -> UptimeProbeSummary:
        return await workflow.execute_activity(
            probe_uptime_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
