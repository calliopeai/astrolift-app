"""Vanilla Kubernetes ClusterDriver (#48 + #6).

Spec ref: spec 23-provider-plugin-k8s-native + _sdk/cluster.py.

Targets any standards-compliant k8s cluster (kind, minikube, k3d,
k3s, EKS-without-AWS-features, etc.). Auth via kubeconfig file or
in-cluster ServiceAccount token.

This driver is the structural twin of EKSClusterDriver — same
shape, same operations, but no boto3. Production use requires the
``kubernetes`` Python client (declared as the ``k8s`` extra).
"""

from __future__ import annotations

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
    ClusterEvent,
    DeleteResult,
    ExecResult,
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    PodPhaseSummary,
    PortForwardSession,
    RolloutResult,
    TeardownReport,
    WorkloadStatus,
)
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.k8s_dynamic_client import NotFoundError as _NotFound
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


class K8sNativeError(Exception):
    pass


# ``_NotFound`` is aliased to the shared helper's ``NotFoundError`` at
# the top-of-file imports so existing tests that construct raw
# ``_NotFound()`` instances keep working AND ``except _NotFound``
# clauses in this module catch what the wrapper raises after #566.


@dataclass(frozen=True)
class K8sNativeConfig:
    """Auth + connection config for the cluster.

    Exactly one of (kubeconfig_path, in_cluster) should be true at
    use time. ``context`` is optional and only meaningful for
    kubeconfig-based auth.
    """

    kubeconfig_path: str = ""
    """Path to a kubeconfig file. Empty = use in_cluster=True or
    rely on the default kubeconfig discovery (~/.kube/config or
    $KUBECONFIG)."""

    context: str = ""
    """Specific context within the kubeconfig to use. Empty =
    current-context."""

    in_cluster: bool = False
    """Use the projected SA token mounted into the pod (typical
    for control-plane workers running INSIDE a tenant cluster)."""

    rollout_timeout_default_seconds: int = 600


class K8sNativeClusterDriver(ClusterDriver):
    def __init__(
        self,
        *,
        config: K8sNativeConfig,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
    ) -> None:
        self._config = config
        self._factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). The defaults
        # use the live kubernetes client; tests inject fakes so the
        # observability path is exercisable without a real apiserver.
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management backend (#316). Same swap-on-construction
        # pattern as the observability backends; tests inject a fake
        # that records what would have been applied / probed / awaited.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )

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
                client.delete(kind=kind, namespace=namespace, name=name)
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
        client.server_side_apply(
            namespace=None,
            manifest=manifest,
            dry_run=False,
        )
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
        if not wait:
            return
        # Same finalizer-tolerant polling as EKS — namespaces with
        # PVCs / webhooks can take minutes
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self.get_namespace(cluster=cluster, name=name) is None:
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
            obj = client.get(kind=kind, namespace=namespace, name=name)
        except _NotFound as exc:
            raise K8sNativeError(
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
        if timeout <= 0:
            timeout = self._config.rollout_timeout_default_seconds
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
            except K8sNativeError:
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
        client = self._k8s(cluster)
        return client.exec_in_pod(
            namespace=namespace,
            pod=pod,
            container=container,
            command=command,
        )

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
        """Delegate to the (pluggable) pod backend. Subclasses for
        managed-cloud variants (EKS/GKE/AKS) override only when the
        auth path differs; the listing shape is cloud-neutral."""
        return self._pod_backend.list_pods(
            auth=auth,
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
        """Delegate to the (pluggable) log backend. Returns an
        async iterator; tear-down is the backend's responsibility."""
        return self._log_backend.stream(
            auth=auth,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    # ---- bring-into-management (#316) -----------------------------

    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        """Read-only capability probe. Used by the refresh path and
        also called from inside ``bring_into_management`` after the
        RBAC apply succeeds. Raises on auth / network failure."""
        return probe_cluster_capabilities(backend=self._management_backend, cluster=cluster)

    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        """Apply platform RBAC, probe capabilities, run a one-shot
        preflight Job. ``run_preflight=False`` is the refresh path —
        same RBAC reconcile + fresh probe but skip the Job."""
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=cluster,
            run_preflight=run_preflight,
        )

    # ---- bootstrap recipe ------------------------------------------

    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Bare-metal teardown is always a no-op: the platform never
        owned the nodes (operator-managed bare metal / k3s / kind / etc).

        The decommission workflow's cleanup of platform-owned cluster-
        side resources (RBAC, namespaces, registry secrets) is handled
        by the existing ``remove_platform_rbac`` activity — this
        method only covers cloud-managed-cluster destruction, which
        bare metal doesn't have."""
        return TeardownReport(
            success=True,
            skipped=[cluster.slug],
            messages=[
                "k8s_native (bare metal) clusters are operator-owned — the "
                "platform never provisioned the underlying nodes or control "
                "plane, so there's nothing to delete at the infrastructure "
                "level. The platform RBAC bundle is removed via the existing "
                "decommission workflow.",
            ],
        )

    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """Vanilla k8s recipe — assumes nothing the cloud provides.

        The operator must delegate a DNS subzone for external-dns and
        Let's Encrypt DNS-01 challenges (the platform doesn't try to
        manage public DNS roots for them). Storage defaults to
        Longhorn; MetalLB provides LoadBalancer Service IPs since
        bare-metal clusters don't get cloud LBs for free.
        """
        return [
            BootstrapComponent(
                key="tls_issuer",
                title="TLS certificate strategy",
                default_enabled=True,
                rationale=(
                    "Bare-metal clusters need cert-manager + an Issuer the "
                    "platform can drive. Let's Encrypt requires a real "
                    "delegated subzone for DNS-01; self-signed works for "
                    "internal-only traffic."
                ),
                helm_values={
                    "certManager": {"enabled": True, "installCRDs": True},
                    "clusterIssuer": {"enabled": True},
                },
                requires=["subzone:dns (for Let's Encrypt DNS-01)"],
                options=[
                    BootstrapOption(
                        key="mode",
                        label="Issuer",
                        choices=[
                            ("acme_letsencrypt_prod", "Let's Encrypt production"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                            ("self_signed", "Self-signed (internal only)"),
                        ],
                        default="acme_letsencrypt_prod",
                    ),
                ],
            ),
            BootstrapComponent(
                key="ingress-nginx",
                title="Ingress controller (nginx)",
                default_enabled=True,
                rationale=(
                    "Bare-metal clusters don't get a cloud ingress for free. "
                    "ingress-nginx is the platform's default; pair it with "
                    "MetalLB so its Service:LoadBalancer gets an IP."
                ),
                helm_values={"ingress-nginx": {"enabled": True}},
                requires=["metallb"],
                options=[],
            ),
            BootstrapComponent(
                key="metallb",
                title="MetalLB (LoadBalancer Service IP pool)",
                default_enabled=True,
                rationale=(
                    "Without MetalLB, Service:LoadBalancer requests on "
                    "bare-metal stay Pending forever. The operator must "
                    "configure an IPAddressPool with a routable range."
                ),
                helm_values={"metallb": {"enabled": True}},
                requires=["ip_pool: routable LB range"],
                options=[],
            ),
            BootstrapComponent(
                key="external-dns",
                title="external-dns (delegated subzone)",
                default_enabled=True,
                rationale=(
                    "Tenant apps need DNS records under a zone the cluster "
                    "controls. Operator delegates a subzone (e.g. "
                    "astrolift.example.com) to the cluster's DNS via NS "
                    "records; external-dns writes A/CNAME entries there."
                ),
                helm_values={
                    "external-dns": {
                        "enabled": True,
                        "provider": "rfc2136",
                        "sources": ["service", "ingress"],
                    },
                },
                requires=["subzone:dns (delegated)"],
                options=[
                    BootstrapOption(
                        key="provider",
                        label="DNS provider",
                        choices=[
                            ("rfc2136", "RFC 2136 dynamic update (BIND, knot, etc.)"),
                            ("pdns", "PowerDNS"),
                            ("cloudflare", "Cloudflare (API token)"),
                        ],
                        default="rfc2136",
                    ),
                ],
            ),
            BootstrapComponent(
                key="storage",
                title="Storage (Longhorn)",
                default_enabled=True,
                rationale=(
                    "Longhorn provides replicated block storage on bare-metal "
                    "node disks. Operators with existing CSI (Rook-Ceph, "
                    "TopoLVM, NFS) should flip this off and configure that "
                    "StorageClass separately."
                ),
                helm_values={
                    "storageClasses": {"enabled": True, "longhorn": {"enabled": True}},
                },
                requires=["node_disks: dedicated disk path on each node"],
                options=[],
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=True,
                rationale=(
                    "Required for HorizontalPodAutoscaler and ``kubectl top``. Not bundled in vanilla k8s distros."
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
                    "The platform's metrics scraping + dashboarding stack. "
                    "Disable if the operator points the OTel collector at an "
                    "external Prometheus / Mimir / Datadog instead."
                ),
                helm_values={
                    "kube-prometheus-stack": {
                        "enabled": True,
                        "grafana": {"enabled": True},
                    },
                },
                requires=["storage:rwo for Prom + Grafana PVs"],
                options=[],
            ),
        ]

    # ---- Cluster health (#68 slice 1) -----------------------------

    def list_pod_phase_summary(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
    ) -> list[PodPhaseSummary]:
        from _sdk._kube_health import (
            default_namespaces,
            pod_phase_summary_from_client,
        )

        try:
            client = self._k8s(cluster.slug)
        except Exception:
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
    ) -> list[ClusterEvent]:
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
            client, namespaces=default_namespaces(namespaces),
        )

    # ---- internals ------------------------------------------------

    def _k8s(self, cluster: str) -> Any:
        existing = self._k8s_cache.get(cluster)
        if existing is not None:
            return existing
        client = self._factory(
            kubeconfig_path=self._config.kubeconfig_path,
            context=self._config.context,
            in_cluster=self._config.in_cluster,
        )
        self._k8s_cache[cluster] = client
        return client


# ---- Internal client -----------------------------------------------
#
# Closes #566: the previous shape of this module was a real factory
# returning ``_RealK8sClient(api=client)`` whose every method raised
# ``NotImplementedError``. Operators running the BYOC / on-prem path
# would hit the first ``server_side_apply`` and die.
#
# Fix: wire kubeconfig / in-cluster auth into the shared
# ``KubernetesDynamicClient`` helper at ``_sdk/k8s_dynamic_client.py``.
# The shared helper carries the SSA + GET + DELETE + exec + port-
# forward body; this module's only job is to load the kubeconfig and
# hand the resulting ``ApiClient`` to ``from_api_client``.

# Re-export so resolver-side code that imports ``_RealK8sClient`` from
# this module keeps working. The Real class IS the shared helper now.
_RealK8sClient = KubernetesDynamicClient


def _build_k8s_client(
    *,
    kubeconfig_path: str,
    context: str,
    in_cluster: bool,
) -> Any:
    """Default factory that loads kubeconfig and wires the shared helper.

    Three auth paths:
      * ``in_cluster=True`` — ``load_incluster_config`` reads the SA
        token + CA from ``/var/run/secrets/kubernetes.io/serviceaccount``.
      * ``kubeconfig_path`` set — load from that file, optionally
        scoped to ``context``.
      * Neither — fall back to the default kubeconfig (``~/.kube/config``).

    All three paths populate ``client.Configuration().default()`` /
    pass the same auth shape; we then build an ``ApiClient`` from the
    resolved configuration and hand it to the shared helper via
    ``from_api_client``. The kubeconfig already carries the bearer or
    cert-based auth, so ``token_provider`` is a no-op and the shared
    helper's ``_refresh_token`` becomes inert (kubeconfig-based auth
    rotates externally — projected tokens are auto-refreshed by the
    kubelet, static tokens don't expire).
    """
    from kubernetes import client, config

    if in_cluster:
        config.load_incluster_config()
    elif kubeconfig_path:
        config.load_kube_config(
            config_file=kubeconfig_path,
            context=context or None,
        )
    else:
        config.load_kube_config(context=context or None)
    # ``load_*_config`` writes onto the singleton default configuration
    # (the python kubernetes client's convention). Snapshot it so
    # callers that rebuild a fresh ApiClient with overrides don't
    # mutate ours mid-flight.
    api_client = client.ApiClient(
        configuration=client.Configuration.get_default_copy(),
    )
    return KubernetesDynamicClient.from_api_client(api_client=api_client)


