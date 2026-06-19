"""
AgentReconcileTickWorkflow — one tick of the keep-alive agent self-heal
reconcile (#808).

The self-heal sibling of
:mod:`astrolift_workflows.workflows.agent_cron_tick` (which dispatches
Tasks), :mod:`astrolift_workflows.workflows.agent_scale_tick` (which patches
Service replicas), and :mod:`astrolift_workflows.workflows.cron_deploy_tick`
(which fires app deploys). Runs via the platform schedule registry every
5 min. The actual selection + re-apply lives in
:func:`astrolift_workflows.activities.cron_deploy.reconcile_agent_deployments`;
this workflow is the thin Temporal entry point so the tick has durable
retries + a single-flight workflow id, and so agent reconciles show up as
their own run history in the Temporal UI distinct from deploys, dispatches,
and scales.

Why it exists: the keep-alive agent Deployment is only ever (re)applied by
the manual ``deployClusterAgent`` mutation, so a changed ``AGENT_IMAGE`` left
existing clusters pinned to the old image (ImagePullBackOff) until an operator
re-applied by hand. Re-applying the manifests on a cadence (idempotent
server-side apply) converges the live Deployment to the rendered spec — the
fleet self-corrects without a manual call.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cron_deploy import (
        AgentReconcileSummary,
        reconcile_agent_deployments,
    )


_TICK_TIMEOUT = timedelta(minutes=4)
"""Bound the tick to under its 5-min interval so a stuck tick can't overlap
the next one. Re-applying to every cluster is heavier than the dispatch ticks
(one driver apply per cluster), so the budget is wider than the 55s the
minute-grained ticks use — but still inside the interval. Coalescing is the
model: a cluster missed this tick gets re-applied on the next."""


@workflow.defn(name="AgentReconcileTickWorkflow")
class AgentReconcileTickWorkflow:
    @workflow.run
    async def run(self) -> AgentReconcileSummary:
        return await workflow.execute_activity(
            reconcile_agent_deployments,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
