from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Trigger(BaseCoreModel):
    """A trigger definition for a pipeline.

    ``config`` carries kind-specific parameters: branch filters for
    push/pr, cron expressions for schedule, webhook secrets for webhook.
    """

    class Kind(models.TextChoices):
        PUSH = "push", "Push"
        PULL_REQUEST = "pull_request", "Pull Request"
        SCHEDULE = "schedule", "Schedule"
        MANUAL = "manual", "Manual"
        WEBHOOK = "webhook", "Webhook"

    pipeline = models.ForeignKey(
        "astrolift_pipelines.Pipeline",
        related_name="triggers",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["pipeline", "kind"]

    def __str__(self) -> str:
        return f"Pipeline({self.pipeline_id})/{self.kind}"
