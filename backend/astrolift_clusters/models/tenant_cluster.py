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

    class NodeOs(models.TextChoices):
        """Operating system of the cluster's node pool (#80)."""

        LINUX = "linux"
        WINDOWS = "windows"
        MACOS = "macos"

    class NodeArch(models.TextChoices):
        """CPU architecture of the cluster's node pool (#80)."""

        AMD64 = "amd64"
        ARM64 = "arm64"

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
    alb_auth_config = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Cognito IDP config for ALB authenticate-cognito rules. "
            "Keys: user_pool_arn, user_pool_client_id, user_pool_domain. "
            "When set, every ALB Ingress rendered for this cluster carries "
            "the Cognito auth annotations. Null = no auth gate."
        ),
    )
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
    # Stamped by the ``provision_secrets_backend`` activity in the
    # bring-into-management workflow (#379) once the SecretsBackend
    # driver's one-time bootstrap (CSI install / KMS key / Vault auth)
    # has succeeded. Null while the backend hasn't been initialized or
    # when the driver reports it doesn't need initialization (the
    # activity records a ``skipped`` result without writing this).
    secrets_backend_provisioned_at = models.DateTimeField(null=True, blank=True)

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

    # Cluster keep-alive / heartbeat (#808).
    # ``api_key_hash`` is a SHA-256 of the secret token issued to the
    # in-cluster agent at registration time; it is stored hashed so
    # that a DB read does not expose the plaintext.  Null until the
    # cluster registers a heartbeat key.
    # ``last_heartbeat_at`` is stamped by POST
    # /api/dispatch/v1/clusters/<id>/heartbeat/.
    api_key_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    # Node pool platform capability fields (#80).
    # Used by the dispatch router (#82) for ``runs_on`` label matching.
    # Defaults are safe-guess values for existing clusters (linux/amd64);
    # operators can correct via mutation after the cluster is registered.
    node_os = models.CharField(
        max_length=16,
        choices=NodeOs.choices,
        default=NodeOs.LINUX,
        blank=True,
    )
    node_arch = models.CharField(
        max_length=8,
        choices=NodeArch.choices,
        default="",
        blank=True,
    )
    node_labels = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Arbitrary operator-defined labels for runs_on matching "
            "(e.g. ['gpu', 'high-memory', 'spot']). "
            "Case-insensitive in dispatch matching."
        ),
    )

    _HEARTBEAT_LIVE_SECONDS = 300  # 5 minutes

    @property
    def is_live(self) -> bool:
        """True when the cluster sent a heartbeat within the last 5 minutes."""
        if self.last_heartbeat_at is None:
            return False
        from django.utils import timezone
        delta = timezone.now() - self.last_heartbeat_at
        return delta.total_seconds() < self._HEARTBEAT_LIVE_SECONDS

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tenant_cluster_slug_unique_active",
            ),
        ]
