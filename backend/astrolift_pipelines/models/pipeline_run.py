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
    # The commit this run is *of*. `trigger_ref` is a branch or tag name,
    # which moves — two runs of "main" a week apart are different code, and
    # a pipeline definition cannot be pinned to a name that will not hold
    # still. Both webhook receivers already have this in the payload and
    # were discarding it (#1531). Blank for a manual trigger that named
    # only a ref, which is why it is not required.
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    # Set when the run was triggered by a pull request from a fork, under
    # the trigger's `fork_secrets_policy`. Decided at the receiver, where
    # the payload is, and recorded rather than recomputed: by the time a
    # job spawns the payload is gone, and a security decision must not be
    # able to come out differently the second time it is asked.
    skip_secrets = models.BooleanField(default=False)
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
