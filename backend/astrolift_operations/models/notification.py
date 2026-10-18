"""
Notification — inbox-style entry for a user.

Distinct from the platform Event log: events are facts the system
emits, notifications are user-visible items in their inbox. Most
notifications are *generated* from events by a fan-out worker;
the relationship is many-to-many because one event may notify
several users.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class Notification(BaseCoreModel):
    class Kind(models.TextChoices):
        DEPLOY_PENDING_APPROVAL = "deploy_pending_approval"
        DEPLOY_FAILED = "deploy_failed"
        INVITATION = "invitation"
        QUOTA_WARNING = "quota_warning"
        WEBHOOK_DELIVERY_FAILED = "webhook_delivery_failed"
        SYSTEM = "system"
        ACTION_APPROVAL = "action_approval"
        APP_EXPIRING = "app_expiring"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_notifications",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="astrolift_notifications",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    link = models.CharField(max_length=512, blank=True, default="")
    read_at = models.DateTimeField(null=True, blank=True)
    inbox_sequence = models.BigIntegerField(default=0, db_default=0, editable=False)
    expiry_warning = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "read_at"], name="notif_user_unread_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user", "inbox_sequence"], name="notification_inbox_sequence_unique"
            ),
        ]


class NotificationInboxCounter(models.Model):
    """Internal cursor ledger, not a business object or notification.

    The database trigger serializes inserts against this permanent row so a
    cursor cannot skip a notification whose transaction commits late. Keeping
    the counter after notification deletion prevents sequence reuse.
    """

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    last_sequence = models.BigIntegerField(default=0, db_default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="notification_inbox_counter_unique"
            ),
        ]

    def __str__(self):
        return f"Notification inbox {self.organization_id}/{self.user_id}"
