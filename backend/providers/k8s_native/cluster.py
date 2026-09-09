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
    ClusterEvent,
    DeleteResult,
    ExecResult,
    JobStatus,
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    PodPhaseSummary,
    PortForwardSession,
    RolloutResult,
    StorageClassInfo,
    TeardownReport,
    WorkloadStatus,
    classify_apply_error,
    workload_status_from_object,
)
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.k8s_dynamic_client import NotFoundError as _NotFound
from _sdk.k8s_dynamic_client import PreconditionFailedError as _PreconditionFailed
from k8s_native.central_auth import central_auth_component
from k8s_native.management import (
    ManagementBackend,
    default_management_backend,
    probe_cluster_capabilities,
    read_cluster_job_status,
    run_bring_into_management,
)
from k8s_native.observability import (
    LivePodBackend,
    LogBackend,
    PodBackend,
    default_log_backend,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


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

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
    def delete_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
        *,
        propagation_policy: str | None = None,
    ) -> DeleteResult:
        client = self._k8s(cluster)
        deleted: list[str] = []
        not_found: list[str] = []
        errors: list[str] = []
        conflicts: list[str] = []
        for manifest in manifests:
            api_version = manifest.get("apiVersion", "")
            kind_bare = manifest.get("kind", "")
            # For CRDs (apiVersion is "group/version") construct the
            # "group/version/Kind" form that split_kind accepts.
            kind = f"{api_version}/{kind_bare}" if "/" in api_version else kind_bare
            meta = manifest.get("metadata", {}) or {}
            name = meta.get("name", "")
            ref = f"{kind}/{name}"
            # A caller that validated ownership with a GET passes the metadata
            # it read back in. Deleting by name alone would remove whatever
            # holds the name at delete time, which need not be the object that
            # was inspected (#1389). Absent here, the delete stays name-only,
            # so callers that never did an ownership read are unaffected.
            try:
                client.delete(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                    propagation_policy=propagation_policy,
                    uid=meta.get("uid") or None,
                    resource_version=meta.get("resourceVersion") or None,
                )
                deleted.append(ref)
            except _NotFound:
                not_found.append(ref)
            except _PreconditionFailed as exc:
                # Never fall back to an unconditional delete: the object under
                # this name is not the one ownership was validated on.
                conflicts.append(f"{ref}: {exc}")
            except Exception as exc:
                errors.append(f"{ref}: {exc}")
        return DeleteResult(
            deleted=deleted,
            not_found=not_found,
            errors=errors,
            conflicts=conflicts,
        )

    # ---- namespaces -----------------------------------------------

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster", audit=True, sensitive_kind="cluster.delete_namespace")
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
        # PVCs / webhooks can take minutes. Heartbeat per iteration so
        # the Temporal activity stays alive across the 10-minute window
        # (#598).
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            maybe_heartbeat(f"cluster.delete_namespace:{name}")
            if self.get_namespace(cluster=cluster, name=name) is None:
                return
            time.sleep(2)
        raise TimeoutError(
            f"namespace {name} did not delete within 10m",
        )

    @driver_op(cloud="k8s_native", driver="cluster")
    def list_storage_classes(self, cluster: str) -> list[StorageClassInfo]:
        client = self._k8s(cluster)
        out: list[StorageClassInfo] = []
        for sc in client.list(kind="storage.k8s.io/v1/StorageClass"):
            meta = sc.get("metadata", {}) or {}
            ann = meta.get("annotations", {}) or {}
            out.append(
                StorageClassInfo(
                    name=meta.get("name", ""),
                    is_default=(ann.get("storageclass.kubernetes.io/is-default-class") == "true"),
                    provisioner=str(sc.get("provisioner") or ""),
                    reclaim_policy=str(sc.get("reclaimPolicy") or "Delete"),
                )
            )
        return out

    @driver_op(cloud="k8s_native", driver="cluster")
    def list_csi_drivers(self, cluster: str) -> list[str]:
        client = self._k8s(cluster)
        return sorted(
            str((row.get("metadata", {}) or {}).get("name") or "")
            for row in client.list(kind="storage.k8s.io/v1/CSIDriver")
            if (row.get("metadata", {}) or {}).get("name")
        )

    @driver_op(cloud="k8s_native", driver="cluster")
    def persistent_volume_claim_exists(self, cluster: str, namespace: str, name: str) -> bool:
        return self._k8s(cluster).get(kind="PersistentVolumeClaim", namespace=namespace, name=name) is not None

    @driver_op(cloud="k8s_native", driver="cluster")
    def get_manifest(
        self,
        cluster: str,
        namespace: str | None,
        kind: str,
        name: str,
    ) -> dict[str, Any] | None:
        return self._k8s(cluster).get(kind=kind, namespace=namespace, name=name)

    @driver_op(cloud="k8s_native", driver="cluster")
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

    # ---- workload status ------------------------------------------

    @driver_op(cloud="k8s_native", driver="cluster")
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
        return workload_status_from_object(kind, name, namespace, obj)

    @driver_op(cloud="k8s_native", driver="cluster")
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
            maybe_heartbeat(f"cluster.poll_rollout:{kind}/{name}")
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
        stalled, stall_message = self._diagnose_stall(cluster, namespace, kind, name)
        return RolloutResult(
            success=False,
            kind=kind,
            name=name,
            namespace=namespace,
            message=(
                stall_message
                or (
                    last_status.conditions[-1].get("message", "")
                    if last_status and last_status.conditions
                    else "rollout timed out"
                )
            ),
            timed_out=True,
            stalled=stalled,
        )

    def _diagnose_stall(
        self, cluster: str, namespace: str, kind: str, name: str
    ) -> tuple[bool, str]:
        """Say why a StatefulSet rollout could not finish (#1724).

        ``RollingUpdate`` will not advance past a pod that never becomes Ready.
        A workload that crashes on startup therefore keeps its old pod on the
        previous revision indefinitely while the StatefulSet already carries the
        new template -- so a fix can be applied and inert at the same time, and
        every deploy after it reports a bare "rollout timed out" that says
        nothing about the change being stuck one revision away.

        ``currentRevision != updateRevision`` is the whole signal: the
        StatefulSet has accepted a new template and no pod has moved to it.

        Best-effort. Any failure to read the cluster degrades to
        ``(False, "")`` and the caller keeps its original message -- a
        diagnosis is not worth failing a deploy path over.
        """
        if kind.lower() != "statefulset":
            return False, ""
        try:
            obj = self._k8s(cluster).get(kind=kind, namespace=namespace, name=name)
            status = (obj or {}).get("status") or {}
            current = status.get("currentRevision")
            update = status.get("updateRevision")
            if not current or not update or current == update:
                return False, ""
            ready = status.get("readyReplicas") or 0
            return True, (
                f"{kind}/{name}: the new template is applied but cannot roll out. "
                f"Pods are still on revision {current}; the StatefulSet wants {update}. "
                f"RollingUpdate will not replace a pod that never becomes Ready "
                f"({ready} ready), so this will not resolve on its own -- every further "
                f"deploy will apply cleanly and time out the same way. Fix the workload, "
                f"or delete the pod to force it onto the new revision."
            )
        except Exception:  # noqa: BLE001 - diagnosis must never fail the caller
            return False, ""

    # ---- exec / port-forward --------------------------------------

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
    ) -> list[PodInfo]:
        """Delegate to the (pluggable) pod backend. Subclasses for
        managed-cloud variants (EKS/GKE/AKS) override only when the
        auth path differs; the listing shape is cloud-neutral.

        ``task_id`` (#891) selects an agent task pod by its
        ``astrolift.dev/task-id`` label; forwarded only when set so
        backends that predate the kwarg keep working."""
        kwargs: dict[str, Any] = {"auth": auth, "namespace": namespace, "app_slug": app_slug}
        if task_id:
            kwargs["task_id"] = task_id
        return self._pod_backend.list_pods(**kwargs)

    @driver_op(cloud="k8s_native", driver="cluster")
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
        """Open a streaming exec session into a pod (#1040). Delegates to
        the shared exec opener; auth passes through unchanged (the base
        k8s_native driver supports kubeconfig + service_account_token)."""
        from providers.k8s_native.observability import open_interactive_exec

        return open_interactive_exec(
            auth=auth,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            command=command,
            tty=tty,
        )

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        """Read-only capability probe. Used by the refresh path and
        also called from inside ``bring_into_management`` after the
        RBAC apply succeeds. Raises on auth / network failure."""
        return probe_cluster_capabilities(backend=self._management_backend, cluster=cluster)

    @driver_op(cloud="k8s_native", driver="cluster", audit=True, sensitive_kind="cluster.bring_into_management")
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

    @driver_op(cloud="k8s_native", driver="cluster")
    def read_job_status(self, cluster: ClusterContext, *, namespace: str, job_name: str) -> JobStatus:
        """Read one Job's status (run-status reconciler). Read-only."""
        return read_cluster_job_status(
            backend=self._management_backend,
            cluster=cluster,
            namespace=namespace,
            job_name=job_name,
        )

    # ---- bootstrap recipe ------------------------------------------

    @driver_op(cloud="k8s_native", driver="cluster", audit=True, sensitive_kind="cluster.teardown_cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
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
                key="cert-manager",
                title="cert-manager (TLS certificates)",
                default_enabled=True,
                rationale=(
                    "Bare-metal clusters need cert-manager + an Issuer the "
                    "platform can drive. Let's Encrypt requires a real "
                    "delegated subzone for DNS-01; self-signed works for "
                    "internal-only traffic."
                ),
                helm_values={
                    "installCRDs": True,
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
                chart_name="cert-manager",
                chart_repo_url="https://charts.jetstack.io",
                chart_repo_type="default",
                chart_version="v1.16.3",
            ),
            BootstrapComponent(
                key="ingress-nginx",
                title="Ingress controller (nginx)",
                default_enabled=True,
                rationale=(
                    "Bare-metal clusters don't get a cloud ingress for free. "
                    "ingress-nginx is the platform's default; pair it with "
                    "MetalLB so its Service:LoadBalancer gets an IP. "
                    "Controller metrics + ServiceMonitor are enabled (spec 08 "
                    "§6.1): the edge is where the platform sources every app's "
                    "traffic / error-rate / latency golden signals, so a "
                    "cluster whose ingress doesn't export metrics is a "
                    "provisioning defect."
                ),
                helm_values={
                    "controller": {
                        "metrics": {
                            "enabled": True,
                            # Picked up by the kube-prometheus-stack operator;
                            # without a ServiceMonitor the exporter runs but
                            # nothing scrapes it and the RED panels stay empty.
                            "serviceMonitor": {"enabled": True},
                        },
                        # Header buffers sized for a cookie-bearing auth gate
                        # (#1725). Every app behind the central auth host
                        # carries a session cookie, and nginx copies the whole
                        # Cookie header into the auth_request subrequest. The
                        # chart defaults (large_client_header_buffers 4 8k,
                        # proxy_buffer_size 4k) reject a Cookie header past
                        # 8KB, and nginx reports that as an auth subrequest
                        # failure -- which surfaces to the user as a bare 502
                        # / 500 from the edge, with nothing logged by
                        # oauth2-proxy because the subrequest never reached
                        # it. A browser accumulates well past 8KB: an
                        # oauth2-proxy session split across _0/_1 alongside
                        # the AWSELBAuthSessionCookie pair a cluster leaves
                        # behind when it migrates off ALB auth is already
                        # ~16KB, so the ceiling is reached in normal use
                        # rather than by abuse.
                        "config": {
                            "large-client-header-buffers": "4 32k",
                            "proxy-buffer-size": "16k",
                        },
                    },
                },
                requires=["metallb"],
                options=[],
                chart_name="ingress-nginx",
                chart_repo_url="https://kubernetes.github.io/ingress-nginx",
                chart_repo_type="default",
                chart_version="4.11.1",
                # The chart's ServiceMonitor needs the monitoring.coreos.com
                # CRDs; installing after kube-prometheus-stack avoids a
                # first-reconcile flap while Flux waits for the CRD.
                depends_on=["kube-prometheus-stack"],
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
                helm_values={},
                requires=["ip_pool: routable LB range"],
                options=[],
                chart_name="metallb",
                chart_repo_url="https://metallb.github.io/metallb",
                chart_repo_type="default",
                chart_version="0.14.8",
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
                    "provider": "rfc2136",
                    "sources": ["service", "ingress"],
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
                chart_name="external-dns",
                chart_repo_url="https://kubernetes-sigs.github.io/external-dns/",
                chart_repo_type="default",
                chart_version="1.14.5",
            ),
            BootstrapComponent(
                key="longhorn",
                title="Storage (Longhorn)",
                default_enabled=True,
                rationale=(
                    "Longhorn provides replicated block storage on bare-metal "
                    "node disks. Operators with existing CSI (Rook-Ceph, "
                    "TopoLVM, NFS) should flip this off and configure that "
                    "StorageClass separately."
                ),
                helm_values={},
                requires=["node_disks: dedicated disk path on each node"],
                options=[],
                chart_name="longhorn",
                chart_repo_url="https://charts.longhorn.io",
                chart_repo_type="default",
                chart_version="1.7.2",
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=True,
                rationale=(
                    "Required for HorizontalPodAutoscaler and ``kubectl top``. Not bundled in vanilla k8s distros."
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
                    "The platform's metrics scraping + dashboarding stack. "
                    "Disable if the operator points the OTel collector at an "
                    "external Prometheus / Mimir / Datadog instead."
                ),
                helm_values={
                    "grafana": {
                        "enabled": True,
                        "sidecar": {
                            "dashboards": {
                                "enabled": True,
                                "searchNamespace": "ALL",
                            },
                        },
                    },
                    "prometheus": {
                        "prometheusSpec": {
                            # Watch ALL ServiceMonitors — sibling addons
                            # (ingress-nginx's controller monitor, spec 08
                            # §6.1) ship their own and must be scraped
                            # without per-monitor release labeling.
                            "serviceMonitorSelectorNilUsesHelmValues": False,
                            "podMonitorSelectorNilUsesHelmValues": False,
                            "ruleSelectorNilUsesHelmValues": False,
                            "serviceMonitorNamespaceSelector": {},
                            "podMonitorNamespaceSelector": {},
                            "ruleNamespaceSelector": {},
                        },
                    },
                    "extraManifests": [
                        {
                            "apiVersion": "networking.k8s.io/v1",
                            "kind": "NetworkPolicy",
                            "metadata": {"name": "astrolift-prometheus-trusted-ingress"},
                            "spec": {
                                "podSelector": {
                                    "matchLabels": {
                                        "app.kubernetes.io/name": "prometheus",
                                        "prometheus": "astrolift-kube-prometheus-prometheus",
                                    },
                                },
                                "policyTypes": ["Ingress"],
                                "ingress": [
                                    {
                                        "from": [
                                            {
                                                "namespaceSelector": {
                                                    "matchLabels": {
                                                        "kubernetes.io/metadata.name": "astrolift-system",
                                                    },
                                                },
                                            },
                                            {
                                                "namespaceSelector": {
                                                    "matchLabels": {
                                                        "astrolift.io/trusted-observability-access": "true",
                                                    },
                                                },
                                            },
                                        ],
                                        "ports": [{"port": 9090, "protocol": "TCP"}],
                                    },
                                ],
                            },
                        },
                        {
                            "apiVersion": "networking.k8s.io/v1",
                            "kind": "NetworkPolicy",
                            "metadata": {"name": "astrolift-alertmanager-operator-ingress"},
                            "spec": {
                                "podSelector": {
                                    "matchLabels": {
                                        "alertmanager": "astrolift-kube-prometheus-alertmanager",
                                        "app.kubernetes.io/name": "alertmanager",
                                    },
                                },
                                "policyTypes": ["Ingress"],
                                "ingress": [
                                    {
                                        "from": [
                                            {
                                                "namespaceSelector": {
                                                    "matchLabels": {
                                                        "kubernetes.io/metadata.name": "astrolift-system",
                                                    },
                                                },
                                            },
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                },
                requires=["storage:rwo for Prom + Grafana PVs"],
                options=[],
                chart_name="kube-prometheus-stack",
                chart_repo_url="https://prometheus-community.github.io/helm-charts",
                chart_repo_type="default",
                chart_version="65.1.0",
                install_timeout="15m",
            ),
            BootstrapComponent(
                key="dex",
                title="Dex (in-cluster OIDC provider)",
                default_enabled=False,
                rationale=(
                    "Dex federates an upstream IdP (Google, GitHub, SAML, LDAP, "
                    "generic OIDC) into a single in-cluster OIDC endpoint that "
                    "oauth2-proxy authenticates against. Required when "
                    "oidc_auth_config.kind = 'dex'. Operators who bring their "
                    "own OIDC provider can skip Dex and point oauth2-proxy "
                    "directly at the external discovery URL."
                ),
                helm_values={
                    "config": {
                        "issuer": "https://dex.example.com/dex",
                        "storage": {"type": "kubernetes", "config": {"inCluster": True}},
                        "oauth2": {"skipApprovalScreen": True},
                        "staticClients": [],
                        "connectors": [],
                    },
                    "service": {"type": "ClusterIP"},
                    "ingress": {
                        "enabled": True,
                        "className": "nginx",
                        "annotations": {
                            "cert-manager.io/cluster-issuer": "letsencrypt-prod",
                        },
                    },
                },
                requires=["upstream IdP credentials (connector config)"],
                options=[
                    BootstrapOption(
                        key="upstream_connector",
                        label="Upstream IdP connector",
                        choices=[
                            ("google", "Google Workspace"),
                            ("github", "GitHub (org/team membership)"),
                            ("saml", "SAML 2.0 (Okta, OneLogin, ADFS)"),
                            ("ldap", "LDAP / Active Directory"),
                            ("oidc", "Generic OIDC (any upstream provider)"),
                        ],
                        default="google",
                    ),
                ],
                chart_name="dex",
                chart_repo_url="https://charts.dexidp.io",
                chart_repo_type="default",
                chart_version="0.19.1",
            ),
            central_auth_component(getattr(cluster, "oidc_auth_config", None)),
        ]

    # ---- Cluster health (#68 slice 1) -----------------------------

    @driver_op(cloud="k8s_native", driver="cluster")
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
            client,
            namespaces=default_namespaces(namespaces),
        )

    @driver_op(cloud="k8s_native", driver="cluster")
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

    @driver_op(cloud="k8s_native", driver="cluster")
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
