"""AKS ClusterDriver (#42).

Same shape as EKS / GKE — auth via ContainerServiceClient
list_cluster_admin_credentials (or user_credentials), parse the
resulting kubeconfig for endpoint + CA, then kubernetes-client for
k8s API ops. Tokens come from Azure CLI / Workload Identity at the
deployment site.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from _sdk.cluster import (
    ApplyResult,
    ClusterDriver,
    DeleteResult,
    Namespace,
    NamespaceState,
    RolloutResult,
    WorkloadStatus,
)

from azure._errors import NotFoundError, map_api_error


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

    def apply_manifests(
        self, cluster, namespace, manifests, *, dry_run=False,
    ):
        client = self._k8s(cluster)
        created, updated, unchanged, errors = [], [], [], []
        for m in manifests:
            ref = (
                f"{m.get('kind', '')}/"
                f"{m.get('metadata', {}).get('name', '')}"
            )
            try:
                outcome = client.server_side_apply(
                    namespace=namespace, manifest=m, dry_run=dry_run,
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
            created=created, updated=updated,
            unchanged=unchanged, errors=errors,
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
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ref}: {exc}")
        return DeleteResult(
            deleted=deleted, not_found=not_found, errors=errors,
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
                "apiVersion": "v1", "kind": "Namespace",
                "metadata": {
                    "name": name, "labels": labels,
                    "annotations": annotations,
                },
            },
            dry_run=False,
        )
        return Namespace(
            name=name, labels=dict(labels), annotations=dict(annotations),
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
            kind=kind, name=name, namespace=namespace,
            ready_replicas=int(status.get("readyReplicas", 0)),
            desired_replicas=int(
                spec.get("replicas", status.get("replicas", 0)),
            ),
            conditions=status.get("conditions", []) or [],
        )

    def poll_rollout(
        self, cluster, namespace, kind, name, timeout, *, on_tick=None,
    ):
        import time

        if timeout <= 0:
            timeout = 600
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self.get_workload_status(
                    cluster=cluster, namespace=namespace,
                    kind=kind, name=name,
                )
            except NotFoundError:
                return RolloutResult(
                    success=False, kind=kind, name=name,
                    namespace=namespace,
                    message="workload not found", timed_out=False,
                )
            if on_tick:
                on_tick(status)
            if (
                status.ready_replicas == status.desired_replicas
                and status.desired_replicas > 0
            ):
                return RolloutResult(
                    success=True, kind=kind, name=name,
                    namespace=namespace,
                    message=(
                        f"{status.ready_replicas}/"
                        f"{status.desired_replicas} ready"
                    ),
                    timed_out=False,
                )
            for cond in status.conditions:
                if (
                    cond.get("type") == "Progressing"
                    and cond.get("status") == "False"
                ):
                    return RolloutResult(
                        success=False, kind=kind, name=name,
                        namespace=namespace,
                        message=cond.get("message", "Progressing=False"),
                        timed_out=False,
                    )
            time.sleep(min(15, max(1, timeout // 20)))
        return RolloutResult(
            success=False, kind=kind, name=name, namespace=namespace,
            message="rollout timed out", timed_out=True,
        )

    def exec_in_pod(self, cluster, namespace, pod, container, command):
        return self._k8s(cluster).exec_in_pod(
            namespace=namespace, pod=pod,
            container=container, command=command,
        )

    def port_forward(self, cluster, namespace, pod, ports):
        return self._k8s(cluster).port_forward(
            namespace=namespace, pod=pod, ports=ports,
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
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        endpoint = getattr(cluster, "fqdn", "") or getattr(
            cluster, "private_fqdn", "",
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
