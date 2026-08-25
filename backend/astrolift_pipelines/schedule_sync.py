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

from asgiref.sync import async_to_sync

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _temporal_schedule_id(trigger) -> str:
    return f"pipeline-schedule-{trigger.guid}"


def _cron_from_trigger(trigger) -> str | None:
    """Extract the cron expression from trigger.config.

    Expected: ``{"cron": "*/5 * * * *", "branch": "main"}``
    """
    config = trigger.config or {}
    return config.get("cron")


def _temporal_enabled() -> bool:
    """Reuse the client's own switch rather than a second one.

    `astrolift_workflows.client._temporal_enabled` resolves the constance
    toggle and the settings kill switch in that precedence. A local copy here
    would be a second answer to the same question, free to disagree.
    """
    from astrolift_workflows.client import _temporal_enabled as enabled

    return bool(enabled())


@async_to_sync
async def _write_schedule(*, schedule_id: str, cron: str, action_input: dict) -> None:
    """Create or replace one pipeline schedule in Temporal.

    Async, bridged with ``async_to_sync`` (#1614). What stood here called
    ``client.get_schedule_handle(...).delete()`` and ``client.create_schedule(...)``
    synchronously on the Temporal SDK's async client, so both returned
    un-awaited coroutines and neither ran -- behind an import of
    ``astrolift_workflows.client.get_temporal_client``, which does not exist,
    so the whole block raised ImportError on its first line and the bare
    ``except`` logged "Temporal not available" on every schedule-trigger save.

    Delete-then-create rather than ``handle.update``: the cron expression and
    the action both change, and a delete of a schedule that is not there is
    already the no-op this needs. ``schedule_boot`` uses ``update`` because it
    reconciles a fixed catalog, where the schedule reliably exists.

    ``task_queue`` comes from settings. The literal ``"pipelines"`` that stood
    here names no queue any worker polls -- the same defect as the webhook
    dispatch in #1623.
    """
    from django.conf import settings
    from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec

    from astrolift_workflows.client import _get_client_async

    client = await _get_client_async()
    try:
        await client.get_schedule_handle(schedule_id).delete()
    except Exception:  # noqa: BLE001 - not present is the common case
        pass

    await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                "PipelineScheduleWorkflow",
                action_input,
                id=f"{schedule_id}-run",
                task_queue=getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main"),
            ),
            spec=ScheduleSpec(cron_expressions=[cron]),
        ),
    )


def create_or_update_schedule(trigger) -> None:
    """Create or update a Temporal schedule for a cron trigger.

    No-ops when Temporal is disabled or cron expression is missing.
    """
    cron = _cron_from_trigger(trigger)
    if not cron:
        logger.warning("pipelines.schedule_sync: trigger %s has no cron expression — skipping", trigger.guid)
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

    if not _temporal_enabled():
        # Same kill switch `start_workflow` honours. Without this, every
        # schedule-trigger save on a Temporal-less install pays a connection
        # timeout -- which is new as of #1614, because before the fix this
        # path died on an ImportError long before it tried to connect.
        logger.info("pipelines.schedule_sync: Temporal disabled, skipping schedule %s", schedule_id)
        return

    try:
        _write_schedule(schedule_id=schedule_id, cron=cron, action_input=action_input)
        logger.info("pipelines.schedule_sync: schedule %s created/updated (cron=%s)", schedule_id, cron)
    except ImportError:
        # Temporal SDK not fully installed — fallback to a simpler note
        logger.info(
            "pipelines.schedule_sync: Temporal SDK not available, schedule %s not synced", schedule_id
        )
    except Exception:  # noqa: BLE001
        logger.exception("pipelines.schedule_sync: failed to sync schedule %s", schedule_id)


@async_to_sync
async def _drop_schedule(*, schedule_id: str) -> None:
    """Delete one schedule. Same fault as the writer above (#1614): this
    imported a factory that does not exist and then called an async delete
    synchronously, so a soft-deleted trigger kept firing."""
    from astrolift_workflows.client import _get_client_async

    client = await _get_client_async()
    await client.get_schedule_handle(schedule_id).delete()


def delete_schedule(trigger) -> None:
    """Delete the Temporal schedule for a cron trigger.

    Called on trigger soft-delete or pipeline delete.
    """
    schedule_id = _temporal_schedule_id(trigger)
    if not _temporal_enabled():
        return
    try:
        _drop_schedule(schedule_id=schedule_id)
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
