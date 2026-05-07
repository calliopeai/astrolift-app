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

    class Meta:
        indexes = [
            models.Index(fields=["user", "read_at"], name="notif_user_unread_idx"),
        ]
