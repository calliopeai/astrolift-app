"""
CronDeployTickWorkflow — one minute-grained tick of the cron-deploy
dispatcher (#296).

Runs via the platform schedule registry every 60 s. The actual
matching + deploy enqueueing lives in
:func:`astrolift_workflows.activities.cron_deploy.dispatch_cron_deploys`;
this workflow is the thin Temporal entry point so the tick has
durable retries + a single-flight workflow id.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cron_deploy import (
        CronDispatchSummary,
        dispatch_cron_deploys,
    )


_TICK_TIMEOUT = timedelta(seconds=55)
"""Bound the tick to under a minute so a stuck tick can't overlap
the next one. Coalescing is the dispatch model — pick up missed
rows on the next minute."""


@workflow.defn(name="CronDeployTickWorkflow")
class CronDeployTickWorkflow:
    @workflow.run
    async def run(self) -> CronDispatchSummary:
        return await workflow.execute_activity(
            dispatch_cron_deploys,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
