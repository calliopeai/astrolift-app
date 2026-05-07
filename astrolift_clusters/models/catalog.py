"""
ManagedServiceCatalogEntry — (kind, variant) pairs the platform offers.

Provider plugins register one or more entries when they load. The
manifest parser uses these entries to validate ``managed_service``
declarations and resolve ``kind = "postgres"`` (no variant) to the
default variant for the active plugin.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ManagedServiceCatalogEntry(BaseCoreModel):
    kind = models.CharField(max_length=32, db_index=True)
    variant = models.CharField(max_length=64)
    provider_plugin = models.ForeignKey(
        "astrolift_clusters.ProviderPlugin",
        related_name="catalog_entries",
        on_delete=models.CASCADE,
    )
    display_name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    default_config = models.JSONField(default=dict, blank=True)
    config_schema = models.JSONField(default=dict, blank=True)
    binding_envs = models.JSONField(default=list, blank=True)
    is_default_for_kind = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider_plugin", "kind", "variant"],
                condition=models.Q(deleted_at__isnull=True),
                name="catalog_entry_unique_active",
            ),
            models.UniqueConstraint(
                fields=["provider_plugin", "kind"],
                condition=models.Q(deleted_at__isnull=True, is_default_for_kind=True),
                name="catalog_entry_one_default_per_kind",
            ),
        ]
