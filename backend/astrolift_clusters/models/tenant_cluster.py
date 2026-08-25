"""
TenantCluster — a Kubernetes cluster registered with the platform.

Clusters are scoped per-org (``organization`` set) or shared
(``organization`` null). The control plane probes capabilities on
register / on a periodic refresh and caches the result so deploy
planning doesn't pay for a probe each time.
"""

from __future__ import annotations

from django.db import models

from astrolift_clusters.ingress_modes import IngressMode
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

    # The cloud account this cluster is actually in, proved rather than
    # declared (#1422). Written once, when the cluster is brought into
    # management and the credential in use is asked who it is; immutable
    # after, because a cluster cannot move between accounts — the control
    # plane would be building ARNs for one account against resources in
    # another, which is precisely the failure this closes.
    #
    # Distinct from `provider_config["account_id"]`, which is what the
    # operator *said*. That stays as the declaration; this is the answer.
    # Every ARN should be built from this one.
    cloud_account_id = models.CharField(max_length=64, blank=True, default="")
    cloud_account_verified_at = models.DateTimeField(null=True, blank=True)

    region = models.CharField(max_length=64, blank=True, default="")
    endpoint = models.URLField(blank=True, default="")
    ca_cert = models.TextField(blank=True, default="")
    auth_method = models.CharField(max_length=32, choices=AuthMethod.choices)
    auth_config = models.JSONField(default=dict, blank=True)
    ingress_class = models.CharField(max_length=64, default="nginx")
    # Ingress mode (#64). Decides how many cloud load balancers the
    # cluster ends up provisioning: ``shared_ingress`` puts every app in
    # an org into one ALB group, ``per_app_ingress`` leaves each Ingress
    # with its own load balancer.
    #
    # Defaults to per_app_ingress even though
    # ``astrolift_clusters.ingress_modes`` documents shared as the
    # cheaper default: nothing emitted a grouping annotation before this
    # field existed, so defaulting to shared would re-group load
    # balancers that are already serving traffic on the next deploy.
    # That has to be an operator's decision, not a migration's.
    ingress_mode = models.CharField(
        max_length=32,
        choices=[(m.value, m.value) for m in IngressMode],
        default=IngressMode.PER_APP_INGRESS.value,
        help_text=(
            "shared_ingress: one load balancer fronts every app in the org "
            "(ALB group annotation). per_app_ingress: one load balancer per "
            "app Ingress. Only honoured on ALB clusters -- an nginx-family "
            "controller already shares one load balancer per cluster."
        ),
    )
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
    oidc_auth_config = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Dex + oauth2-proxy OIDC config for k8s_native edge auth. "
            "Keys: discovery_url, client_id, cookie_secret, upstream_connector. "
            "When set, every nginx Ingress rendered for this cluster carries "
            "auth-url / auth-signin annotations pointing at the in-cluster "
            "oauth2-proxy. Null = no auth gate. Dex + oauth2-proxy must have "
            "been installed via the bootstrap recipe for this to function."
        ),
    )
    default_namespace_prefix = models.CharField(max_length=64, blank=True, default="")
    capabilities = models.JSONField(default=dict, blank=True)
    capabilities_probed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # ---- In-cluster agent heartbeat (#808) ----------------------------
    # The platform has no cheap liveness signal for a registered cluster:
    # every Status-tab card does a synchronous driver / Prometheus /
    # Temporal call, so the tabs hang when the apiserver is unreachable.
    # The keep-alive is a lightweight in-cluster agent (Deployment in the
    # astrolift-system namespace) that POSTs a heartbeat on a fixed
    # interval, signed with the scoped agent key below. The persisted
    # ``last_heartbeat_at`` + ``last_heartbeat_payload`` drive a derived
    # live-status badge (see ``heartbeat_status`` policy) that short-
    # circuits the expensive cards into a targeted offline empty-state.
    agent_key_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "SHA-256 of the scoped agent key the in-cluster keep-alive "
            "agent signs its heartbeat with. The plaintext is surfaced "
            "exactly once at issuance (issueClusterAgentKey); only the "
            "hash persists. Empty = no agent provisioned yet."
        ),
    )
    last_heartbeat_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Wall-clock time of the most recent agent heartbeat pulse.",
    )
    last_heartbeat_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Snapshot from the most recent heartbeat — node count, pod "
            "counts by namespace, CPU/memory utilization summary, ingress "
            "IPs, agent version. Free-form JSON so the agent version can "
            "evolve without a control-plane lockstep."
        ),
    )
    heartbeat_interval_seconds = models.IntegerField(
        default=30,
        help_text=(
            "Cadence (seconds) the agent should pulse at. Returned in the "
            "heartbeat response so the agent can self-tune. The derived "
            "live status treats a cluster as DEGRADED once it misses one "
            "interval and OFFLINE once it misses three."
        ),
    )

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
    # A list, not a single value (#1604). A cluster with amd64 and arm64
    # node groups is ordinary, and the old CharField could not say so: the
    # dispatch matcher did `cluster.node_arch not in required_arch`, which
    # forces every cluster to claim exactly one architecture or none. That
    # is why nothing ever wrote it -- the producer had no honest value to
    # write for a mixed fleet, so the column sat at its empty default and
    # every arch-labelled job routed nowhere.
    #
    # Populated by `reconcile_cluster_capabilities` from the cluster's own
    # nodes. Empty still means "unknown", and the matcher still treats
    # unknown as a non-match, because an unrecorded architecture is not
    # evidence a cluster is suitable.
    node_archs = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Architectures present across this cluster's nodes, e.g. "
            '["amd64", "arm64"]. Discovered on capability reconcile; empty '
            "means not yet probed."
        ),
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tenant_cluster_slug_unique_active",
            ),
        ]
