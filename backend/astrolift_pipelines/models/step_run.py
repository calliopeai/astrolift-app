from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class StepRun(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"

    job_run = models.ForeignKey(
        "astrolift_pipelines.JobRun",
        related_name="step_runs",
        on_delete=models.CASCADE,
    )
    step = models.ForeignKey(
        "astrolift_pipelines.Step",
        related_name="step_runs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    exit_code = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["job_run", "step__position"]

    def __str__(self) -> str:
        return f"JobRun({self.job_run_id})/Step({self.step_id})"
