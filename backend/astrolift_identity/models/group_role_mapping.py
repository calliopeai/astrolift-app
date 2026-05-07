"""
GroupRoleMapping — IdP group → Astrolift Role per scope.

For SCIM-provisioned organizations, group membership drives role
bindings. The mapping table is the source of truth for which IdP
group grants which role on which scope; on every login (or SCIM
group-membership change) the binding set is recomputed from this.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class GroupRoleMapping(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"
        APP = "APP"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="group_role_mappings",
        on_delete=models.CASCADE,
    )
    group_external_id = models.CharField(max_length=255, db_index=True)
    role = models.ForeignKey(
        "astrolift_identity.Role",
        related_name="group_mappings",
        on_delete=models.CASCADE,
    )
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "group_external_id", "role", "scope_kind", "scope_id"],
                condition=models.Q(deleted_at__isnull=True),
                name="grm_unique_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "group_external_id"],
                name="grm_org_group_idx",
            ),
        ]
