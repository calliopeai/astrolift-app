"""Org-owned SCIM groups; memberships are the revocable grant provenance."""

from django.db import models

from core.models.base import BaseCoreModel


class ScimGroup(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="scim_groups", on_delete=models.CASCADE
    )
    display_name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, blank=True, default="")
    retired_external_ids = models.JSONField(default=list, blank=True)
    members = models.ManyToManyField("astrolift_identity.Member", related_name="scim_groups", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(external_id=""),
                name="scim_group_org_external_live",
            ),
        ]

    @property
    def group_external_id(self) -> str:
        # Name is mutable, so mappings use the IdP's stable external id or
        # the server-issued SCIM resource id when externalId was omitted.
        return self.external_id or str(self.guid)
