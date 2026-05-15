"""AKS ClusterDriver (#42).

Same shape as EKS / GKE — auth via ContainerServiceClient
list_cluster_admin_credentials (or user_credentials), parse the
resulting kubeconfig for endpoint + CA, then kubernetes-client for
k8s API ops. Tokens come from Azure CLI / Workload Identity at the
deployment site.
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
from azure._errors import NotFoundError, map_api_error
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
class AKSConfig:
    subscription_id: str
    resource_group: str
    cluster_name: str
    container_service_client: Any | None = None


class AKSClusterDriver(ClusterDriver):
    def __init__(
        self,
        *,
        config: AKSConfig,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
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
        # ClusterAuth blob is in hand; AKS federated-credential
        # exec_plugin token mint lives in #311 follow-up.
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). Inherits k8s_native's body
        # via the management backend; AKS federated-credential
        # exec_plugin token mint is irrelevant for the kubeconfig +
        # service-account auth paths that route through
        # ``build_api_client``.
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
        """Listing path is shared with k8s_native — the AKS-specific
        bit (federated-credential exec_plugin token mint) lives in
        #311 follow-up."""
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

    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """AKS recipe — uses Azure-native paths where they're the
        easiest option, falls back to in-cluster controllers where
        Azure doesn't provide a managed equivalent."""
        return [
            BootstrapComponent(
                key="tls_issuer",
                title="TLS certificate strategy",
                default_enabled=True,
                rationale=(
                    "Application Gateway with Key Vault-backed certs is "
                    "the AKS-native path (no in-cluster controller needed). "
                    "cert-manager + ACME-LetsEncrypt or self-signed available "
                    "when the operator runs nginx ingress instead."
                ),
                helm_values={},
                requires=[],
                options=[
                    BootstrapOption(
                        key="mode",
                        label="Issuer",
                        choices=[
                            ("appgw_keyvault", "Application Gateway + Key Vault (recommended)"),
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (cert-manager + Azure DNS DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                            ("self_signed", "Self-signed (internal only)"),
                        ],
                        default="appgw_keyvault",
                    ),
                ],
            ),
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
                    "external-dns": {
                        "enabled": True,
                        "provider": "azure",
                        "sources": ["service", "ingress"],
                    },
                },
                requires=["federated_identity:external-dns", "azuredns_zone"],
                options=[],
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
                helm_values={"metricsServer": {"enabled": True}},
                requires=[],
                options=[],
            ),
            BootstrapComponent(
                key="kube-prometheus-stack",
                title="Prometheus + Grafana + Alertmanager",
                default_enabled=True,
                rationale=(
                    "Metrics scraping + dashboarding. Persistent disk via "
                    "Azure-managed-disk StorageClass; operators on Azure "
                    "Monitor for containers can disable this and point "
                    "the OTel collector at Log Analytics instead."
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


def _build_k8s_client(*, endpoint: str, ca_data: str) -> Any:
    """Production wires this to kubernetes.dynamic.DynamicClient
    + Azure CLI / Workload Identity token. Tests inject a stub."""
    raise NotImplementedError(
        "requires kubernetes-client wiring at deploy time",
    )
