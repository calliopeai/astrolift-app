"""Pipeline job cancellation and timeout enforcement (#93).

Cancellation paths:
1. **User-initiated**: operator calls the ``cancelPipelineRun`` mutation.
   The mutation signals the Temporal workflow with ``cancel``, which
   propagates to all running job runs.
2. **Concurrency policy**: concurrency.py cancels older runs when
   cancel_in_progress=true fires.
3. **Timeout enforcement**: each job run has a ``timeout_seconds`` from
   the TOML. The Temporal activity runs inside a schedule_to_close_timeout.
   For K8s jobs, the pod's ``activeDeadlineSeconds`` also enforces the limit.

Cleanup steps after cancellation:
- Mark all pending/running steps in the job as CANCELLED
- Delete the pipeline namespace if all jobs are terminal
- Emit an audit event

DSL fields (from spec #65):
    [concurrency]
    group = "deploy"
    cancel_in_progress = true

    [[jobs.timeout]]
    timeout_minutes = 30   # per-job timeout; maps to K8s activeDeadlineSeconds

Per-step timeout is not in v1.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import JobRun, PipelineRun

logger = logging.getLogger(__name__)

_DEFAULT_JOB_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def get_job_timeout_seconds(job_run: JobRun) -> int:
    """Return the timeout in seconds for a job run.

    Reads from Job.config (from the pipeline TOML). Falls back to the default.
    """
    if hasattr(job_run, "job") and job_run.job_id:
        # Extract from the raw TOML config if available
        timeout_minutes = job_run.job.with_params.get("timeout_minutes") if job_run.job.with_params else None
        if timeout_minutes is not None:
            return int(timeout_minutes) * 60
    return _DEFAULT_JOB_TIMEOUT_SECONDS


def cancel_pipeline_run(run: PipelineRun, *, actor_display: str = "operator") -> None:
    """Cancel a pipeline run and all its job runs.

    1. Signals Temporal to stop the PipelineRunWorkflow.
    2. Marks all pending/running job runs as CANCELLED.
    3. Marks all pending/running step runs in those jobs as CANCELLED.
    4. Transitions the PipelineRun itself to CANCELLED.
    """
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun, StepRun
    from astrolift_pipelines.state_machine import (
        InvalidTransition,
        transition_job_run,
        transition_pipeline_run,
    )

    # Signal Temporal first (best-effort)
    _signal_temporal_cancel(run)

    now = timezone.now()

    # Cancel all active job runs
    active_job_runs = JobRun.objects.filter(
        pipeline_run=run,
        status__in=["pending", "running"],
    )
    for job_run in active_job_runs:
        # Cancel steps within the job. No live path did this: the Temporal
        # workflow's cancel handler cascades to JobRun and stops there, and
        # `_settle_step_runs` only runs when a pod actually completed. So a
        # cancelled run's steps read `running` forever, in every deployment.
        #
        # A bulk update rather than per-row saves because a job can have
        # many steps and none of them needs an audit event of its own; the
        # timestamps are set explicitly since `.update()` does not fire
        # `auto_now`.
        StepRun.objects.filter(
            job_run=job_run,
            status__in=["pending", "running"],
        ).update(status="cancelled", finished_at=now, updated_at=now)

        try:
            transition_job_run(job_run, "cancelled", actor_display=actor_display)
        except InvalidTransition:
            pass  # Already in a terminal state — skip

    # Transition the pipeline run
    try:
        transition_pipeline_run(run, "cancelled", actor_display=actor_display)
    except InvalidTransition:
        logger.info("pipelines.cancellation: run %s already in terminal state", run.guid)

    # Metric and commit status live here, at the site that performs the
    # transition, rather than at the caller. The mutation used to own them,
    # which meant a second cancel entry point would have to remember both.
    # Both swallow: the cancel is already committed.
    from astrolift_pipelines.commit_status import post_commit_status_for_run
    from astrolift_pipelines.metrics import record_run_completed

    try:
        record_run_completed(run)
    except Exception:  # noqa: BLE001
        logger.warning("pipeline metrics: pipeline_run=%s not recorded", run.pk, exc_info=True)
    post_commit_status_for_run(run)

    logger.info("pipelines.cancellation: PipelineRun %s cancelled by %s", run.guid, actor_display)


def _signal_temporal_cancel(run: PipelineRun) -> None:
    """Signal the Temporal PipelineRunWorkflow to cancel.

    The signature here was wrong for as long as this module had no caller:
    it passed ``arg=`` and ``task_queue=``, and the real signature is
    ``signal_workflow(workflow_id, signal_name, *args)``. That is a
    TypeError, and the bare ``except Exception: pass`` below meant it would
    have raised on every call and reported nothing -- the cancel signal
    would silently never reach Temporal. A test suite cannot catch that
    while nothing calls the function.

    Still best-effort: Temporal may be disabled or the workflow already
    gone, and the database cascade below is what the operator sees. But it
    logs now, because "swallowed silently" is how the above survived.
    """
    if not run.temporal_workflow_id:
        return
    try:
        from astrolift_workflows.client import signal_workflow

        signal_workflow(run.temporal_workflow_id, "cancel")
    except Exception:  # noqa: BLE001
        logger.warning(
            "pipelines.cancellation: cancel signal failed for run %s",
            run.guid,
            exc_info=True,
        )


def build_k8s_job_with_timeout(job_manifest: dict, timeout_seconds: int) -> dict:
    """Add activeDeadlineSeconds to a batch/v1 Job manifest for timeout enforcement.

    K8s will terminate the pod and mark the Job as Failed when the deadline
    is exceeded. This is the primary timeout mechanism for K8s-backed jobs.
    """
    spec = job_manifest.get("spec", {})
    spec["activeDeadlineSeconds"] = timeout_seconds
    job_manifest["spec"] = spec
    return job_manifest


def check_timed_out_runs() -> int:
    """Mark running job runs as TIMED_OUT if they exceeded their timeout.

    Called periodically by a scheduled Temporal activity. Returns the
    number of runs timed out.
    """
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_pipelines.models import JobRun
    from astrolift_pipelines.state_machine import (
        InvalidTransition,
        transition_job_run,
    )

    count = 0
    running_jobs = JobRun.objects.filter(
        status="running",
        started_at__isnull=False,
    ).select_related("pipeline_run", "pipeline_run__pipeline")

    now = timezone.now()
    for job_run in running_jobs:
        timeout_seconds = get_job_timeout_seconds(job_run)
        deadline = job_run.started_at + timedelta(seconds=timeout_seconds)
        if now < deadline:
            continue

        logger.info(
            "pipelines.cancellation: job run %s exceeded timeout (%ds)", job_run.guid, timeout_seconds
        )
        try:
            transition_job_run(job_run, "failure", actor_display="timeout-checker")
            count += 1

            # If the pipeline run is still going, mark it failed
            pipeline_run = job_run.pipeline_run
            if pipeline_run.status == "running":
                cancel_pipeline_run(pipeline_run, actor_display="timeout-checker")
        except InvalidTransition:
            pass

    return count
