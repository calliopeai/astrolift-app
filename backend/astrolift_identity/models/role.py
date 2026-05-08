"""
Role — a named bag of permissions, scoped to a level.

System roles (``is_system=True``) ship with the platform and cannot be
edited; custom roles are scoped to their organization and may be
cloned from a system role and pruned/extended.

The permission column stores a list of values from
``core.permissions.Permission``. The model validates membership at
save time so a typo can't make it into the database.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models.base import NamedBaseCoreModel
from core.permissions import Permission


class Role(NamedBaseCoreModel):
    class ScopeLevel(models.TextChoices):
        ORG = "ORG", "Organization"
        TEAM = "TEAM", "Team"
        PROJECT = "PROJECT", "Project"
        APP = "APP", "App"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="roles",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    scope_level = models.CharField(max_length=16, choices=ScopeLevel.choices)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="role_slug_unique_active_per_org",
            ),
        ]

    def clean(self):
        super().clean()
        catalog = {p.value for p in Permission}
        bad = [p for p in self.permissions if p not in catalog]
        if bad:
            raise ValidationError({"permissions": f"unknown permission(s): {sorted(bad)}"})
