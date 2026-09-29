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

    # UI preferences that follow the person across browsers (#2154). Each
    # choice column is empty until the person chooses; the read resolves an
    # empty column to the default in ``astrolift_identity.ui_preferences``.
    # Values are validated against the frontend registries on write.
    home_layout = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Home layout key (spec 44 §4.3). Empty means the default from access.",
    )
    home_layout_asked = models.BooleanField(
        default=False,
        help_text="The first-sign-in layout question was answered, so it is not asked again.",
    )
    fleet_view = models.CharField(max_length=32, blank=True, default="")
    workflow_view = models.CharField(max_length=32, blank=True, default="")
    app_view = models.CharField(max_length=32, blank=True, default="")
    flow_particles = models.BooleanField(null=True, blank=True, default=None)
    motion = models.CharField(max_length=16, blank=True, default="")
    restricted_settings = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text="Settings the person cannot change: show or hide. Empty follows the org default.",
    )
    appearance = models.JSONField(
        default=dict,
        blank=True,
        help_text="The person's own partial appearance, validated by core.appearance.",
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
