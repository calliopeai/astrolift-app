"""
OrgDomain — email domain bound to an Organization.

Used for JIT provisioning: a successful IdP login with an email
matching ``OrgDomain.domain`` auto-creates a Member with the
configured default role. Domain values are unique among live rows so
two orgs cannot both claim the same email domain at once.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class OrgDomain(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="domains",
        on_delete=models.CASCADE,
    )
    domain = models.CharField(max_length=255)
    jit_enabled = models.BooleanField(default=False)
    default_role = models.ForeignKey(
        "astrolift_identity.Role",
        related_name="default_for_domains",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    default_team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="default_for_domains",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["domain"],
                condition=models.Q(deleted_at__isnull=True),
                name="org_domain_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["domain"], name="org_domain_value_idx"),
        ]
