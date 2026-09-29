"""Create the run a configured Workflow's schedule fire needs (#2053).

A schedule's action is written once, when the Workflow is saved, and the run
a fire needs does not exist until the fire happens. Baking one run into the
action made every fire reuse it. This is the step between, the configured
Workflow sibling of `pipeline_schedule_fire` (#1614).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from temporalio import activity

from astrolift_workflows.inputs import WorkflowDefinitionRunInput

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduledWorkflowFire:
    """Why a fire was skipped, or the fresh run it should execute."""

    skipped: str = ""
    workflow_id: str = ""
    run_input: WorkflowDefinitionRunInput | None = None


def _create_sync(action: dict[str, Any]) -> ScheduledWorkflowFire:
    from django.db import transaction

    from astrolift_workflows.inputs import Actor
    from core.run_trigger import RunTrigger
    from workflows.models import Workflow, WorkflowInstance
    from workflows.run_service import build_workflow_definition_run_input
    from workflows.schedule_sync import schedule_inactive_reason

    workflow_guid = str(action.get("workflow_guid") or "")
    organization_id = action.get("organization_id")
    wf = (
        Workflow.objects.filter(guid=workflow_guid, organization_id=organization_id)
        .select_related("definition")
        .first()
        if workflow_guid and organization_id is not None
        else None
    )
    reason = "workflow no longer exists" if wf is None else schedule_inactive_reason(wf)
    if reason:
        # Temporal keeps firing until something deletes the schedule.
        log.info("configured workflow schedule: %s (%s); skipping fire", reason, workflow_guid)
        return ScheduledWorkflowFire(skipped=reason)

    with transaction.atomic():
        run, run_input, workflow_id = build_workflow_definition_run_input(
            wf.definition,
            trigger_payload=dict(wf.inputs or {}),
            organization_id=wf.organization_id,
            actor=Actor(kind="system", user_id=None, display="scheduled"),
            stage_bindings=wf.stage_bindings,
            trigger_kind=RunTrigger.SCHEDULE,
        )
        # `runCount` and the Workflow's run history count these records; the
        # manual `runWorkflow` path writes one per run too.
        WorkflowInstance.start(configured_workflow=wf, temporal_workflow_id=workflow_id)
    log.info("configured workflow schedule: created run %s for workflow %s", run.pk, workflow_guid)
    return ScheduledWorkflowFire(workflow_id=workflow_id, run_input=run_input)


def _record_start_sync(workflow_run_id: str, temporal_run_id: str) -> None:
    """Stamp the child's Temporal run id on its run and configured record.

    The reconciler skips a run without one and the stage reader keys on the
    id pair. The manual path stamps both when `start_workflow` returns; a
    fire learns the id only once its child has started.
    """
    from django.db import transaction

    from astrolift_operations.models import WorkflowRun
    from workflows.run_status import synchronize_workflow_instances

    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().get(pk=int(workflow_run_id))
        if run.run_id and run.run_id != temporal_run_id:
            raise RuntimeError("scheduled workflow run already has a different Temporal run id")
        if run.run_id != temporal_run_id:
            run.run_id = temporal_run_id
            run.save(update_fields=["run_id", "updated_at", "version"])
        # Authoritative: the record takes the run's state as it stands, which
        # may already be closed if the child finished before this ran.
        synchronize_workflow_instances(run, authoritative=True)


@activity.defn(name="astrolift.configured_workflow.create_scheduled_run")
async def create_scheduled_workflow_run(action: dict[str, Any]) -> ScheduledWorkflowFire:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_create_sync)(action)


@activity.defn(name="astrolift.configured_workflow.record_scheduled_start")
async def record_scheduled_workflow_start(workflow_run_id: str, temporal_run_id: str) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_record_start_sync)(workflow_run_id, temporal_run_id)
