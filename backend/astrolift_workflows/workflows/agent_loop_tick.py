"""
AgentLoopTickWorkflow — one minute-grained tick of the Loop dispatcher
for Task-family agents in ``run_mode='loop'`` (spec 33, PR-6).

The continuous-re-dispatch sibling of
:mod:`astrolift_workflows.workflows.agent_cron_tick` (cron Task dispatch),
:mod:`astrolift_workflows.workflows.agent_scale_tick` (Service replica
patch), and :mod:`astrolift_workflows.workflows.cron_deploy_tick` (app
deploys). Runs via the platform schedule registry every 60 s. The actual
selection + concurrency-capped dispatch lives in
:func:`astrolift_workflows.activities.cron_deploy.dispatch_agent_loops`;
this workflow is the thin Temporal entry point so the tick has durable
retries + a single-flight workflow id, and so Loop dispatches show up as
their own run history in the Temporal UI distinct from cron dispatches,
scales, and deploys.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cron_deploy import (
        LoopDispatchSummary,
        dispatch_agent_loops,
    )


_TICK_TIMEOUT = timedelta(seconds=55)
"""Bound the tick to under a minute so a stuck tick can't overlap the
next one. Coalescing is the model — refill to the cap on the next minute
(mirrors the deploy + agent-cron + scale ticks). The per-agent row lock in
the activity is what makes a (rare) overlap cap-safe regardless."""


@workflow.defn(name="AgentLoopTickWorkflow")
class AgentLoopTickWorkflow:
    @workflow.run
    async def run(self) -> LoopDispatchSummary:
        return await workflow.execute_activity(
            dispatch_agent_loops,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
