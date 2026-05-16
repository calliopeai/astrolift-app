"""AWS EKS ClusterDriver (#29).

Spec ref: spec 23-provider-plugin-aws + _sdk/cluster.py.

EKS auth flow: the driver calls boto3 EKS DescribeCluster to get
the cluster's API endpoint + CA, then calls EKS GetToken (via
the local AWS-IAM-authenticator helper or direct STS) to get a
short-lived bearer token. The kubernetes Python client uses
those to talk to the cluster's API server.

Token refresh: tokens expire ~15 minutes after signing. The
apply-manifests path mints fresh on every operation (high
amortized cost is fine for low-frequency lifecycle activities);
the runtime-observability path (``list_pods`` / ``stream_logs``,
#299) mints lazily and caches per (cluster_name, region) for
~13 minutes so back-to-back resolver calls don't pay the STS
round trip each time. Cache is in-process on the driver
instance — long-lived workers benefit; short CLI invocations
get a single mint anyway.

This module is the platform's authoritative way to apply
manifests, manage namespaces, and observe rollouts. Other
drivers (ALB ingress, managed services bind step) compose on
top of it.
"""

from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from _sdk.cluster import (
    ApplyResult,
    BootstrapComponent,
    BootstrapOption,
    ClusterAuth,
    ClusterContext,
    ClusterDriver,
    DeleteResult,
    ExecResult,
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    PortForwardSession,
    RolloutResult,
    TeardownReport,
    WorkloadStatus,
)
from aws._eks_auth import mint_eks_token
from aws._errors import NotFoundError, map_client_error
from k8s_native.management import (
    ManagementBackend,
    default_management_backend,
    probe_cluster_capabilities,
    run_bring_into_management,
)
from k8s_native.observability import (
    LivePodBackend,
    LogBackend,
    PodBackend,
    default_log_backend,
)


@dataclass(frozen=True)
class EKSConfig:
    """AWS-specific EKS auth + connection config."""

    region: str
    cluster_name: str
    """The EKS cluster name (used in DescribeCluster + GetToken).
    Distinct from the platform's logical 'cluster' identifier."""

    sts_token_lifetime_seconds: int = 60
    """How long to ask STS to make the token valid for. EKS caps
    at 14 minutes; we ask for 60s + re-fetch per operation."""

    exec_plugin_token_ttl_seconds: int = 13 * 60
    """How long the observability-path token cache holds a minted
    bearer before re-signing. The STS-presigned URL is valid for
    15 min by EKS protocol; 13 min gives a 2 min safety margin
    against clock skew + cluster-side acceptance windows."""


@dataclass
class _TokenCacheEntry:
    """One cached bearer token with its absolute expiry timestamp.

    ``expires_at`` is a monotonic-clock deadline so the cache is
    immune to wall-clock jumps (DST, NTP slew, container migration).
    """

    token: str
    expires_at: float


@dataclass
class _DescribeCacheEntry:
    """Endpoint + base64-CA cached from EKS DescribeCluster.

    Kept distinct from the bearer-token cache because DescribeCluster
    output rarely rotates (it changes when the operator rotates the
    cluster CA, which is rare and out-of-band). Cleared explicitly
    in ``invalidate_describe_cache`` if a caller ever needs to.
    """

    endpoint: str
    ca_data: str


class EKSClusterDriver(ClusterDriver):
    """boto3 + kubernetes-client backed EKS driver."""

    def __init__(
        self,
        *,
        config: EKSConfig,
        eks_client: Any | None = None,
        sts_client: Any | None = None,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
        token_minter: Callable[[str, str], str] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        if eks_client is not None:
            self._eks = eks_client
        else:
            import boto3

            self._eks = boto3.client("eks", region_name=config.region)
        if sts_client is not None:
            self._sts = sts_client
        else:
            import boto3

            self._sts = boto3.client("sts", region_name=config.region)
        # Factory injection lets tests pass a stubbed kubernetes
        # client without contacting a real apiserver.
        self._k8s_factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). Same shape
        # as K8sNativeClusterDriver — the listing + log-streaming
        # path is cloud-neutral as soon as the ClusterAuth blob is
        # in hand; what's EKS-specific is *how* the operator's
        # exec_plugin row gets turned into kubeconfig (#309).
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). The RBAC apply + capability
        # probe + preflight Job body is the k8s_native canonical
        # version; EKS auth is routed through the synthesized
        # ``kubeconfig`` blob the helpers below build, so the shared
        # backend's ``build_api_client`` is the only thing the probe
        # path needs to satisfy.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )
        # Token-mint injection lets tests substitute a recording
        # double for ``mint_eks_token`` without monkey-patching the
        # module. Default points at the production helper.
        self._token_minter: Callable[[str, str], str] = token_minter or (
            lambda cluster_name, region: mint_eks_token(
                cluster_name=cluster_name,
                region=region,
            )
        )
        # Monotonic clock is parametrized so the cache-TTL tests can
        # drive expiry without sleeping. ``time.monotonic`` is the
        # production source — wall-clock-jump-immune.
        self._clock: Callable[[], float] = monotonic_clock or time.monotonic
        # Per-(cluster_name, region) bearer-token cache for the
        # observability path. The apply-manifests path mints
        # per-operation via ``_eks_token`` and does NOT use this
        # cache — it's strictly for resolver-side call hot-loops.
        self._token_cache: dict[tuple[str, str], _TokenCacheEntry] = {}
        # Per-cluster_name describe-cache for endpoint + CA. Cleared
        # by ``invalidate_describe_cache`` on the rare CA rotation.
        self._describe_cache: dict[str, _DescribeCacheEntry] = {}

    # ---- apply / delete -------------------------------------------

    def apply_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> ApplyResult:
        client = self._k8s(cluster)
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        errors: list[str] = []

        for manifest in manifests:
            kind = manifest.get("kind", "")
            name = manifest.get("metadata", {}).get("name", "")
            ref = f"{kind}/{name}"
            try:
                outcome = client.server_side_apply(
                    namespace=namespace,
                    manifest=manifest,
                    dry_run=dry_run,
                )
            except Exception as exc:
                errors.append(f"{ref}: {exc}")
                continue
            if outcome == "created":
                created.append(ref)
            elif outcome == "updated":
                updated.append(ref)
            else:
                unchanged.append(ref)
        return ApplyResult(
            created=created,
            updated=updated,
            unchanged=unchanged,
            errors=errors,
        )

    def delete_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
    ) -> DeleteResult:
        client = self._k8s(cluster)
        deleted: list[str] = []
        not_found: list[str] = []
        errors: list[str] = []
        for manifest in manifests:
            kind = manifest.get("kind", "")
            name = manifest.get("metadata", {}).get("name", "")
            ref = f"{kind}/{name}"
            try:
                client.delete(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                )
                deleted.append(ref)
            except _NotFound:
                not_found.append(ref)
            except Exception as exc:
                errors.append(f"{ref}: {exc}")
        return DeleteResult(
            deleted=deleted,
            not_found=not_found,
            errors=errors,
        )

    # ---- namespaces -----------------------------------------------

    def get_namespace(
        self,
        cluster: str,
        name: str,
    ) -> NamespaceState | None:
        client = self._k8s(cluster)
        try:
            ns = client.get_namespace(name=name)
        except _NotFound:
            return None
        except Exception as exc:
            raise RuntimeError(f"get_namespace {name}: {exc}") from exc
        return NamespaceState(
            name=ns["metadata"]["name"],
            labels=ns["metadata"].get("labels", {}) or {},
            annotations=ns["metadata"].get("annotations", {}) or {},
            phase=ns.get("status", {}).get("phase", "Active"),
        )

    def ensure_namespace(
        self,
        cluster: str,
        name: str,
        labels: dict[str, str],
        annotations: dict[str, str],
    ) -> Namespace:
        client = self._k8s(cluster)
        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": name,
                "labels": labels,
                "annotations": annotations,
            },
        }
        try:
            client.server_side_apply(
                namespace=None,
                manifest=manifest,
                dry_run=False,
            )
        except Exception as exc:
            raise RuntimeError(f"ensure_namespace {name}: {exc}") from exc
        return Namespace(
            name=name,
            labels=dict(labels),
            annotations=dict(annotations),
        )

    def delete_namespace(
        self,
        cluster: str,
        name: str,
        *,
        wait: bool = True,
    ) -> None:
        client = self._k8s(cluster)
        try:
            client.delete(kind="Namespace", namespace=None, name=name)
        except _NotFound:
            return
        except Exception as exc:
            raise RuntimeError(f"delete_namespace {name}: {exc}") from exc

        if not wait:
            return
        # Poll until the namespace is gone (k8s finalizers can take
        # minutes for namespaces with PVCs / webhooks).
        deadline = time.monotonic() + 600  # 10 minutes
        while time.monotonic() < deadline:
            existing = self.get_namespace(cluster=cluster, name=name)
            if existing is None:
                return
            time.sleep(2)
        raise TimeoutError(
            f"namespace {name} did not delete within 10m",
        )

    # ---- workload status ------------------------------------------

    def get_workload_status(
        self,
        cluster: str,
        namespace: str,
        kind: str,
        name: str,
    ) -> WorkloadStatus:
        client = self._k8s(cluster)
        try:
            obj = client.get(
                kind=kind,
                namespace=namespace,
                name=name,
            )
        except _NotFound as exc:
            raise NotFoundError(
                f"{kind}/{name} in namespace {namespace}",
            ) from exc

        status = obj.get("status", {})
        spec = obj.get("spec", {})
        return WorkloadStatus(
            kind=kind,
            name=name,
            namespace=namespace,
            ready_replicas=int(status.get("readyReplicas", 0)),
            desired_replicas=int(
                spec.get("replicas", status.get("replicas", 0)),
            ),
            conditions=status.get("conditions", []) or [],
        )

    def poll_rollout(
        self,
        cluster: str,
        namespace: str,
        kind: str,
        name: str,
        timeout: int,
        *,
        on_tick: Callable[[WorkloadStatus], None] | None = None,
    ) -> RolloutResult:
        deadline = time.monotonic() + timeout
        last_status: WorkloadStatus | None = None
        while time.monotonic() < deadline:
            try:
                status = self.get_workload_status(
                    cluster=cluster,
                    namespace=namespace,
                    kind=kind,
                    name=name,
                )
            except NotFoundError:
                return RolloutResult(
                    success=False,
                    kind=kind,
                    name=name,
                    namespace=namespace,
                    message="workload not found",
                    timed_out=False,
                )
            last_status = status
            if on_tick:
                on_tick(status)
            if status.ready_replicas == status.desired_replicas and status.desired_replicas > 0:
                return RolloutResult(
                    success=True,
                    kind=kind,
                    name=name,
                    namespace=namespace,
                    message=(f"{status.ready_replicas}/{status.desired_replicas} ready"),
                    timed_out=False,
                )
            # Check for rollout failure conditions
            for cond in status.conditions:
                if (
                    cond.get("type") == "Progressing"
                    and cond.get(
                        "status",
                    )
                    == "False"
                ):
                    return RolloutResult(
                        success=False,
                        kind=kind,
                        name=name,
                        namespace=namespace,
                        message=cond.get("message", "Progressing=False"),
                        timed_out=False,
                    )
            time.sleep(min(15, max(1, timeout // 20)))
        return RolloutResult(
            success=False,
            kind=kind,
            name=name,
            namespace=namespace,
            message=(
                last_status.conditions[-1].get("message", "")
                if last_status and last_status.conditions
                else "rollout timed out"
            ),
            timed_out=True,
        )

    # ---- exec / port-forward --------------------------------------

    def exec_in_pod(
        self,
        cluster: str,
        namespace: str,
        pod: str,
        container: str,
        command: list[str],
    ) -> ExecResult:
        """Returns ExecResult; client wrapper handles the WS
        connection + stdout/stderr capture."""
        client = self._k8s(cluster)
        try:
            return client.exec_in_pod(
                namespace=namespace,
                pod=pod,
                container=container,
                command=command,
            )
        except _NotFound as exc:
            raise NotFoundError(
                f"pod {pod} in namespace {namespace}",
            ) from exc

    def port_forward(
        self,
        cluster: str,
        namespace: str,
        pod: str,
        ports: list[tuple[int, int]],
    ) -> PortForwardSession:
        client = self._k8s(cluster)
        return client.port_forward(
            namespace=namespace,
            pod=pod,
            ports=ports,
        )

    # ---- runtime observability (#299) -----------------------------

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]:
        """List pods on the EKS cluster.

        Materializes ``exec_plugin`` auth into a real bearer token
        (AWS-IAM-Authenticator presigned URL) before delegating to the
        shared k8s_native pod backend. ``kubeconfig`` /
        ``service_account_token`` pass through unchanged.
        """
        return self._pod_backend.list_pods(
            auth=self._resolve_eks_auth(auth),
            namespace=namespace,
            app_slug=app_slug,
        )

    def stream_logs(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[PodLogLine]:
        """See ``list_pods`` — same materialize-then-delegate pattern."""
        return self._log_backend.stream(
            auth=self._resolve_eks_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    # ---- bring-into-management (#316) -----------------------------

    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        return probe_cluster_capabilities(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
        )

    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
            run_preflight=run_preflight,
        )

    # ---- cluster teardown (#337) ---------------------------------

    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Delete the EKS cluster + its platform-tagged node groups +
        Fargate profiles.

        ``delete_cloud_infra=False`` is a no-op for symmetry with the
        protocol — the cluster row is the platform's record; the EKS
        cluster itself stays running.

        When deleting:
          1. Drain + delete all managed node groups owned by the
             cluster (EKS rejects ``delete_cluster`` while node groups
             exist).
          2. Delete every Fargate profile.
          3. Call ``eks.delete_cluster`` and wait for the cluster to
             reach DELETED.

        Idempotent: ``ResourceNotFoundException`` on any step is
        treated as "already gone" and contributes to ``deleted``
        rather than failing. VPC / subnets / IAM roles are NOT
        touched — those were provisioned outside the driver (in
        opscode TF / the operator's own IaC) and remain operator-owned.
        """
        if not delete_cloud_infra:
            return TeardownReport(
                success=True,
                skipped=[f"eks/{self._config.cluster_name}"],
                messages=["delete_cloud_infra=false; EKS cluster left running"],
            )

        deleted: list[str] = []
        skipped: list[str] = []
        messages: list[str] = []

        # Node groups first — EKS refuses cluster delete while these exist.
        try:
            node_groups = self._eks.list_nodegroups(clusterName=self._config.cluster_name).get(
                "nodegroups",
                [],
            )
        except Exception as exc:
            return TeardownReport(
                success=False,
                error=f"list_nodegroups failed: {exc}",
                deleted=deleted,
                skipped=skipped,
                messages=messages,
            )
        for ng in node_groups:
            try:
                self._eks.delete_nodegroup(
                    clusterName=self._config.cluster_name,
                    nodegroupName=ng,
                )
                deleted.append(f"nodegroup/{ng}")
            except self._eks.exceptions.ResourceNotFoundException:
                skipped.append(f"nodegroup/{ng} (already deleted)")
            except Exception as exc:
                return TeardownReport(
                    success=False,
                    error=f"delete_nodegroup {ng} failed: {exc}",
                    deleted=deleted,
                    skipped=skipped,
                    messages=messages,
                )

        # Wait for node groups to be gone before attempting cluster delete.
        if node_groups:
            try:
                self._eks.get_waiter("nodegroup_deleted").wait(
                    clusterName=self._config.cluster_name,
                    nodegroupName=node_groups[0],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 60},
                )
                messages.append(f"waited for {len(node_groups)} node group(s) to delete")
            except Exception as exc:
                messages.append(f"node group wait raised {exc}; continuing")

        # Fargate profiles next.
        try:
            fargate_profiles = self._eks.list_fargate_profiles(
                clusterName=self._config.cluster_name,
            ).get("fargateProfileNames", [])
        except Exception as exc:
            messages.append(f"list_fargate_profiles failed: {exc}; skipping")
            fargate_profiles = []
        for fp in fargate_profiles:
            try:
                self._eks.delete_fargate_profile(
                    clusterName=self._config.cluster_name,
                    fargateProfileName=fp,
                )
                deleted.append(f"fargate-profile/{fp}")
            except self._eks.exceptions.ResourceNotFoundException:
                skipped.append(f"fargate-profile/{fp} (already deleted)")
            except Exception as exc:
                return TeardownReport(
                    success=False,
                    error=f"delete_fargate_profile {fp} failed: {exc}",
                    deleted=deleted,
                    skipped=skipped,
                    messages=messages,
                )

        # Cluster itself. EKS will return ResourceInUseException if any
        # subresource is still terminating; the operator can retry.
        try:
            self._eks.delete_cluster(name=self._config.cluster_name)
            deleted.append(f"eks-cluster/{self._config.cluster_name}")
            messages.append("eks.delete_cluster submitted; full deletion takes ~10 min")
        except self._eks.exceptions.ResourceNotFoundException:
            skipped.append(f"eks-cluster/{self._config.cluster_name} (already deleted)")
        except Exception as exc:
            return TeardownReport(
                success=False,
                error=f"delete_cluster failed: {exc}",
                deleted=deleted,
                skipped=skipped,
                messages=messages,
            )

        return TeardownReport(
            success=True,
            deleted=deleted,
            skipped=skipped,
            messages=messages,
        )

    # ---- bootstrap recipe ----------------------------------------

    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """EKS recipe — leans on AWS-native services where they're the
        path of least resistance and falls back to in-cluster controllers
        only where AWS doesn't provide a managed equivalent.

        Native cloud paths:
          - TLS: ACM via aws-load-balancer-controller annotations on
            Ingress/Service (cert-manager only if the operator needs
            internal mTLS or non-ALB cert flows).
          - Storage: EBS CSI is an EKS managed addon (already provided
            in the prd-eks-astrolift TF; not in the bootstrap recipe).
          - Ingress: aws-load-balancer-controller renders Ingress as ALB.

        Auth wiring:
          - All controllers that need AWS API calls (LB controller,
            external-dns, cluster-autoscaler) bind to IRSA roles
            provisioned by the opscode TF. The recipe pre-fills the
            ServiceAccount annotations with the role ARN pattern; the
            install workflow resolves the actual ARN from the cluster
            row's provider_config before invoking helm.
        """
        return [
            BootstrapComponent(
                key="tls_issuer",
                title="TLS certificate strategy",
                default_enabled=True,
                rationale=(
                    "EKS clusters typically use ACM for public TLS via the "
                    "AWS Load Balancer Controller — operators issue certs "
                    "in ACM (cheap, auto-renewed) and reference them by "
                    "ARN on each Ingress. cert-manager is only needed for "
                    "internal mTLS or non-ALB cert flows."
                ),
                helm_values={
                    # 'acm' mode is the default — no chart values; the
                    # LB controller picks up cert ARNs from Ingress
                    # annotations operators set per-app.
                },
                requires=["aws-load-balancer-controller"],
                options=[
                    BootstrapOption(
                        key="mode",
                        label="Issuer",
                        choices=[
                            ("acm", "AWS Certificate Manager (recommended, ALB ingress)"),
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (cert-manager + Route53 DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging (testing)"),
                            ("self_signed", "Self-signed (internal traffic only)"),
                        ],
                        default="acm",
                    ),
                ],
            ),
            BootstrapComponent(
                key="aws-load-balancer-controller",
                title="AWS Load Balancer Controller",
                default_enabled=True,
                rationale=(
                    "Renders Kubernetes Ingress as AWS ALBs and Service "
                    "type=LoadBalancer as NLBs. EKS doesn't ship a default "
                    "Service controller; without this, ingress doesn't work. "
                    "Bound to its IRSA role via ServiceAccount annotation."
                ),
                helm_values={
                    "aws-load-balancer-controller": {
                        "enabled": True,
                        # cluster name + region resolved by the install
                        # workflow from the cluster row's auth_config.
                        "serviceAccount": {"create": True, "annotations": {}},
                    },
                },
                requires=["irsa:aws-load-balancer-controller"],
                options=[],
            ),
            BootstrapComponent(
                key="external-dns",
                title="external-dns (Route53)",
                default_enabled=True,
                rationale=(
                    "Auto-creates Route53 records from Ingress + Service "
                    "annotations. IRSA-bound; the install workflow scopes "
                    "the role's permissions to the operator's Route53 "
                    "hosted zone."
                ),
                helm_values={
                    "external-dns": {
                        "enabled": True,
                        "provider": "aws",
                        "sources": ["service", "ingress"],
                        "serviceAccount": {"create": True, "annotations": {}},
                    },
                },
                requires=["irsa:external-dns", "route53_zone_id"],
                options=[],
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=True,
                rationale=(
                    "Required for HorizontalPodAutoscaler. EKS doesn't ship "
                    "it as a managed addon (the upstream chart is what we use)."
                ),
                helm_values={"metricsServer": {"enabled": True}},
                requires=[],
                options=[],
            ),
            BootstrapComponent(
                key="kube-prometheus-stack",
                title="Prometheus + Grafana + Alertmanager",
                default_enabled=True,
                rationale=(
                    "Metrics scraping + dashboarding for the platform UI's "
                    "cluster-status charts. Prom storage backed by EBS gp3 "
                    "PVs (the EKS-installed default StorageClass)."
                ),
                helm_values={
                    "kube-prometheus-stack": {
                        "enabled": True,
                        "prometheus": {
                            "prometheusSpec": {
                                "storageSpec": {
                                    "volumeClaimTemplate": {
                                        "spec": {
                                            "storageClassName": "gp3",
                                            "accessModes": ["ReadWriteOnce"],
                                            "resources": {"requests": {"storage": "50Gi"}},
                                        },
                                    },
                                },
                            },
                        },
                        "grafana": {"enabled": True, "persistence": {"storageClassName": "gp3"}},
                    },
                },
                requires=["storage:gp3"],
                options=[],
            ),
        ]

    # ---- exec_plugin token materialization (#309) -----------------
    #
    # The k8s_native backend's ``build_api_client`` handles
    # ``kubeconfig`` + ``service_account_token`` auth natively but
    # raises on ``exec_plugin`` — EKS-specific token minting is the
    # cloud driver's job. We mint the bearer token via the
    # AWS-IAM-Authenticator protocol, fetch the cluster's endpoint +
    # CA via DescribeCluster, synthesize an in-memory kubeconfig
    # blob, and rewrite the auth payload so the shared backend sees
    # a plain ``kubeconfig`` row. The kubeconfig branch is the more
    # tolerant path (carries endpoint, CA, and bearer in one YAML
    # doc) and is what ``aws eks get-token`` users actually consume
    # via their kubeconfig file — so the resolver path matches the
    # operator's local kubectl path exactly.
    #
    # Two helpers because ClusterContext (for bring/probe) and
    # ClusterAuth (for list_pods/stream_logs) are different frozen
    # dataclasses — dataclasses.replace is type-specific. Both
    # converge on ``_synthesize_kubeconfig`` for the actual work.

    def _resolve_eks_target(
        self,
        cluster_name: str | None,
        region: str | None,
    ) -> tuple[str, str]:
        """Resolve the (cluster_name, region) to use for token mint +
        describe. Falls back to the driver's configured values for
        legacy TenantCluster rows that pre-date auto-discovery."""
        return (
            cluster_name or self._config.cluster_name,
            region or self._config.region,
        )

    def _cached_token(self, cluster_name: str, region: str) -> str:
        """Mint-or-return-cached bearer token for the (cluster, region).

        Tokens are cached for ``exec_plugin_token_ttl_seconds`` (13 min
        by default) against the monotonic clock so resolver hot-loops
        don't re-sign on every call. Re-minted on miss / expiry."""
        key = (cluster_name, region)
        now = self._clock()
        entry = self._token_cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.token
        try:
            token = self._token_minter(cluster_name, region)
        except Exception as exc:
            raise map_client_error(exc) from exc
        self._token_cache[key] = _TokenCacheEntry(
            token=token,
            expires_at=now + float(self._config.exec_plugin_token_ttl_seconds),
        )
        return token

    def _cached_describe(self, cluster_name: str) -> _DescribeCacheEntry:
        """Endpoint + base64-CA for the cluster, cached after first hit.

        ``ClusterAuthError`` from the shared backend is what the
        resolver layer expects on auth failure, but at this layer we
        raise via ``map_client_error`` so the caller sees a typed
        ``NotFoundError`` / ``ProviderError`` and can decide whether
        to log + swallow or surface."""
        cached = self._describe_cache.get(cluster_name)
        if cached is not None:
            return cached
        try:
            response = self._eks.describe_cluster(name=cluster_name)
        except Exception as exc:
            raise map_client_error(exc) from exc
        cluster_payload = response["cluster"]
        entry = _DescribeCacheEntry(
            endpoint=cluster_payload["endpoint"],
            ca_data=cluster_payload["certificateAuthority"]["data"],
        )
        self._describe_cache[cluster_name] = entry
        return entry

    def invalidate_describe_cache(self, cluster_name: str | None = None) -> None:
        """Drop a cached DescribeCluster entry. Operators rotating
        the cluster CA can call this; the next observability call
        re-fetches. Passing None clears the whole cache."""
        if cluster_name is None:
            self._describe_cache.clear()
        else:
            self._describe_cache.pop(cluster_name, None)

    def _synthesize_kubeconfig(
        self,
        *,
        slug: str,
        cluster_name: str,
        region: str,
    ) -> dict[str, Any]:
        """Build the auth_config blob (``{"kubeconfig": <yaml>}``)
        a synthesized ``ClusterAuth(auth_method='kubeconfig', ...)``
        carries through ``build_api_client``.

        The YAML shape is the standard EKS kubeconfig — one cluster,
        one user, one context — with the bearer token inlined under
        the user's ``token`` field. We deliberately do NOT emit an
        ``exec`` stanza pointing at ``aws eks get-token`` because the
        platform worker container doesn't ship the AWS CLI; inlining
        the token sidesteps that and matches what ``mint_eks_token``
        already does for in-process consumption."""
        import yaml

        describe = self._cached_describe(cluster_name)
        token = self._cached_token(cluster_name, region)
        user_name = f"astrolift-{slug}"
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": slug,
            "clusters": [
                {
                    "name": cluster_name,
                    "cluster": {
                        "server": describe.endpoint,
                        # CA is delivered base64-encoded by EKS;
                        # kubeconfig spec also expects base64 under
                        # ``certificate-authority-data`` — pass through
                        # unchanged.
                        "certificate-authority-data": describe.ca_data,
                    },
                },
            ],
            "users": [
                {
                    "name": user_name,
                    "user": {"token": token},
                },
            ],
            "contexts": [
                {
                    "name": slug,
                    "context": {
                        "cluster": cluster_name,
                        "user": user_name,
                    },
                },
            ],
        }
        return {"kubeconfig": yaml.safe_dump(kubeconfig, sort_keys=False)}

    def _resolve_eks_auth_context(self, cluster: ClusterContext) -> ClusterContext:
        if cluster.auth_method != "exec_plugin":
            return cluster
        import dataclasses

        cfg = cluster.auth_config or {}
        cluster_name, region = self._resolve_eks_target(
            cfg.get("cluster_name"),
            cfg.get("region"),
        )
        auth_config = self._synthesize_kubeconfig(
            slug=cluster.slug,
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            cluster,
            auth_method="kubeconfig",
            auth_config=auth_config,
        )

    def _resolve_eks_auth(self, auth: ClusterAuth) -> ClusterAuth:
        if auth.auth_method != "exec_plugin":
            return auth
        import dataclasses

        cfg = auth.auth_config or {}
        cluster_name, region = self._resolve_eks_target(
            cfg.get("cluster_name"),
            cfg.get("region"),
        )
        auth_config = self._synthesize_kubeconfig(
            slug=auth.slug,
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            auth,
            auth_method="kubeconfig",
            auth_config=auth_config,
        )

    # ---- Cluster health (#68 slice 1) -----------------------------

    def list_pod_phase_summary(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
    ):
        from _sdk._kube_health import (
            default_namespaces,
            pod_phase_summary_from_client,
        )

        try:
            client = self._k8s(cluster.slug)
        except Exception:  # noqa: BLE001
            return []
        return pod_phase_summary_from_client(
            client, namespaces=default_namespaces(namespaces),
        )

    def list_events(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
        event_type: str | None = "Warning",
        limit: int = 50,
    ):
        from _sdk._kube_health import default_namespaces, events_from_client

        try:
            client = self._k8s(cluster.slug)
        except Exception:  # noqa: BLE001
            return []
        return events_from_client(
            client,
            namespaces=default_namespaces(namespaces),
            event_type=event_type,
            limit=limit,
        )

    # ---- internals ------------------------------------------------

    def _k8s(self, cluster: str) -> Any:
        """Get / build the cached kubernetes client for the cluster.
        Tokens still get refreshed per-operation by the client wrapper."""
        existing = self._k8s_cache.get(cluster)
        if existing is not None:
            return existing
        endpoint, ca_data = self._describe_cluster()
        client = self._k8s_factory(
            endpoint=endpoint,
            ca_data=ca_data,
            token_provider=lambda: self._eks_token(),
        )
        self._k8s_cache[cluster] = client
        return client

    def _describe_cluster(self) -> tuple[str, str]:
        # Shared with the observability path's ``_cached_describe`` so
        # both the apply-manifests boto3 flow and the synthesize-
        # kubeconfig flow read the same canonical endpoint + CA pair.
        # ca_data is base64-encoded by EKS; the kubernetes client
        # handles the decode per its config shape.
        entry = self._cached_describe(self._config.cluster_name)
        return entry.endpoint, entry.ca_data

    def _eks_token(self) -> str:
        """Generate a short-lived EKS bearer token via the
        AWS-IAM-Authenticator protocol (presigned STS GetCallerIdentity
        URL with ``x-k8s-aws-id`` header). Re-minted per operation
        rather than cached; STS rejects URLs older than 15 minutes,
        but we set a 60s expiry so a leaked URL is useless quickly."""
        try:
            return mint_eks_token(
                cluster_name=self._config.cluster_name,
                region=self._config.region,
            )
        except Exception as exc:
            raise map_client_error(exc) from exc


# ---- Internal client + exception types -----------------------------


class _NotFound(Exception):
    """Raised by the k8s client wrapper when a resource doesn't exist."""


def _build_k8s_client(
    *,
    endpoint: str,
    ca_data: str,
    token_provider: Callable[[], str],
) -> Any:
    """Default factory that returns a real kubernetes client wrapper.

    Tests inject their own factory via EKSClusterDriver's
    ``k8s_client_factory`` arg so we don't need a live apiserver.
    """
    return _RealK8sClient(
        endpoint=endpoint,
        ca_data=ca_data,
        token_provider=token_provider,
    )


class _RealK8sClient:
    """Thin wrapper around kubernetes.dynamic + kubernetes.config
    so the rest of the driver doesn't import kubernetes at module
    level (keeps the import optional for non-AWS users)."""

    def __init__(
        self,
        *,
        endpoint: str,
        ca_data: str,
        token_provider: Callable[[], str],
    ) -> None:
        from kubernetes import client, config

        cfg = client.Configuration()
        cfg.host = endpoint
        cfg.api_key = {"authorization": f"Bearer {token_provider()}"}
        # Decode the EKS-supplied CA into a PEM file the client
        # can read. Kept on disk per kubernetes-client convention.
        import tempfile

        ca_pem = base64.b64decode(ca_data)
        ca_file = tempfile.NamedTemporaryFile(
            suffix=".crt",
            delete=False,
        )
        ca_file.write(ca_pem)
        ca_file.close()
        cfg.ssl_ca_cert = ca_file.name
        self._api_client = client.ApiClient(configuration=cfg)
        self._token_provider = token_provider
        self._config = cfg
        self._client_module = client
        self._k8s_config = config

    # The methods below would wrap kubernetes.dynamic +
    # kubernetes.client to provide server_side_apply / get / delete /
    # exec / port_forward. Deferred to integration testing against
    # a real cluster — unit-test path uses the injected factory.

    def server_side_apply(self, *, namespace, manifest, dry_run):
        raise NotImplementedError(
            "server_side_apply requires live cluster — wire via kubernetes.dynamic.DynamicClient at deploy time",
        )

    def get(self, *, kind, namespace, name):
        raise NotImplementedError("requires live cluster")

    def delete(self, *, kind, namespace, name):
        raise NotImplementedError("requires live cluster")

    def get_namespace(self, *, name):
        raise NotImplementedError("requires live cluster")

    def exec_in_pod(self, *, namespace, pod, container, command):
        raise NotImplementedError("requires live cluster")

    def port_forward(self, *, namespace, pod, ports):
        raise NotImplementedError("requires live cluster")
