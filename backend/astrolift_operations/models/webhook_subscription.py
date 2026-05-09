"""
WebhookSubscription — outbound webhook fan-out.

The platform delivers each event matching ``events`` to the URL,
signing the body with the secret derived from ``secret_hash``. The
delivery loop tracks last status + failure count for backoff.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WebhookSubscription(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="webhook_subscriptions",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="webhook_subscriptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    url = models.URLField()
    secret_hash = models.CharField(max_length=128)
    events = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_response_status = models.IntegerField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    disabled_at = models.DateTimeField(null=True, blank=True)
    # Free-form reason set by the auto-disable code or operators —
    # surfaces on the UI alongside the disabled badge so re-enable
    # flows can show what tripped.
    disabled_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active"], name="webhook_org_active_idx"),
        ]
