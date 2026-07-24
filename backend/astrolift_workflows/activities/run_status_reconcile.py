"""Run-status reconcile tick activity.

Thin Temporal activity wrapping the synchronous run reconciler so the
sweep gets durable retries + shows up as its own run history. The ORM +
cluster reads are blocking, so it runs in a worker thread
(``thread_sensitive=False``). Mirrors ``activities/alert_eval.py``.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class RunStatusReconcileSummary:
    evaluated: int
    succeeded: int
    failed: int
    running: int
    unchanged: int
    skipped: int
    errors: int


@activity.defn(name="astrolift.runs.reconcile_status_tick")
async def reconcile_runs_tick() -> RunStatusReconcileSummary:
    """One sweep: reconcile every non-terminal ScheduledJobRun + TaskRun
    against its k8s Job status, writing back status/timings/exit_code."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_reconcile_runs_tick_sync, thread_sensitive=False)()


def _reconcile_runs_tick_sync() -> RunStatusReconcileSummary:
    from astrolift_lifecycle.services.run_reconciler import reconcile_runs

    summary = reconcile_runs()
    return RunStatusReconcileSummary(
        evaluated=summary["evaluated"],
        succeeded=summary["succeeded"],
        failed=summary["failed"],
        running=summary["running"],
        unchanged=summary["unchanged"],
        skipped=summary["skipped"],
        errors=summary["errors"],
    )
