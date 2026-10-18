"""Personal quiet hours for external agent-message notifications (#2434)."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class NotificationQuietHours(BaseCoreModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="notification_quiet_hours", on_delete=models.CASCADE
    )
    enabled = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, default="UTC")
    start_minute = models.PositiveSmallIntegerField(default=1320)
    end_minute = models.PositiveSmallIntegerField(default=480)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(deleted_at__isnull=True),
                name="notif_quiet_user_live_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(start_minute__lt=1440, end_minute__lt=1440)
                & ~models.Q(start_minute=models.F("end_minute")),
                name="notif_quiet_valid_window",
            ),
        ]
