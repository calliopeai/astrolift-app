"""
DeviceRegistration — a mobile / desktop endpoint that receives pushes
through the platform NotificationDriver (#476, #490).

One row per (user, device_token) tuple. The token is the FCM /
APNs / Azure Notification Hubs registration identifier — opaque to
the platform, validated by the configured driver. A device may also
optionally bind to the ``AstroliftSession`` row it was enrolled
from; that lets the #499 new-session dispatcher exclude the
just-issued session's own device when fanning out the "new sign-in"
push to other devices.

Soft-deletable per platform convention; the driver-side dead-letter
path (FCM `unregistered`, APNs `Unregistered`) marks the row stale
+ soft-deletes it without operator intervention.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class DeviceRegistration(BaseCoreModel):
    class Platform(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"
        WEB_PUSH = "web_push", "Web push"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_device_registrations",
        on_delete=models.CASCADE,
    )

    # The opaque registration identifier the driver hands back when we
    # call ``driver.register_device(...)``. Hashed-equivalent for
    # lookup purposes: we never need to decrypt it, just present it
    # back to the driver verbatim. ``db_index=True`` because the
    # device-flow re-register path looks rows up by token.
    device_token = models.CharField(max_length=512, db_index=True)

    platform = models.CharField(max_length=16, choices=Platform.choices)

    # Free-form display label set at registration time
    # (``"alice@iPhone 17 Pro"``). Surfaced in /settings/devices so
    # the user can revoke by recognising the row.
    label = models.CharField(max_length=200, blank=True, default="")

    # Optional binding to the session this device was enrolled from —
    # used by the #499 new-session dispatcher to skip the just-issued
    # device when fanning out. NULL when the device was registered
    # outside of a session-create flow (e.g. legacy import).
    enrolled_session = models.ForeignKey(
        "astrolift_identity.AstroliftSession",
        related_name="enrolled_devices",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Organization context at enrollment time; used by per-org event
    # routing so an org-scoped push (alert.fired) doesn't leak across
    # tenants. NULL when the user holds memberships in multiple orgs
    # and the registration was generic (web push from a personal page).
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="device_registrations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # The dispatcher driver that handed back this token. Stored as a
    # short slug ("aws_sns", "gcp_fcm", "azure_anh", "memory") so the
    # send path knows which driver to call. A mismatch between the
    # row's driver and the install's active driver = the row is
    # orphaned; the dispatcher logs + skips it rather than swallowing
    # someone else's token.
    driver = models.CharField(max_length=32)

    registered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # When the upstream provider reports the token is dead (FCM
    # ``unregistered`` / APNs ``Unregistered``) the dispatcher stamps
    # ``stale_at`` + ``soft_delete()`` so future fan-outs skip it.
    # Kept separate from ``deleted_at`` so the audit trail can answer
    # "did the user revoke or did the device unregister itself."
    stale_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "platform"],
                name="device_reg_user_plat_idx",
            ),
            models.Index(
                fields=["organization", "user"],
                name="device_reg_org_user_idx",
            ),
        ]
        constraints = [
            # (user, device_token) is unique among live rows. A
            # re-register with the same token resurrects the row
            # instead of inserting a duplicate.
            models.UniqueConstraint(
                fields=("user", "device_token"),
                condition=models.Q(deleted_at__isnull=True),
                name="device_reg_user_token_live_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"DeviceRegistration({self.platform}, user_id={self.user_id})"

    def mark_stale(self) -> None:
        """Mark this row stale and soft-delete it.

        Called by the dispatcher when the upstream provider reports
        the token is dead. Idempotent — re-stamping an already-stale
        row is a no-op.
        """
        if self.stale_at is not None:
            return
        from django.utils import timezone

        now = timezone.now()
        self.stale_at = now
        self.deleted_at = now
        self.save(
            update_fields=[
                "stale_at",
                "deleted_at",
                "updated_at",
                "version",
            ]
        )
