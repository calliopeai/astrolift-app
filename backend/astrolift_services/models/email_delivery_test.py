"""A replay-safe diagnostic intent, without stored message content or credentials."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class EmailDeliveryTest(BaseCoreModel):
    class Status(models.TextChoices):
        SUBMITTING = "submitting"
        ACCEPTED = "accepted"
        UNKNOWN = "unknown"
        FAILED = "failed"
        SUPPRESSED = "suppressed"
        DELIVERED = "delivered"
        DEFERRED = "deferred"
        BOUNCED = "bounced"
        COMPLAINED = "complained"
        REJECTED = "rejected"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    managed_service = models.ForeignKey("astrolift_services.ManagedService", on_delete=models.PROTECT)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    intent_sha256 = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    sender = models.EmailField(max_length=254)
    recipient = models.EmailField(max_length=254)
    account_id = models.CharField(max_length=12)
    region = models.CharField(max_length=64)
    identity = models.CharField(max_length=254)
    configuration_set = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTING)
    provider_message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    feedback_topic_arns = models.JSONField(default=list)
    simulator = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    reason_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id"], name="email_test_intent_org_request"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "submitting",
                        "accepted",
                        "unknown",
                        "failed",
                        "suppressed",
                        "delivered",
                        "deferred",
                        "bounced",
                        "complained",
                        "rejected",
                    ]
                ),
                name="email_test_status_known",
            ),
        ]
        indexes = [models.Index(fields=["managed_service", "created_at"], name="email_test_service_created")]


class EmailDeliveryObservation(BaseCoreModel):
    delivery_test = models.ForeignKey(
        EmailDeliveryTest, on_delete=models.PROTECT, related_name="observations"
    )
    event_sha256 = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16)
    provider_message_id = models.CharField(max_length=255)
    provider_topic_arn = models.CharField(max_length=512)
    occurred_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["delivery_test", "occurred_at"], name="email_test_observed_time")]
