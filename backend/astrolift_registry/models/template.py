"""
Template — starter project for ``astro app new <template>``.

System templates have ``organization=NULL``; org-private templates
are scoped to one organization. ``manifest_overrides`` gets merged
into the cloned repo's manifest before the first deploy.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Template(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="templates",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    source_repo = models.CharField(max_length=512)
    manifest_overrides = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="template_slug_unique_active_per_org",
            ),
        ]
