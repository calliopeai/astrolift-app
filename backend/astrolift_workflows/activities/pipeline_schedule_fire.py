"""Create the PipelineRun a schedule fire needs (#1614).

`PipelineRunWorkflow` takes an integer primary key, and a Temporal schedule
cannot supply one because the row does not exist until the fire happens.
This is the step between.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger(__name__)


def _create_sync(action: dict[str, Any]) -> dict[str, Any]:
    from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger

    trigger_guid = str(action.get("trigger_id") or "")
    pipeline_guid = str(action.get("pipeline_id") or "")

    trigger = (
        Trigger.objects.filter(guid=trigger_guid, deleted_at__isnull=True).select_related("pipeline").first()
        if trigger_guid
        else None
    )
    if trigger is None:
        # The schedule outlived its trigger. Temporal keeps firing until
        # something deletes the schedule, so this is the expected shape of
        # that race rather than an error.
        log.info("pipeline schedule: trigger %s no longer exists; skipping fire", trigger_guid)
        return {"skipped": "trigger no longer exists"}
    if not getattr(trigger, "is_active", True):
        return {"skipped": "trigger is not active"}

    pipeline = trigger.pipeline or Pipeline.objects.filter(guid=pipeline_guid).first()
    if pipeline is None:
        return {"skipped": "pipeline no longer exists"}

    branch = str(action.get("branch") or "") or pipeline.default_branch
    run = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=_next_run_number(pipeline),
        status="pending",
        trigger_kind=str(action.get("trigger_kind") or "schedule"),
        trigger_ref=f"refs/heads/{branch}" if branch and not branch.startswith("refs/") else branch,
    )
    log.info(
        "pipeline schedule: created run %s (#%s) for pipeline %s",
        run.guid,
        run.run_number,
        pipeline.guid,
    )
    return {"pipeline_run_id": run.pk, "run_number": run.run_number}


def _next_run_number(pipeline) -> int:
    """Monotonic per pipeline, matching the webhook path's own helper.

    Not imported from `webhook_views`: that module is a view layer and
    importing it into an activity drags Strawberry and DRF into the worker.
    """
    from django.db.models import Max

    from astrolift_pipelines.models import PipelineRun

    current = PipelineRun.objects.filter(pipeline=pipeline).aggregate(Max("run_number"))
    return int(current["run_number__max"] or 0) + 1


@activity.defn(name="astrolift.pipeline.create_scheduled_run")
async def create_scheduled_pipeline_run(action: dict[str, Any]) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_create_sync)(action)
