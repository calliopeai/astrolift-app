"""AKS ClusterDriver (#42).

Same shape as EKS / GKE — auth via ContainerServiceClient
list_cluster_admin_credentials (or user_credentials), parse the
resulting kubeconfig for endpoint + CA, then kubernetes-client for
k8s API ops. Tokens come from Azure CLI / Workload Identity at the
deployment site.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    RegionInfo,
    RolloutResult,
    TeardownReport,
    WorkloadStatus,
    classify_apply_error,
)
from _sdk.k8s_dynamic_client import (
    KubernetesDynamicClient as _RealK8sClient,
)
from _sdk.k8s_dynamic_client import (
    NotFoundError as _NotFound,
)
from azure._errors import NotFoundError, map_api_error
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

# AKS admin kubeconfig TTL. The blob carries a long-lived client
# certificate, but we re-fetch every ~50 min so a key-rotation event
# on the Azure side propagates without the operator restarting the
# control plane. 50 min is comfortably under the 60-min Azure AAD
# token refresh window and matches what kubelogin defaults to.
_AKS_KUBECONFIG_TTL_SECONDS = 50 * 60


# ``_NotFound`` is aliased to the shared helper's ``NotFoundError`` at
# the top-of-file imports so existing ``except _NotFound`` clauses keep
# catching what the wrapper raises after the #567 fix.


# Curated Azure regions for the cluster-register picker (#860). Live
# subscriptions.list_locations needs a subscription + authenticated
# management client; the register path has neither, so this static list
# (slug, label, continent) backs the picker. Continent strings match
# the AWS/GCP tables so the UI buckets all clouds consistently.
_AZURE_REGIONS: tuple[tuple[str, str, str], ...] = (
    ("eastus", "East US", "Americas"),
    ("eastus2", "East US 2", "Americas"),
    ("centralus", "Central US", "Americas"),
    ("northcentralus", "North Central US", "Americas"),
    ("southcentralus", "South Central US", "Americas"),
    ("westcentralus", "West Central US", "Americas"),
    ("westus", "West US", "Americas"),
    ("westus2", "West US 2", "Americas"),
    ("westus3", "West US 3", "Americas"),
    ("canadacentral", "Canada Central", "Americas"),
    ("canadaeast", "Canada East", "Americas"),
    ("brazilsouth", "Brazil South", "Americas"),
    ("mexicocentral", "Mexico Central", "Americas"),
    ("northeurope", "North Europe", "Europe"),
    ("westeurope", "West Europe", "Europe"),
    ("francecentral", "France Central", "Europe"),
    ("germanywestcentral", "Germany West Central", "Europe"),
    ("italynorth", "Italy North", "Europe"),
    ("norwayeast", "Norway East", "Europe"),
    ("polandcentral", "Poland Central", "Europe"),
    ("spaincentral", "Spain Central", "Europe"),
    ("swedencentral", "Sweden Central", "Europe"),
    ("switzerlandnorth", "Switzerland North", "Europe"),
    ("uksouth", "UK South", "Europe"),
    ("ukwest", "UK West", "Europe"),
    ("eastasia", "East Asia", "Asia Pacific"),
    ("southeastasia", "Southeast Asia", "Asia Pacific"),
    ("australiaeast", "Australia East", "Asia Pacific"),
    ("australiasoutheast", "Australia Southeast", "Asia Pacific"),
    ("centralindia", "Central India", "Asia Pacific"),
    ("southindia", "South India", "Asia Pacific"),
    ("japaneast", "Japan East", "Asia Pacific"),
    ("japanwest", "Japan West", "Asia Pacific"),
    ("koreacentral", "Korea Central", "Asia Pacific"),
    ("uaenorth", "UAE North", "Middle East"),
    ("qatarcentral", "Qatar Central", "Middle East"),
    ("israelcentral", "Israel Central", "Middle East"),
    ("southafricanorth", "South Africa North", "Africa"),
)


@dataclass(frozen=True)
class AKSConfig:
    subscription_id: str
    resource_group: str
    cluster_name: str
    container_service_client: Any | None = None
    use_federated_token: bool = False
    """Reserved for the Path B (federated-credential AAD token exchange)
    slice; today the driver always routes ``exec_plugin`` auth through
    ``list_cluster_admin_credentials`` regardless of this flag. The
    follow-on slice will wire the DefaultAzureCredential → AAD →
    kubelogin equivalent so private-cluster operators who've disabled
    admin credentials can still surface live pod state.
    """


class AKSClusterDriver(ClusterDriver):
    def __init__(
        self,
        *,
        config: AKSConfig,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        if config.container_service_client is not None:
            self._aks = config.container_service_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerservice import (
                ContainerServiceClient,
            )

            self._aks = ContainerServiceClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        self._factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). The listing
        # + log-streaming path is cloud-neutral as soon as the
        # ClusterAuth blob is in hand; the AKS-specific exec_plugin
        # token mint (#311) materializes ``exec_plugin`` rows into
        # ``kubeconfig`` before delegating.
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). Inherits k8s_native's body
        # via the management backend; ``exec_plugin`` rows are
        # materialized to ``kubeconfig`` before being routed through
        # the shared probe / preflight path so capability probes work
        # over the AKS-native auth dance.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )
        # exec_plugin → kubeconfig token cache (#311). Keyed by
        # (resource_group, cluster_name) so a single driver can serve
        # multiple TenantCluster rows that resolve to different AKS
        # managed clusters. ``clock`` is injected so tests can advance
        # past the TTL without sleeping.
        self._clock: Callable[[], float] = clock or time.monotonic
        self._kubeconfig_cache: dict[tuple[str, str], tuple[float, str]] = {}

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
    def delete_manifests(self, cluster, namespace, manifests):
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

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster", audit=True, sensitive_kind="cluster.delete_namespace")
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
            # Heartbeat per iteration so the wrapping Temporal activity
            # stays alive across the 10-minute finalizer-wait (#597).
            maybe_heartbeat(f"cluster.delete_namespace:{name}")
            if self.get_namespace(cluster=cluster, name=name) is None:
                return
            time.sleep(2)
        raise TimeoutError(f"namespace {name} did not delete within 10m")

    @driver_op(cloud="azure", driver="cluster")
    def list_csi_drivers(self, cluster: str) -> list[str]:
        client = self._k8s(cluster)
        return sorted(
            str((row.get("metadata", {}) or {}).get("name") or "")
            for row in client.list(kind="storage.k8s.io/v1/CSIDriver")
            if (row.get("metadata", {}) or {}).get("name")
        )

    @driver_op(cloud="azure", driver="cluster")
    def persistent_volume_claim_exists(self, cluster: str, namespace: str, name: str) -> bool:
        return self._k8s(cluster).get(kind="PersistentVolumeClaim", namespace=namespace, name=name) is not None

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
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
                    message=(f"{status.ready_replicas}/{status.desired_replicas} ready"),
                    timed_out=False,
                )
            for cond in status.conditions:
                if cond.get("type") == "Progressing" and cond.get("status") == "False":
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

    @driver_op(cloud="azure", driver="cluster")
    def exec_in_pod(self, cluster, namespace, pod, container, command):
        return self._k8s(cluster).exec_in_pod(
            namespace=namespace,
            pod=pod,
            container=container,
            command=command,
        )

    @driver_op(cloud="azure", driver="cluster")
    def port_forward(self, cluster, namespace, pod, ports):
        return self._k8s(cluster).port_forward(
            namespace=namespace,
            pod=pod,
            ports=ports,
        )

    # ---- runtime observability (#299) -----------------------------

    @driver_op(cloud="azure", driver="cluster")
    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
    ) -> list[PodInfo]:
        """List pods on the AKS cluster.

        Materializes ``exec_plugin`` auth into a kubeconfig blob
        (admin credentials fetched via
        ``managed_clusters.list_cluster_admin_credentials``) before
        delegating to the shared k8s_native pod backend.
        ``kubeconfig`` / ``service_account_token`` pass through
        unchanged.

        ``task_id`` (#891) selects an agent task pod by its
        ``astrolift.dev/task-id`` label; forwarded only when set so
        backends that predate the kwarg keep working.
        """
        kwargs: dict[str, Any] = {
            "auth": self._resolve_aks_auth(auth),
            "namespace": namespace,
            "app_slug": app_slug,
        }
        if task_id:
            kwargs["task_id"] = task_id
        return self._pod_backend.list_pods(**kwargs)

    @driver_op(cloud="azure", driver="cluster")
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
        """Open a streaming exec session on the AKS cluster (#1040).
        Materializes exec_plugin auth into a kubeconfig (as list_pods)
        before delegating to the shared k8s_native exec opener."""
        from providers.k8s_native.observability import open_interactive_exec

        return open_interactive_exec(
            auth=self._resolve_aks_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            command=command,
            tty=tty,
        )

    @driver_op(cloud="azure", driver="cluster")
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
        """See ``list_pods`` — same resolve-then-delegate pattern."""
        return self._log_backend.stream(
            auth=self._resolve_aks_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    # ---- bring-into-management (#316) -----------------------------

    @driver_op(cloud="azure", driver="cluster")
    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        return probe_cluster_capabilities(
            backend=self._management_backend,
            cluster=self._resolve_aks_auth_context(cluster),
        )

    @driver_op(cloud="azure", driver="cluster", audit=True, sensitive_kind="cluster.bring_into_management")
    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=self._resolve_aks_auth_context(cluster),
            run_preflight=run_preflight,
        )

    @driver_op(cloud="azure", driver="cluster")
    def read_job_status(self, cluster: ClusterContext, *, namespace: str, job_name: str) -> JobStatus:
        """Read one Job's status (run-status reconciler). Read-only —
        resolves the AKS exec-plugin auth to a kubeconfig context first."""
        return read_cluster_job_status(
            backend=self._management_backend,
            cluster=self._resolve_aks_auth_context(cluster),
            namespace=namespace,
            job_name=job_name,
        )

    # ---- cluster teardown (#337) ---------------------------------

    @driver_op(cloud="azure", driver="cluster", audit=True, sensitive_kind="cluster.teardown_cluster")
    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Delete the AKS managed cluster.

        ``delete_cloud_infra=False`` is a no-op for symmetry with the
        protocol — the cluster row is the platform's record; the AKS
        cluster itself stays running.

        When deleting, calls
        ``managed_clusters.begin_delete(resource_group, cluster_name)``.
        Azure cascades node-pool VMs + load balancers tagged with the
        cluster's MC resource group. Resource group / VNet / IAM
        roles are operator-owned and stay.

        Idempotent: a ``ResourceNotFoundError`` (Azure SDK) on the
        begin_delete call is treated as "already gone".
        """
        if not delete_cloud_infra:
            return TeardownReport(
                success=True,
                skipped=[f"aks/{self._config.cluster_name}"],
                messages=["delete_cloud_infra=false; AKS cluster left running"],
            )
        try:
            poller = self._aks.managed_clusters.begin_delete(
                resource_group_name=self._config.resource_group,
                resource_name=self._config.cluster_name,
            )
            # Don't block on poller.result() here — Azure deletes take
            # 5-10 min; the workflow's activity timeout would push the
            # heartbeat ratio uncomfortably. The submission succeeding
            # is the durable signal.
            _ = poller
        except Exception as exc:
            mapped = map_api_error(exc)
            if isinstance(mapped, NotFoundError):
                return TeardownReport(
                    success=True,
                    skipped=[f"aks-cluster/{self._config.cluster_name} (already deleted)"],
                    messages=["AKS cluster lookup returned 404 — already gone"],
                )
            return TeardownReport(
                success=False,
                error=f"managed_clusters.begin_delete failed: {mapped}",
            )
        return TeardownReport(
            success=True,
            deleted=[f"aks-cluster/{self._config.cluster_name}"],
            messages=[
                "managed_clusters.begin_delete submitted; node-pool VMs + "
                "tagged load balancers cascade-delete in the MC_<rg> resource "
                "group; full deletion typically completes in 5-10 min",
            ],
        )

    # ---- bootstrap recipe ----------------------------------------

    @driver_op(cloud="azure", driver="cluster")
    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """AKS recipe — uses Azure-native paths where they're the
        easiest option, falls back to in-cluster controllers where
        Azure doesn't provide a managed equivalent."""
        return [
            BootstrapComponent(
                key="external-dns",
                title="external-dns (Azure DNS)",
                default_enabled=True,
                rationale=(
                    "Auto-creates Azure DNS records from Ingress + "
                    "Service annotations. Bound to a managed identity via "
                    "Azure AD Federated Identity Credentials."
                ),
                helm_values={
                    "provider": "azure",
                    "sources": ["service", "ingress"],
                },
                requires=["federated_identity:external-dns", "azuredns_zone"],
                options=[],
                chart_name="external-dns",
                chart_repo_url="https://kubernetes-sigs.github.io/external-dns/",
                chart_repo_type="default",
                chart_version="1.14.5",
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=False,
                rationale=(
                    "AKS ships metrics-server as part of the cluster by "
                    "default — only enable if the cluster was created "
                    "without it (legacy bootstrap or custom node config)."
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
                    "Metrics scraping + dashboarding. The default storage "
                    "mode is ephemeral (emptyDir) — 24h retention, TSDB "
                    "resets on pod restart. Select 'azurefile_persistent' "
                    "to get 30d retention backed by Azure Files (SMB/NFS); "
                    "that requires the Azure Files CSI driver and the "
                    "'azurefile-prometheus' StorageClass applied from "
                    "Terraform output before running this recipe. Operators "
                    "on Azure Monitor for containers can disable this "
                    "component and point the OTel collector at Log Analytics."
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
                                "azurefile_persistent",
                                "Persistent Azure Files — 30d retention, survives restarts; "
                                "requires Azure Files CSI addon + azurefile-prometheus StorageClass",
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
                title="cert-manager (non-AppGW TLS)",
                default_enabled=False,
                rationale=(
                    "Application Gateway with Key Vault-backed certs is "
                    "the AKS-native path — no in-cluster controller needed. "
                    "Enable cert-manager when you run nginx ingress instead "
                    "or need internal mTLS."
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
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (Azure DNS DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                            ("self_signed", "Self-signed (internal only)"),
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

    # ---- Cluster health (#68 slice 1) -----------------------------

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
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

    @driver_op(cloud="azure", driver="cluster")
    def list_regions(self) -> list[RegionInfo]:
        """Curated static list of Azure regions for the register picker
        (#860).

        Live ``subscription_client.subscriptions.list_locations`` needs
        a subscription id + an authenticated management client, neither
        of which is available on the no-cluster register path — so this
        returns a curated static list. The frontend keeps free-entry on
        top for regions not listed here.
        """
        return [RegionInfo(id=slug, label=label, continent=continent) for slug, label, continent in _AZURE_REGIONS]

    def _k8s(self, cluster: str) -> Any:
        if cluster in self._k8s_cache:
            return self._k8s_cache[cluster]
        endpoint, ca_data = self._describe_cluster()
        client = self._factory(endpoint=endpoint, ca_data=ca_data)
        self._k8s_cache[cluster] = client
        return client

    def _describe_cluster(self) -> tuple[str, str]:
        try:
            cluster = self._aks.managed_clusters.get(
                resource_group_name=self._config.resource_group,
                resource_name=self._config.cluster_name,
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
        endpoint = getattr(cluster, "fqdn", "") or getattr(
            cluster,
            "private_fqdn",
            "",
        )
        # AKS doesn't surface the CA on the managed-cluster object;
        # production wiring fetches kubeconfig via
        # list_cluster_admin_credentials and parses cluster-data.
        return f"https://{endpoint}", ""

    # ---- exec_plugin token materialization (#311) -----------------
    #
    # The shared k8s_native ``build_api_client`` accepts ``kubeconfig``
    # + ``service_account_token`` but rejects ``exec_plugin`` — AKS
    # token minting is the cloud driver's job. We resolve the row by
    # calling ``managed_clusters.list_cluster_admin_credentials``,
    # which returns a kubeconfig blob already wired to the AKS
    # control plane (cluster client cert + private key + CA), and
    # rewrite the auth so the shared backend sees plain ``kubeconfig``.
    #
    # Path B (federated-credential AAD-token exchange via kubelogin)
    # is reserved for a follow-on slice — see ``AKSConfig.use_federated_token``.
    # The acceptance criterion just needs live pods, which Path A
    # satisfies for the common case.
    #
    # Two helpers because ClusterContext (for bring/probe) and
    # ClusterAuth (for list_pods/stream_logs) are different frozen
    # dataclasses — ``dataclasses.replace`` is type-specific. The
    # kubeconfig fetch + TTL cache is shared via ``_admin_kubeconfig``.

    def _admin_kubeconfig(
        self,
        *,
        resource_group: str,
        cluster_name: str,
    ) -> str:
        """Fetch (and cache) the admin kubeconfig blob for an AKS
        managed cluster.

        Cached for ``_AKS_KUBECONFIG_TTL_SECONDS`` per
        (resource_group, cluster_name). Raises ``ClusterAuthError``
        on any SDK failure with the mapped error wrapped as cause —
        the resolver-side log shows both the cluster slug and the
        underlying Azure error.
        """
        key = (resource_group, cluster_name)
        now = self._clock()
        cached = self._kubeconfig_cache.get(key)
        if cached is not None:
            expires_at, blob = cached
            if expires_at > now:
                return blob

        try:
            result = self._aks.managed_clusters.list_cluster_admin_credentials(
                resource_group_name=resource_group,
                resource_name=cluster_name,
            )
        except Exception as exc:
            mapped = map_api_error(exc)
            raise ClusterAuthError(
                f"AKS {resource_group}/{cluster_name}: list_cluster_admin_credentials failed: {mapped}",
            ) from exc

        kubeconfigs = getattr(result, "kubeconfigs", None) or []
        if not kubeconfigs:
            raise ClusterAuthError(
                f"AKS {resource_group}/{cluster_name}: list_cluster_admin_credentials returned no kubeconfigs",
            )
        raw = getattr(kubeconfigs[0], "value", None)
        if raw is None:
            raise ClusterAuthError(
                f"AKS {resource_group}/{cluster_name}: list_cluster_admin_credentials kubeconfig.value is empty",
            )
        # Azure returns ``value`` as bytes (the kubeconfig YAML) on
        # the real SDK; some fakes return str directly. Some operator
        # tooling also base64-encodes the blob — be tolerant of both.
        if isinstance(raw, bytes):
            try:
                blob = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    blob = base64.b64decode(raw).decode("utf-8")
                except Exception as exc:
                    raise ClusterAuthError(
                        f"AKS {resource_group}/{cluster_name}: kubeconfig blob is neither UTF-8 nor base64-UTF-8",
                    ) from exc
        else:
            blob = str(raw)

        self._kubeconfig_cache[key] = (
            now + _AKS_KUBECONFIG_TTL_SECONDS,
            blob,
        )
        return blob

    def _resolve_aks_auth(self, auth: ClusterAuth) -> ClusterAuth:
        if auth.auth_method != "exec_plugin":
            return auth
        import dataclasses

        cfg = auth.auth_config or {}
        resource_group = cfg.get("resource_group") or self._config.resource_group
        cluster_name = cfg.get("cluster_name") or self._config.cluster_name
        blob = self._admin_kubeconfig(
            resource_group=resource_group,
            cluster_name=cluster_name,
        )
        new_cfg: dict[str, Any] = {"kubeconfig": blob}
        context = cfg.get("context")
        if context:
            new_cfg["context"] = context
        return dataclasses.replace(
            auth,
            auth_method="kubeconfig",
            auth_config=new_cfg,
        )

    def _resolve_aks_auth_context(
        self,
        cluster: ClusterContext,
    ) -> ClusterContext:
        if cluster.auth_method != "exec_plugin":
            return cluster
        import dataclasses

        cfg = cluster.auth_config or {}
        resource_group = cfg.get("resource_group") or self._config.resource_group
        cluster_name = cfg.get("cluster_name") or self._config.cluster_name
        blob = self._admin_kubeconfig(
            resource_group=resource_group,
            cluster_name=cluster_name,
        )
        new_cfg: dict[str, Any] = {"kubeconfig": blob}
        context = cfg.get("context")
        if context:
            new_cfg["context"] = context
        return dataclasses.replace(
            cluster,
            auth_method="kubeconfig",
            auth_config=new_cfg,
        )


# AKS apiserver AAD scope. The AKS managed identity registers a
# server-side AAD application with this fixed app-id; AKS-AAD clusters
# accept bearer tokens minted for ``<app-id>/.default``. Tokens have a
# ~5 minute lifetime, which is why ``KubernetesDynamicClient``
# re-mints via ``token_provider`` before every top-level op.
_AKS_AAD_SCOPE = "6dae42f8-4368-4678-94ff-3960e28e3630/.default"


def _build_k8s_client(
    *,
    endpoint: str,
    ca_data: str,
    credential_factory: Callable[[], Any] | None = None,
) -> Any:
    """Production factory: wire the shared k8s helper for AKS.

    Closes #567. AKS auth is the trickiest of the four clouds because
    the managed-cluster object doesn't surface the CA on its API model
    — the CA is embedded inside the kubeconfig blob returned by
    ``list_cluster_admin_credentials``. The driver's ``_describe_cluster``
    therefore returns ``(https://<fqdn>, "")`` and we lean on the
    system trust bundle (Azure-managed certs chain to a public CA the
    OS already trusts).

    Token minting uses ``azure.identity.DefaultAzureCredential`` with
    the AKS AAD scope; the credential chain resolves to:
      * Workload Identity (when running inside an AAD-bound pod),
      * Managed Identity (when running on an Azure VM with IMDS),
      * Azure CLI / dev credentials (for off-cluster operator calls).

    The token has a ~5min lifetime; the shared helper's
    ``token_provider`` hook re-mints via ``credential.get_token`` on
    each top-level op so long-running workflows can't 401 mid-flight.
    ``credential_factory`` is injected by tests so we don't talk to
    real Azure during unit-test runs.
    """
    if credential_factory is None:
        # Import inside the function so a missing ``azure-identity``
        # install only bites at the point of use — matches the GCP
        # ``_default_credentials_factory`` pattern below.
        def credential_factory() -> Any:  # type: ignore[misc]
            from azure.identity import DefaultAzureCredential

            return DefaultAzureCredential()

    credential = credential_factory()

    def _mint_token() -> str:
        # ``get_token`` returns ``AccessToken(token=..., expires_on=...)``;
        # the kubernetes-client config only consumes the bearer string.
        # ``DefaultAzureCredential.get_token`` caches internally until
        # ~5min before expiry, so calling on every op is cheap.
        access_token = credential.get_token(_AKS_AAD_SCOPE)
        return str(getattr(access_token, "token", ""))

    return _RealK8sClient(
        endpoint=endpoint,
        ca_data=ca_data,
        token_provider=_mint_token,
    )


# ``_RealK8sClient`` is the shared helper at top-of-file imports —
# kept as a comment so future readers find the alias without grep.
