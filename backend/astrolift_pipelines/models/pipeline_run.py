from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class PipelineRun(BaseCoreModel):
    class TriggerKind(models.TextChoices):
        PUSH = "push", "Push"
        PULL_REQUEST = "pull_request", "Pull Request"
        SCHEDULE = "schedule", "Schedule"
        MANUAL = "manual", "Manual"
        API = "api", "API"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        CANCELLED = "cancelled", "Cancelled"

    pipeline = models.ForeignKey(
        "astrolift_pipelines.Pipeline",
        related_name="runs",
        on_delete=models.CASCADE,
    )
    run_number = models.PositiveIntegerField()
    trigger_kind = models.CharField(max_length=32, choices=TriggerKind.choices)
    trigger_ref = models.CharField(max_length=255, blank=True, default="")
    trigger_actor = models.CharField(max_length=255, blank=True, default="")
    temporal_workflow_id = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pipeline", "-run_number"]
        indexes = [
            models.Index(fields=["pipeline", "-run_number"], name="prun_pipeline_run_number_idx"),
            models.Index(fields=["status"], name="prun_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.pipeline_id}#{self.run_number}"
