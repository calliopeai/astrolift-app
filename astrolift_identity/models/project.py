"""
Project — third level of the tenant hierarchy.

A project groups apps that share a deploy cadence, a domain, or a
stack, and is the unit of cost reporting. The ``organization_id`` is
denormalized (equal to ``team.organization_id``) so every leaf join
reaches Organization in O(1).
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Project(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="projects",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="projects",
        on_delete=models.CASCADE,
    )

    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="default_for_projects",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="project_slug_unique_active_per_team",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "team"], name="project_org_team_idx"),
        ]

    def save(self, *args, **kwargs):
        # Denormalize the org from the team so leaf queries can filter
        # by organization without a join.
        if self.team_id and self.organization_id != self.team.organization_id:
            self.organization_id = self.team.organization_id
        super().save(*args, **kwargs)
