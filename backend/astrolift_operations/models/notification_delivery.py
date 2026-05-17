"""NotificationDelivery -- per-send audit row (#490).

One row per ``NotificationDriver.send`` result. Drives the
dispatcher's reporting + the operator-facing delivery log. Not
append-only (status can flip from queued -> delivered when an
async ack lands later) but the (delivery_id, attempt) tuple is
write-once per dispatch attempt.

``status`` mirrors ``_sdk.notification.SendStatus`` literal:
``delivered`` / ``queued`` / ``failed`` / ``unsupported`` /
``rate_limited`` / ``invalid_token``. The dispatcher consumes
``invalid_token`` to soft-delete the corresponding
``DeviceRegistration``.

``target_kind`` carries which channel the dispatcher tried (push
/ email / sms / webhook) so a single ``NotificationDispatch``
job can write multiple rows per user (one per channel) and the
operator can see exactly what landed where.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class NotificationDelivery(BaseCoreModel):
    class Status(models.TextChoices):
        DELIVERED = "delivered", "Delivered"
        QUEUED = "queued", "Queued (async)"
        FAILED = "failed", "Failed"
        UNSUPPORTED = "unsupported", "Unsupported channel"
        RATE_LIMITED = "rate_limited", "Rate limited"
        INVALID_TOKEN = "invalid_token", "Invalid device token"

    class TargetKind(models.TextChoices):
        PUSH = "push", "Push notification"
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WEBHOOK = "webhook", "Webhook"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="astrolift_notification_deliveries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_notification_deliveries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    device = models.ForeignKey(
        "astrolift_operations.DeviceRegistration",
        related_name="deliveries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    event_type = models.CharField(max_length=128, blank=True, default="")
    """The platform event this delivery was generated from, e.g.
    ``deploy.failed``. Lets operators answer "which event types
    are getting through?" without joining back to the event log."""

    driver_name = models.CharField(max_length=32, db_index=True)
    """Which NotificationDriver returned the SendResult."""

    target_kind = models.CharField(
        max_length=16,
        choices=TargetKind.choices,
        db_index=True,
    )
    target_address = models.CharField(max_length=512, blank=True, default="")
    """For push: the registration_id. For email: the recipient
    address. For sms: E.164 phone. For webhook: URL."""

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        db_index=True,
    )
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    retriable = models.BooleanField(default=False)

    payload_excerpt = models.CharField(max_length=512, blank=True, default="")
    """First ~512 chars of the rendered payload, for operator
    debugging. Truncated to keep the audit log lean."""

    delivered_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-delivered_at"],
                name="delivery_org_recent_idx",
            ),
            models.Index(
                fields=["user", "-delivered_at"],
                name="delivery_user_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"NotificationDelivery {self.driver_name}/{self.status}"
