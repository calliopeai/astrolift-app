"""ClusterDriver protocol -- apply/get/delete Kubernetes objects in a target cluster."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


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


# ---- Runtime observability (#299) ---------------------------------
#
# ``list_pods`` + ``stream_logs`` let resolver-entry surfaces (the
# observability page, the on-app-log subscription) read live pod
# state directly from a tenant cluster without importing the
# kubernetes SDK at the call site. The cloud-specific auth dance
# stays inside the per-cloud driver implementation; the call site
# only carries the runtime auth blob the operator pinned on the
# TenantCluster row.


@dataclass(frozen=True)
class ClusterAuth:
    """Runtime auth payload for a tenant cluster.

    Mirrors what the platform persists on ``TenantCluster.auth_method``
    + ``auth_config`` so the lifecycle resolver can build the input
    without the driver needing to import Django models. The shape is
    deliberately small — anything cloud-specific (IRSA role ARN, GKE
    Workload Identity binding, AKS federated issuer) is resolved
    *before* this dataclass is built and lands in ``auth_config`` as
    a kubeconfig blob or a bearer token.
    """

    slug: str
    """Cluster slug — used in error messages so a misconfigured row
    is identifiable in logs."""

    auth_method: str
    """One of ``kubeconfig`` / ``service_account_token`` /
    ``exec_plugin``. The driver decides whether it can satisfy the
    method or raises with a clear error."""

    auth_config: dict[str, Any] = field(default_factory=dict)
    """Method-specific credential material. Shape per method:
      kubeconfig: {"kubeconfig": "<yaml>", "context": "<name?>"}
      service_account_token: {"token": "<bearer>", "ca_cert": "<pem?>"}
      exec_plugin: handled by the cloud-specific subclass.
    """

    endpoint: str = ""
    """Cluster API server URL. Required for ``service_account_token``
    auth; ignored when ``kubeconfig`` already carries it."""

    ca_cert: str = ""
    """Optional row-level CA. Falls back to ``auth_config.ca_cert``
    when not set on the row."""

    namespace_prefix: str = ""
    """Hint for namespace resolution; the caller computes the actual
    per-app namespace and passes it through."""


@dataclass(frozen=True)
class ContainerStatusInfo:
    """One container's status within a Pod.

    ``state`` is the surface union: ``running`` / ``waiting`` /
    ``terminated`` / ``unknown``. ``waiting_reason`` is populated when
    state is ``waiting`` (CrashLoopBackOff, ImagePullBackOff, etc.);
    ``terminated_reason`` when state is ``terminated`` (Completed,
    OOMKilled, Error). Empty string when not applicable."""

    name: str
    ready: bool
    restart_count: int
    image: str
    state: str
    waiting_reason: str = ""
    terminated_reason: str = ""


@dataclass(frozen=True)
class PodInfo:
    """A single pod's live state, namespace-scoped to one app.

    Surface-friendly view — the GraphQL type maps straight onto this.
    ``status`` is the rolled-up worst of (phase, container waiting /
    terminated reasons); ``phase`` is the raw value from k8s.
    """

    name: str
    workload: str
    """Owning workload slug. Best-effort: pulled from
    ``astrolift.io/workload`` label, falling back to the
    owner-reference's controller name."""

    status: str
    phase: str
    ready: bool
    restarts: int
    age: datetime | None
    node: str
    container_statuses: list[ContainerStatusInfo] = field(default_factory=list)


# ---- Bring-into-management (#316) ---------------------------------
#
# ``ClusterContext`` carries the row's identifying + auth metadata into
# the management driver — equivalent to ``ClusterAuth`` but extended
# with the slug fields the workflow needs for logging + idempotency
# keys. ``ManagementReport`` is the structured return; the workflow
# persists ``capabilities`` onto the TenantCluster row and surfaces
# ``messages`` + ``error`` in the management report card.


@dataclass(frozen=True)
class ClusterContext:
    """Identifying + auth metadata for a tenant cluster, plus the hint
    fields the workflow needs to apply RBAC and run the probe.

    The shape is deliberately a strict superset of ``ClusterAuth`` so
    cloud-specific subclasses that already build auth from a kubeconfig
    blob can use ``ClusterContext.to_auth()`` without re-deriving any
    fields.
    """

    slug: str
    """Cluster slug — logged on every step + used in resource names."""

    auth_method: str
    """One of ``kubeconfig`` / ``service_account_token`` / ``exec_plugin``."""

    auth_config: dict[str, Any] = field(default_factory=dict)
    """Method-specific credential material; see ``ClusterAuth.auth_config``."""

    endpoint: str = ""
    """Cluster API server URL — required for ``service_account_token``
    auth, ignored when the kubeconfig already carries it."""

    ca_cert: str = ""
    """Optional row-level CA. Falls back to ``auth_config.ca_cert``."""

    ingress_class: str = ""
    """Operator's declared ingress class on the row. The probe matches
    this against the controllers it finds so a mis-declared class
    surfaces as a probe finding rather than a deploy-time error."""

    provider_plugin_slug: str = ""
    """Surfaces the provider plugin in error messages; not used for auth."""

    def to_auth(self) -> ClusterAuth:
        """Project this context onto the ``ClusterAuth`` shape used by
        ``build_api_client``. Workflow + driver share the conversion
        so a context built from a TenantCluster row reuses the same
        auth-validation path the observability surface already uses."""
        return ClusterAuth(
            slug=self.slug,
            auth_method=self.auth_method,
            auth_config=dict(self.auth_config),
            endpoint=self.endpoint,
            ca_cert=self.ca_cert,
        )


@dataclass(frozen=True)
class ManagementReport:
    """Outcome of a single ``bring_into_management`` run.

    ``success`` is the workflow's pass/fail gate — anything false
    flips the row to ``error`` lifecycle. ``rbac_applied`` records
    whether the platform RBAC manifests landed; the probe + preflight
    can still fail after a successful apply, so the boolean is
    independent of ``success``. ``capabilities`` is the merged result
    of the capability probe and is persisted onto the TenantCluster
    row's ``capabilities`` JSONField. ``preflight_status`` is one of
    ``passed`` / ``failed`` / ``skipped`` (skipped on idempotent
    re-runs against managed clusters when ``force_preflight=False``).
    ``messages`` is a structured list rendered in the management
    report card; ``error`` is non-empty only when ``success=False``.
    """

    success: bool
    rbac_applied: bool
    capabilities: dict[str, Any]
    preflight_status: str
    error: str | None = None
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PodLogLine:
    """One log line streamed from a pod/container.

    Distinct from ``_sdk.log_stream.LogLine`` — that is for the
    Loki / CloudWatch query-based aggregator surface. This one is
    the kubelet-stream-API shape (per-pod, per-container, follow=true)
    used by the on-app-log subscription.
    """

    pod_name: str
    container: str
    timestamp: datetime
    message: str
    stream: str
    """``stdout`` or ``stderr``. The kubernetes log API doesn't
    separate the two on the wire, so the live backend reports
    everything as ``stdout``; test fakes can emit ``stderr``."""


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

    # ---- Runtime observability (#299) -----------------------------
    #
    # The resolver layer calls into these two with a ``ClusterAuth``
    # already built from a ``TenantCluster`` row. Implementations
    # MUST tolerate transient failures by raising — the resolver
    # swallows exceptions and renders an empty UI rather than
    # surfacing a stack trace to the operator.

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]:
        """Return live pods in ``namespace`` filtered to ``app_slug``.

        Filter key is the ``astrolift.io/app`` label — workloads
        rendered by the manifest layer always carry it. Returns an
        empty list when no pods match (a new app, scaled-to-zero
        deployment); raises on auth / network / cluster errors and
        lets the caller decide whether to surface or swallow.
        """
        ...

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
        """Stream log lines for ``pod_name`` (optionally one container).

        Implementations MUST respect ``asyncio.CancelledError`` and
        ``GeneratorExit`` so a subscriber-disconnect cleanly tears
        down the underlying connection — log streams pin a pod's
        kubelet-stream socket and leak resources fast otherwise.

        ``tail_lines`` is the initial replay; ``follow=True`` keeps
        the stream open afterwards.
        """
        ...

    # ---- Bring-into-management (#316) -----------------------------
    #
    # ``probe_capabilities`` is read-only: it inspects CRDs +
    # well-known namespaces and returns the structured capability
    # shape the workflow persists onto ``TenantCluster.capabilities``.
    #
    # ``bring_into_management`` is write-side: it applies the
    # platform RBAC (``astrolift-system`` namespace +
    # ``astrolift-control-plane`` SA + ClusterRole + ClusterRoleBinding),
    # runs the capability probe, executes a one-shot preflight Job
    # (unless ``run_preflight=False``), and returns a
    # ``ManagementReport``. Implementations MUST be idempotent —
    # re-running on a managed cluster reconciles RBAC + re-probes
    # without recreating fresh resources.

    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        """Inspect the cluster and return the capability shape
        documented in ``ManagementReport.capabilities``.

        Raises on auth / reachability failure; the workflow's
        ``verify_reachability`` activity is expected to gate this
        call, but a defensive raise here lets one-shot CLI callers
        skip the gate and still get a useful error.
        """
        ...

    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        """Apply platform RBAC + probe capabilities + (optionally)
        run a one-shot preflight Job against the cluster.

        ``run_preflight=False`` is used for idempotent re-runs on
        managed clusters (refresh) where the operator just wants a
        fresh probe + RBAC reconcile without paying the Job's
        60-second wall clock. The workflow flips this back to
        ``True`` when the resolver passes ``forcePreflight=true``.
        """
        ...
