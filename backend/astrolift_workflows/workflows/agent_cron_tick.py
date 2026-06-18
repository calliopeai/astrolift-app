"""
AgentCronTickWorkflow — one minute-grained tick of the agent-cron
dispatcher (spec 33, PR-4).

The Task-dispatch sibling of :mod:`astrolift_workflows.workflows.cron_deploy_tick`.
Runs via the platform schedule registry every 60 s. The actual
matching + Task dispatch lives in
:func:`astrolift_workflows.activities.cron_deploy.dispatch_agent_crons`;
this workflow is the thin Temporal entry point so the tick has durable
retries + a single-flight workflow id, and so agent dispatches show up
as their own run history in the Temporal UI distinct from app deploys.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cron_deploy import (
        AgentCronDispatchSummary,
        dispatch_agent_crons,
    )


_TICK_TIMEOUT = timedelta(seconds=55)
"""Bound the tick to under a minute so a stuck tick can't overlap the
next one. Coalescing is the dispatch model — pick up missed rows on the
next minute (mirrors the app-deploy tick)."""


@workflow.defn(name="AgentCronTickWorkflow")
class AgentCronTickWorkflow:
    @workflow.run
    async def run(self) -> AgentCronDispatchSummary:
        return await workflow.execute_activity(
            dispatch_agent_crons,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
