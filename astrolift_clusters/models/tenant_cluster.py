"""
TenantCluster — a Kubernetes cluster registered with the platform.

Clusters are scoped per-org (``organization`` set) or shared
(``organization`` null). The control plane probes capabilities on
register / on a periodic refresh and caches the result so deploy
planning doesn't pay for a probe each time.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class TenantCluster(NamedBaseCoreModel):
    class AuthMethod(models.TextChoices):
        KUBECONFIG = "kubeconfig"
        EXEC_PLUGIN = "exec_plugin"
        SERVICE_ACCOUNT_TOKEN = "service_account_token"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="tenant_clusters",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    provider_plugin = models.ForeignKey(
        "astrolift_clusters.ProviderPlugin",
        related_name="clusters",
        on_delete=models.PROTECT,
    )
    provider_config = models.JSONField(default=dict, blank=True)

    region = models.CharField(max_length=64, blank=True, default="")
    endpoint = models.URLField(blank=True, default="")
    ca_cert = models.TextField(blank=True, default="")
    auth_method = models.CharField(max_length=32, choices=AuthMethod.choices)
    auth_config = models.JSONField(default=dict, blank=True)
    ingress_class = models.CharField(max_length=64, default="nginx")
    default_namespace_prefix = models.CharField(max_length=64, blank=True, default="")
    capabilities = models.JSONField(default=dict, blank=True)
    capabilities_probed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tenant_cluster_slug_unique_active",
            ),
        ]
