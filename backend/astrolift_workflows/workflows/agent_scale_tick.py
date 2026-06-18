"""
AgentScaleTickWorkflow — one minute-grained tick of the scheduled-scaling
loop for Service-family agents (spec 33, PR-5).

The replica-patch sibling of
:mod:`astrolift_workflows.workflows.agent_cron_tick` (which dispatches
Tasks) and :mod:`astrolift_workflows.workflows.cron_deploy_tick` (which
fires app deploys). Runs via the platform schedule registry every 60 s.
The actual selection + replica patch lives in
:func:`astrolift_workflows.activities.cron_deploy.dispatch_scale_ticks`;
this workflow is the thin Temporal entry point so the tick has durable
retries + a single-flight workflow id, and so scheduled scales show up as
their own run history in the Temporal UI distinct from deploys + dispatches.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cron_deploy import (
        ScaleTickSummary,
        dispatch_scale_ticks,
    )


_TICK_TIMEOUT = timedelta(seconds=55)
"""Bound the tick to under a minute so a stuck tick can't overlap the
next one. Coalescing is the model — pick up missed rows on the next
minute (mirrors the deploy + agent-cron ticks)."""


@workflow.defn(name="AgentScaleTickWorkflow")
class AgentScaleTickWorkflow:
    @workflow.run
    async def run(self) -> ScaleTickSummary:
        return await workflow.execute_activity(
            dispatch_scale_ticks,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
