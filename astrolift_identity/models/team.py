"""
Team — the second level of the tenant hierarchy.

Teams are the primary permission boundary for groups of humans
(engineering, platform, mobile) and the unit that owns API tokens.

Slugs are unique per organization, scoped to live rows.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Team(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="teams",
        on_delete=models.CASCADE,
    )

    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="default_for_teams",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    default_managed_domain = models.ForeignKey(
        "astrolift_clusters.ManagedDomain",
        related_name="default_for_teams",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="team_slug_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "slug"], name="team_org_slug_idx"),
        ]
