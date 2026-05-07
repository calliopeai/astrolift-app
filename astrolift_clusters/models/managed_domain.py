"""
ManagedDomain — DNS zone owned by the platform or a customer.

The platform manages records under a managed domain on behalf of
tenant apps. ``default_for`` selects whether this domain is the default
for tenant apps, preview environments, both, or none.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ManagedDomain(BaseCoreModel):
    class DefaultFor(models.TextChoices):
        TENANT_APPS = "tenant_apps"
        PREVIEW_ENVS = "preview_envs"
        BOTH = "both"
        NONE = "none"

    zone = models.CharField(max_length=255)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="managed_domains",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    dns_driver = models.CharField(max_length=64)
    dns_config = models.JSONField(default=dict, blank=True)
    default_for = models.CharField(
        max_length=32,
        choices=DefaultFor.choices,
        default=DefaultFor.NONE,
    )
    is_wildcard_managed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["zone"],
                condition=models.Q(deleted_at__isnull=True),
                name="managed_domain_zone_unique_active",
            ),
        ]
