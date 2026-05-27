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

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

from _sdk._telemetry import driver_op, maybe_heartbeat
from _sdk.cluster import (
    ApplyError,
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
    classify_apply_error,
)

# Shared helper re-exports. ``_RealK8sClient`` + ``_NotFoundError`` are
# the names this module's call sites use; ``_DEFAULT_API_VERSION_FOR_KIND``,
# ``_PortForwardHandle``, ``_split_kind`` are kept as re-exports because
# ``tests/aws/test_real_k8s_client.py`` imports them from here (the
# helper-body tests live under ``tests/_sdk/`` now; the AWS test file
# is preserved as-is so the refactor doesn't churn 36 tests).
from _sdk.k8s_dynamic_client import (
    DEFAULT_API_VERSION_FOR_KIND as _DEFAULT_API_VERSION_FOR_KIND,  # noqa: F401
)
from _sdk.k8s_dynamic_client import (
    KubernetesDynamicClient as _RealK8sClient,
)
from _sdk.k8s_dynamic_client import (
    NotFoundError as _NotFoundError,
)
from _sdk.k8s_dynamic_client import (
    PortForwardHandle as _PortForwardHandle,  # noqa: F401
)
from _sdk.k8s_dynamic_client import (
    split_kind as _split_kind,  # noqa: F401
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

    sts_token_lifetime_seconds: int = 900
    """How long to ask STS to make the presigned URL valid for. EKS's
    IAM authenticator caps at 15 minutes (900s); the k8s client does
    not auto-retry on 401, so a longer window matches the EKS
    GetToken upper bound and avoids spurious mid-operation auth
    failures on first-rollout deploys (#359)."""

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
                expires_in_seconds=self._config.sts_token_lifetime_seconds,
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

    @driver_op(cloud="aws", driver="cluster")
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
        errors: list[ApplyError] = []

        for manifest in manifests:
            # Per-manifest heartbeat: a 100-manifest apply against a slow
            # cluster easily exceeds the default activity start_to_close
            # timeout otherwise (#595-#598).
            maybe_heartbeat(f"cluster.apply_manifests:{cluster}")
            kind = manifest.get("kind", "")
            name = manifest.get("metadata", {}).get("name", "")
            try:
                outcome = client.server_side_apply(
                    namespace=namespace,
                    manifest=manifest,
                    dry_run=dry_run,
                )
            except Exception as exc:
                errors.append(
                    ApplyError(
                        kind=kind,
                        name=name,
                        namespace=namespace,
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        is_retryable=classify_apply_error(exc),
                    )
                )
                continue
            ref = f"{kind}/{name}"
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

    @driver_op(cloud="aws", driver="cluster")
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
            except _NotFoundError:
                not_found.append(ref)
            except Exception as exc:
                errors.append(f"{ref}: {exc}")
        return DeleteResult(
            deleted=deleted,
            not_found=not_found,
            errors=errors,
        )

    # ---- namespaces -----------------------------------------------

    @driver_op(cloud="aws", driver="cluster")
    def get_namespace(
        self,
        cluster: str,
        name: str,
    ) -> NamespaceState | None:
        client = self._k8s(cluster)
        try:
            ns = client.get_namespace(name=name)
        except _NotFoundError:
            return None
        except Exception as exc:
            raise RuntimeError(f"get_namespace {name}: {exc}") from exc
        return NamespaceState(
            name=ns["metadata"]["name"],
            labels=ns["metadata"].get("labels", {}) or {},
            annotations=ns["metadata"].get("annotations", {}) or {},
            phase=ns.get("status", {}).get("phase", "Active"),
        )

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.delete_namespace")
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
        except _NotFoundError:
            return
        except Exception as exc:
            raise RuntimeError(f"delete_namespace {name}: {exc}") from exc

        if not wait:
            return
        # Poll until the namespace is gone (k8s finalizers can take
        # minutes for namespaces with PVCs / webhooks). Heartbeat per
        # iteration so the Temporal activity stays alive across the full
        # 10-minute window (#595-#598).
        deadline = time.monotonic() + 600  # 10 minutes
        while time.monotonic() < deadline:
            maybe_heartbeat(f"cluster.delete_namespace:{name}")
            existing = self.get_namespace(cluster=cluster, name=name)
            if existing is None:
                return
            time.sleep(2)
        raise TimeoutError(
            f"namespace {name} did not delete within 10m",
        )

    # ---- workload status ------------------------------------------

    @driver_op(cloud="aws", driver="cluster")
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
        except _NotFoundError as exc:
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

    @driver_op(cloud="aws", driver="cluster")
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
            # Per-poll heartbeat keeps the wrapping Temporal activity
            # alive across the full ``timeout`` (default 10m). The
            # entry-time heartbeat from ``@driver_op`` alone isn't
            # enough on long rollouts (#595-#598).
            maybe_heartbeat(f"cluster.poll_rollout:{kind}/{name}")
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

    @driver_op(cloud="aws", driver="cluster")
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
        except _NotFoundError as exc:
            raise NotFoundError(
                f"pod {pod} in namespace {namespace}",
            ) from exc

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        return probe_cluster_capabilities(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
        )

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.bring_into_management")
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

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.teardown_cluster")
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
            # Heartbeat before the boto3 waiter: it blocks for up to 15
            # minutes by default and Temporal would time out the activity
            # without it (#595).
            maybe_heartbeat("cluster.teardown_cluster:wait_nodegroups")
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

    @driver_op(cloud="aws", driver="cluster")
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
                    "clusterName": "",  # resolved at install time from cluster row
                    "serviceAccount": {"create": True, "annotations": {}},
                },
                requires=["irsa:aws-load-balancer-controller"],
                options=[],
                chart_name="aws-load-balancer-controller",
                chart_repo_url="https://aws.github.io/eks-charts",
                chart_repo_type="default",
                chart_version="1.9.2",
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
                    "provider": "aws",
                    "sources": ["service", "ingress"],
                    "serviceAccount": {"create": True, "annotations": {}},
                },
                requires=["irsa:external-dns", "route53_zone_id"],
                options=[],
                chart_name="external-dns",
                chart_repo_url="https://kubernetes-sigs.github.io/external-dns/",
                chart_repo_type="default",
                chart_version="1.14.5",
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=True,
                rationale=(
                    "Required for HorizontalPodAutoscaler. EKS doesn't ship "
                    "it as a managed addon (the upstream chart is what we use)."
                ),
                helm_values={},
                requires=[],
                options=[],
                chart_name="metrics-server",
                chart_repo_url="https://kubernetes-sigs.github.io/metrics-server/",
                chart_repo_type="default",
                chart_version="3.12.2",
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
                requires=["storage:gp3"],
                options=[],
                chart_name="kube-prometheus-stack",
                chart_repo_url="https://prometheus-community.github.io/helm-charts",
                chart_repo_type="default",
                chart_version="65.1.0",
            ),
            BootstrapComponent(
                key="cert-manager",
                title="cert-manager (in-cluster TLS, internal mTLS)",
                default_enabled=False,
                rationale=(
                    "EKS operators typically use ACM via ALB annotations for "
                    "public TLS — no in-cluster cert controller needed. Enable "
                    "cert-manager only when you need internal mTLS, webhook "
                    "certificates, or non-ALB cert flows."
                ),
                helm_values={
                    "installCRDs": True,
                },
                requires=[],
                options=[
                    BootstrapOption(
                        key="mode",
                        label="Issuer",
                        choices=[
                            ("self_signed", "Self-signed (internal / mTLS)"),
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (Route53 DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                        ],
                        default="self_signed",
                    ),
                ],
                chart_name="cert-manager",
                chart_repo_url="https://charts.jetstack.io",
                chart_repo_type="default",
                chart_version="v1.16.3",
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

    @driver_op(cloud="aws", driver="cluster", heartbeat=False)
    def invalidate_describe_cache(self, cluster_name: str | None = None) -> None:
        """Drop a cached DescribeCluster entry. Operators rotating
        the cluster CA can call this; the next observability call
        re-fetches. Passing None clears the whole cache."""
        if cluster_name is None:
            self._describe_cache.clear()
        else:
            self._describe_cache.pop(cluster_name, None)

    def _synthesize_token_auth(
        self,
        *,
        cluster_name: str,
        region: str,
    ) -> tuple[dict[str, Any], str]:
        """Build ``(auth_config, endpoint)`` for a ``service_account_token``-
        shaped ClusterAuth.

        Returns the bearer token + base64 CA in the shape
        ``build_api_client``'s ``service_account_token`` branch consumes
        (``{"token": <bearer>, "ca_cert": <pem-or-base64>}``) plus the
        cluster API endpoint so the caller can stamp it onto the
        replaced ClusterAuth / ClusterContext.

        Why not ``kubeconfig``: ``kubernetes.config.load_kube_config_from_dict``
        populates ``Configuration.api_key["authorization"]`` correctly,
        but the ApiClient's request dispatch silently drops the
        Authorization header for inline-``token`` users when the
        kubeconfig has no exec stanza — every request goes out unauthed
        and EKS returns 401. The bypass-kubeconfig pattern (direct
        Configuration object with ``host``/``ssl_ca_cert``/``api_key``)
        is what the python kubernetes-client actually ships working for
        EKS Bearer auth.
        """
        describe = self._cached_describe(cluster_name)
        token = self._cached_token(cluster_name, region)
        return (
            {"token": token, "ca_cert": describe.ca_data},
            describe.endpoint,
        )

    def _resolve_eks_auth_context(self, cluster: ClusterContext) -> ClusterContext:
        if cluster.auth_method != "exec_plugin":
            return cluster
        import dataclasses

        cfg = cluster.auth_config or {}
        cluster_name, region = self._resolve_eks_target(
            cfg.get("cluster_name"),
            cfg.get("region"),
        )
        auth_config, endpoint = self._synthesize_token_auth(
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            cluster,
            auth_method="service_account_token",
            auth_config=auth_config,
            endpoint=endpoint,
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
        auth_config, endpoint = self._synthesize_token_auth(
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            auth,
            auth_method="service_account_token",
            auth_config=auth_config,
            endpoint=endpoint,
        )

    # ---- Cluster health (#68 slice 1) -----------------------------

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception:
            return []
        return pod_phase_summary_from_client(
            client,
            namespaces=default_namespaces(namespaces),
        )

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception:
            return []
        return events_from_client(
            client,
            namespaces=default_namespaces(namespaces),
            event_type=event_type,
            limit=limit,
        )

    @driver_op(cloud="aws", driver="cluster")
    def list_workload_health(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
    ):
        from _sdk._kube_health import (
            default_namespaces,
            workload_health_from_client,
        )

        try:
            client = self._k8s(cluster.slug)
        except Exception:
            return []
        return workload_health_from_client(
            client,
            namespaces=default_namespaces(namespaces),
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
        so we ask for the EKS ceiling (``sts_token_lifetime_seconds``,
        default 900) to avoid spurious mid-operation auth failures
        on first-rollout deploys (#359)."""
        try:
            return mint_eks_token(
                cluster_name=self._config.cluster_name,
                region=self._config.region,
                expires_in_seconds=self._config.sts_token_lifetime_seconds,
            )
        except Exception as exc:
            raise map_client_error(exc) from exc


# ---- Internal client + exception types -----------------------------
#
# The DynamicClient wrapper + helpers (``_split_kind``,
# ``_DEFAULT_API_VERSION_FOR_KIND``, ``_PortForwardHandle``) used to
# live in this module. They were extracted into
# ``_sdk/k8s_dynamic_client.py`` (the audit-recommended shared helper,
# closes #566 / #567 / #568) so every cloud cluster driver can wire its
# auth shim to the same body without copy-pasting 400 lines of
# DynamicClient plumbing per cloud.
#
# What remains AWS-specific in this file is the bearer-token mint
# (``_eks_token`` above — presigned STS GetCallerIdentity) and the
# base64-encoded CA shape EKS hands back from DescribeCluster (the
# shared helper handles the decode for the EKS / GKE wire shape).

# Re-exports (``_RealK8sClient``, ``_DEFAULT_API_VERSION_FOR_KIND``,
# ``_NotFoundError``, ``_PortForwardHandle``, ``_split_kind``) live at
# the top of the file alongside the rest of the imports — kept here
# as a comment so future readers find the alias map without grep.
#
# Callers in this module catch ``_NotFoundError`` (== the shared
# helper's ``NotFoundError``) on resolver paths.


def _build_k8s_client(
    *,
    endpoint: str,
    ca_data: str,
    token_provider: Callable[[], str],
) -> Any:
    """Default factory that returns a real kubernetes client wrapper.

    Tests inject their own factory via EKSClusterDriver's
    ``k8s_client_factory`` arg so we don't need a live apiserver. The
    EKS wire hands us ``ca_data`` base64-encoded; the shared helper
    handles the decode.
    """
    return _RealK8sClient(
        endpoint=endpoint,
        ca_data=ca_data,
        token_provider=token_provider,
    )
