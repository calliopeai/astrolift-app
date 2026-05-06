"""ClusterDriver protocol -- apply/get/delete Kubernetes objects in a target cluster."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ApplyResult:
    """Result of applying manifests to a cluster."""

    created: list[str]
    updated: list[str]
    unchanged: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


@dataclass(frozen=True)
class DeleteResult:
    """Result of deleting manifests from a cluster."""

    deleted: list[str]
    not_found: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


@dataclass(frozen=True)
class NamespaceState:
    name: str
    labels: dict[str, str]
    annotations: dict[str, str]
    phase: str


@dataclass(frozen=True)
class Namespace:
    name: str
    labels: dict[str, str]
    annotations: dict[str, str]


@dataclass(frozen=True)
class WorkloadStatus:
    kind: str
    name: str
    namespace: str
    ready_replicas: int
    desired_replicas: int
    conditions: list[dict[str, Any]]


@dataclass(frozen=True)
class RolloutResult:
    """Result of polling a rollout to completion."""

    success: bool
    kind: str
    name: str
    namespace: str
    message: str
    timed_out: bool


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class PortForwardSession:
    """Handle for an active port-forward session."""

    local_port: int
    remote_port: int

    def close(self) -> None: ...


class ClusterDriver(Protocol):
    """Protocol for applying, querying, and managing Kubernetes objects on a target cluster.

    Semantic guarantees:
    - apply_manifests is idempotent (server-side apply preferred; falls back to client-side patch).
    - ensure_namespace is idempotent.
    - poll_rollout ticks at least every 15s, times out at 10m by default.
    - All operations propagate errors as typed exceptions, not bare strings.
    """

    def apply_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> ApplyResult: ...

    def delete_manifests(
        self,
        cluster: str,
        namespace: str,
        manifests: list[dict[str, Any]],
    ) -> DeleteResult: ...

    def get_namespace(self, cluster: str, name: str) -> NamespaceState | None: ...

    def ensure_namespace(
        self,
        cluster: str,
        name: str,
        labels: dict[str, str],
        annotations: dict[str, str],
    ) -> Namespace: ...

    def delete_namespace(self, cluster: str, name: str, *, wait: bool = True) -> None: ...

    def get_workload_status(
        self,
        cluster: str,
        namespace: str,
        kind: str,
        name: str,
    ) -> WorkloadStatus: ...

    def poll_rollout(
        self,
        cluster: str,
        namespace: str,
        kind: str,
        name: str,
        timeout: int,
        *,
        on_tick: Callable[[WorkloadStatus], None] | None = None,
    ) -> RolloutResult: ...

    def exec_in_pod(
        self,
        cluster: str,
        namespace: str,
        pod: str,
        container: str,
        command: list[str],
    ) -> ExecResult: ...

    def port_forward(
        self,
        cluster: str,
        namespace: str,
        pod: str,
        ports: list[tuple[int, int]],
    ) -> PortForwardSession: ...
