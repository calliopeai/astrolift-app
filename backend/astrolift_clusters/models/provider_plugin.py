"""
ProviderPlugin — a plugin catalog entry (e.g. aws, gcp, k8s_native).

Plugins declare their capabilities (which drivers they implement) via
``capabilities_manifest`` and validate their configuration with
``config_schema`` (JSON-Schema). The control plane never assumes a
plugin's behavior beyond what the manifest claims.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class ProviderPlugin(NamedBaseCoreModel):
    version = models.CharField(max_length=64, default="0.0.0")
    capabilities_manifest = models.JSONField(default=dict, blank=True)
    config_schema = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="provider_plugin_slug_unique_active",
            ),
        ]


class ProviderPluginConfig(NamedBaseCoreModel):
    """Per-org configuration for a provider plugin.

    ``organization`` is nullable for platform-level config (used by
    SaaS where the platform itself owns plugin credentials).
    """

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="provider_plugin_configs",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    provider_plugin = models.ForeignKey(
        "astrolift_clusters.ProviderPlugin",
        related_name="configs",
        on_delete=models.CASCADE,
    )
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "provider_plugin"],
                name="ppconfig_org_plugin_idx",
            ),
        ]
