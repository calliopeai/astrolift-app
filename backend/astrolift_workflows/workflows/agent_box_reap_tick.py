"""AgentBoxReapTickWorkflow — one tick of the agent-box reaper (#128).

Runs via the platform schedule registry (~every 60 s). Thin Temporal entry
point around :func:`astrolift_workflows.activities.agent_box_reap.
reap_agent_boxes_tick` so the sweep has durable retries and a single-flight
workflow id, and each sweep is its own run in the Temporal UI.

Coalescing tick model (mirrors the run-status + alert-eval ticks): the
activity timeout is bounded under the interval so a stuck sweep cannot
overlap the next.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.agent_box_reap import (
        AgentBoxReapSummary,
        reap_agent_boxes_tick,
    )


_TICK_TIMEOUT = timedelta(seconds=50)
"""Under the 60 s interval so a slow sweep can't overlap the next tick."""


@workflow.defn(name="AgentBoxReapTickWorkflow")
class AgentBoxReapTickWorkflow:
    @workflow.run
    async def run(self) -> AgentBoxReapSummary:
        return await workflow.execute_activity(
            reap_agent_boxes_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
