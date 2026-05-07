"""
Invitation — short-lived token tied to an email address.

Default TTL is 7 days; the token is hashed at rest so a row leak does
not give attackers usable credentials. The plaintext value is shown
once at creation and never persisted.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models.base import BaseCoreModel


def _default_expiry():
    return timezone.now() + timedelta(days=7)


class Invitation(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"
        APP = "APP"

    class Status(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        EXPIRED = "expired"
        REVOKED = "revoked"

    email = models.EmailField(db_index=True)
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()
    role = models.ForeignKey(
        "astrolift_identity.Role",
        related_name="invitations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    token_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(default=_default_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sent_invitations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        indexes = [
            models.Index(fields=["email", "scope_kind", "scope_id"], name="inv_email_scope_idx"),
            models.Index(fields=["status", "expires_at"], name="inv_status_expires_idx"),
        ]

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()
