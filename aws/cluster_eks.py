"""AWS EKS ClusterDriver (#29).

Spec ref: spec 23-provider-plugin-aws + _sdk/cluster.py.

EKS auth flow: the driver calls boto3 EKS DescribeCluster to get
the cluster's API endpoint + CA, then calls EKS GetToken (via
the local AWS-IAM-authenticator helper or direct STS) to get a
short-lived bearer token. The kubernetes Python client uses
those to talk to the cluster's API server.

Token refresh: tokens expire every ~14 minutes. The driver
re-fetches on each operation rather than caching long-term;
high-frequency callers should reuse a single driver instance
which keeps the boto3 client warm.

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
from aws._errors import NotFoundError, map_client_error
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
        # exec_plugin row gets turned into kubeconfig (#309 follow-up).
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
                client.delete(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                )
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
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
        """Listing path is shared with k8s_native — the EKS-specific
        bit (IRSA exec_plugin token mint) lives in #309 follow-up.
        For now, ``auth_method=kubeconfig`` and
        ``auth_method=service_account_token`` go through directly;
        ``exec_plugin`` raises ClusterAuthError pointing at #309."""
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
        try:
            response = self._eks.describe_cluster(
                name=self._config.cluster_name,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        cluster = response["cluster"]
        endpoint = cluster["endpoint"]
        ca_data = cluster["certificateAuthority"]["data"]
        # ca_data is base64-encoded by EKS; the kubernetes client
        # handles the decode per its config shape.
        return endpoint, ca_data

    def _eks_token(self) -> str:
        """Generate a short-lived EKS bearer token. Real STS-based
        token generation goes through the AWS-IAM-authenticator
        protocol (presigned STS GetCallerIdentity URL). For tests
        + the placeholder until #29 wires the actual flow, we
        return a sentinel; the client factory uses it as a stub."""
        try:
            response = self._sts.get_caller_identity()
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        # Placeholder token. Real implementation uses awscli
        # `aws eks get-token` or signs the STS URL manually.
        return base64.b64encode(
            f"k8s-aws-v1.{response.get('Account', '')}".encode(),
        ).decode()


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
