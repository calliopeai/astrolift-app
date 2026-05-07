"""
WorkloadIdentityRole — bound cloud identity for a tenant workload.

The platform never ships long-lived static cloud credentials; instead,
each workload assumes a workload-identity role (IRSA on AWS, GCP WI,
AKS Federated, projected SA on vanilla k8s). This row captures the
binding so we can audit who's running as what and revoke quickly.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WorkloadIdentityRole(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="workload_identity_roles",
        on_delete=models.CASCADE,
    )
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="workload_identity_roles",
        on_delete=models.CASCADE,
    )
    role_arn = models.CharField(max_length=512)
    service_account = models.CharField(max_length=255)
    namespace = models.CharField(max_length=128)
    issuer_url = models.URLField(blank=True, default="")
    audience = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster", "service_account", "namespace"],
                condition=models.Q(deleted_at__isnull=True),
                name="wir_unique_active_per_app_cluster_sa_ns",
            ),
        ]
