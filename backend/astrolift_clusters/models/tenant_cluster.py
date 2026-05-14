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

    class Lifecycle(models.TextChoices):
        """Cluster management lifecycle (#316).

        ``registered`` rows carry metadata + auth shape only — they are
        not deploy targets. ``managing`` rows have an in-flight
        ``BringClusterIntoManagementWorkflow``. ``managed`` rows have
        had platform RBAC applied, capabilities probed, and a preflight
        Job complete successfully and are deploy-eligible. ``error``
        rows hit a recoverable failure during the workflow; the
        operator inspects ``last_management_error`` and clicks Retry
        (which restarts the workflow). ``decommissioning`` rows have an
        in-flight ``DecommissionClusterWorkflow`` removing platform
        RBAC + clearing app bindings; ``decommissioned`` rows are
        terminal — kept for audit but no longer deploy-eligible and not
        shown in the active-cluster picker. Spec ref: issue #316.
        """

        REGISTERED = "registered"
        MANAGING = "managing"
        MANAGED = "managed"
        ERROR = "error"
        DECOMMISSIONING = "decommissioning"
        DECOMMISSIONED = "decommissioned"

    class DeliveryMode(models.TextChoices):
        """Spec 07 §2 — how rendered manifests reach this cluster."""

        DIRECT_API = "direct_api"
        """Server-side apply via the cluster's apiserver. Lowest
        latency; default for most clusters."""

        GITOPS_ARGOCD = "gitops_argocd"
        """Manifests committed to the GitOps repo; ArgoCD syncs
        them. Used when the operator wants Argo as the audit and
        rollback boundary."""

        GITOPS_FLUX = "gitops_flux"
        """Same shape as ArgoCD but Flux is the syncer."""

        HYBRID = "hybrid"
        """Direct-apply for stateless workloads; GitOps for
        platform-level objects (CRDs, RBAC). Decided per-object
        by the activity layer."""

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

    # Bring-into-management state machine (#316). Default ``registered``
    # so a row created via the existing register_tenant_cluster mutation
    # is metadata-only until the operator explicitly opts the cluster
    # into platform management. ``managed_at`` is the last successful
    # bring/refresh transition; ``last_management_error`` carries the
    # last activity's failure message and is cleared on the next
    # successful transition.
    lifecycle = models.CharField(
        max_length=32,
        choices=Lifecycle.choices,
        default=Lifecycle.REGISTERED,
    )
    last_management_error = models.TextField(blank=True, default="")
    managed_at = models.DateTimeField(null=True, blank=True)

    delivery_mode = models.CharField(
        max_length=32,
        choices=DeliveryMode.choices,
        default=DeliveryMode.DIRECT_API,
    )
    delivery_config = models.JSONField(default=dict, blank=True)
    """Per-cluster delivery settings (GitOps repo URL + branch +
    path prefix for the GitOps modes; nothing for direct_api).
    Shape is mode-specific — see services/delivery for what each
    mode reads."""

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tenant_cluster_slug_unique_active",
            ),
        ]
