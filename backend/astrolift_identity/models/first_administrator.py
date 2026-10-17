"""FirstAdministrator: the install's first owner, pending until claimed (installer #503).

``bootstrap_admin`` records the operator's ``ASTROLIFT_ADMIN_EMAIL`` here for
the organization it creates. Nothing is granted then: no user, no password,
no ``org_owner`` binding. The first sign-in whose provider-verified email
equals ``email`` claims the row (``astrolift_identity.first_admin.claim``),
and from then on the row is history: later starts read ``claimed_at`` and do
nothing, whatever the variable says.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class FirstAdministrator(models.Model):
    organization = models.OneToOneField(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        related_name="first_administrator",
    )
    # Lower-cased and stripped; the claim compares it case-insensitively.
    email = models.EmailField(max_length=254)
    # ASTROLIFT_ADMIN_SUPERUSER, applied to the account at the claim.
    is_superuser = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_subject = models.CharField(max_length=128, blank=True, default="")
    claimed_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"FirstAdministrator(org={self.organization_id}, claimed={self.claimed_at is not None})"
