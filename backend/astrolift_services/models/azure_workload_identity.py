"""The managed identity the platform created for an app on one AKS cluster (#2279).

AKS workload identity binds a pod's ServiceAccount to a user-assigned managed
identity through a federated credential, and the pod finds the identity by the
client ID on its ServiceAccount. Azure mints that ID when the identity is
created; nothing in the cluster's configuration can derive it. This row is the
platform's observation of the identity it created. The deploy render reads the
client and tenant IDs from it, and every later reconcile or removal refuses an
identity that no longer carries these IDs, so a same-name replacement is never
adopted.

One row per app per cluster, shared by the app's environments there, as the
identity is.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AzureWorkloadIdentity(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="azure_workload_identities",
        on_delete=models.CASCADE,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="azure_workload_identities",
        on_delete=models.CASCADE,
    )
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="azure_workload_identities",
        on_delete=models.CASCADE,
    )
    tenant_id = models.CharField(max_length=36)
    subscription_id = models.CharField(max_length=36)
    resource_group = models.CharField(max_length=90)
    identity_name = models.CharField(max_length=128)
    resource_id = models.CharField(max_length=512)
    client_id = models.CharField(max_length=36)
    principal_id = models.CharField(max_length=36)
    issuer = models.CharField(
        max_length=512,
        help_text="The OIDC issuer the identity's federated credentials trust.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster"],
                condition=models.Q(deleted_at__isnull=True),
                name="azure_wi_unique_active_per_app_cluster",
            ),
        ]
