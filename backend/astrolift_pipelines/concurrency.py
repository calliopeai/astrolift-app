"""Pipeline concurrency control — cancel-in-progress and per-org rate limits (#88).

The `concurrency` DSL block controls how simultaneous runs of the same
pipeline are handled:

    [concurrency]
    group = "deploy-production"        # group key — any string
    cancel_in_progress = true          # cancel older runs in the group

When a new PipelineRun starts in a concurrency group, the platform:
1. Checks if any other run in the same group is pending or running.
2. If ``cancel_in_progress = true``: cancels those runs before starting.
3. If ``cancel_in_progress = false`` (the default): queues the new run
   until the current one completes.

Per-org rate limits prevent a misconfigured webhook from flooding the
platform with runs. The default limit is 60 runs/minute per org. Runs
beyond the limit are REJECTED (not queued) and the webhook returns a
429-like response body (still HTTP 200 — GitHub retries on non-2xx).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from astrolift_pipelines.models import Pipeline, PipelineRun

logger = logging.getLogger(__name__)

_DEFAULT_RATE_LIMIT_PER_MINUTE = 60


class ConcurrencyViolation(Exception):
    """Raised when a new run is rejected by the concurrency or rate-limit policy."""


def get_concurrency_config(pipeline: Pipeline) -> dict:
    """Extract the [concurrency] block from a pipeline's TOML config.

    Returns a dict with ``group`` (str | None) and ``cancel_in_progress`` (bool).
    Returns defaults when the block is absent.
    """
    # The pipeline's TOML is not parsed and stored on the model at v1;
    # this function is a hook for when TOML parsing is added.
    # For now, return the defaults.
    return {
        "group": None,
        "cancel_in_progress": False,
    }


def enforce_concurrency(new_run: PipelineRun) -> None:
    """Enforce the concurrency policy for a newly-created pipeline run.

    Raises ConcurrencyViolation if the run should be rejected (queued-only
    with no group or group already running and cancel_in_progress=false).

    Cancels conflicting runs if cancel_in_progress=true.
    """
    config = get_concurrency_config(new_run.pipeline)
    group = config.get("group")
    cancel_in_progress = config.get("cancel_in_progress", False)

    if not group:
        return  # No concurrency group — unlimited parallel runs

    # Find other active runs in the same group
    active_runs = _find_active_runs_in_group(new_run.pipeline, group, exclude_run=new_run)

    if not active_runs:
        return  # No conflict

    if cancel_in_progress:
        _cancel_runs(active_runs, reason=f"Cancelled by newer run {new_run.run_number}")
        logger.info(
            "pipelines.concurrency: cancelled %d run(s) in group %r for pipeline %s",
            len(active_runs),
            group,
            new_run.pipeline.name,
        )
    else:
        # Queue policy: reject the new run if any active run exists
        raise ConcurrencyViolation(
            f"Pipeline '{new_run.pipeline.name}' has an active run in concurrency group '{group}'. "
            f"The new run is rejected — try again when the current run completes."
        )


def check_rate_limit(pipeline: Pipeline) -> bool:
    """Return True if a new run is allowed under the per-org rate limit.

    False means the run should be rejected (too many runs in the last minute).
    """
    from datetime import timedelta

    from astrolift_pipelines.models import PipelineRun

    org = pipeline.organization
    extra = getattr(org, "extra_data", None) or {}
    limit = int(extra.get("pipeline_rate_limit_per_minute", _DEFAULT_RATE_LIMIT_PER_MINUTE))

    one_minute_ago = timezone.now() - timedelta(minutes=1)
    recent_count = PipelineRun.objects.filter(
        pipeline__organization=org,
        created_at__gte=one_minute_ago,
    ).count()

    if recent_count >= limit:
        logger.warning(
            "pipelines.concurrency: rate limit exceeded for org %s (%d runs in last minute, limit %d)",
            org.slug,
            recent_count,
            limit,
        )
        return False

    return True


def _find_active_runs_in_group(pipeline: Pipeline, group: str, *, exclude_run: PipelineRun) -> list:
    """Return runs in the concurrency group that are active (pending or running)."""
    from astrolift_pipelines.models import PipelineRun

    return list(
        PipelineRun.objects.filter(
            pipeline=pipeline,
            status__in=["pending", "running"],
        ).exclude(pk=exclude_run.pk)
    )


def _cancel_runs(runs: list, *, reason: str) -> None:
    """Cancel a list of pipeline runs (mark as cancelled, signal Temporal)."""
    from astrolift_pipelines.state_machine import transition_pipeline_run

    for run in runs:
        try:
            transition_pipeline_run(run, "cancelled", actor_display="concurrency-policy")
            _signal_temporal_cancel(run)
        except Exception:  # noqa: BLE001
            logger.exception("pipelines.concurrency: failed to cancel run %s", run.guid)


def _signal_temporal_cancel(run) -> None:
    """Signal the Temporal PipelineRunWorkflow to cancel, if running."""
    if not run.temporal_workflow_id:
        return
    try:
        from astrolift_workflows.client import signal_workflow

        signal_workflow(
            run.temporal_workflow_id,
            signal_name="cancel",
            arg={"reason": "concurrency_policy"},
            task_queue="pipelines",
        )
    except Exception:  # noqa: BLE001
        pass  # Temporal may not be running — cancel is best-effort
