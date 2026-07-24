"""ClusterDriver protocol -- apply/get/delete Kubernetes objects in a target cluster."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from datetime import datetime


@dataclass(frozen=True)
class ApplyError:
    """One per-manifest failure inside :class:`ApplyResult`.

    Carries the manifest's identifying fields plus the exception class
    name + message so the workflow can distinguish transient (HTTP 5xx,
    ``ConnectionError``, ``TimeoutError``) from permanent (HTTP 4xx,
    ``ValidationError``) failures without re-parsing free-text strings.
    Audit issue #603 -- the previous shape stringified the exception into
    ``errors: list[str]`` and the workflow had to substring-match to
    decide whether to retry.

    ``is_retryable`` is the driver's call. The default classifier
    (:func:`classify_apply_error`) maps the common kubernetes /
    requests / urllib3 exception types to a sensible default; drivers
    may override per call.
    """

    kind: str
    name: str
    namespace: str
    exception_type: str
    exception_message: str
    is_retryable: bool

    def __str__(self) -> str:
        """Match the old ``f\"{kind}/{name}: {exc}\"`` string shape.

        Preserves backwards-compat for log emission + any caller that
        ingested the historical ``errors: list[str]`` representation.
        """
        return f"{self.kind}/{self.name}: {self.exception_message}"


# Exception-class name patterns the audit's recommended classifier treats
# as transient. The list is conservative on purpose -- a false-positive
# permanent-classification keeps the workflow from retrying, which is the
# safer default than retrying a hard validation failure forever.
_TRANSIENT_EXCEPTION_NAMES = frozenset(
    {
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "TimeoutError",
        "ReadTimeoutError",
        "ConnectTimeoutError",
        "ProtocolError",
        "ChunkedEncodingError",
        "MaxRetryError",
        "ServiceUnavailable",
        "InternalServerError",
        "ServerSelectionTimeoutError",
    }
)


def classify_apply_error(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is transient and the workflow should retry.

    Distinguishes:

    * HTTP 5xx + ``ConnectionError`` + ``TimeoutError`` family -> ``True``
    * HTTP 4xx + ``ValidationError`` / ``ValueError`` / ``TypeError`` -> ``False``

    The kubernetes Python client raises ``ApiException`` with a ``status``
    attribute on HTTP errors; the classifier prefers the status when it's
    set, falling back to the exception class name match. Anything outside
    the known transient set is treated as permanent so the workflow doesn't
    retry hard validation failures forever.
    """
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        if 500 <= status < 600:
            return True
        if 400 <= status < 500:
            return False
    return type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES


@dataclass(frozen=True)
class ApplyResult:
    """Result of applying manifests to a cluster.

    ``errors`` carries structured :class:`ApplyError` rows so the workflow
    can classify failures + decide retry policy without re-parsing strings.
    The legacy ``list[str]`` representation is still emitted by
    :meth:`summary` for log lines + downstream consumers that haven't
    migrated.
    """

    created: list[str]
    updated: list[str]
    unchanged: list[str]
    errors: list[ApplyError]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> list[str]:
        """Return the legacy ``f\"{kind}/{name}: {exc}\"`` string list.

        Use when emitting a free-text log line or when an external
        consumer still expects the historical shape. Audit issue #603 --
        new code should iterate ``errors`` directly to read
        ``is_retryable`` + ``exception_type``.
        """
        return [str(e) for e in self.errors]

    @property
    def has_retryable(self) -> bool:
        """``True`` when at least one error is retryable.

        Workflow uses this to pick between ``ActivityFailure`` (re-raise
        for retry) and ``ApplicationError`` (terminal, surface to the
        UI).
        """
        return any(e.is_retryable for e in self.errors)


@dataclass(frozen=True)
class DeleteResult:
    """Result of deleting manifests from a cluster.

    ``errors`` stays ``list[str]`` here -- the per-manifest delete path
    doesn't surface the same retry-class distinction the apply path
    needs. ``.summary()`` mirrors :class:`ApplyResult` so polymorphic
    callers (managed-service drivers that compose apply + delete) can
    use the same accessor regardless of which result type they got.
    """

    deleted: list[str]
    not_found: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> list[str]:
        return list(self.errors)


@dataclass(frozen=True)
class NamespaceState:
    name: str
    labels: dict[str, str]
    annotations: dict[str, str]
    phase: str


@dataclass(frozen=True)
class StorageClassInfo:
    name: str
    is_default: bool


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
class ContainerResources:
    """Per-container resource requests + limits, surfaced exactly as
    the cluster reports them (cores / kubernetes resource-quantity
    strings — ``"100m"``, ``"512Mi"``, etc.). Empty string means the
    field wasn't set on the pod spec.

    Surfaced separately from ``ContainerStatusInfo`` so the workload-
    detail page can show istio-proxy / linkerd-proxy / open-telemetry
    sidecar resource cost without those numbers blurring into the
    primary container's budget."""

    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""


@dataclass(frozen=True)
class ContainerStatusInfo:
    """One container's status within a Pod.

    ``state`` is the surface union: ``running`` / ``waiting`` /
    ``terminated`` / ``unknown``. ``waiting_reason`` is populated when
    state is ``waiting`` (CrashLoopBackOff, ImagePullBackOff, etc.);
    ``terminated_reason`` when state is ``terminated`` (Completed,
    OOMKilled, Error). Empty string when not applicable.

    ``kind`` classifies the container slot:
      - ``init`` — declared under ``spec.initContainers``.
      - ``primary`` — the workload's main container (name matches the
        workload slug or, failing that, the first non-init container
        in the spec).
      - ``sidecar`` — every other ``spec.containers`` slot. Service
        meshes (istio-proxy, linkerd-proxy) and log shippers fall
        here.

    ``last_restart_reasons`` is the last *up to three* k8s
    ``lastState.terminated.reason`` values observed by the apiserver
    (``OOMKilled`` / ``Error`` / ``ContainerCannotRun`` / ``Unhealthy``
    probe). The k8s API surfaces only the most recent restart on each
    poll, so a flapping container's history is necessarily
    best-effort — the surface still gives an operator the *what* of
    the latest crash, which is the highest-signal datum for incident
    response.

    ``last_restart_at`` is the timestamp of the most recent restart
    (``lastState.terminated.finishedAt``); used by the UI to badge
    flapping pods (count > 5 in the last hour).

    ``resources`` carries the spec-side requests + limits joined into
    the status payload at backend-build time. We join on container
    name so the resolver only has to fan out one query per pod."""

    name: str
    ready: bool
    restart_count: int
    image: str
    state: str
    waiting_reason: str = ""
    terminated_reason: str = ""
    kind: str = "primary"
    last_restart_reasons: list[str] = field(default_factory=list)
    last_restart_at: datetime | None = None
    resources: ContainerResources = field(default_factory=ContainerResources)


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
class JobStatus:
    """Read-only snapshot of a batch/v1 Job's ``status`` sub-resource.

    Projected from ``read_namespaced_job_status`` so a caller can decide
    whether a Job is still running, succeeded, or failed without importing
    the kubernetes client model types. All counts default to ``0`` and the
    timestamps to ``None`` so a Job whose pod hasn't scheduled yet (empty
    ``status``) reads as "nothing determined yet" rather than raising.

    ``conditions`` carries the human-readable messages off the Job's
    ``status.conditions`` (e.g. ``"Job has reached the specified backoff
    limit"``) — the best diagnostic available from a pure status read,
    since the container exit code lives on the pod, not the Job.
    """

    active: int = 0
    succeeded: int = 0
    failed: int = 0
    start_time: datetime | None = None
    completion_time: datetime | None = None
    conditions: tuple[str, ...] = ()


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


# ---- Cluster teardown (#337) --------------------------------------
#
# Inverse of ``bring_into_management``: deletes the cluster's
# *infrastructure* (EKS / GKE / AKS managed cluster, node pools, the
# cluster row's bound cloud resources). Distinct from
# ``DecommissionClusterWorkflow`` which only removes the platform's
# RBAC bundle from the cluster — that leaves the cluster running and
# operator-owned. Teardown is the "delete the cluster entirely" flow.
#
# Returns a structured report so the workflow can record the outcome
# on the TenantCluster row and the UI can surface what was actually
# deleted (or skipped — bare-metal clusters return an "operator-owned;
# nothing to delete" no-op result).


@dataclass(frozen=True)
class TeardownReport:
    """Outcome of ``teardown_cluster``.

    ``deleted`` is the list of cloud resource identifiers the driver
    removed (cluster ARN, node group names, etc.). ``skipped`` is
    resources the driver elected not to touch (operator-owned, in
    use, manual cleanup required). ``messages`` is operator-facing
    info to surface in the UI. ``error`` is set when the teardown
    failed mid-flight — the workflow flips lifecycle to ``error``
    and persists the message.
    """

    success: bool
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    error: str = ""


# ---- Bootstrap recipe (cluster prereqs install) -------------------
#
# After a cluster is brought into management the platform offers an
# install step that lays down the controllers + operators tenant
# deploys depend on. The recipe is driver-specific — each provider
# knows which components the cloud already provides natively, which
# need helm-install on the cluster, and how to wire each to the
# cloud's auth model (IRSA on AWS, Workload Identity on GCP, Federated
# Identity on Azure, rfc2136 / self-signed on bare metal).


@dataclass(frozen=True)
class BootstrapOption:
    """One operator-pickable sub-choice on a BootstrapComponent.

    Example: ``tls_issuer.mode = acm | acme_letsencrypt_prod |
    acme_letsencrypt_staging | self_signed``. The UI renders a select;
    the workflow merges the chosen value into ``helm_values`` before
    installing.
    """

    key: str
    """Stable identifier — used as the override key the operator sends
    back when picking a value (e.g. ``mode``, ``ingress_class``)."""

    label: str
    """Human-readable label shown next to the select in the UI."""

    choices: list[tuple[str, str]] = field(default_factory=list)
    """``[(value, label), ...]`` — first element is the wire value the
    workflow consumes, second is the UI label."""

    default: str = ""
    """Default ``value`` (from ``choices``) when the operator doesn't
    explicitly pick one."""


@dataclass(frozen=True)
class BootstrapComponent:
    """One installable prerequisite in the driver's recipe.

    ``helm_values`` is the *pre-tuned* default value set the workflow
    will pass to ``helm install`` if the operator enables this
    component. Driver implementations bake provider-specific knobs
    (IRSA role ARN, managed-DNS provider, ACM cert handling, etc.)
    into this dict so the operator doesn't have to know.
    """

    key: str
    """Stable identifier; used as the HelmRelease name suffix
    (``astrolift-{key}``) and the key the operator sends back
    in ``option_overrides`` (e.g. ``cert-manager``,
    ``external-dns``, ``kube-prometheus-stack``)."""

    title: str
    """Human-readable name shown in the UI checkbox list."""

    default_enabled: bool
    """Driver's opinion on whether this should be on by default for
    THIS provider. Operators can flip it off."""

    rationale: str
    """One-line explanation of *why* this is enabled / skipped on this
    provider. Surfaced as the helper text under the checkbox so
    operators learn the design as they read."""

    helm_values: dict[str, Any] = field(default_factory=dict)
    """Pre-tuned helm values for the subchart this component installs.
    The workflow merges operator overrides on top before invoking helm."""

    requires: list[str] = field(default_factory=list)
    """Preconditions the workflow checks before installing — e.g.
    ``irsa:cert-manager`` (an IRSA role must exist on AWS), or
    ``subzone:dns`` (operator must have delegated a DNS subzone)."""

    options: list[BootstrapOption] = field(default_factory=list)
    """Operator-pickable sub-options for this component. Empty list
    means no sub-choices; the component is on/off only."""

    chart_name: str = ""
    """Helm chart name to install for this component.
    Empty string means no HelmRelease is emitted (e.g. cloud-native
    annotation-based TLS — ACM on EKS, GKE Managed Certs, AppGW on AKS)."""

    chart_repo_url: str = ""
    """Helm repository URL. Empty when ``chart_name`` is empty."""

    chart_repo_type: str = "default"
    """``"default"`` for HTTP Helm repos, ``"oci"`` for OCI registries."""

    chart_version: str = ""
    """Pinned chart version. Empty means Flux fetches latest (not
    recommended for production — always pin to a tested release)."""

    install_timeout: str = "5m"
    """Flux HelmRelease install/upgrade timeout. Override for heavy charts
    (e.g. kube-prometheus-stack) that take longer than the 5-minute default
    to reach a ready state, especially on Fargate where image pulls are cold."""

    depends_on: list[str] = field(default_factory=list)
    """Component keys that must reach Ready=True before Flux starts this
    component's reconciliation. Maps to the Flux HelmRelease ``dependsOn``
    field. Use when one component registers webhooks or CRDs that another
    component depends on at install time (e.g. kube-prometheus-stack waiting
    for aws-load-balancer-controller's webhook to be live)."""


@dataclass(frozen=True)
class PodPhaseSummary:
    """Pod-phase rollup for the Cluster Status tab (#68 slice 1).

    One row per (namespace, phase) bucket. The driver returns the
    aggregated count rather than streaming raw Pod objects so the
    workflow + UI never need to chunk through large fleets.
    """

    namespace: str
    phase: str
    """Kubernetes pod phase — ``Pending`` / ``Running`` / ``Succeeded``
    / ``Failed`` / ``Unknown``. Mirrored verbatim from the API."""

    count: int


@dataclass(frozen=True)
class ClusterEvent:
    """A recent Kubernetes Event surfaced to the operator for triage.

    Mirrored 1:1 from the API; the driver filters to ``Warning`` type
    by default but the contract lets the workflow request a broader
    range. ``involved_object`` is the ``kind/name`` of the object the
    event targets ("Pod/api-7d-x9k1q") so the UI can deep-link.
    """

    namespace: str
    name: str
    reason: str
    message: str
    type: str  # Normal | Warning
    count: int
    """K8s coalesces repeated events; this is the running count."""

    first_seen: str  # RFC3339 timestamp
    last_seen: str
    involved_object: str


@dataclass(frozen=True)
class RegionInfo:
    """One selectable cloud region for the cluster-register picker (#860).

    ``id`` is the wire-form slug the platform persists on
    ``TenantCluster.region`` (``us-west-2`` / ``us-central1`` /
    ``eastus``). ``label`` is the operator-facing display name
    (``US West (Oregon)``). ``continent`` is an optional grouping
    string (``Americas`` / ``Europe`` / ``Asia Pacific`` / ``Middle
    East`` / ``Africa``) the UI can use to bucket long lists; empty
    when the driver can't classify the region.
    """

    id: str
    label: str
    continent: str = ""


@dataclass(frozen=True)
class CognitoUserPoolInfo:
    """One Cognito user pool surfaced to the auth-gate picker (#859).

    AWS-specific — only ``EKSClusterDriver`` returns these. ``pool_arn``
    is composed from the caller's account id + region + pool id (the
    ListUserPools API returns only ``Id`` + ``Name``, not the ARN).
    ``domain`` is the Cognito-hosted domain prefix from
    DescribeUserPool; empty when the pool has no hosted domain
    configured.
    """

    pool_id: str
    pool_arn: str
    name: str
    domain: str = ""
    region: str = ""


@dataclass(frozen=True)
class CognitoUserPoolClientInfo:
    """One app client within a Cognito user pool (#859).

    Returned by ``list_cognito_user_pool_clients`` once the operator
    has picked a pool in the auth-gate dialog.
    """

    client_id: str
    client_name: str


@dataclass(frozen=True)
class WorkloadHealth:
    """Per-Deployment health row for the Cluster Status tab (#362).

    The pod-phase rollup answers "is anything red"; this answers
    "which workload is red". One row per Deployment across the
    operator-facing namespaces; the driver leaves further
    aggregation (StatefulSet, DaemonSet) for a future protocol bump
    — the platform's current manifest renderer always uses
    Deployments for tenant workloads.

    ``restart_count_24h`` is the sum of container ``restartCount``
    across pods owned by the Deployment whose containers' last
    restart fell inside the trailing 24h. Drivers that can't compute
    the per-window restart count (no ContainerStatus parsing
    available) report ``0`` rather than failing; the UI distinguishes
    "0 restarts in 24h" from "no data" only when the whole row is
    absent.

    ``last_image_deployed_at`` is the Deployment's
    ``status.conditions[type=Progressing,reason=NewReplicaSetAvailable]``
    last-transition time, RFC3339-stringified. Empty string when the
    Deployment has never rolled (just created) or when the condition
    isn't present (older k8s versions).
    """

    namespace: str
    name: str
    desired_replicas: int
    ready_replicas: int
    restart_count_24h: int
    last_image_deployed_at: str


@dataclass(frozen=True)
class CertificateInfo:
    """One TLS certificate the cluster's provider can offer for an SNI /
    custom-domain binding (#858).

    Returned by the cloud-specific ``list_certificates`` method (AWS →
    ACM; GCP → Certificate Manager; Azure → Key Vault). The cert picker
    in the UI stores ``arn`` (the cloud-native identifier the platform
    persists on the domain's ``sni_cert_ref`` / ``dns_config
    ['certificate_arn']``) and shows ``domain_name`` + ``status`` so the
    operator picks the right one without leaving the form.

    ``arn`` is the canonical identifier regardless of cloud — an ACM
    ARN on AWS, a Certificate Manager resource name on GCP, a Key Vault
    cert id on Azure. ``name`` is a short human label (often the cert's
    primary domain or a console-assigned name); ``domain_name`` is the
    primary subject (CN or first SAN). ``status`` is mirrored from the
    cloud (``ISSUED`` / ``PENDING_VALIDATION`` / ``ACTIVE`` / ...); the
    dispatch layer filters to issued/usable certs so the picker never
    offers a cert that can't terminate TLS yet.
    """

    arn: str
    name: str
    domain_name: str
    status: str


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

    def list_storage_classes(self, cluster: str) -> list[StorageClassInfo]:
        """Return the cluster's StorageClasses (name + whether default).

        Used to stamp a ``storageClassName`` on a StatefulSet's
        volumeClaimTemplate when the manifest omits ``storage_class`` and the
        cluster has no default-annotated SC (#1023), so stateful apps bind
        their PVCs without the operator knowing the cluster's SC name.
        Default returns ``[]`` — a driver that can't enumerate leaves the
        claim's storageClassName unset (same as before)."""
        return []

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
        task_id: str = "",
    ) -> list[PodInfo]:
        """Return live pods in ``namespace`` filtered to ``app_slug``.

        Filter key is the ``astrolift.io/app`` label — workloads
        rendered by the manifest layer always carry it. Returns an
        empty list when no pods match (a new app, scaled-to-zero
        deployment); raises on auth / network / cluster errors and
        lets the caller decide whether to surface or swallow.

        ``task_id`` (#891), when set, selects an agent task pod by its
        ``astrolift.dev/task-id`` label instead — the agent dispatch
        path labels Job pods with the task guid, not an app slug.
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

    def read_job_status(
        self,
        cluster: ClusterContext,
        *,
        namespace: str,
        job_name: str,
    ) -> JobStatus:
        """Read one batch/v1 Job's status. Read-only — never mutates the
        cluster.

        Used by the run-status reconciler to advance a non-terminal
        ScheduledJobRun / TaskRun to its terminal outcome. Raises on auth /
        reachability failure, and lets a not-found (404) propagate — the
        reconciler treats any read failure as "can't determine, leave the
        row as-is" so a torn-down or GC'd Job never crashes the sweep.
        """
        ...

    # ---- Bootstrap recipe (#???: cluster prereqs install) -----------
    #
    # Once a cluster is ``managed``, the platform offers an opinionated
    # "make this cluster uniform" step that installs the controllers
    # and operators a tenant deploy depends on (cert-manager, ingress
    # controller, external-dns, metrics-server, Prometheus, etc.).
    # The list + their default helm values is provider-specific: AWS
    # leans on IRSA + ACM via the ALB controller; GCP uses managed
    # certs + Workload Identity; bare-metal uses ACME-LetsEncrypt or
    # self-signed cert-manager + MetalLB + Longhorn.
    #
    # ``bootstrap_components`` is the read-only declaration the
    # control plane fetches when rendering the install card on the
    # cluster detail page; the operator picks a subset + tweaks values
    # and fires ``InstallClusterPrereqsWorkflow`` which runs a one-shot
    # Job (the astrolift-cli image, chart embedded) in
    # ``astrolift-system`` with the resolved helm values.

    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """Return the provider's opinionated install recipe.

        Each component carries a key, default-enabled bool, rationale
        string explaining the choice for this provider, helm values
        pre-tuned for the provider (IRSA ARNs, regions, managed-cert
        wiring), preconditions, and optional sub-options the operator
        can pick from at install time (e.g. tls_issuer mode).
        """
        ...

    # ---- Cluster teardown (#337) ----------------------------------

    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Delete the cluster's cloud infrastructure.

        When ``delete_cloud_infra=False`` this is a no-op returning
        ``TeardownReport(success=True, skipped=[<cluster>])`` — the
        decommission workflow uses that mode for "remove platform
        management but leave the cluster running" (the historical
        decommission behavior).

        When ``delete_cloud_infra=True`` the driver deletes the
        managed cluster (eks.delete_cluster /
        container.delete_cluster / aks.delete_managed_cluster) plus
        any node pools the platform tagged as platform-managed.
        Bare-metal (``k8s_native``) returns ``success=True,
        skipped=[<cluster>]`` with an operator-facing message — the
        platform never owned the underlying nodes.

        Must be idempotent: re-running on a half-torn-down cluster
        reaches the same terminal state. The kube apiserver may
        become unreachable mid-flight; drivers should handle that
        gracefully (the cluster IS being deleted).
        """
        ...

    # ---- Cluster health (#68 slice 1) -----------------------------

    def list_pod_phase_summary(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
    ) -> list[PodPhaseSummary]:
        """Pod-phase rollup across the operator-facing namespaces.

        Drivers list pods (or query the API server's
        ``/api/v1/pods`` with the appropriate ``fieldSelector``) and
        aggregate by ``(namespace, phase)``. The Cluster Status tab
        renders the result as a one-glance "is anything red".

        ``namespaces=None`` defaults to ``["astrolift-system"]`` plus
        every namespace currently bound to a platform-managed
        AppEnvironment — drivers don't filter on the caller's behalf,
        the workflow layer threads the list in.

        Bare-metal drivers without API-server credentials may return
        an empty list; the UI surfaces "no pod data available".
        """
        ...

    def list_events(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
        event_type: str | None = "Warning",
        limit: int = 50,
    ) -> list[ClusterEvent]:
        """Recent Kubernetes Events across the operator-facing
        namespaces, defaulting to ``Warning`` events for the typical
        triage flow. ``event_type=None`` returns all types.

        Drivers return at most ``limit`` events, newest first, with
        the standard fields the API surfaces (reason, message, count
        of repeats, first/last seen, involved object). Bare-metal
        drivers without API-server credentials may return an empty
        list — the UI's empty-state copy covers that case.
        """
        ...

    def list_workload_health(
        self,
        cluster: ClusterContext,
        *,
        namespaces: list[str] | None = None,
    ) -> list[WorkloadHealth]:
        """Per-Deployment health rollup across the operator-facing
        namespaces (#362). Drivers list Deployments via the apps/v1
        API and aggregate restart counts by walking the owned pods.

        ``namespaces=None`` follows the same fallback as the pod /
        event methods (``astrolift-system`` only) — the workflow
        layer is responsible for threading in the bound app
        namespaces. Bare-metal drivers without API-server credentials
        return an empty list; the UI surfaces "no workload data".
        """
        ...

    # ---- Region / Cognito discovery (#860 / #859) -----------------
    #
    # These three feed operator-facing pickers that replace free-text
    # entry on the cluster-register dialog (region) and the ingress
    # auth-gate card (Cognito pool / client). They're discovery reads —
    # no cluster mutation — and degrade to free-entry on the frontend
    # when the driver can't satisfy them, so implementations may raise
    # on credential / API failure and the resolver layer swallows it.
    #
    # ``list_regions`` is the only one with a sensible cross-cloud
    # default (a curated static list), so the SDK ships a default
    # implementation; drivers override it to go live (EKS:
    # ec2:DescribeRegions). The two Cognito methods are AWS-only — the
    # default returns an empty list so non-AWS drivers don't have to
    # implement them and the resolver renders the free-entry fallback.

    def list_regions(self) -> list[RegionInfo]:
        """Return the provider's selectable regions for the register
        picker (#860).

        No cluster context — this is called with a driver built from
        the provider plugin slug alone (a bootstrap config). Live
        implementations (EKS: ``ec2:DescribeRegions``) MUST fall back
        to a curated static list on API / credential failure rather
        than raising, so the picker always has options. The default
        returns an empty list — drivers without a region concept
        (k8s_native) inherit it untouched.
        """
        return []

    def list_cognito_user_pools(self) -> list[CognitoUserPoolInfo]:
        """Return the Cognito user pools reachable in the cluster's
        region (#859). AWS-only; the default returns an empty list so
        non-AWS drivers don't implement it and the auth-gate picker
        falls back to free-entry. Live implementations raise on
        credential / API failure; the resolver swallows it."""
        return []

    def list_cognito_user_pool_clients(self, pool_id: str) -> list[CognitoUserPoolClientInfo]:
        """Return the app clients within ``pool_id`` (#859). AWS-only;
        default returns an empty list. Live implementations raise on
        credential / API failure; the resolver swallows it."""
        return []
