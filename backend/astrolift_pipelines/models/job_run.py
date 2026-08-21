from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class JobRun(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"

    pipeline_run = models.ForeignKey(
        "astrolift_pipelines.PipelineRun",
        related_name="job_runs",
        on_delete=models.CASCADE,
    )
    job = models.ForeignKey(
        "astrolift_pipelines.Job",
        related_name="job_runs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    temporal_activity_id = models.CharField(max_length=512, blank=True, default="")
    # The tail of the pod's output, captured when the Job settles and
    # before it is deleted (#1218). On JobRun rather than StepRun because
    # a job is one container: every step writes to the same stream, and
    # splitting it per step would mean inventing boundaries the stream
    # does not carry.
    log_excerpt = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pipeline_run", "job__job_id"]
        indexes = [
            models.Index(fields=["pipeline_run", "status"], name="jrun_prun_status_idx"),
        ]

    def __str__(self) -> str:
        return f"PipelineRun({self.pipeline_run_id})/Job({self.job_id})"
