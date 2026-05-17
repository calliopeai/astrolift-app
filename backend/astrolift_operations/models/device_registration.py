"""DeviceRegistration -- a per-user push device token (#490).

Sibling of the user-inbox ``Notification`` row: that one is
inbox-style ("show in the bell-icon drawer"), this one is the
delivery-side artefact ("here's a push token to send to").

Soft-deleted on revoke -- driver-side revocation may fail (network
flake, already-gone) but the platform should still mark the row
inactive so subsequent sends skip it. Operators can re-register
the same token + user pair by undeleting (UI surface is a future
ticket).

``platform`` mirrors ``_sdk.notification.DevicePlatform`` --
``ios`` / ``android`` / ``web``. Wire values stay lowercase so the
SDK enum round-trips through ``DevicePlatform(self.platform)``.

``provider_metadata`` is the opaque driver-side blob (SNS
``endpoint_arn``, ANH ``installation_id``, FCM project hint). The
dispatcher round-trips it on every send so we don't pay the
``CreatePlatformEndpoint`` cost twice.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class DeviceRegistration(BaseCoreModel):
    class Platform(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"
        WEB = "web", "Web Push"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_device_registrations",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="astrolift_device_registrations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    device_token = models.CharField(max_length=4096)
    """The cloud-supplied push token. APNs is 64 hex; FCM is up to
    ~300 chars; ANH stores them under installations of variable
    length. 4096 leaves headroom for future formats without burning
    storage."""

    platform = models.CharField(max_length=16, choices=Platform.choices)
    label = models.CharField(max_length=255, blank=True, default="")
    """Operator-visible label, e.g. 'Leo's iPhone 15'."""

    registration_id = models.CharField(max_length=4096, blank=True, default="")
    """Driver-side handle returned by ``register_device`` (SNS
    endpoint ARN, ANH installation id, FCM token-as-id). Empty
    when registration hasn't completed yet."""

    driver_name = models.CharField(max_length=32, blank=True, default="")
    """The ``NotificationDriver.name`` that minted this
    registration. Lets the dispatcher route revoke calls to the
    right driver even when the profile has since changed."""

    provider_metadata = models.JSONField(default=dict, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True, default="")
    """Free-form note set when the dispatcher revokes (e.g.
    'invalid_token' from a send response). Surfaces in the audit
    log so operators can correlate."""

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "device_token"],
                condition=models.Q(deleted_at__isnull=True),
                name="device_user_token_unique_live",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "platform"],
                name="device_user_platform_idx",
            ),
            models.Index(
                fields=["organization", "deleted_at"],
                name="device_org_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"DeviceRegistration {self.platform} for user {self.user_id}"
