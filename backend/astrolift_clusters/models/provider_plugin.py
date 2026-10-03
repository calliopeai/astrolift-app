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
    # The plugin's own semver, not to be confused with
    # ``BaseCoreModel.version`` — the optimistic-lock counter this used to
    # shadow. While it was called ``version``, ``save()`` evaluated
    # ``"0.0.0" + 1`` and raised, so the model could not be written through
    # the ORM at all and every caller reached for ``bulk_create`` (#1517).
    plugin_version = models.CharField(max_length=64, default="0.0.0")
    capabilities_manifest = models.JSONField(default=dict, blank=True)
    config_schema = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        from django.db import transaction

        fields = kwargs.get("update_fields")
        source_write = fields is None or bool(
            set(fields)
            & {
                "deleted_at",
                "config_schema",
                "plugin_version",
                "slug",
                "capabilities_manifest",
                "is_enabled",
            }
        )
        if not source_write:
            return super().save(*args, **kwargs)
        if fields is not None:
            kwargs["update_fields"] = set(fields) | {"version", "updated_at"}
        # A reused stale instance must not reuse an observed review counter.
        # Source writers and reviewed dispatch take the same canonical row lock;
        # telemetry-only conditional updates intentionally bypass this path.
        with transaction.atomic():
            if self.pk is not None:
                persisted = (
                    type(self)
                    .all_objects.select_for_update()
                    .filter(pk=self.pk)
                    .values_list("version", flat=True)
                    .first()
                )
                if persisted is not None:
                    self.version = persisted
            return super().save(*args, **kwargs)

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
