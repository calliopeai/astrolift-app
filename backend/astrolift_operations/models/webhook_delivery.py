"""
WebhookDelivery — per-attempt delivery record.

Each outbound POST (real or test-fire) leaves one row. The operator
UI's expand-row consults the most recent N rows to surface
"the integration is broken" diagnostics:

* HTTP status + latency for trend lines.
* Response body excerpt for the operator to read.
* ``is_test`` flag separates synthetic probes from real fan-out so
  health-rate widgets don't pollute on operator testing.

This is bookkeeping only — the policy lives in
``astrolift_workflows.webhook_delivery_history`` and the
crypto/headers in ``astrolift_operations.webhook_delivery``.

Soft-deleted with the parent subscription via ``CASCADE`` at the FK
level + the standard ``deleted_at`` column inherited from
``BaseCoreModel``; the platform never hard-deletes business rows.
Old rows are GC'd by ``RehealWebhookSubscriptionsWorkflow`` on its
periodic sweep.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WebhookDelivery(BaseCoreModel):
    subscription = models.ForeignKey(
        "astrolift_operations.WebhookSubscription",
        related_name="deliveries",
        on_delete=models.CASCADE,
    )
    # Event-type the delivery was triggered for. Free-form: covers
    # both real platform event types and the synthetic ``webhook.test``
    # used by operator test-fires.
    event_type = models.CharField(max_length=128, db_index=True)
    # 1-indexed retry counter — 1 for the initial attempt, 2 for the
    # first retry, etc. Tests always fire with attempt=1.
    retry_attempt = models.PositiveSmallIntegerField(default=1)
    status_code = models.IntegerField(null=True, blank=True)
    """``None`` when the request died at the transport layer (DNS,
    TCP, TLS) before any HTTP exchange. ``error`` then carries the
    diagnostic."""

    latency_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    is_test = models.BooleanField(default=False)
    """``True`` for operator-initiated test-fires; ``False`` for
    real platform events. Lets UI health widgets exclude probe
    traffic and lets retention pick test rows off faster."""

    request_payload_excerpt = models.TextField(blank=True, default="")
    response_body_excerpt = models.TextField(blank=True, default="")
    error = models.CharField(max_length=512, blank=True, default="")
    # Echo the X-Astrolift-Delivery-Id header we sent, so operators
    # can correlate the UI row with whatever the subscriber logged.
    delivery_id = models.CharField(max_length=64, blank=True, default="")
    delivered_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["subscription", "-delivered_at"],
                name="wh_delivery_sub_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"WebhookDelivery {self.event_type} {self.status_code} @ {self.delivered_at}"
