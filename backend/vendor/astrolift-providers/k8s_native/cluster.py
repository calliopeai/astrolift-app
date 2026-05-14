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
    ClusterAuth,
    ClusterDriver,
    DeleteResult,
    ExecResult,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    PortForwardSession,
    RolloutResult,
    WorkloadStatus,
)
from k8s_native.observability import (
    LivePodBackend,
    LogBackend,
    PodBackend,
    default_log_backend,
)


class K8sNativeError(Exception):
    pass


class _NotFound(Exception):
    """Raised by the k8s client wrapper when a resource doesn't exist."""


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
    ) -> None:
        self._config = config
        self._factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). The defaults
        # use the live kubernetes client; tests inject fakes so the
        # observability path is exercisable without a real apiserver.
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()

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
            except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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


def _build_k8s_client(
    *,
    kubeconfig_path: str,
    context: str,
    in_cluster: bool,
) -> Any:
    """Default factory using kubernetes Python client."""
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
    return _RealK8sClient(api=client)


class _RealK8sClient:
    def __init__(self, *, api: Any) -> None:
        self._api = api

    def server_side_apply(self, *, namespace, manifest, dry_run):
        raise NotImplementedError(
            "server_side_apply requires kubernetes.dynamic.DynamicClient wiring at deploy time",
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
