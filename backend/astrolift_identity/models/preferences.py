"""UserPreferences — per-user settings that don't belong on auth.User.

Django's built-in auth.User is not subclassed here (no custom user
model). Instead we carry optional settings in a OneToOneField'd row
that is lazily created on first write. Reads via
``UserPreferences.for_user(user)`` return safe defaults when no row
exists so callers never need to null-check.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class UserPreferences(models.Model):
    """Optional settings for a Django auth.User.

    Created lazily — no row exists for users who haven't customised
    anything. Use ``UserPreferences.for_user(user)`` to get or create.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
        primary_key=True,
    )

    timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "IANA timezone name (e.g. 'America/New_York'). "
            "Empty means the UI falls back to the browser-detected zone."
        ),
    )

    class Meta:
        app_label = "astrolift_identity"
        verbose_name = "user preferences"
        verbose_name_plural = "user preferences"

    def __str__(self) -> str:
        return f"UserPreferences(user={self.user_id}, timezone={self.timezone!r})"

    @classmethod
    def for_user(cls, user) -> UserPreferences:
        """Return the UserPreferences row for *user*, creating it if absent.

        Uses get_or_create so concurrent first-write calls converge on
        the same row rather than racing to insert a duplicate.
        """
        prefs, _ = cls.objects.get_or_create(user=user)
        return prefs
