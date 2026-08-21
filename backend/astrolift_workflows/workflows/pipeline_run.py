"""
PipelineRunWorkflow — DAG-ordered execution of pipeline jobs (#68).

One workflow per PipelineRun. Job parallelism and dependency ordering
are handled by Temporal's activity + asyncio.gather model.

Execution model:
  1. Load PipelineRun + all Jobs from DB.
  2. Compute topological tiers from the Job.needs DAG.
  3. For each tier: fan out spawn_pipeline_job activities in parallel,
     then poll each until terminal.
  4. On any job failure: cancel remaining pending job runs, mark run
     FAILURE, exit.
  5. On all success: mark run SUCCESS.

Workflow ID scheme: ``pipeline-run-{pipeline_id}-{run_number}``

Signals:
  cancel — cancel all in-progress job runs, mark run CANCELLED.

Queries:
  status — returns current run status + per-job statuses as a dict.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.pipeline_job_spawn import (
        cancel_pipeline_job,
        mark_job_run_cancelled,
        mark_job_run_failed,
        mark_pipeline_run_failed,
        mark_pipeline_run_running,
        mark_pipeline_run_success,
        poll_pipeline_job,
        spawn_pipeline_job,
    )


_SPAWN_TIMEOUT = timedelta(minutes=5)
_POLL_TIMEOUT = timedelta(hours=6)
_MARK_TIMEOUT = timedelta(minutes=2)

# Maximum interval between poll heartbeats.
_POLL_INTERVAL = timedelta(seconds=30)


def topological_sort(jobs: list[dict]) -> list[list[dict]]:
    """Return jobs grouped into execution tiers via topological sort.

    Each tier is a list of jobs whose ``needs`` are all satisfied by
    previous tiers. Jobs with no needs land in tier 0. Raises
    ``ValueError`` when the dependency graph contains a cycle.

    ``jobs`` is a list of dicts with at minimum ``job_id`` and
    ``needs`` (list of job_id strings) keys.
    """
    # Map job_id → job dict for dependency resolution.
    by_id: dict[str, dict] = {j["job_id"]: j for j in jobs}

    # Kahn's algorithm: track in-degree per node.
    in_degree: dict[str, int] = {j["job_id"]: 0 for j in jobs}
    dependents: dict[str, list[str]] = {j["job_id"]: [] for j in jobs}

    for job in jobs:
        for dep in job.get("needs") or []:
            if dep not in by_id:
                raise ValueError(
                    f"job {job['job_id']!r} declares needs={dep!r} but no such job exists in this pipeline"
                )
            in_degree[job["job_id"]] += 1
            dependents[dep].append(job["job_id"])

    tiers: list[list[dict]] = []
    current_tier = [j for j in jobs if in_degree[j["job_id"]] == 0]

    while current_tier:
        tiers.append(current_tier)
        next_tier = []
        for job in current_tier:
            for dep_id in dependents[job["job_id"]]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    next_tier.append(by_id[dep_id])
        current_tier = next_tier

    processed = sum(len(t) for t in tiers)
    if processed < len(jobs):
        cycle_nodes = [j["job_id"] for j in jobs if in_degree[j["job_id"]] > 0]
        raise ValueError(
            f"cycle detected in pipeline job dependency graph; nodes still in-degree>0: {cycle_nodes}"
        )

    return tiers


@workflow.defn(name="PipelineRunWorkflow")
class PipelineRunWorkflow:
    def __init__(self) -> None:
        self._cancel_requested: bool = False
        self._run_status: str = "pending"
        self._job_statuses: dict[str, str] = {}

    @workflow.signal(name="cancel")
    def request_cancel(self) -> None:
        self._cancel_requested = True

    @workflow.query(name="status")
    def query_status(self) -> dict:
        return {
            "run_status": self._run_status,
            "job_statuses": dict(self._job_statuses),
        }

    @workflow.run
    async def run(self, pipeline_run_id: int) -> WorkflowResult:
        # Mark the run as running and load job metadata from DB.
        run_meta = await workflow.execute_activity(
            mark_pipeline_run_running,
            pipeline_run_id,
            start_to_close_timeout=_MARK_TIMEOUT,
        )
        self._run_status = "running"

        # The definition could not be read at all — distinct from a
        # definition that declares no jobs, and the run must not pass.
        definition_error = run_meta.get("error")
        if definition_error:
            await workflow.execute_activity(
                mark_pipeline_run_failed,
                args=[pipeline_run_id, definition_error],
                start_to_close_timeout=_MARK_TIMEOUT,
            )
            self._run_status = "failure"
            return WorkflowResult(ok=False, message=definition_error)

        jobs: list[dict] = run_meta.get("jobs", [])
        if not jobs:
            # No jobs → trivially successful.
            await workflow.execute_activity(
                mark_pipeline_run_success,
                pipeline_run_id,
                start_to_close_timeout=_MARK_TIMEOUT,
            )
            self._run_status = "success"
            return WorkflowResult(ok=True, message="no jobs to run")

        # Initialise per-job status tracking.
        for job in jobs:
            self._job_statuses[job["job_id"]] = "pending"

        # Compute topological tiers for ordered fan-out.
        try:
            tiers = topological_sort(jobs)
        except ValueError as exc:
            await workflow.execute_activity(
                mark_pipeline_run_failed,
                args=[pipeline_run_id, str(exc)],
                start_to_close_timeout=_MARK_TIMEOUT,
            )
            self._run_status = "failure"
            return WorkflowResult(ok=False, message=str(exc))

        # Execute each tier in order; within a tier, run jobs in parallel.
        for tier in tiers:
            if self._cancel_requested:
                break

            tier_results = await asyncio.gather(
                *[self._run_job(pipeline_run_id, job) for job in tier],
                return_exceptions=True,
            )

            # Check for failures in this tier.
            failed_jobs = []
            for job, result in zip(tier, tier_results, strict=True):
                if isinstance(result, Exception):
                    failed_jobs.append(job["job_id"])
                    self._job_statuses[job["job_id"]] = "failure"
                elif isinstance(result, dict) and result.get("failed"):
                    failed_jobs.append(job["job_id"])
                    self._job_statuses[job["job_id"]] = "failure"
                elif not isinstance(result, Exception):
                    self._job_statuses[job["job_id"]] = "success"

            if failed_jobs:
                # Cancel any still-pending job runs in subsequent tiers.
                pending_job_ids = [
                    j["job_run_id"] for t in tiers[tiers.index(tier) + 1 :] for j in t if j.get("job_run_id")
                ]
                for job_run_id in pending_job_ids:
                    try:
                        await workflow.execute_activity(
                            mark_job_run_cancelled,
                            job_run_id,
                            start_to_close_timeout=_MARK_TIMEOUT,
                        )
                    except Exception:  # noqa: BLE001 — best-effort cleanup
                        pass

                await workflow.execute_activity(
                    mark_pipeline_run_failed,
                    args=[pipeline_run_id, f"jobs failed: {failed_jobs}"],
                    start_to_close_timeout=_MARK_TIMEOUT,
                )
                self._run_status = "failure"
                return WorkflowResult(
                    ok=False,
                    message=f"pipeline run failed: {failed_jobs}",
                )

        if self._cancel_requested:
            # Cancel any still-pending job runs.
            pending_job_ids = [
                j.get("job_run_id")
                for tier in tiers
                for j in tier
                if self._job_statuses.get(j["job_id"]) == "pending" and j.get("job_run_id")
            ]
            for job_run_id in pending_job_ids:
                try:
                    await workflow.execute_activity(
                        mark_job_run_cancelled,
                        job_run_id,
                        start_to_close_timeout=_MARK_TIMEOUT,
                    )
                except Exception:  # noqa: BLE001 — best-effort
                    pass

            await workflow.execute_activity(
                mark_pipeline_run_failed,
                args=[pipeline_run_id, "cancelled by signal"],
                start_to_close_timeout=_MARK_TIMEOUT,
            )
            self._run_status = "cancelled"
            return WorkflowResult(ok=False, message="pipeline run cancelled")

        await workflow.execute_activity(
            mark_pipeline_run_success,
            pipeline_run_id,
            start_to_close_timeout=_MARK_TIMEOUT,
        )
        self._run_status = "success"
        return WorkflowResult(ok=True, message="pipeline run completed successfully")

    async def _run_job(self, pipeline_run_id: int, job: dict) -> dict:
        """Spawn and poll a single job run. Returns a status dict."""
        self._job_statuses[job["job_id"]] = "running"

        try:
            job_run_id = await workflow.execute_activity(
                spawn_pipeline_job,
                args=[pipeline_run_id, job["job_id"]],
                start_to_close_timeout=_SPAWN_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            self._job_statuses[job["job_id"]] = "failure"
            return {"failed": True, "reason": f"spawn failed: {exc}"}

        # Poll until the job reaches a terminal state.
        while True:
            if self._cancel_requested:
                try:
                    await workflow.execute_activity(
                        cancel_pipeline_job,
                        job_run_id,
                        start_to_close_timeout=_MARK_TIMEOUT,
                    )
                except Exception:  # noqa: BLE001 — best-effort cancel
                    pass
                self._job_statuses[job["job_id"]] = "cancelled"
                return {"failed": True, "reason": "cancelled"}

            result = await workflow.execute_activity(
                poll_pipeline_job,
                job_run_id,
                start_to_close_timeout=_POLL_TIMEOUT,
            )

            if result.get("completed"):
                self._job_statuses[job["job_id"]] = "success"
                return {"failed": False}

            if result.get("failed"):
                self._job_statuses[job["job_id"]] = "failure"
                await workflow.execute_activity(
                    mark_job_run_failed,
                    args=[job_run_id, result.get("exit_code")],
                    start_to_close_timeout=_MARK_TIMEOUT,
                )
                return {"failed": True, "exit_code": result.get("exit_code")}

            # Not yet terminal — wait before polling again.
            await asyncio.sleep(_POLL_INTERVAL.total_seconds())
