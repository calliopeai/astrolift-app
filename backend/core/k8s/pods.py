"""List-pods adapter — read live pod state for one app's namespace.

The resolver layer hands us a ``TenantCluster`` row + a namespace
and expects a list of plain dataclasses. The default backend uses
``kubernetes.client.CoreV1Api.list_namespaced_pod``; tests inject a
deterministic stub via :func:`set_pod_backend`.

Keeping the resolver-facing shape free of ``kubernetes`` types
(rather than handing back ``V1Pod``) means the GraphQL type layer
can map straight into the wire shape without ever importing the
client lib.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from core.k8s.client import ClusterClientError, client_for_cluster

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster


# ---- Plain dataclass payloads -------------------------------------


@dataclass(frozen=True, slots=True)
class ContainerStatusInfo:
    name: str
    ready: bool
    restart_count: int
    image: str
    state: str
    """One of ``running`` / ``waiting`` / ``terminated`` /
    ``unknown``. Matches the k8s container-status union."""

    waiting_reason: str = ""
    terminated_reason: str = ""


@dataclass(frozen=True, slots=True)
class PodInfo:
    name: str
    workload: str
    """Owning workload slug — best-effort, derived from the
    ``astrolift.io/workload`` label or, failing that, the
    owner-reference's controller name. Empty string when neither
    can be resolved."""

    status: str
    """Surface-friendly status: the worst of (phase, container
    waiting-reasons). ``CrashLoopBackOff`` here means at least one
    container is in that state even if ``phase`` is still
    ``Running``."""

    phase: str
    """Raw ``Pod.status.phase`` — ``Running``, ``Pending``,
    ``Succeeded``, ``Failed``, ``Unknown``."""

    ready: bool
    restarts: int
    age: datetime | None
    """``Pod.metadata.creation_timestamp``. GraphQL exposes this as
    a datetime so the UI can format relative-time itself."""

    node: str
    container_statuses: list[ContainerStatusInfo] = field(default_factory=list)


# ---- Backend protocol --------------------------------------------


class PodBackend(Protocol):
    """List pods in ``namespace`` for ``cluster``, optionally
    filtered to ``app_slug`` via the ``astrolift.io/app`` label."""

    def list_pods(
        self,
        *,
        cluster: TenantCluster,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]: ...


# ---- Default backend (real kubernetes client) --------------------


def _classify_status(pod_phase: str, containers: list[ContainerStatusInfo]) -> str:
    """Roll up the worst-case status across container states.

    The k8s phase alone hides waiting/terminated reasons, which is
    where the actionable info lives (CrashLoopBackOff, ImagePullBackOff,
    ErrImagePull). Mirror what `kubectl get pods` does: prefer a
    waiting reason or terminal-failure reason over the raw phase
    when present."""
    bad_waiting = {
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "CreateContainerConfigError",
        "InvalidImageName",
        "CreateContainerError",
    }
    for c in containers:
        if c.state == "waiting" and c.waiting_reason in bad_waiting:
            return c.waiting_reason
    for c in containers:
        if c.state == "terminated" and c.terminated_reason not in {"", "Completed"}:
            return c.terminated_reason
    return pod_phase or "Unknown"


def _workload_slug_for(metadata, app_slug: str) -> str:
    """Pull the workload slug off the pod's labels, falling back to
    its owner-reference. Empty string when neither can be inferred."""
    labels = getattr(metadata, "labels", None) or {}
    for key in ("astrolift.io/workload", "app.kubernetes.io/component"):
        v = labels.get(key)
        if v:
            return str(v)
    name_label = labels.get("app.kubernetes.io/name")
    if name_label and name_label != app_slug:
        return str(name_label)
    refs = getattr(metadata, "owner_references", None) or []
    for ref in refs:
        if getattr(ref, "controller", False):
            return str(getattr(ref, "name", "") or "")
    return ""


def _to_container_statuses(raw_statuses) -> list[ContainerStatusInfo]:
    out: list[ContainerStatusInfo] = []
    for cs in raw_statuses or []:
        state = "unknown"
        waiting_reason = ""
        terminated_reason = ""
        s = getattr(cs, "state", None)
        if s is not None:
            if getattr(s, "running", None) is not None:
                state = "running"
            elif getattr(s, "waiting", None) is not None:
                state = "waiting"
                waiting_reason = getattr(s.waiting, "reason", "") or ""
            elif getattr(s, "terminated", None) is not None:
                state = "terminated"
                terminated_reason = getattr(s.terminated, "reason", "") or ""
        out.append(
            ContainerStatusInfo(
                name=getattr(cs, "name", "") or "",
                ready=bool(getattr(cs, "ready", False)),
                restart_count=int(getattr(cs, "restart_count", 0) or 0),
                image=getattr(cs, "image", "") or "",
                state=state,
                waiting_reason=waiting_reason,
                terminated_reason=terminated_reason,
            )
        )
    return out


class _RealPodBackend:
    """Default backend backed by the live kubernetes-client lib."""

    def list_pods(
        self,
        *,
        cluster: TenantCluster,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise ClusterClientError(
                "kubernetes python client is not installed",
            ) from exc

        cc = client_for_cluster(cluster)
        core_v1 = k8s_client.CoreV1Api(cc.api_client)

        label_selector = f"astrolift.io/app={app_slug}"
        resp = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
            timeout_seconds=10,
        )
        items = list(getattr(resp, "items", []) or [])
        out: list[PodInfo] = []
        for pod in items:
            metadata = getattr(pod, "metadata", None)
            status = getattr(pod, "status", None)
            spec = getattr(pod, "spec", None)
            statuses = _to_container_statuses(getattr(status, "container_statuses", None) if status else None)
            phase = (getattr(status, "phase", "") if status else "") or ""
            ready = bool(statuses) and all(c.ready for c in statuses)
            restarts = sum(c.restart_count for c in statuses)
            out.append(
                PodInfo(
                    name=getattr(metadata, "name", "") or "" if metadata else "",
                    workload=_workload_slug_for(metadata, app_slug) if metadata else "",
                    status=_classify_status(phase, statuses),
                    phase=phase,
                    ready=ready,
                    restarts=restarts,
                    age=getattr(metadata, "creation_timestamp", None) if metadata else None,
                    node=getattr(spec, "node_name", "") or "" if spec else "",
                    container_statuses=statuses,
                )
            )
        return out


_BACKEND: PodBackend = _RealPodBackend()


def set_pod_backend(backend: PodBackend) -> None:
    """Swap the pod backend — tests use this to inject a fake."""
    global _BACKEND
    _BACKEND = backend


def get_pod_backend() -> PodBackend:
    return _BACKEND


def reset_pod_backend() -> None:
    """Restore the default real backend. Tests use this for teardown."""
    global _BACKEND
    _BACKEND = _RealPodBackend()


# ---- Public entry -------------------------------------------------


def list_app_pods(*, cluster: TenantCluster, namespace: str, app_slug: str) -> list[PodInfo]:
    """Resolver-facing entry point. Resolver layer is responsible
    for catching :class:`ClusterClientError` if the cluster row
    can't be turned into a usable client."""
    return _BACKEND.list_pods(
        cluster=cluster,
        namespace=namespace,
        app_slug=app_slug,
    )


# Helper a Callable factory can use to install a function as a backend.
def install_pod_backend_function(
    fn: Callable[..., list[PodInfo]],
) -> None:
    class _FnBackend:
        def list_pods(self, **kw):
            return fn(**kw)

    set_pod_backend(_FnBackend())
