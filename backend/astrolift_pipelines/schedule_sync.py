"""Temporal schedule management for pipeline cron triggers (#74).

When a ``Trigger`` record with ``kind="schedule"`` is created, updated,
or deleted, these functions synchronise the corresponding Temporal
schedule so pipelines fire at the configured cron interval.

Schedule ID convention:
    ``pipeline-schedule-{trigger_guid}``

This makes schedule management idempotent: re-creating a trigger with
the same GUID resolves to the same schedule. Temporal de-dupes by ID.

The functions in this module are called from Django signals (wired in
``apps.py``) so the database transaction is committed before the Temporal
call. All Temporal errors are logged and swallowed — a schedule sync
failure must never block the trigger save or delete.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import Pipeline, Trigger

logger = logging.getLogger(__name__)


def _temporal_schedule_id(trigger) -> str:
    return f"pipeline-schedule-{trigger.guid}"


def _cron_from_trigger(trigger) -> str | None:
    """Extract the cron expression from trigger.config.

    Expected: ``{"cron": "*/5 * * * *", "branch": "main"}``
    """
    config = trigger.config or {}
    return config.get("cron")


def create_or_update_schedule(trigger) -> None:
    """Create or update a Temporal schedule for a cron trigger.

    No-ops when Temporal is disabled or cron expression is missing.
    """
    cron = _cron_from_trigger(trigger)
    if not cron:
        logger.warning(
            "pipelines.schedule_sync: trigger %s has no cron expression — skipping", trigger.guid
        )
        return

    try:
        from astrolift_workflows.client import get_temporal_client
        client = get_temporal_client()
    except Exception:  # noqa: BLE001 — Temporal may not be running
        logger.info("pipelines.schedule_sync: Temporal not available, skipping schedule sync")
        return

    schedule_id = _temporal_schedule_id(trigger)
    branch = (trigger.config or {}).get("branch", trigger.pipeline.default_branch)

    # We pass the pipeline_run parameters as schedule action args.
    # The PipelineScheduleWorkflow is a thin wrapper that creates a
    # PipelineRun and dispatches PipelineRunWorkflow.
    action_input = {
        "pipeline_id": str(trigger.pipeline.guid),
        "trigger_id": str(trigger.guid),
        "branch": branch,
        "trigger_kind": "schedule",
    }

    try:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleSpec,
            ScheduleIntervalSpec,
        )
        # Try to delete existing schedule (update via delete+recreate)
        try:
            handle = client.get_schedule_handle(schedule_id)
            handle.delete()
        except Exception:  # noqa: BLE001 — schedule may not exist
            pass

        client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    workflow="PipelineScheduleWorkflow",
                    arg=action_input,
                    id=f"{schedule_id}-run",
                    task_queue="pipelines",
                ),
                spec=ScheduleSpec(
                    cron_expressions=[cron],
                ),
            ),
        )
        logger.info(
            "pipelines.schedule_sync: schedule %s created/updated (cron=%s)", schedule_id, cron
        )
    except ImportError:
        # Temporal SDK not fully installed — fallback to a simpler note
        logger.info("pipelines.schedule_sync: Temporal SDK not available, schedule %s not synced", schedule_id)
    except Exception:  # noqa: BLE001
        logger.exception("pipelines.schedule_sync: failed to sync schedule %s", schedule_id)


def delete_schedule(trigger) -> None:
    """Delete the Temporal schedule for a cron trigger.

    Called on trigger soft-delete or pipeline delete.
    """
    schedule_id = _temporal_schedule_id(trigger)
    try:
        from astrolift_workflows.client import get_temporal_client
        client = get_temporal_client()
        handle = client.get_schedule_handle(schedule_id)
        handle.delete()
        logger.info("pipelines.schedule_sync: schedule %s deleted", schedule_id)
    except Exception:  # noqa: BLE001 — schedule may not exist or Temporal may be down
        logger.debug("pipelines.schedule_sync: could not delete schedule %s (may not exist)", schedule_id)


def sync_pipeline_schedules(pipeline) -> None:
    """Re-sync all schedule triggers for a pipeline.

    Called when a pipeline is enabled/disabled or its default_branch changes.
    """
    from astrolift_pipelines.models import Trigger
    triggers = Trigger.objects.filter(
        pipeline=pipeline,
        kind="schedule",
        deleted_at__isnull=True,
    )
    for trigger in triggers:
        if pipeline.deleted_at is None:
            create_or_update_schedule(trigger)
        else:
            delete_schedule(trigger)
