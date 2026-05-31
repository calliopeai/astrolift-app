"""
JobRun — a single execution of a pipeline job (#64).

This is the execution record for one job within a PipelineRun. A
PipelineRun may have many JobRuns (one per job defined in the TOML).
The Runner agent claims a JobRun via the agent protocol and posts
completion status when done (see views.py).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class JobRun(BaseCoreModel):
    """Execution record for a single pipeline job."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        CANCELLED = "cancelled", "Cancelled"
        SKIPPED = "skipped", "Skipped"
        TIMED_OUT = "timed_out", "Timed Out"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="job_runs",
        on_delete=models.CASCADE,
    )
    # The pipeline job name as declared in TOML.
    job_name = models.CharField(max_length=200, db_index=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # The runner that claimed this job; null for K8s-dispatched jobs.
    claimed_by_runner = models.ForeignKey(
        "astrolift_pipelines.Runner",
        related_name="job_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Resolved step list passed to the runner agent (JSON).
    steps_payload = models.JSONField(default=list, blank=True)

    # Completion detail: per-step exit codes + status posted by agent.
    steps_result = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "status"]),
        ]

    def __str__(self) -> str:
        return f"JobRun({self.job_name}, {self.status})"
