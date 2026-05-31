"""Pipeline job log streaming — transport and durable storage (#92).

Pipeline job logs are generated inside K8s Job pods. They need to be:
1. Streamed in near-real-time to the operator's browser while the job runs
2. Stored durably for post-run inspection
3. Redacted of secret values before reaching storage (see secret_plumbing.py)

Architecture:
- Logs flow: pod stdout/stderr → K8s log stream → Astrolift worker → StepRun.log_excerpt
- Multi-replica fan-out: each job pod has one log streaming goroutine
- Durable log storage: in StepRun.log_excerpt (last 10,000 lines) + optionally
  forwarded to the org's observability backend (Loki/CloudWatch/Datadog)
- Real-time delivery: Server-Sent Events from the GraphQL subscription or
  polling the StepRun query

This module provides:
- stream_job_logs(): generator that yields log lines from a K8s Job
- store_log_chunk(): append to StepRun.log_excerpt (ring buffer, 10K lines)
- finalize_job_logs(): flush + mark log streaming complete
"""

from __future__ import annotations

import logging
from typing import Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import JobRun, StepRun

logger = logging.getLogger(__name__)

_MAX_LOG_LINES = 10_000


def stream_job_logs(
    job_run: "JobRun",
    k8s_job_name: str,
    namespace: str,
    cluster,
    *,
    redactor=None,
) -> Generator[str, None, None]:
    """Stream log lines from a K8s Job's pod(s).

    Yields individual log lines (already redacted if redactor is provided).
    The caller is responsible for calling store_log_chunk() with the yielded lines.

    This is a blocking generator — the caller should run it in a thread or
    Temporal activity.
    """
    try:
        from core.cluster_observability import get_dynamic_client
        client = get_dynamic_client(cluster)
    except Exception as exc:
        logger.warning("pipelines.log_streaming: could not connect to cluster: %s", exc)
        return

    try:
        pod_api = client.resources.get(api_version="v1", kind="Pod")
        # Find the pod for this job
        pods = pod_api.get(
            label_selector=f"job-name={k8s_job_name}",
            namespace=namespace,
        )
        if not pods.items:
            logger.info("pipelines.log_streaming: no pods found for job %s", k8s_job_name)
            return

        pod_name = pods.items[0].metadata.name

        # Stream logs from the pod
        log_stream = pod_api.log(
            name=pod_name,
            namespace=namespace,
            follow=True,
            _preload_content=False,
        )
        for line in log_stream:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.rstrip("\n")
            if redactor:
                line = redactor.redact(line)
            yield line

    except Exception as exc:
        logger.exception("pipelines.log_streaming: error streaming logs for %s", k8s_job_name)
        yield f"[log streaming error: {exc}]"


def store_log_chunk(step_run: "StepRun", lines: list[str]) -> None:
    """Append log lines to a StepRun's log_excerpt, keeping the last N lines.

    Uses a ring-buffer approach: keep only the last _MAX_LOG_LINES lines.
    Saves to DB after appending.
    """
    existing = (step_run.log_excerpt or "").splitlines()
    combined = existing + lines
    if len(combined) > _MAX_LOG_LINES:
        combined = combined[-_MAX_LOG_LINES:]

    step_run.log_excerpt = "\n".join(combined)
    step_run.save(update_fields=["log_excerpt", "updated_at", "version"])


def finalize_job_logs(job_run: "JobRun") -> None:
    """Mark log streaming complete for a job run.

    Forward logs to the org's observability backend if configured.
    """
    _forward_to_observability_backend(job_run)


def _forward_to_observability_backend(job_run: "JobRun") -> None:
    """Forward step run logs to the org's configured log backend.

    No-ops gracefully if no log backend is configured (common in dev).
    """
    try:
        from astrolift_observability.services import get_log_driver_for_org
        from astrolift_pipelines.models import StepRun

        org = job_run.pipeline_run.pipeline.organization
        driver = get_log_driver_for_org(org)
        if driver is None:
            return

        step_runs = StepRun.objects.filter(job_run=job_run)
        for step_run in step_runs:
            if step_run.log_excerpt:
                driver.ingest(
                    labels={
                        "job_id": job_run.job.job_id if job_run.job_id else "",
                        "pipeline": job_run.pipeline_run.pipeline.name,
                        "run_number": str(job_run.pipeline_run.run_number),
                    },
                    log_text=step_run.log_excerpt,
                )
    except Exception:  # noqa: BLE001
        pass  # Observability is best-effort


def build_log_excerpt_for_run(job_run: "JobRun") -> str:
    """Return a combined log excerpt for all steps in a job run.

    Useful for the job detail view which shows a combined log without
    step boundaries visible.
    """
    from astrolift_pipelines.models import StepRun

    step_runs = StepRun.objects.filter(job_run=job_run).order_by("step__position")
    parts = []
    for sr in step_runs:
        step_name = sr.step.step_id or sr.step.name if sr.step_id else f"step-{sr.pk}"
        if sr.log_excerpt:
            parts.append(f"=== {step_name} ===")
            parts.append(sr.log_excerpt)

    return "\n".join(parts)
