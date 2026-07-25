"""CI-workflow resync sweep tick activity (#1211, Phase 3).

Thin Temporal activity wrapping the synchronous fleet sweep so the sweep
gets durable retries + shows up as its own run history. The ORM + source-host
reads/writes are blocking, so it runs in a worker thread
(``thread_sensitive=False``). Mirrors ``activities/run_status_reconcile.py`` and
``activities/uptime.py``.

The real work — classify each managed app's CI workflow file and auto-push
only the safe (``template_stale`` / ``absent``) states — lives in
:func:`astrolift_scm.services.ci_workflow_drift.sweep_ci_workflows`, which reuses
the Phase 1 push + Phase 2 fetch/drift machinery. This module only adapts it to
Temporal. The summary dataclass is owned by the service and re-exported here so
the tick workflow imports its return type from the activity module (the sibling
tick convention).
"""

from __future__ import annotations

from temporalio import activity

# Re-export the service's sweep summary as this activity's return type so
# ``workflows/ci_workflow_resync_tick.py`` imports it from the activity module,
# matching the sibling tick workflows. The service module's top-level imports
# are stdlib + ``django.utils.timezone`` + ``ci_templates`` only (its Django
# model access is lazy inside the sweep), so importing it here is import-safe.
from astrolift_scm.services.ci_workflow_drift import CiWorkflowResyncSummary

__all__ = ["CiWorkflowResyncSummary", "resync_ci_workflows_tick"]


@activity.defn(name="astrolift.ci_workflow.resync_tick")
async def resync_ci_workflows_tick() -> CiWorkflowResyncSummary:
    """One sweep: recompute drift for every managed app and auto-push the
    ``template_stale`` / ``absent`` ones (never the drift/conflict ones)."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_resync_ci_workflows_tick_sync, thread_sensitive=False)()


def _resync_ci_workflows_tick_sync() -> CiWorkflowResyncSummary:
    from astrolift_scm.services.ci_workflow_drift import sweep_ci_workflows

    return sweep_ci_workflows()
