"""Temporal schedule management for tier-2 ``Workflow`` cron triggers (spec 40 §3).

When a configured ``Workflow`` is saved with ``trigger_kind=schedule`` and a
``schedule_cron``, exactly one Temporal Schedule is created/updated for it
(id ``workflow-<guid>``) that starts ``WorkflowDefinitionRunWorkflow`` on the
cron. The schedule is paused/deleted when the Workflow is disabled or deleted.

Mirrors ``astrolift_pipelines/schedule_sync.py`` (the per-trigger pattern) —
NOT ``schedule_boot`` (the platform-sweep allowlist registrar). The schedule
action carries a real ``WorkflowDefinitionRunInput`` (built the same way the
inline run path builds it via ``build_workflow_definition_run_input``) so each
fire deserializes correctly, the same fix the agent-workflow scheduled path
landed (#1036).

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


def sync_workflow_schedule(workflow) -> None:
    """Create or update the Temporal Schedule for a scheduled ``Workflow``.

    No-ops (and removes any existing schedule) when the Workflow isn't a
    live, enabled ``schedule``-trigger with a cron — so toggling
    ``is_enabled`` off or switching trigger kind tears the schedule down.
    """
    schedule_id = schedule_id_for(workflow)

    active = (
        workflow.deleted_at is None
        and workflow.is_enabled
        and workflow.trigger_kind == "schedule"
        and bool(workflow.schedule_cron)
    )
    if not active:
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
        _create_or_update(workflow, schedule_id)
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


def _create_or_update(workflow, schedule_id: str) -> None:
    """Build the run input + (re)create the Temporal Schedule (sync wrapper)."""
    from asgiref.sync import async_to_sync
    from django.conf import settings

    from astrolift_workflows.client import _get_client_async
    from astrolift_workflows.inputs import Actor
    from workflows.run_service import build_workflow_definition_run_input

    _run, run_input, run_workflow_id = build_workflow_definition_run_input(
        workflow.definition,
        trigger_payload=dict(workflow.inputs or {}),
        organization_id=workflow.organization_id,
        actor=Actor(kind="system", user_id=None, display="scheduled"),
        stage_bindings=workflow.stage_bindings,
    )
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
            "WorkflowDefinitionRunWorkflow",
            run_input,
            id=run_workflow_id,
            task_queue=task_queue,
        )
        spec = ScheduleSpec(cron_expressions=[cron])
        schedule = Schedule(action=action, spec=spec, state=ScheduleState(paused=False))
        # Update via delete+recreate so a changed cron / bindings take effect.
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
