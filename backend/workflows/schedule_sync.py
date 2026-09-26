"""Temporal schedule management for tier-2 ``Workflow`` cron triggers (spec 40 §3).

When a configured ``Workflow`` is saved with ``trigger_kind=schedule`` and a
``schedule_cron``, exactly one Temporal Schedule is created/updated for it
(id ``workflow-<guid>``). The schedule is paused/deleted when the Workflow is
disabled or deleted.

Mirrors ``astrolift_pipelines/schedule_sync.py`` (the per-trigger pattern) —
NOT ``schedule_boot`` (the platform-sweep allowlist registrar). The action
starts ``ConfiguredWorkflowScheduleWorkflow`` with the Workflow's guid and
organization only, and that wrapper builds a fresh run per fire (#2053). What
stood here built the ``WorkflowDefinitionRunInput`` once, at save time, and
baked it into the action: it named one ``WorkflowRun``, every fire reused it,
and once the first fire closed it every later fire failed at its first stage
with "Cannot open a stage on a closed workflow".

All Temporal calls are best-effort: a sync failure is logged and swallowed so
it never blocks the Workflow save or delete (the row is the source of truth;
a missed schedule is recoverable by re-saving).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def schedule_id_for(workflow) -> str:
    """Stable per-Workflow Temporal schedule id (spec 40 §3)."""
    return f"workflow-{workflow.guid}"


def schedule_inactive_reason(workflow) -> str | None:
    """Why ``workflow`` should have no schedule, or None when it should.

    The save-time sync and the fire (``create_scheduled_workflow_run``) both
    ask this, so a fire never runs a Workflow its own save would have
    unscheduled.
    """
    if workflow.deleted_at is not None:
        return "workflow was deleted"
    if not workflow.is_enabled:
        return "workflow is disabled"
    if workflow.trigger_kind != "schedule" or not workflow.schedule_cron:
        return "workflow is no longer schedule-triggered"
    return None


def sync_workflow_schedule(workflow) -> None:
    """Create or update the Temporal Schedule for a scheduled ``Workflow``.

    No-ops (and removes any existing schedule) when the Workflow isn't a
    live, enabled ``schedule``-trigger with a cron — so toggling
    ``is_enabled`` off or switching trigger kind tears the schedule down.
    """
    schedule_id = schedule_id_for(workflow)

    if schedule_inactive_reason(workflow) is not None:
        delete_workflow_schedule(workflow)
        return

    from astrolift_workflows.client import _temporal_enabled

    if not _temporal_enabled():
        logger.info(
            "workflows.schedule_sync: Temporal disabled — skipping schedule %s",
            schedule_id,
        )
        return

    try:
        write_workflow_schedule(workflow)
    except Exception:  # noqa: BLE001 — never block the save
        logger.exception("workflows.schedule_sync: failed to sync schedule %s", schedule_id)


def delete_workflow_schedule(workflow) -> None:
    """Delete the Temporal Schedule for a ``Workflow`` (disable / soft-delete)."""
    schedule_id = schedule_id_for(workflow)

    from astrolift_workflows.client import _temporal_enabled

    if not _temporal_enabled():
        return

    from asgiref.sync import async_to_sync

    from astrolift_workflows.client import _get_client_async

    @async_to_sync
    async def _delete():
        client = await _get_client_async()
        await client.get_schedule_handle(schedule_id).delete()

    try:
        _delete()
        logger.info("workflows.schedule_sync: schedule %s deleted", schedule_id)
    except Exception:  # noqa: BLE001 — schedule may not exist or Temporal down
        logger.debug(
            "workflows.schedule_sync: could not delete schedule %s (may not exist)",
            schedule_id,
        )


def write_workflow_schedule(workflow) -> None:
    """(Re)create the Workflow's schedule. Raises when Temporal refuses.

    ``sync_workflow_schedule`` swallows that so a save never blocks;
    ``manage.py resync_workflow_schedules`` reports it.
    """
    from asgiref.sync import async_to_sync
    from django.conf import settings

    from astrolift_workflows.client import _get_client_async

    schedule_id = schedule_id_for(workflow)
    action_input = {
        "workflow_guid": str(workflow.guid),
        "organization_id": workflow.organization_id,
    }
    task_queue = getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")
    cron = workflow.schedule_cron

    @async_to_sync
    async def _apply():
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleSpec,
            ScheduleState,
        )

        client = await _get_client_async()
        action = ScheduleActionStartWorkflow(
            "ConfiguredWorkflowScheduleWorkflow",
            action_input,
            # Temporal appends each fire's nominal time to this id.
            id=f"{schedule_id}-run",
            task_queue=task_queue,
        )
        spec = ScheduleSpec(cron_expressions=[cron])
        schedule = Schedule(action=action, spec=spec, state=ScheduleState(paused=False))
        # Update via delete+recreate so a changed cron takes effect, and so a
        # schedule written before #2053 loses its baked action.
        try:
            await client.get_schedule_handle(schedule_id).delete()
        except Exception:  # noqa: BLE001 — first sync: no schedule yet
            pass
        await client.create_schedule(schedule_id, schedule)

    _apply()
    logger.info(
        "workflows.schedule_sync: schedule %s created/updated (cron=%r)",
        schedule_id,
        cron,
    )
