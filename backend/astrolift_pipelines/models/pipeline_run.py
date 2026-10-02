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
    organization = models.ForeignKey("astrolift_identity.Organization", null=True, on_delete=models.PROTECT)
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp", null=True, blank=True, on_delete=models.PROTECT
    )
    actor_key = models.CharField(max_length=96, blank=True, default="")
    # Null preserves untracked historical runs under the actor/request uniqueness constraint.
    request_id = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    request_digest = models.CharField(max_length=64, blank=True, default="")
    pipeline_version = models.PositiveIntegerField(default=0)
    dispatch_status = models.CharField(max_length=16, default="reserved")
    dispatch_last_error = models.CharField(max_length=255, blank=True, default="")
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
    temporal_run_id = models.CharField(max_length=128, blank=True, default="")
    cancellation_status = models.CharField(max_length=32, default="not_requested")
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancellation_observed_at = models.DateTimeField(null=True, blank=True)
    cancellation_last_error = models.CharField(max_length=255, blank=True, default="")
    cleanup_status = models.CharField(max_length=16, default="unknown")
    # What definition this run actually executed (#65, spec requirement 5).
    #
    # `trigger_ref` records the ref that fired the run, which is not the same
    # question: a branch moves, and a definition fetched from it is a
    # different document a minute later. Without a digest, "why did this run
    # behave differently" is unanswerable after the fact -- and it becomes
    # unanswerable across N files the moment the DSL grows includes.
    #
    # SHA-256 over the fetched document text, hex, no prefix. Empty when the
    # run had no TOML behind it (an API/dashboard-defined pipeline), which is
    # a real state and distinct from "we did not record it".
    definition_digest = models.CharField(max_length=64, blank=True, default="")
    # The dialect that document declared, so a run stays interpretable after
    # the schema moves on. 0 = no document (see above).
    definition_schema_version = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pipeline", "-run_number"]
        constraints = [
            models.UniqueConstraint(fields=["pipeline", "run_number"], name="pipeline_run_number_unique"),
            models.UniqueConstraint(
                fields=["organization", "actor_key", "request_id"], name="pipeline_start_request_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["pipeline", "-run_number"], name="prun_pipeline_run_number_idx"),
            models.Index(fields=["status"], name="prun_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.pipeline_id}#{self.run_number}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.organization_id = self.pipeline.organization_id
            self.registered_app_id = self.pipeline.registered_app_id
            self.pipeline_version = self.pipeline.version
        super().save(*args, **kwargs)
