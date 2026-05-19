"""UserAlertSubscription — per-app alert notification preferences (#747).

Lets users opt in/out of specific alert kinds on a per-app basis.
The notification dispatcher checks this table before fan-out; when no
row exists the default is to deliver (noisy-fallback = today's behaviour).

One row per (user, registered_app, alert_kind). Soft-deleted rows are
treated as absent so ``clearAlertSubscription`` reverts to the default
without leaving a permanent disabled row.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class UserAlertSubscription(BaseCoreModel):
    class AlertKind(models.TextChoices):
        DEPLOY_SUCCESS = "deploy_success", "Deploy success"
        DEPLOY_FAILURE = "deploy_failure", "Deploy failure"
        ERROR_SPIKE = "error_spike", "Error spike"
        EMAIL_BOUNCE_THRESHOLD = "email_bounce_threshold", "Email bounce threshold"
        PREVIEW_CREATED = "preview_created", "Preview created"
        PREVIEW_DESTROYED = "preview_destroyed", "Preview destroyed"
        CERT_RENEWAL_FAILED = "cert_renewal_failed", "Cert renewal failed"

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WEB = "web", "Web / push"
        BOTH = "both", "Both"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="alert_subscriptions",
        on_delete=models.CASCADE,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="user_alert_subscriptions",
        on_delete=models.CASCADE,
    )
    alert_kind = models.CharField(max_length=64, choices=AlertKind.choices)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.BOTH)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "registered_app", "alert_kind"),
                condition=models.Q(deleted_at__isnull=True),
                name="user_alert_sub_user_app_kind_live_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "registered_app"],
                name="user_alert_sub_user_app_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"UserAlertSubscription(user={self.user_id}, app={self.registered_app_id}, "
            f"kind={self.alert_kind}, enabled={self.enabled})"
        )
