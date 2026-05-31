"""Pipeline Prometheus metrics surface (#98).

Exports Prometheus metrics for pipeline run tracking, queue depth,
runner health, and webhook ingest. The platform defaults to Prometheus.

These counters/histograms/gauges are registered at app startup and
updated throughout the pipeline lifecycle by calling the helper functions
below.

Metrics are served at /metrics/ alongside the platform's existing metrics.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------
# We use lazy import + try/except so the app boots even when prometheus_client
# is not installed (dev installs without monitoring).

try:
    from prometheus_client import Counter, Gauge, Histogram

    _RUNS_TOTAL = Counter(
        "astrolift_pipeline_runs_total",
        "Total pipeline runs by outcome",
        ["org", "pipeline", "status", "trigger_kind"],
    )

    _RUN_DURATION = Histogram(
        "astrolift_pipeline_run_duration_seconds",
        "End-to-end pipeline run wall-clock duration",
        ["org", "pipeline", "status"],
        buckets=[1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600],
    )

    _JOB_DURATION = Histogram(
        "astrolift_pipeline_job_duration_seconds",
        "Per-job execution duration",
        ["org", "pipeline", "job_id", "status", "backend"],
        buckets=[1, 5, 15, 30, 60, 120, 300, 600],
    )

    _JOBS_PENDING = Gauge(
        "astrolift_pipeline_jobs_pending",
        "Jobs waiting for a runner/cluster slot",
        ["org", "pipeline", "backend"],
    )

    _WEBHOOK_DELIVERIES = Counter(
        "astrolift_pipeline_webhook_deliveries_total",
        "Webhook delivery outcomes",
        ["org", "provider", "outcome"],
    )

    _WEBHOOK_SIG_FAILURES = Counter(
        "astrolift_pipeline_webhook_signature_failures_total",
        "Webhook signature validation failures (security signal)",
        ["org", "provider"],
    )

    _PROMETHEUS_AVAILABLE = True

except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.info("pipelines.metrics: prometheus_client not installed — metrics disabled")


# ---------------------------------------------------------------------------
# Update helpers — called from lifecycle hooks and webhook views
# ---------------------------------------------------------------------------


def record_run_completed(pipeline_run) -> None:
    """Record a pipeline run completion counter + duration histogram."""
    if not _PROMETHEUS_AVAILABLE:
        return

    org = pipeline_run.pipeline.organization.slug
    pipeline = pipeline_run.pipeline.name
    status = pipeline_run.status
    trigger = pipeline_run.trigger_kind

    _RUNS_TOTAL.labels(org=org, pipeline=pipeline, status=status, trigger_kind=trigger).inc()

    if pipeline_run.started_at and pipeline_run.finished_at:
        duration = (pipeline_run.finished_at - pipeline_run.started_at).total_seconds()
        _RUN_DURATION.labels(org=org, pipeline=pipeline, status=status).observe(duration)


def record_job_completed(job_run, backend: str = "k8s_job") -> None:
    """Record a job run duration histogram."""
    if not _PROMETHEUS_AVAILABLE:
        return

    run = job_run.pipeline_run
    org = run.pipeline.organization.slug
    pipeline = run.pipeline.name
    job_id = job_run.job.job_id if job_run.job_id else "unknown"

    if job_run.started_at and job_run.finished_at:
        duration = (job_run.finished_at - job_run.started_at).total_seconds()
        _JOB_DURATION.labels(
            org=org,
            pipeline=pipeline,
            job_id=job_id,
            status=job_run.status,
            backend=backend,
        ).observe(duration)


def update_jobs_pending(org_slug: str, pipeline_name: str, backend: str, count: int) -> None:
    """Update the pending job count gauge."""
    if not _PROMETHEUS_AVAILABLE:
        return
    _JOBS_PENDING.labels(org=org_slug, pipeline=pipeline_name, backend=backend).set(count)


def record_webhook_delivery(org_slug: str, provider: str, outcome: str) -> None:
    """Record a webhook delivery outcome.

    outcome: dispatched | rate_limited | signature_invalid | filtered | replay
    """
    if not _PROMETHEUS_AVAILABLE:
        return
    _WEBHOOK_DELIVERIES.labels(org=org_slug, provider=provider, outcome=outcome).inc()


def record_webhook_signature_failure(org_slug: str, provider: str) -> None:
    """Record a webhook signature failure. Alert when this spikes."""
    if not _PROMETHEUS_AVAILABLE:
        return
    _WEBHOOK_SIG_FAILURES.labels(org=org_slug, provider=provider).inc()
