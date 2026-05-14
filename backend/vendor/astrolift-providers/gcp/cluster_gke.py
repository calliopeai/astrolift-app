"""GKE ClusterDriver (#36).

Same shape as EKSClusterDriver — auth via google-cloud-container
DescribeCluster (endpoint + CA), then kubernetes-client for k8s
API ops. Tokens come from the local default GCP credentials
(ADC) which auto-refreshes.
"""

from __future__ import annotations

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
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    RolloutResult,
    TeardownReport,
    WorkloadStatus,
)
from gcp._errors import NotFoundError, map_api_error
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


class _NotFound(Exception):
    pass


@dataclass(frozen=True)
class GKEConfig:
    project_id: str
    location: str
    """Region or zone (e.g. us-central1 for regional, us-central1-a for zonal)."""

    cluster_name: str
    container_client: Any | None = None


class GKEClusterDriver(ClusterDriver):
    def __init__(
        self,
        *,
        config: GKEConfig,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
    ) -> None:
        self._config = config
        if config.container_client is not None:
            self._gke = config.container_client
        else:
            from google.cloud import container_v1

            self._gke = container_v1.ClusterManagerClient()
        self._factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). The listing
        # + log-streaming path is cloud-neutral as soon as the
        # ClusterAuth blob is in hand; the GKE-specific Workload
        # Identity exec_plugin token mint lives in #310 follow-up.
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). Inherits k8s_native's body
        # via the management backend; the GKE Workload Identity
        # token mint is irrelevant for kubeconfig + service-account
        # auth, both of which go straight through to ``build_api_client``.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )

    def apply_manifests(
        self,
        cluster,
        namespace,
        manifests,
        *,
        dry_run=False,
    ):
        client = self._k8s(cluster)
        created, updated, unchanged, errors = [], [], [], []
        for m in manifests:
            ref = f"{m.get('kind', '')}/{m.get('metadata', {}).get('name', '')}"
            try:
                outcome = client.server_side_apply(
                    namespace=namespace,
                    manifest=m,
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

    def delete_manifests(self, cluster, namespace, manifests):
        client = self._k8s(cluster)
        deleted, not_found, errors = [], [], []
        for m in manifests:
            kind = m.get("kind", "")
            name = m.get("metadata", {}).get("name", "")
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

    def get_namespace(self, cluster, name):
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

    def ensure_namespace(self, cluster, name, labels, annotations):
        client = self._k8s(cluster)
        client.server_side_apply(
            namespace=None,
            manifest={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": name,
                    "labels": labels,
                    "annotations": annotations,
                },
            },
            dry_run=False,
        )
        return Namespace(
            name=name,
            labels=dict(labels),
            annotations=dict(annotations),
        )

    def delete_namespace(self, cluster, name, *, wait=True):
        import time

        client = self._k8s(cluster)
        try:
            client.delete(kind="Namespace", namespace=None, name=name)
        except _NotFound:
            return
        if not wait:
            return
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self.get_namespace(cluster=cluster, name=name) is None:
                return
            time.sleep(2)
        raise TimeoutError(f"namespace {name} did not delete within 10m")

    def get_workload_status(self, cluster, namespace, kind, name):
        client = self._k8s(cluster)
        try:
            obj = client.get(kind=kind, namespace=namespace, name=name)
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
        cluster,
        namespace,
        kind,
        name,
        timeout,
        *,
        on_tick=None,
    ):
        import time

        if timeout <= 0:
            timeout = 600
        deadline = time.monotonic() + timeout
        last_status = None
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
                    message=f"{status.ready_replicas}/{status.desired_replicas} ready",
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
            message="rollout timed out",
            timed_out=True,
        )

    def exec_in_pod(self, cluster, namespace, pod, container, command):
        return self._k8s(cluster).exec_in_pod(
            namespace=namespace,
            pod=pod,
            container=container,
            command=command,
        )

    def port_forward(self, cluster, namespace, pod, ports):
        return self._k8s(cluster).port_forward(
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
        """Listing path is shared with k8s_native — the GKE-specific
        bit (Workload Identity exec_plugin token mint) lives in #310
        follow-up. For now, ``auth_method=kubeconfig`` /
        ``service_account_token`` go through directly."""
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
        """See ``list_pods`` — same shared k8s_native path."""
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
        return probe_cluster_capabilities(backend=self._management_backend, cluster=cluster)

    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=cluster,
            run_preflight=run_preflight,
        )

    # ---- cluster teardown (#337) ---------------------------------

    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Delete the GKE cluster + its node pools.

        ``delete_cloud_infra=False`` is a no-op for symmetry with the
        protocol — the cluster row is the platform's record; the GKE
        cluster itself stays running.

        When deleting, calls ``container.delete_cluster`` (which
        cascade-deletes node pools, persistent disks tagged with the
        cluster's autopilot/standard tag, etc.). VPC / subnets / IAM
        bindings are operator-owned and stay.

        Idempotent: a ``404`` on cluster lookup is treated as "already
        gone".
        """
        if not delete_cloud_infra:
            return TeardownReport(
                success=True,
                skipped=[f"gke/{self._config.cluster_name}"],
                messages=["delete_cloud_infra=false; GKE cluster left running"],
            )
        cluster_ref = (
            f"projects/{self._config.project_id}/locations/{self._config.location}/clusters/{self._config.cluster_name}"
        )
        try:
            self._container.delete_cluster(name=cluster_ref)
        except NotFoundError:
            return TeardownReport(
                success=True,
                skipped=[f"gke-cluster/{self._config.cluster_name} (already deleted)"],
                messages=["GKE cluster lookup returned 404 — already gone"],
            )
        except Exception as exc:
            return TeardownReport(
                success=False,
                error=f"container.delete_cluster failed: {map_api_error(exc)}",
            )
        return TeardownReport(
            success=True,
            deleted=[f"gke-cluster/{self._config.cluster_name}"],
            messages=[
                "container.delete_cluster submitted; node pools + tagged PDs "
                "cascade-delete; full deletion typically completes in 5-10 min",
            ],
        )

    # ---- bootstrap recipe ----------------------------------------

    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """GKE recipe — uses GCP-native paths where they're the
        easiest option, falls back to in-cluster controllers when
        they aren't.

        GKE already provides metrics + an ingress (GCE ingress
        controller) so those don't appear in the recipe by default;
        the operator opts in to nginx + metrics-server only when
        replacing the native pieces.
        """
        return [
            BootstrapComponent(
                key="tls_issuer",
                title="TLS certificate strategy",
                default_enabled=True,
                rationale=(
                    "GKE supports Google-managed SSL certs via the "
                    "ManagedCertificate CRD — least operational overhead. "
                    "cert-manager + ACME-LetsEncrypt available when "
                    "operators need a portable cert flow."
                ),
                helm_values={},
                requires=[],
                options=[
                    BootstrapOption(
                        key="mode",
                        label="Issuer",
                        choices=[
                            ("gke_managed", "Google-managed certs (recommended, GCE ingress)"),
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (cert-manager + Cloud DNS DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                            ("self_signed", "Self-signed (internal only)"),
                        ],
                        default="gke_managed",
                    ),
                ],
            ),
            BootstrapComponent(
                key="external-dns",
                title="external-dns (Cloud DNS)",
                default_enabled=True,
                rationale=(
                    "Auto-creates Cloud DNS records from Ingress + "
                    "Service annotations. Bound to a GCP service account "
                    "via Workload Identity."
                ),
                helm_values={
                    "external-dns": {
                        "enabled": True,
                        "provider": "google",
                        "sources": ["service", "ingress"],
                    },
                },
                requires=["workload_identity:external-dns", "clouddns_zone"],
                options=[],
            ),
            BootstrapComponent(
                key="kube-prometheus-stack",
                title="Prometheus + Grafana + Alertmanager",
                default_enabled=True,
                rationale=(
                    "Metrics scraping + dashboarding. Persistent disk via "
                    "the GKE-default pd-standard StorageClass; flip to "
                    "pd-ssd in helm values for higher write throughput."
                ),
                helm_values={
                    "kube-prometheus-stack": {
                        "enabled": True,
                        "grafana": {"enabled": True},
                    },
                },
                requires=["storage:rwo"],
                options=[],
            ),
        ]

    def _k8s(self, cluster: str) -> Any:
        if cluster in self._k8s_cache:
            return self._k8s_cache[cluster]
        endpoint, ca_data = self._describe_cluster()
        client = self._factory(endpoint=endpoint, ca_data=ca_data)
        self._k8s_cache[cluster] = client
        return client

    def _describe_cluster(self) -> tuple[str, str]:
        try:
            cluster = self._gke.get_cluster(
                name=(
                    f"projects/{self._config.project_id}"
                    f"/locations/{self._config.location}"
                    f"/clusters/{self._config.cluster_name}"
                ),
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
        return f"https://{cluster.endpoint}", cluster.master_auth.cluster_ca_certificate


def _build_k8s_client(*, endpoint: str, ca_data: str) -> Any:
    """Production wires this to kubernetes.dynamic.DynamicClient
    + google ADC for token. Tests inject a stub."""
    raise NotImplementedError("requires kubernetes-client wiring at deploy time")
