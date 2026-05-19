"""Per-message SES email event record (#756).

Populated by the SNS webhook receiver
(``astrolift_services.views.ses_events_webhook``) when SES delivers
SEND / DELIVERY / BOUNCE / COMPLAINT / OPEN / CLICK notifications via
the platform's SNS topic. Append-only: rows are inserted and never
mutated — the SES event stream is the source of truth, and operators
read the log via ``astroliftEmailMessages`` / engagement metrics
queries.

The ``managed_service`` FK is nullable on purpose: SNS deliveries can
race ahead of the ``ManagedService`` row that owns the SES
configuration set (e.g. a notification fires before the provisioning
workflow has refreshed the DB), and we'd rather record the event with
an unbound FK than drop it on the floor. A backfill job can re-link
rows once the service row exists; the FK lookup happens by SES
configuration-set name (``{prefix}-{identity}``) at receive time.

Soft-delete is intentionally NOT applied: this is an event log, not a
business entity. Retention is window-based on ``occurred_at`` (a
separate sweep deletes rows older than the per-tenant SES log
retention window) rather than soft-delete + filter.
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class EmailEventKind(models.TextChoices):
    """Mirrors the SES ``notificationType`` enum, lowercased.

    Kept tight to the six event kinds the ingestion pipeline emits so
    a typo in a downstream filter fails closed at the resolver layer
    rather than silently returning zero rows."""

    SEND = "send"
    DELIVERY = "delivery"
    BOUNCE = "bounce"
    COMPLAINT = "complaint"
    OPEN = "open"
    CLICK = "click"


class EmailEvent(AppendOnlyMixin, models.Model):
    """One SES event row, keyed by (managed_service, message_id,
    recipient, event_kind, occurred_at).

    The same SES ``messageId`` can yield multiple rows when the
    notification fires for multiple recipients or moves through
    several lifecycle kinds (send → delivery → open → click); we
    insert one row per ``(message_id, recipient, event_kind)`` triple.
    """

    guid = UUIDv7Field(unique=True, db_index=True)

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        on_delete=models.CASCADE,
        related_name="email_events",
        null=True,
        blank=True,
        db_index=True,
    )

    message_id = models.CharField(max_length=255, db_index=True)
    recipient = models.CharField(max_length=320, db_index=True)
    subject = models.CharField(max_length=998, blank=True, default="")
    event_kind = models.CharField(max_length=16, choices=EmailEventKind.choices, db_index=True)

    metadata = models.JSONField(default=dict, blank=True)
    """Raw SES notification body so a debugger can rebuild bounce /
    complaint reason chains without re-reading the SNS message."""

    occurred_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    """When the platform received the notification (vs. when SES
    recorded it). Diff between the two helps debug delivery lag."""

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(
                fields=["managed_service", "event_kind", "occurred_at"],
                name="emailevent_svc_kind_time_idx",
            ),
            models.Index(
                fields=["message_id", "event_kind"],
                name="emailevent_msg_kind_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"EmailEvent[{self.event_kind}] {self.message_id} -> {self.recipient}"
