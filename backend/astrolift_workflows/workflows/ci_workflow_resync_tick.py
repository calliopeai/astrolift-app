"""CiWorkflowResyncTickWorkflow — one tick of the managed-CI-workflow resync
sweep (#1211, Phase 3).

Runs via the platform schedule registry when the operator opts the sweep in
(its ``ScheduleKind.CI_WORKFLOW_RESYNC`` is deliberately HELD out of
``PHASE_3A_ACTIVE_KINDS`` — it ships inert). Thin Temporal entry point around
:func:`astrolift_workflows.activities.ci_workflow_resync.resync_ci_workflows_tick`
so the sweep has durable retries + a single-flight workflow id, and each sweep
is its own run in the Temporal UI.

Coalescing tick model (mirrors the uptime / run-status ticks): a stuck sweep
can't overlap the next — the activity timeout is bounded under the interval.
The per-app reconcile fans out INSIDE the activity (sequential, per-app
try/except, one app's failure never aborts the sweep), matching the sibling
sweep workflows which likewise iterate the fleet inside a single tick activity
rather than fanning out child workflows.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.ci_workflow_resync import (
        CiWorkflowResyncSummary,
        resync_ci_workflows_tick,
    )


_TICK_TIMEOUT = timedelta(minutes=50)
"""Under the 1-hour interval so a slow sweep can't overlap the next tick. The
sweep reads (and, for stale/absent apps, pushes) once per managed app
sequentially; a fetch/push per app dominates the budget. Coalescing is the
model — an app missed this tick is reconciled on the next. Parallelize inside
the activity before this bound is at risk."""


@workflow.defn(name="CiWorkflowResyncTickWorkflow")
class CiWorkflowResyncTickWorkflow:
    @workflow.run
    async def run(self) -> CiWorkflowResyncSummary:
        return await workflow.execute_activity(
            resync_ci_workflows_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
