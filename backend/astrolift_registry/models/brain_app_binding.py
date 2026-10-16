"""An app hosted on a brain (#2243): the grant that lets it read the brain.

Created by someone who can read the brain (``hostAppOnBrain``). Each deploy
of the app issues its environment a read token signed by a key of this
binding's own (``BrainTokenKey.binding``), so deleting the binding, the app
or the brain drops those keys from the published JWKS and the edge stops
admitting the app's tokens.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class BrainAppBinding(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="brain_app_bindings",
        on_delete=models.CASCADE,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="brain_bindings",
        on_delete=models.CASCADE,
    )
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="hosted_app_bindings",
        on_delete=models.CASCADE,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app"],
                condition=models.Q(deleted_at__isnull=True),
                name="brain_app_binding_one_per_app",
            ),
        ]

    def __str__(self) -> str:
        return f"BrainAppBinding({self.registered_app_id} -> {self.brain_id})"
