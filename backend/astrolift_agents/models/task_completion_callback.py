"""Durable task-specific completion delivery, never an agent execution retry."""

from django.db import models

from core.models.base import BaseCoreModel


class AgentTaskCallbackPolicy(BaseCoreModel):
    organization = models.OneToOneField(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        related_name="agent_callback_policy",
    )
    allowed_hosts = models.JSONField(default=list, blank=True)


class AgentTaskCompletionCallback(BaseCoreModel):
    class Mode(models.TextChoices):
        FULL = "FULL"
        NOTIFY = "NOTIFY"

    class Status(models.TextChoices):
        PENDING = "pending"
        DELIVERED = "delivered"
        FAILED = "failed"

    task = models.OneToOneField(
        "astrolift_agents.AgentTask",
        on_delete=models.CASCADE,
        related_name="completion_callback",
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        related_name="agent_completion_callbacks",
    )
    callback_url = models.URLField(max_length=2048)
    secret_ref = models.CharField(max_length=128)
    correlation_id = models.CharField(max_length=128, blank=True, default="")
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.FULL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=256, blank=True, default="")
    final_event_id = models.UUIDField(null=True, blank=True)
    # Final non-result facts survive delivery; sensitive result/failure text
    # is read from the task for manual replay and never retained here in plaintext.
    event_metadata = models.JSONField(default=dict, blank=True)
    payload_backend_kind = models.CharField(max_length=64, blank=True, default="")
    payload_ciphertext = models.BinaryField(blank=True, default=bytes)
    generation = models.PositiveIntegerField(default=0)
    retry_started_at = models.DateTimeField(null=True, blank=True)
    retry_deadline = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    lease_id = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="agent_callback_due_idx"),
        ]
