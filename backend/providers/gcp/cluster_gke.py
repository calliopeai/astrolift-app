"""GKE ClusterDriver (#36).

Same shape as EKSClusterDriver — auth via google-cloud-container
DescribeCluster (endpoint + CA), then kubernetes-client for k8s
API ops. Tokens come from the local default GCP credentials
(ADC) which auto-refreshes.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

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
    JobStatus,
    ManagedModelNotSupportedError,
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    RegionInfo,
    RolloutResult,
    StorageClassInfo,
    TeardownReport,
    classify_apply_error,
    workload_status_from_object,
)
from _sdk.k8s_dynamic_client import (
    KubernetesDynamicClient as _RealK8sClient,
)
from _sdk.k8s_dynamic_client import (
    NotFoundError as _NotFound,
)
from gcp._errors import NotFoundError, map_api_error
from k8s_native.management import (
    ManagementBackend,
    default_management_backend,
    probe_cluster_capabilities,
    read_cluster_job_status,
    run_bring_into_management,
)
from k8s_native.observability import (
    ClusterAuthError,
    LivePodBackend,
    LogBackend,
    PodBackend,
    default_log_backend,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

# ``_NotFound`` is aliased to the shared helper's ``NotFoundError`` at
# the top-of-file imports so existing ``except _NotFound`` clauses keep
# catching what the wrapper raises after the #568 fix.


# Workload Identity bearer tokens minted via google.auth ADC are
# valid for ~60 minutes. We cache the synthesized kubeconfig blob
# slightly under that to absorb clock skew + give the next mint a
# wide margin before any in-flight k8s call would hit a 401. The
# cache key is the (project, location, cluster_name) triple — one
# entry per cluster the driver talks to.
_WI_KUBECONFIG_TTL_SECONDS = 50 * 60


# ---- Managed model (Vertex) defaults --------------------------------
#
# Default Vertex AI model ids injected on the managed-model agent path
# (Claude Code on Vertex reads ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL).
# A Claude Sonnet + Claude Haiku pair in Vertex's ``model@version`` form.
# Overridable per-cluster via ``provider_config["vertex_model_id"]`` /
# ``["vertex_small_fast_model_id"]``; the operator must have Claude model
# access enabled in the project + region.
_DEFAULT_VERTEX_MODEL_ID = "claude-sonnet-4@20250514"
_DEFAULT_VERTEX_SMALL_FAST_MODEL_ID = "claude-3-5-haiku@20241022"


# Curated GCP regions for the cluster-register picker (#860). Live
# compute.regions.list needs a project + Compute Engine API; the
# register path has neither, so this static list (slug, label,
# continent) backs the picker. Continent strings match the AWS/Azure
# tables so the UI buckets all clouds consistently.
_GCP_REGIONS: tuple[tuple[str, str, str], ...] = (
    ("us-central1", "Iowa", "Americas"),
    ("us-east1", "South Carolina", "Americas"),
    ("us-east4", "Northern Virginia", "Americas"),
    ("us-east5", "Columbus", "Americas"),
    ("us-south1", "Dallas", "Americas"),
    ("us-west1", "Oregon", "Americas"),
    ("us-west2", "Los Angeles", "Americas"),
    ("us-west3", "Salt Lake City", "Americas"),
    ("us-west4", "Las Vegas", "Americas"),
    ("northamerica-northeast1", "Montréal", "Americas"),
    ("northamerica-northeast2", "Toronto", "Americas"),
    ("northamerica-south1", "Mexico", "Americas"),
    ("southamerica-east1", "São Paulo", "Americas"),
    ("southamerica-west1", "Santiago", "Americas"),
    ("europe-central2", "Warsaw", "Europe"),
    ("europe-north1", "Finland", "Europe"),
    ("europe-southwest1", "Madrid", "Europe"),
    ("europe-west1", "Belgium", "Europe"),
    ("europe-west2", "London", "Europe"),
    ("europe-west3", "Frankfurt", "Europe"),
    ("europe-west4", "Netherlands", "Europe"),
    ("europe-west6", "Zürich", "Europe"),
    ("europe-west8", "Milan", "Europe"),
    ("europe-west9", "Paris", "Europe"),
    ("europe-west10", "Berlin", "Europe"),
    ("europe-west12", "Turin", "Europe"),
    ("asia-east1", "Taiwan", "Asia Pacific"),
    ("asia-east2", "Hong Kong", "Asia Pacific"),
    ("asia-northeast1", "Tokyo", "Asia Pacific"),
    ("asia-northeast2", "Osaka", "Asia Pacific"),
    ("asia-northeast3", "Seoul", "Asia Pacific"),
    ("asia-south1", "Mumbai", "Asia Pacific"),
    ("asia-south2", "Delhi", "Asia Pacific"),
    ("asia-southeast1", "Singapore", "Asia Pacific"),
    ("asia-southeast2", "Jakarta", "Asia Pacific"),
    ("australia-southeast1", "Sydney", "Asia Pacific"),
    ("australia-southeast2", "Melbourne", "Asia Pacific"),
    ("me-central1", "Doha", "Middle East"),
    ("me-central2", "Dammam", "Middle East"),
    ("me-west1", "Tel Aviv", "Middle East"),
    ("africa-south1", "Johannesburg", "Africa"),
)


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
        credentials_factory: Callable[[], tuple[Any, str | None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        if config.container_client is not None:
            self._gke = config.container_client
        else:
            from google.cloud import container_v1

            self._gke = container_v1.ClusterManagerClient()
        self._factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). The
        # ``exec_plugin`` row is materialized into a ``kubeconfig``
        # blob (with a freshly-minted Workload Identity bearer baked
        # in) before delegating, so the shared k8s_native backend
        # never has to know about GCP auth specifics (#310).
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). Inherits k8s_native's body
        # via the management backend; ``exec_plugin`` auth is
        # materialized into kubeconfig before the backend sees it,
        # same as the runtime-observability path.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )
        # Workload Identity token mint plumbing (#310). The
        # credentials factory is injected for tests; production
        # resolves via ``google.auth.default()`` which picks up the
        # Workload Identity binding when running inside a GCP-bound
        # pod and falls back to ADC otherwise. The clock is injected
        # so cache-expiry tests don't have to sleep.
        self._credentials_factory: Callable[[], tuple[Any, str | None]] = (
            credentials_factory or _default_credentials_factory
        )
        self._clock: Callable[[], float] = clock or time.monotonic
        # Maps (project, location, cluster_name) → (kubeconfig_yaml, expires_at)
        self._wi_kubeconfig_cache: dict[tuple[str, str, str], tuple[str, float]] = {}

    @driver_op(cloud="gcp", driver="cluster")
    def apply_manifests(
        self,
        cluster,
        namespace,
        manifests,
        *,
        dry_run=False,
    ):
        client = self._k8s(cluster)
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        errors: list[ApplyError] = []
        for m in manifests:
            maybe_heartbeat(f"cluster.apply_manifests:{cluster}")
            kind = m.get("kind", "")
            name = m.get("metadata", {}).get("name", "")
            try:
                outcome = client.server_side_apply(
                    namespace=namespace,
                    manifest=m,
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

    @driver_op(cloud="gcp", driver="cluster")
    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):
        client = self._k8s(cluster)
        deleted, not_found, errors = [], [], []
        for m in manifests:
            api_version = m.get("apiVersion", "")
            kind_bare = m.get("kind", "")
            # For CRDs (apiVersion is "group/version") construct the
            # "group/version/Kind" form that split_kind accepts.
            kind = f"{api_version}/{kind_bare}" if "/" in api_version else kind_bare
            name = m.get("metadata", {}).get("name", "")
            ref = f"{kind}/{name}"
            try:
                client.delete(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                    propagation_policy=propagation_policy,
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

    @driver_op(cloud="gcp", driver="cluster")
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

    @driver_op(cloud="gcp", driver="cluster")
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

    @driver_op(cloud="gcp", driver="cluster", audit=True, sensitive_kind="cluster.delete_namespace")
    def delete_namespace(self, cluster, name, *, wait=True):
        import time

        client = self._k8s(cluster)
        try:
            client.delete(kind="Namespace", namespace=None, name=name)
        except _NotFound:
            return
        if not wait:
            return
        # Heartbeat per iteration so the wrapping Temporal activity
        # stays alive across the 10-minute finalizer-wait (#596).
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            maybe_heartbeat(f"cluster.delete_namespace:{name}")
            if self.get_namespace(cluster=cluster, name=name) is None:
                return
            time.sleep(2)
        raise TimeoutError(f"namespace {name} did not delete within 10m")

    @driver_op(cloud="gcp", driver="cluster")
    def list_storage_classes(self, cluster: str) -> list[StorageClassInfo]:
        # The SDK default returns [], which reads as "this cluster has no
        # StorageClasses" rather than "this driver cannot enumerate them".
        # In-cluster stateful drivers (OpenSearch, SQL Server Express,
        # storage_class_pvc) refuse to provision on that answer, so a GKE
        # cluster could not book them once #1484 made them reachable.
        client = self._k8s(cluster)
        return [
            StorageClassInfo(
                name=(sc.get("metadata", {}) or {}).get("name", ""),
                is_default=(
                    ((sc.get("metadata", {}) or {}).get("annotations", {}) or {}).get(
                        "storageclass.kubernetes.io/is-default-class",
                    )
                    == "true"
                ),
                provisioner=str(sc.get("provisioner") or ""),
                reclaim_policy=str(sc.get("reclaimPolicy") or "Delete"),
            )
            for sc in client.list(kind="storage.k8s.io/v1/StorageClass")
        ]

    @driver_op(cloud="gcp", driver="cluster")
    def list_csi_drivers(self, cluster: str) -> list[str]:
        client = self._k8s(cluster)
        return sorted(
            str((row.get("metadata", {}) or {}).get("name") or "")
            for row in client.list(kind="storage.k8s.io/v1/CSIDriver")
            if (row.get("metadata", {}) or {}).get("name")
        )

    @driver_op(cloud="gcp", driver="cluster")
    def persistent_volume_claim_exists(self, cluster: str, namespace: str, name: str) -> bool:
        return self._k8s(cluster).get(kind="PersistentVolumeClaim", namespace=namespace, name=name) is not None

    @driver_op(cloud="gcp", driver="cluster")
    def get_manifest(
        self,
        cluster: str,
        namespace: str | None,
        kind: str,
        name: str,
    ) -> dict[str, Any] | None:
        return self._k8s(cluster).get(kind=kind, namespace=namespace, name=name)

    @driver_op(cloud="gcp", driver="cluster")
    def list_manifests(
        self,
        cluster: str,
        namespace: str | None,
        kind: str,
    ) -> list[dict[str, Any]]:
        return cast(
            "list[dict[str, Any]]",
            self._k8s(cluster).list(kind=kind, namespace=namespace),
        )

    @driver_op(cloud="gcp", driver="cluster")
    def get_workload_status(self, cluster, namespace, kind, name):
        client = self._k8s(cluster)
        try:
            obj = client.get(kind=kind, namespace=namespace, name=name)
        except _NotFound as exc:
            raise NotFoundError(
                f"{kind}/{name} in namespace {namespace}",
            ) from exc
        return workload_status_from_object(kind, name, namespace, obj)

    @driver_op(cloud="gcp", driver="cluster")
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
        while time.monotonic() < deadline:
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

    @driver_op(cloud="gcp", driver="cluster")
    def exec_in_pod(self, cluster, namespace, pod, container, command):
        return self._k8s(cluster).exec_in_pod(
            namespace=namespace,
            pod=pod,
            container=container,
            command=command,
        )

    @driver_op(cloud="gcp", driver="cluster")
    def port_forward(self, cluster, namespace, pod, ports):
        return self._k8s(cluster).port_forward(
            namespace=namespace,
            pod=pod,
            ports=ports,
        )

    # ---- runtime observability (#299) -----------------------------

    @driver_op(cloud="gcp", driver="cluster")
    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
    ) -> list[PodInfo]:
        """List pods on the GKE cluster.

        Materializes ``exec_plugin`` auth into a real bearer token
        (Workload Identity ADC) before delegating to the shared
        k8s_native pod backend. ``kubeconfig`` /
        ``service_account_token`` pass through unchanged.

        ``task_id`` (#891) selects an agent task pod by its
        ``astrolift.dev/task-id`` label; forwarded only when set so
        backends that predate the kwarg keep working.
        """
        kwargs: dict[str, Any] = {
            "auth": self._materialize_gke_auth_auth(auth),
            "namespace": namespace,
            "app_slug": app_slug,
        }
        if task_id:
            kwargs["task_id"] = task_id
        return self._pod_backend.list_pods(**kwargs)

    @driver_op(cloud="gcp", driver="cluster")
    def interactive_exec(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str,
        command: list[str],
        tty: bool = True,
    ) -> Any:
        """Open a streaming exec session on the GKE cluster (#1040).
        Materializes exec_plugin auth into a bearer token (as list_pods)
        before delegating to the shared k8s_native exec opener."""
        from providers.k8s_native.observability import open_interactive_exec

        return open_interactive_exec(
            auth=self._materialize_gke_auth_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            command=command,
            tty=tty,
        )

    @driver_op(cloud="gcp", driver="cluster")
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
            auth=self._materialize_gke_auth_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    # ---- bring-into-management (#316) -----------------------------

    @driver_op(cloud="gcp", driver="cluster")
    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        return probe_cluster_capabilities(
            backend=self._management_backend,
            cluster=self._materialize_gke_auth_context(cluster),
        )

    @driver_op(cloud="gcp", driver="cluster", audit=True, sensitive_kind="cluster.bring_into_management")
    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=self._materialize_gke_auth_context(cluster),
            run_preflight=run_preflight,
        )

    @driver_op(cloud="gcp", driver="cluster")
    def read_job_status(self, cluster: ClusterContext, *, namespace: str, job_name: str) -> JobStatus:
        """Read one Job's status (run-status reconciler). Read-only —
        materializes the GKE exec-plugin auth to a kubeconfig context first."""
        return read_cluster_job_status(
            backend=self._management_backend,
            cluster=self._materialize_gke_auth_context(cluster),
            namespace=namespace,
            job_name=job_name,
        )

    # ---- cluster teardown (#337) ---------------------------------

    @driver_op(cloud="gcp", driver="cluster", audit=True, sensitive_kind="cluster.teardown_cluster")
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

    @driver_op(cloud="gcp", driver="cluster")
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
                key="external-dns",
                title="external-dns (Cloud DNS)",
                default_enabled=True,
                rationale=(
                    "Auto-creates Cloud DNS records from Ingress + "
                    "Service annotations. Bound to a GCP service account "
                    "via Workload Identity."
                ),
                helm_values={
                    "provider": "google",
                    "sources": ["service", "ingress"],
                },
                requires=["workload_identity:external-dns", "clouddns_zone"],
                options=[],
                chart_name="external-dns",
                chart_repo_url="https://kubernetes-sigs.github.io/external-dns/",
                chart_repo_type="default",
                chart_version="1.14.5",
            ),
            BootstrapComponent(
                key="kube-prometheus-stack",
                title="Prometheus + Grafana + Alertmanager",
                default_enabled=True,
                rationale=(
                    "Metrics scraping + dashboarding. The default storage "
                    "mode is ephemeral (emptyDir) — 24h retention, TSDB "
                    "resets on pod restart. Select 'filestore_persistent' "
                    "to get 30d retention backed by a GCP Filestore NFS "
                    "volume; that requires the Filestore CSI driver and the "
                    "'filestore-prometheus' StorageClass applied from "
                    "Terraform output before running this recipe."
                ),
                helm_values={
                    "grafana": {"enabled": True},
                    "prometheus": {
                        "prometheusSpec": {
                            "retention": "24h",
                            "storageSpec": {},
                        },
                    },
                },
                requires=["storage:rwo"],
                options=[
                    BootstrapOption(
                        key="prometheus_storage",
                        label="Prometheus storage backend",
                        choices=[
                            (
                                "ephemeral",
                                "Ephemeral (emptyDir) — 24h retention, resets on pod restart",
                            ),
                            (
                                "filestore_persistent",
                                "Persistent Filestore — 30d retention, survives restarts; "
                                "requires Filestore CSI addon + filestore-prometheus StorageClass",
                            ),
                        ],
                        default="ephemeral",
                    ),
                ],
                chart_name="kube-prometheus-stack",
                chart_repo_url="https://prometheus-community.github.io/helm-charts",
                chart_repo_type="default",
                chart_version="65.1.0",
                install_timeout="15m",
            ),
            BootstrapComponent(
                key="cert-manager",
                title="cert-manager (non-GKE-managed TLS)",
                default_enabled=False,
                rationale=(
                    "GKE supports Google-managed SSL certs natively via "
                    "ManagedCertificate — least operational overhead. Enable "
                    "cert-manager only when you need portable ACME issuance "
                    "or internal mTLS outside the GCE ingress path."
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
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (Cloud DNS DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                            ("self_signed", "Self-signed (internal only)"),
                        ],
                        default="acme_letsencrypt_prod",
                    ),
                ],
                chart_name="cert-manager",
                chart_repo_url="https://charts.jetstack.io",
                chart_repo_type="default",
                chart_version="v1.16.3",
            ),
        ]

    # ---- Cluster health (#68 slice 1) -----------------------------

    @driver_op(cloud="gcp", driver="cluster")
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

    @driver_op(cloud="gcp", driver="cluster")
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

    @driver_op(cloud="gcp", driver="cluster")
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

    @driver_op(cloud="gcp", driver="cluster")
    def list_regions(self) -> list[RegionInfo]:
        """Curated static list of GCP regions for the register picker
        (#860).

        Live ``compute.regions.list`` needs a project id + the Compute
        Engine API enabled, neither of which is available on the
        no-cluster register path — so this returns a curated static
        list (GCP regions change infrequently). The frontend keeps
        free-entry on top for regions not listed here. Sorted by slug
        for a stable, scannable list.
        """
        return [RegionInfo(id=slug, label=label, continent=continent) for slug, label, continent in _GCP_REGIONS]

    # ---- managed model (Vertex auto-wire) -------------------------

    @driver_op(cloud="gcp", driver="cluster", heartbeat=False)
    def agent_model_env(self, *, region: str, provider_config: dict[str, Any]) -> dict[str, str]:
        """Vertex AI model env for the managed-model agent path.

        Returns the env a Claude Code runner reads to target Vertex
        instead of an ANTHROPIC_API_KEY: ``CLAUDE_CODE_USE_VERTEX=1`` +
        ``CLOUD_ML_REGION`` + ``ANTHROPIC_VERTEX_PROJECT_ID`` + the Sonnet
        / Haiku model ids. The project id comes from
        ``provider_config["project_id"]`` (falling back to the driver's
        configured project); model ids default to
        :data:`_DEFAULT_VERTEX_MODEL_ID` / :data:`_DEFAULT_VERTEX_SMALL_FAST_MODEL_ID`
        and are overridable via ``provider_config``. ``region`` falls back
        to the driver's configured location. Pure — no cloud call.

        Raises :class:`ManagedModelNotSupportedError` when no GCP project
        can be resolved (Vertex is project-scoped — an empty project can't
        target a model). NOTE: the Workload Identity *binding* the pod
        needs is not yet minted here — GKE managed model fails fast at
        ``ensure_agent_model_identity`` (the inherited default) until that
        wiring lands; this method exists so the env contract is defined.
        """
        provider_config = provider_config or {}
        project_id = str(provider_config.get("project_id") or self._config.project_id or "").strip()
        if not project_id:
            raise ManagedModelNotSupportedError(
                "managed model on GCP requires a project id (set provider_config['project_id'] on the cluster)",
            )
        model_id = str(provider_config.get("vertex_model_id") or _DEFAULT_VERTEX_MODEL_ID)
        small_fast = str(
            provider_config.get("vertex_small_fast_model_id") or _DEFAULT_VERTEX_SMALL_FAST_MODEL_ID,
        )
        return {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLOUD_ML_REGION": region or self._config.location,
            "ANTHROPIC_VERTEX_PROJECT_ID": project_id,
            "ANTHROPIC_MODEL": model_id,
            "ANTHROPIC_SMALL_FAST_MODEL": small_fast,
        }

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

    # ---- exec_plugin token materialization (#310) -----------------
    #
    # The k8s_native backend's ``build_api_client`` knows how to
    # handle ``kubeconfig`` + ``service_account_token`` auth but
    # raises on ``exec_plugin`` — GKE-specific token minting is the
    # cloud driver's job. We synthesize a kubeconfig blob carrying
    # the cluster's endpoint + CA + a freshly-minted Workload
    # Identity bearer (or ADC bearer when running off-cluster) and
    # rewrite the auth so the shared backend sees a plain
    # ``kubeconfig`` payload.
    #
    # Two helpers because ClusterContext (bring/probe) and
    # ClusterAuth (list_pods/stream_logs) are different frozen
    # dataclasses — dataclasses.replace is type-specific. The
    # kubeconfig synthesis logic is shared via
    # ``_mint_wi_kubeconfig``.

    def _mint_wi_kubeconfig(
        self,
        *,
        project_id: str | None,
        location: str | None,
        cluster_name: str | None,
    ) -> str:
        """Synthesize a kubeconfig YAML blob for the target cluster.

        Pulls endpoint + CA via ``container.get_cluster`` and mints a
        bearer via ``google.auth.default()`` + ``creds.refresh()``.
        Result is cached per ``(project, location, cluster)`` for
        ~50 minutes — GCP ADC tokens are typically 60-min valid so
        the cache absorbs the bulk of back-to-back resolver calls
        without re-refreshing.

        Raises ``ClusterAuthError`` on any failure (get_cluster 4xx,
        ADC resolution failure, refresh failure). The resolver layer
        maps that to an empty UI state.
        """
        proj = project_id or self._config.project_id
        loc = location or self._config.location
        name = cluster_name or self._config.cluster_name
        cache_key = (proj, loc, name)
        cached = self._wi_kubeconfig_cache.get(cache_key)
        if cached is not None and cached[1] > self._clock():
            return cached[0]

        # Describe the cluster first so a bogus row fails fast with a
        # clear error before we burn an ADC refresh round-trip.
        try:
            cluster_resp = self._gke.get_cluster(
                name=f"projects/{proj}/locations/{loc}/clusters/{name}",
            )
        except Exception as exc:
            raise ClusterAuthError(
                f"GKE get_cluster failed for projects/{proj}/locations/{loc}/clusters/{name}: {exc}",
            ) from exc
        endpoint = (
            f"https://{getattr(cluster_resp, 'endpoint', '') or ''}"
            if not str(getattr(cluster_resp, "endpoint", "")).startswith("http")
            else str(cluster_resp.endpoint)
        )
        master_auth = getattr(cluster_resp, "master_auth", None)
        ca_data = getattr(master_auth, "cluster_ca_certificate", "") if master_auth else ""

        try:
            creds, _project = self._credentials_factory()
        except Exception as exc:
            raise ClusterAuthError(
                f"google.auth.default() failed: {exc}",
            ) from exc
        try:
            _refresh_credentials(creds)
        except Exception as exc:
            raise ClusterAuthError(
                f"credentials refresh failed: {exc}",
            ) from exc
        token = getattr(creds, "token", None) or ""
        if not token:
            raise ClusterAuthError(
                "google.auth credentials returned an empty token after refresh",
            )

        context_name = f"gke_{proj}_{loc}_{name}"
        kubeconfig_dict = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": context_name,
                    "cluster": {
                        "server": endpoint,
                        "certificate-authority-data": ca_data,
                    },
                }
            ],
            "users": [
                {
                    "name": context_name,
                    "user": {"token": token},
                }
            ],
            "contexts": [
                {
                    "name": context_name,
                    "context": {"cluster": context_name, "user": context_name},
                }
            ],
            "current-context": context_name,
        }
        # ``yaml`` is already a transitive dep of the k8s_native
        # observability layer (used in ``build_api_client``); we
        # import inside the helper so unit tests for other GCP
        # drivers don't pay for the import on collection.
        import yaml

        kubeconfig_blob = yaml.safe_dump(kubeconfig_dict, sort_keys=False)
        self._wi_kubeconfig_cache[cache_key] = (
            kubeconfig_blob,
            self._clock() + _WI_KUBECONFIG_TTL_SECONDS,
        )
        return kubeconfig_blob

    def _materialize_gke_auth_auth(self, auth: ClusterAuth) -> ClusterAuth:
        if auth.auth_method != "exec_plugin":
            return auth
        import dataclasses

        cfg = auth.auth_config or {}
        kubeconfig_blob = self._mint_wi_kubeconfig(
            project_id=cfg.get("project_id"),
            location=cfg.get("location"),
            cluster_name=cfg.get("cluster_name"),
        )
        return dataclasses.replace(
            auth,
            auth_method="kubeconfig",
            auth_config={"kubeconfig": kubeconfig_blob},
        )

    def _materialize_gke_auth_context(self, cluster: ClusterContext) -> ClusterContext:
        if cluster.auth_method != "exec_plugin":
            return cluster
        import dataclasses

        cfg = cluster.auth_config or {}
        kubeconfig_blob = self._mint_wi_kubeconfig(
            project_id=cfg.get("project_id"),
            location=cfg.get("location"),
            cluster_name=cfg.get("cluster_name"),
        )
        return dataclasses.replace(
            cluster,
            auth_method="kubeconfig",
            auth_config={"kubeconfig": kubeconfig_blob},
        )


def _default_credentials_factory() -> tuple[Any, str | None]:
    """Production credentials factory.

    Resolves ADC via ``google.auth.default()`` — picks up Workload
    Identity automatically when running inside a GCP-bound pod, and
    falls back to user / SA-key credentials for off-cluster callers.
    Scope is the bare-minimum ``cloud-platform`` token the GKE
    apiserver accepts; broader scopes are forbidden by the WI
    binding anyway.
    """
    import google.auth

    return google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])


def _refresh_credentials(creds: Any) -> None:
    """Refresh ``creds`` against ``google.auth.transport.requests.Request``.

    Carved out so tests can pass credentials whose ``refresh`` is a
    plain mock without needing to vendor a fake transport. Imports
    are kept inside the function so a missing ``google-auth``
    install only bites at the point of use.
    """
    from google.auth.transport.requests import Request

    creds.refresh(Request())


def _build_k8s_client(
    *,
    endpoint: str,
    ca_data: str,
    credentials_factory: Callable[[], tuple[Any, str | None]] | None = None,
) -> Any:
    """Production factory: wire the shared k8s helper for GKE.

    Closes #568. GKE auth uses Workload Identity (or ADC for
    off-cluster callers); the existing ``_default_credentials_factory``
    + ``_refresh_credentials`` helpers below already resolve creds
    with the right scope. We wrap them into a ``token_provider``
    closure that refreshes creds and returns ``creds.token`` on each
    op so a single helper instance can survive across activities
    (GKE ADC tokens are typically 60-min valid; the refresh path is
    a cheap local rotation against ``google.auth.transport.requests``).

    ``ca_data`` here is the base64-encoded PEM ``get_cluster`` returns
    on ``master_auth.cluster_ca_certificate``; the shared helper
    decodes it. ``credentials_factory`` is injected by tests so we
    don't talk to real Google during unit-test runs.
    """
    factory = credentials_factory or _default_credentials_factory
    # Resolve creds once at construction so any auth misconfiguration
    # surfaces immediately rather than on the first op.
    creds, _project = factory()
    _refresh_credentials(creds)

    def _mint_token() -> str:
        # google-auth's ``creds.refresh`` rotates the cached token in
        # place; we re-call on every op to make sure the cached value
        # is fresh. The refresh is a single HTTP request against the
        # metadata server (inside GCP) or stsservice.googleapis.com
        # (off-cluster), so it's cheap to over-refresh.
        # On a transient refresh failure, fall back to the existing cached
        # token rather than failing the op. The kubernetes client will
        # surface the 401 if the token is genuinely expired, which the
        # resolver layer renders as an empty UI.
        with contextlib.suppress(Exception):
            _refresh_credentials(creds)
        return str(getattr(creds, "token", "") or "")

    return _RealK8sClient(
        endpoint=endpoint,
        ca_data=ca_data,
        token_provider=_mint_token,
    )


# ``_RealK8sClient`` is the shared helper at top-of-file imports —
# kept as a comment so future readers find the alias without grep.
