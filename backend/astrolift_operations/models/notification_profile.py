"""NotificationProfile -- per-install notification config (#490).

Mirrors ``ObservabilityProfile``: one row per install picks the
driver + per-driver config; the dispatcher resolves a driver
instance at send time.

The shape is validated by ``astrolift_operations.notification_profile``
(the pure-policy module) before persistence; the model only
enforces the structural constraints (one active profile per
org).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class NotificationProfile(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="astrolift_notification_profiles",
        on_delete=models.CASCADE,
    )

    driver = models.CharField(max_length=32)
    """Wire identifier matching
    ``astrolift_operations.notification_profile.NotificationDriverKind``."""

    config = models.JSONField(default=dict, blank=True)
    """Driver-specific config blob. Validated by the policy module
    on every save."""

    retention_delivery_days = models.PositiveIntegerField(default=30)
    """How many days of NotificationDelivery rows to keep. Garbage
    collection runs in a separate retention job."""

    is_active = models.BooleanField(default=True)
    """Inactive profiles are kept for history but don't drive
    new sends. Operators can swap profiles without losing the old
    config."""

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(deleted_at__isnull=True, is_active=True),
                name="notification_profile_one_active_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "is_active"],
                name="notif_profile_org_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"NotificationProfile org={self.organization_id} driver={self.driver}"
