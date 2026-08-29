"""AWS EKS ClusterDriver (#29).

Spec ref: spec 23-provider-plugin-aws + _sdk/cluster.py.

EKS auth flow: the driver calls boto3 EKS DescribeCluster to get
the cluster's API endpoint + CA, then calls EKS GetToken (via
the local AWS-IAM-authenticator helper or direct STS) to get a
short-lived bearer token. The kubernetes Python client uses
those to talk to the cluster's API server.

Token refresh: tokens expire ~15 minutes after signing. The
apply-manifests path mints fresh on every operation (high
amortized cost is fine for low-frequency lifecycle activities);
the runtime-observability path (``list_pods`` / ``stream_logs``,
#299) mints lazily and caches per (cluster_name, region) for
~13 minutes so back-to-back resolver calls don't pay the STS
round trip each time. Cache is in-process on the driver
instance — long-lived workers benefit; short CLI invocations
get a single mint anyway.

This module is the platform's authoritative way to apply
manifests, manage namespaces, and observe rollouts. Other
drivers (ALB ingress, managed services bind step) compose on
top of it.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

from _sdk._telemetry import driver_op, maybe_heartbeat
from _sdk.cloud_credentials import CredentialedConfig
from _sdk.cluster import (
    ApplyError,
    ApplyResult,
    BootstrapComponent,
    BootstrapOption,
    CertificateInfo,
    ClusterAuth,
    ClusterContext,
    ClusterDriver,
    CognitoUserPoolClientInfo,
    CognitoUserPoolInfo,
    DeleteResult,
    ExecResult,
    JobStatus,
    ManagedModelNotSupportedError,
    ManagementReport,
    Namespace,
    NamespaceState,
    PodInfo,
    PodLogLine,
    PortForwardSession,
    RegionInfo,
    RolloutResult,
    StorageClassInfo,
    TeardownReport,
    WorkloadStatus,
    classify_apply_error,
    workload_status_from_object,
)

# Shared helper re-exports. ``_RealK8sClient`` + ``_NotFoundError`` are
# the names this module's call sites use; ``_DEFAULT_API_VERSION_FOR_KIND``,
# ``_PortForwardHandle``, ``_split_kind`` are kept as re-exports because
# ``tests/aws/test_real_k8s_client.py`` imports them from here (the
# helper-body tests live under ``tests/_sdk/`` now; the AWS test file
# is preserved as-is so the refactor doesn't churn 36 tests).
from _sdk.k8s_dynamic_client import (
    DEFAULT_API_VERSION_FOR_KIND as _DEFAULT_API_VERSION_FOR_KIND,  # noqa: F401
)
from _sdk.k8s_dynamic_client import (
    KubernetesDynamicClient as _RealK8sClient,
)
from _sdk.k8s_dynamic_client import (
    NotFoundError as _NotFoundError,
)
from _sdk.k8s_dynamic_client import (
    PortForwardHandle as _PortForwardHandle,  # noqa: F401
)
from _sdk.k8s_dynamic_client import (
    split_kind as _split_kind,  # noqa: F401
)
from aws._eks_auth import mint_eks_token
from aws._errors import NotFoundError, map_client_error
from aws._knative import KNATIVE_OPERATOR_MANIFESTS
from aws._naming import iam_role_name
from aws.session import aws_client
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

log = logging.getLogger("astrolift_providers.aws.cluster_eks")

# ---- Managed model (Bedrock) defaults -------------------------------
#
# Default Bedrock model ids injected on the managed-model agent path
# (Claude Code on Bedrock reads ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL).
# A Claude Opus + Claude Haiku pair, given as cross-region *inference
# profile* ids (the ``us.`` geo prefix — Bedrock's on-demand Claude models
# are only invokable through an inference profile, not the bare model id).
# Overridable per-cluster via ``provider_config["bedrock_model_id"]`` /
# ``["bedrock_small_fast_model_id"]``, then process-wide via
# ``ANTHROPIC_MODEL`` / ``ANTHROPIC_SMALL_FAST_MODEL``. The environment
# fallback lets operators replace a retired model without an Astrolift image
# release; per-cluster settings remain the strongest override for non-US
# partitions and installations with different model-access policy.
_DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-opus-5"
_DEFAULT_BEDROCK_SMALL_FAST_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _first_nonblank(*values: object) -> str:
    """Return the first non-empty string-like configuration value."""
    for value in values:
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return ""


# ---- Knative Serving request-log template (#kind=function) ----------
#
# A Go text/template evaluated by Knative's queue-proxy for every request
# to a ``kind=function`` workload (a serving.knative.dev Service). Emitting
# it as structured JSON on the pod's stdout pre-wires the *future* function
# invocation pipeline: a log tailer can parse one line into a
# ``FunctionInvocation`` row (method -> http_method, path -> http_path,
# status -> http_status_code, latency -> duration_ms, revision.podName ->
# k8s_pod_name). The template variable names below are Knative's documented
# request-log fields (``.Request.Method`` / ``.Request.RequestURI`` /
# ``.Response.Code`` / ``.Response.Latency`` / ``.Revision.*``) — the same
# ones Knative's built-in default template uses, so they're known-valid.
# ``{{js ...}}`` escapes user-controlled strings for safe JSON embedding.
_KNATIVE_REQUEST_LOG_TEMPLATE = (
    '{"httpRequest": {'
    '"requestMethod": "{{.Request.Method}}", '
    '"requestUrl": "{{js .Request.RequestURI}}", '
    '"status": {{.Response.Code}}, '
    '"latency": "{{.Response.Latency}}s", '
    '"protocol": "{{.Request.Proto}}"}, '
    '"revision": {'
    '"name": "{{.Revision.Name}}", '
    '"service": "{{.Revision.Service}}", '
    '"namespace": "{{.Revision.Namespace}}", '
    '"podName": "{{.Revision.PodName}}"}}'
)

# Region slug -> (display label, continent grouping) for the
# cluster-register picker (#860). ec2:DescribeRegions returns only the
# slug + endpoint, so the friendly label + continent are mapped here.
# Regions absent from this table still surface (the slug doubles as the
# label) — the table is a UX nicety, not an allow-list. Continent
# strings match the GCP/Azure static tables so the UI can bucket all
# three clouds consistently.
_AWS_REGION_LABELS: dict[str, tuple[str, str]] = {
    "us-east-1": ("US East (N. Virginia)", "Americas"),
    "us-east-2": ("US East (Ohio)", "Americas"),
    "us-west-1": ("US West (N. California)", "Americas"),
    "us-west-2": ("US West (Oregon)", "Americas"),
    "ca-central-1": ("Canada (Central)", "Americas"),
    "ca-west-1": ("Canada West (Calgary)", "Americas"),
    "sa-east-1": ("South America (São Paulo)", "Americas"),
    "mx-central-1": ("Mexico (Central)", "Americas"),
    "eu-west-1": ("Europe (Ireland)", "Europe"),
    "eu-west-2": ("Europe (London)", "Europe"),
    "eu-west-3": ("Europe (Paris)", "Europe"),
    "eu-central-1": ("Europe (Frankfurt)", "Europe"),
    "eu-central-2": ("Europe (Zurich)", "Europe"),
    "eu-north-1": ("Europe (Stockholm)", "Europe"),
    "eu-south-1": ("Europe (Milan)", "Europe"),
    "eu-south-2": ("Europe (Spain)", "Europe"),
    "ap-east-1": ("Asia Pacific (Hong Kong)", "Asia Pacific"),
    "ap-south-1": ("Asia Pacific (Mumbai)", "Asia Pacific"),
    "ap-south-2": ("Asia Pacific (Hyderabad)", "Asia Pacific"),
    "ap-northeast-1": ("Asia Pacific (Tokyo)", "Asia Pacific"),
    "ap-northeast-2": ("Asia Pacific (Seoul)", "Asia Pacific"),
    "ap-northeast-3": ("Asia Pacific (Osaka)", "Asia Pacific"),
    "ap-southeast-1": ("Asia Pacific (Singapore)", "Asia Pacific"),
    "ap-southeast-2": ("Asia Pacific (Sydney)", "Asia Pacific"),
    "ap-southeast-3": ("Asia Pacific (Jakarta)", "Asia Pacific"),
    "ap-southeast-4": ("Asia Pacific (Melbourne)", "Asia Pacific"),
    "ap-southeast-5": ("Asia Pacific (Malaysia)", "Asia Pacific"),
    "ap-southeast-7": ("Asia Pacific (Thailand)", "Asia Pacific"),
    "me-south-1": ("Middle East (Bahrain)", "Middle East"),
    "me-central-1": ("Middle East (UAE)", "Middle East"),
    "il-central-1": ("Israel (Tel Aviv)", "Middle East"),
    "af-south-1": ("Africa (Cape Town)", "Africa"),
}

# Fallback region list when ec2:DescribeRegions can't be called (no
# credentials, throttled, network). Covers the commercial-partition
# regions enabled by default on a standard account — enough for the
# register picker to be useful while the operator wires credentials.
_AWS_FALLBACK_REGIONS: tuple[str, ...] = (
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ca-central-1",
    "sa-east-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-south-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-southeast-1",
    "ap-southeast-2",
)


def _region_info(slug: str) -> RegionInfo:
    """Build a ``RegionInfo`` from a slug, decorating with the friendly
    label + continent from ``_AWS_REGION_LABELS`` when known."""
    label, continent = _AWS_REGION_LABELS.get(slug, (slug, ""))
    return RegionInfo(id=slug, label=label, continent=continent)


@dataclass(frozen=True)
class EKSConfig(CredentialedConfig):
    """AWS-specific EKS auth + connection config."""

    region: str
    cluster_name: str
    """The EKS cluster name (used in DescribeCluster + GetToken).
    Distinct from the platform's logical 'cluster' identifier."""

    sts_token_lifetime_seconds: int = 900
    """How long to ask STS to make the presigned URL valid for. EKS's
    IAM authenticator caps at 15 minutes (900s); the k8s client does
    not auto-retry on 401, so a longer window matches the EKS
    GetToken upper bound and avoids spurious mid-operation auth
    failures on first-rollout deploys (#359)."""

    exec_plugin_token_ttl_seconds: int = 13 * 60
    """How long the observability-path token cache holds a minted
    bearer before re-signing. The STS-presigned URL is valid for
    15 min by EKS protocol; 13 min gives a 2 min safety margin
    against clock skew + cluster-side acceptance windows."""


@dataclass
class _TokenCacheEntry:
    """One cached bearer token with its absolute expiry timestamp.

    ``expires_at`` is a monotonic-clock deadline so the cache is
    immune to wall-clock jumps (DST, NTP slew, container migration).
    """

    token: str
    expires_at: float


@dataclass
class _DescribeCacheEntry:
    """Endpoint + base64-CA cached from EKS DescribeCluster.

    Kept distinct from the bearer-token cache because DescribeCluster
    output rarely rotates (it changes when the operator rotates the
    cluster CA, which is rare and out-of-band). Cleared explicitly
    in ``invalidate_describe_cache`` if a caller ever needs to.
    """

    endpoint: str
    ca_data: str


class EKSClusterDriver(ClusterDriver):
    """boto3 + kubernetes-client backed EKS driver."""

    def __init__(
        self,
        *,
        config: EKSConfig,
        eks_client: Any | None = None,
        sts_client: Any | None = None,
        ec2_client: Any | None = None,
        cognito_idp_client: Any | None = None,
        acm_client: Any | None = None,
        iam_client: Any | None = None,
        k8s_client_factory: Callable[..., Any] | None = None,
        pod_backend: PodBackend | None = None,
        log_backend: LogBackend | None = None,
        management_backend: ManagementBackend | None = None,
        token_minter: Callable[[str, str], str] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        if eks_client is not None:
            self._eks = eks_client
        else:
            self._eks = aws_client("eks", region=config.region, credential=config.credential)
        if sts_client is not None:
            self._sts = sts_client
        else:
            self._sts = aws_client("sts", region=config.region, credential=config.credential)
        if ec2_client is not None:
            self._ec2 = ec2_client
        else:
            self._ec2 = aws_client("ec2", region=config.region, credential=config.credential)
        # Cognito IDP client is built lazily on first use (the auth-gate
        # picker path, #859) so the common apply/probe paths don't pay
        # to construct a client they never touch. Tests inject a stub
        # here; production resolves it in ``_cognito_idp`` below.
        self._cognito_idp = cognito_idp_client
        # ACM client is built lazily on first ``list_certificates`` call
        # (the cert-picker path, #858) so the common deploy / observability
        # flows don't pay for a client they never use. Injectable for moto
        # tests, mirroring eks/sts/ec2 above.
        self._acm: Any | None = acm_client
        # IAM client is built lazily on first ``ensure_agent_model_identity``
        # call (the managed-model path) so the common apply / observability
        # flows don't pay for a client they never touch. Injectable for moto
        # tests, mirroring the ACM/Cognito lazy clients.
        self._iam: Any | None = iam_client
        # Factory injection lets tests pass a stubbed kubernetes
        # client without contacting a real apiserver.
        self._k8s_factory = k8s_client_factory or _build_k8s_client
        self._k8s_cache: dict[str, Any] = {}
        # Pluggable runtime-observability backends (#299). Same shape
        # as K8sNativeClusterDriver — the listing + log-streaming
        # path is cloud-neutral as soon as the ClusterAuth blob is
        # in hand; what's EKS-specific is *how* the operator's
        # exec_plugin row gets turned into kubeconfig (#309).
        self._pod_backend: PodBackend = pod_backend or LivePodBackend()
        self._log_backend: LogBackend = log_backend if log_backend is not None else default_log_backend()
        # Bring-into-management (#316). The RBAC apply + capability
        # probe + preflight Job body is the k8s_native canonical
        # version; EKS auth is routed through the synthesized
        # ``kubeconfig`` blob the helpers below build, so the shared
        # backend's ``build_api_client`` is the only thing the probe
        # path needs to satisfy.
        self._management_backend: ManagementBackend = (
            management_backend if management_backend is not None else default_management_backend()
        )
        # Token-mint injection lets tests substitute a recording
        # double for ``mint_eks_token`` without monkey-patching the
        # module. Default points at the production helper.
        self._token_minter: Callable[[str, str], str] = token_minter or (
            lambda cluster_name, region: mint_eks_token(
                cluster_name=cluster_name,
                region=region,
                expires_in_seconds=self._config.sts_token_lifetime_seconds,
            )
        )
        # Monotonic clock is parametrized so the cache-TTL tests can
        # drive expiry without sleeping. ``time.monotonic`` is the
        # production source — wall-clock-jump-immune.
        self._clock: Callable[[], float] = monotonic_clock or time.monotonic
        # Per-(cluster_name, region) bearer-token cache for the
        # observability path. The apply-manifests path mints
        # per-operation via ``_eks_token`` and does NOT use this
        # cache — it's strictly for resolver-side call hot-loops.
        self._token_cache: dict[tuple[str, str], _TokenCacheEntry] = {}
        # Per-cluster_name describe-cache for endpoint + CA. Cleared
        # by ``invalidate_describe_cache`` on the rare CA rotation.
        self._describe_cache: dict[str, _DescribeCacheEntry] = {}

    # ---- apply / delete -------------------------------------------

    @driver_op(cloud="aws", driver="cluster")
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
            # Per-manifest heartbeat: a 100-manifest apply against a slow
            # cluster easily exceeds the default activity start_to_close
            # timeout otherwise (#595-#598).
            maybe_heartbeat(f"cluster.apply_manifests:{cluster}")
            kind = manifest.get("kind", "")
            meta = manifest.get("metadata") or {}
            name = meta.get("name", "")
            # Honor each manifest's own metadata.namespace (kubectl-style) so a
            # single call can carry a multi-namespace batch — e.g. the Knative
            # post-install applies the vendored operator (knative-operator ns)
            # alongside the KnativeServing CR (knative-serving ns). The passed
            # ``namespace`` is the default for manifests that don't declare one;
            # server-side apply puts the namespace in the request path, so a CR
            # forced into the wrong namespace would be 422-rejected. The
            # apiserver ignores namespace for cluster-scoped kinds (Namespace,
            # CustomResourceDefinition, ClusterRole/Binding).
            manifest_ns = meta.get("namespace") or namespace
            try:
                outcome = client.server_side_apply(
                    namespace=manifest_ns,
                    manifest=manifest,
                    dry_run=dry_run,
                )
            except Exception as exc:
                errors.append(
                    ApplyError(
                        kind=kind,
                        name=name,
                        namespace=manifest_ns,
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

    @driver_op(cloud="aws", driver="cluster")
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
        for manifest in manifests:
            api_version = manifest.get("apiVersion", "")
            kind_bare = manifest.get("kind", "")
            # For CRDs (apiVersion is "group/version") construct the
            # "group/version/Kind" form that split_kind accepts.
            kind = f"{api_version}/{kind_bare}" if "/" in api_version else kind_bare
            name = manifest.get("metadata", {}).get("name", "")
            ref = f"{kind}/{name}"
            try:
                client.delete(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                    propagation_policy=propagation_policy,
                )
                deleted.append(ref)
            except _NotFoundError:
                not_found.append(ref)
            except Exception as exc:
                errors.append(f"{ref}: {exc}")
        return DeleteResult(
            deleted=deleted,
            not_found=not_found,
            errors=errors,
        )

    # ---- namespaces -----------------------------------------------

    @driver_op(cloud="aws", driver="cluster")
    def get_namespace(
        self,
        cluster: str,
        name: str,
    ) -> NamespaceState | None:
        client = self._k8s(cluster)
        try:
            ns = client.get_namespace(name=name)
        except _NotFoundError:
            return None
        except Exception as exc:
            raise RuntimeError(f"get_namespace {name}: {exc}") from exc
        # Some client backends return None (rather than raising NotFound) when
        # the namespace is absent — e.g. it finished Terminating between a
        # delete call and this confirm-read. Treat None as "gone" so callers
        # (delete_namespace's wait loop, teardown) see it as deleted instead of
        # crashing on ns["metadata"] and wedging the app at tearing_down (#1015).
        if ns is None:
            return None
        return NamespaceState(
            name=ns["metadata"]["name"],
            labels=ns["metadata"].get("labels", {}) or {},
            annotations=ns["metadata"].get("annotations", {}) or {},
            phase=ns.get("status", {}).get("phase", "Active"),
        )

    @driver_op(cloud="aws", driver="cluster")
    def storage_class_exists(self, cluster: str, name: str) -> bool:
        """Return True if a StorageClass with the given name exists on the cluster.

        Used by the bootstrap preflight to gate persistent-storage HelmRelease
        installs on the required StorageClass being present (#772).
        """
        client = self._k8s(cluster)
        sc = client.get(
            kind="StorageClass",
            namespace=None,
            name=name,
        )
        return sc is not None

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception as exc:
            raise RuntimeError(f"ensure_namespace {name}: {exc}") from exc
        return Namespace(
            name=name,
            labels=dict(labels),
            annotations=dict(annotations),
        )

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.delete_namespace")
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
        except _NotFoundError:
            return
        except Exception as exc:
            raise RuntimeError(f"delete_namespace {name}: {exc}") from exc

        if not wait:
            return
        # Poll until the namespace is gone (k8s finalizers can take
        # minutes for namespaces with PVCs / webhooks). Heartbeat per
        # iteration so the Temporal activity stays alive across the full
        # 10-minute window (#595-#598).
        deadline = time.monotonic() + 600  # 10 minutes
        while time.monotonic() < deadline:
            maybe_heartbeat(f"cluster.delete_namespace:{name}")
            existing = self.get_namespace(cluster=cluster, name=name)
            if existing is None:
                return
            time.sleep(2)
        raise TimeoutError(
            f"namespace {name} did not delete within 10m",
        )

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
    def list_csi_drivers(self, cluster: str) -> list[str]:
        client = self._k8s(cluster)
        return sorted(
            str((row.get("metadata", {}) or {}).get("name") or "")
            for row in client.list(kind="storage.k8s.io/v1/CSIDriver")
            if (row.get("metadata", {}) or {}).get("name")
        )

    @driver_op(cloud="aws", driver="cluster")
    def persistent_volume_claim_exists(self, cluster: str, namespace: str, name: str) -> bool:
        return self._k8s(cluster).get(kind="PersistentVolumeClaim", namespace=namespace, name=name) is not None

    @driver_op(cloud="aws", driver="cluster")
    def get_manifest(
        self,
        cluster: str,
        namespace: str | None,
        kind: str,
        name: str,
    ) -> dict[str, Any] | None:
        return self._k8s(cluster).get(kind=kind, namespace=namespace, name=name)

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
    def patch_workload(
        self,
        cluster: str,
        namespace: str,
        kind: str,
        name: str,
        patch: dict,
    ) -> dict:
        """Apply a JSON merge patch to a workload (Deployment).

        Used for live ops: rollout restart (restart annotation) and
        replica scaling. Returns the patched object.
        """
        if kind != "Deployment":
            raise NotImplementedError(f"patch_workload only supports Deployment, got {kind!r}")
        client = self._k8s(cluster)
        return client.merge_patch_deployment(
            namespace=namespace,
            name=name,
            patch=patch,
        )

    @driver_op(cloud="aws", driver="cluster")
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
        except _NotFoundError as exc:
            raise NotFoundError(
                f"{kind}/{name} in namespace {namespace}",
            ) from exc

        return workload_status_from_object(kind, name, namespace, obj)

    @driver_op(cloud="aws", driver="cluster")
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
            # Per-poll heartbeat keeps the wrapping Temporal activity
            # alive across the full ``timeout`` (default 10m). The
            # entry-time heartbeat from ``@driver_op`` alone isn't
            # enough on long rollouts (#595-#598).
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

    @driver_op(cloud="aws", driver="cluster")
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
        except _NotFoundError as exc:
            raise NotFoundError(
                f"pod {pod} in namespace {namespace}",
            ) from exc

    @driver_op(cloud="aws", driver="cluster")
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

    @driver_op(cloud="aws", driver="cluster")
    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
    ) -> list[PodInfo]:
        """List pods on the EKS cluster.

        Materializes ``exec_plugin`` auth into a real bearer token
        (AWS-IAM-Authenticator presigned URL) before delegating to the
        shared k8s_native pod backend. ``kubeconfig`` /
        ``service_account_token`` pass through unchanged.

        ``task_id`` (#891) selects an agent task pod by its
        ``astrolift.dev/task-id`` label; forwarded only when set so
        backends that predate the kwarg keep working.
        """
        kwargs: dict[str, Any] = {
            "auth": self._resolve_eks_auth(auth),
            "namespace": namespace,
            "app_slug": app_slug,
        }
        if task_id:
            kwargs["task_id"] = task_id
        return self._pod_backend.list_pods(**kwargs)

    @driver_op(cloud="aws", driver="cluster")
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
        """Open a streaming exec session on the EKS cluster (#1040).

        Materializes ``exec_plugin`` auth into a real bearer token (as
        ``list_pods``) before delegating to the shared k8s_native exec
        opener."""
        from providers.k8s_native.observability import open_interactive_exec

        return open_interactive_exec(
            auth=self._resolve_eks_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            command=command,
            tty=tty,
        )

    @driver_op(cloud="aws", driver="cluster")
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
        """See ``list_pods`` — same materialize-then-delegate pattern."""
        return self._log_backend.stream(
            auth=self._resolve_eks_auth(auth),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    # ---- bring-into-management (#316) -----------------------------

    @driver_op(cloud="aws", driver="cluster")
    def probe_capabilities(self, cluster: ClusterContext) -> dict[str, Any]:
        return probe_cluster_capabilities(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
        )

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.bring_into_management")
    def bring_into_management(
        self,
        cluster: ClusterContext,
        *,
        run_preflight: bool = True,
    ) -> ManagementReport:
        return run_bring_into_management(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
            run_preflight=run_preflight,
        )

    @driver_op(cloud="aws", driver="cluster")
    def read_job_status(self, cluster: ClusterContext, *, namespace: str, job_name: str) -> JobStatus:
        """Read one Job's status (run-status reconciler). Read-only —
        resolves the EKS exec-plugin auth to a kubeconfig context first."""
        return read_cluster_job_status(
            backend=self._management_backend,
            cluster=self._resolve_eks_auth_context(cluster),
            namespace=namespace,
            job_name=job_name,
        )

    # ---- cluster teardown (#337) ---------------------------------

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.teardown_cluster")
    def teardown_cluster(
        self,
        cluster: ClusterContext,
        *,
        delete_cloud_infra: bool,
    ) -> TeardownReport:
        """Delete the EKS cluster + its platform-tagged node groups +
        Fargate profiles.

        ``delete_cloud_infra=False`` is a no-op for symmetry with the
        protocol — the cluster row is the platform's record; the EKS
        cluster itself stays running.

        When deleting:
          1. Drain + delete all managed node groups owned by the
             cluster (EKS rejects ``delete_cluster`` while node groups
             exist).
          2. Delete every Fargate profile.
          3. Call ``eks.delete_cluster`` and wait for the cluster to
             reach DELETED.

        Idempotent: ``ResourceNotFoundException`` on any step is
        treated as "already gone" and contributes to ``deleted``
        rather than failing. VPC / subnets / IAM roles are NOT
        touched — those were provisioned outside the driver (in
        opscode TF / the operator's own IaC) and remain operator-owned.
        """
        if not delete_cloud_infra:
            return TeardownReport(
                success=True,
                skipped=[f"eks/{self._config.cluster_name}"],
                messages=["delete_cloud_infra=false; EKS cluster left running"],
            )

        deleted: list[str] = []
        skipped: list[str] = []
        messages: list[str] = []

        # Node groups first — EKS refuses cluster delete while these exist.
        try:
            node_groups = self._eks.list_nodegroups(clusterName=self._config.cluster_name).get(
                "nodegroups",
                [],
            )
        except Exception as exc:
            return TeardownReport(
                success=False,
                error=f"list_nodegroups failed: {exc}",
                deleted=deleted,
                skipped=skipped,
                messages=messages,
            )
        for ng in node_groups:
            try:
                self._eks.delete_nodegroup(
                    clusterName=self._config.cluster_name,
                    nodegroupName=ng,
                )
                deleted.append(f"nodegroup/{ng}")
            except self._eks.exceptions.ResourceNotFoundException:
                skipped.append(f"nodegroup/{ng} (already deleted)")
            except Exception as exc:
                return TeardownReport(
                    success=False,
                    error=f"delete_nodegroup {ng} failed: {exc}",
                    deleted=deleted,
                    skipped=skipped,
                    messages=messages,
                )

        # Wait for node groups to be gone before attempting cluster delete.
        if node_groups:
            # Heartbeat before the boto3 waiter: it blocks for up to 15
            # minutes by default and Temporal would time out the activity
            # without it (#595).
            maybe_heartbeat("cluster.teardown_cluster:wait_nodegroups")
            try:
                self._eks.get_waiter("nodegroup_deleted").wait(
                    clusterName=self._config.cluster_name,
                    nodegroupName=node_groups[0],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 60},
                )
                messages.append(f"waited for {len(node_groups)} node group(s) to delete")
            except Exception as exc:
                messages.append(f"node group wait raised {exc}; continuing")

        # Fargate profiles next.
        try:
            fargate_profiles = self._eks.list_fargate_profiles(
                clusterName=self._config.cluster_name,
            ).get("fargateProfileNames", [])
        except Exception as exc:
            messages.append(f"list_fargate_profiles failed: {exc}; skipping")
            fargate_profiles = []
        for fp in fargate_profiles:
            try:
                self._eks.delete_fargate_profile(
                    clusterName=self._config.cluster_name,
                    fargateProfileName=fp,
                )
                deleted.append(f"fargate-profile/{fp}")
            except self._eks.exceptions.ResourceNotFoundException:
                skipped.append(f"fargate-profile/{fp} (already deleted)")
            except Exception as exc:
                return TeardownReport(
                    success=False,
                    error=f"delete_fargate_profile {fp} failed: {exc}",
                    deleted=deleted,
                    skipped=skipped,
                    messages=messages,
                )

        # Cluster itself. EKS will return ResourceInUseException if any
        # subresource is still terminating; the operator can retry.
        try:
            self._eks.delete_cluster(name=self._config.cluster_name)
            deleted.append(f"eks-cluster/{self._config.cluster_name}")
            messages.append("eks.delete_cluster submitted; full deletion takes ~10 min")
        except self._eks.exceptions.ResourceNotFoundException:
            skipped.append(f"eks-cluster/{self._config.cluster_name} (already deleted)")
        except Exception as exc:
            return TeardownReport(
                success=False,
                error=f"delete_cluster failed: {exc}",
                deleted=deleted,
                skipped=skipped,
                messages=messages,
            )

        return TeardownReport(
            success=True,
            deleted=deleted,
            skipped=skipped,
            messages=messages,
        )

    # ---- bootstrap recipe ----------------------------------------

    def _discover_backend_sg(self, cluster_name: str) -> str:
        """Resolve the node *shared* security group for the LB controller's
        ``backendSecurityGroup`` value.

        Without an explicit value the controller does tag-based SG discovery
        and fails when it finds two groups tagged
        ``kubernetes.io/cluster/<cluster_name>: owned`` on the same ENI —
        the EKS-created cluster SG (``eks-cluster-sg-*``) and the node
        shared SG (``*-node-*``). We want the node shared SG.

        Discovery is by EC2 tag + name: the ``kubernetes.io/cluster/<name>``
        tag scopes to this cluster's groups, and the ``*-node-*`` group-name
        filter selects the node shared SG over the ``eks-cluster-sg-*`` one.
        ``remoteAccessSecurityGroup`` on the node group is deliberately NOT
        used — that SG only exists when SSH remote access is configured and
        is a separate SSH-ingress group, not the node shared SG.

        Returns ``""`` (controller falls back to tag discovery) if the SG
        can't be resolved — the API call failed or matched none/many groups.
        """
        try:
            resp = self._ec2.describe_security_groups(
                Filters=[
                    {
                        "Name": f"tag:kubernetes.io/cluster/{cluster_name}",
                        "Values": ["owned"],
                    },
                    {"Name": "group-name", "Values": ["*-node-*"]},
                ],
            )
        except Exception:
            log.warning(
                "bootstrap_components: EC2 describe_security_groups failed — "
                "backendSecurityGroup will not be set; LB controller may hit "
                "the dual-SG reconcile error",
            )
            return ""

        groups = resp.get("SecurityGroups", [])
        # Defend against the cluster SG slipping through the name filter.
        groups = [g for g in groups if not g.get("GroupName", "").startswith("eks-cluster-sg-")]
        if len(groups) != 1:
            log.warning(
                "bootstrap_components: expected exactly one node shared "
                "security group tagged kubernetes.io/cluster/%s, found %d — "
                "backendSecurityGroup will not be set",
                cluster_name,
                len(groups),
            )
            return ""
        return groups[0].get("GroupId", "")

    @driver_op(cloud="aws", driver="cluster")
    def bootstrap_components(self, cluster: ClusterContext) -> list[BootstrapComponent]:
        """EKS recipe — leans on AWS-native services where they're the
        path of least resistance and falls back to in-cluster controllers
        only where AWS doesn't provide a managed equivalent.

        Native cloud paths:
          - TLS: ACM via aws-load-balancer-controller annotations on
            Ingress/Service (cert-manager only if the operator needs
            internal mTLS or non-ALB cert flows).
          - Storage: aws-ebs-csi-driver installed in-cluster via Helm with
            a platform-minted IRSA role (#1024) — self-sufficient, NOT an
            EKS managed addon and not provisioned out-of-band in Terraform.
          - Ingress: aws-load-balancer-controller renders Ingress as ALB.

        IRSA wiring:
          Controllers that call AWS APIs (LB controller, external-dns)
          need ``eks.amazonaws.com/role-arn`` on their ServiceAccount.
          The ARN is resolved in this order:
            1. ``cluster.auth_config["irsa_roles"][<component-key>]`` —
               explicit override for non-standard role names.
            2. Convention: ``arn:aws:iam::{account_id}:role/{cluster_name}-{key}``
               matching the naming used in the opscode TF modules.
          If neither source yields an ARN the annotation is omitted and
          the controller runs with the node/Fargate execution role (which
          typically lacks the required policies — the pod will fail).

        Fargate notes:
          - DaemonSets with ``hostNetwork:true`` don't schedule on
            Fargate pods; node-exporter is disabled so Pending pods
            don't pile up.
          - EBS volumes attach to EC2 nodegroup instances only; Fargate
            pods have no EBS. The aws-ebs-csi-driver component unblocks
            nodegroup-backed StatefulSet PVCs — Fargate-only workloads
            need EFS (aws-efs-csi-driver) instead.
        """
        auth_cfg = cluster.auth_config or {}
        eks_cluster_name = auth_cfg.get("cluster_name") or self._config.cluster_name

        # IRSA role ARN resolution ----------------------------------------
        # 1. Explicit override map stored in auth_config takes precedence.
        # 2. Convention: <cluster_name>-<component_key>, matching the
        #    opscode TF module naming (iam-role-for-service-accounts with
        #    use_name_prefix = false).
        irsa_overrides: dict[str, str] = auth_cfg.get("irsa_roles", {})
        account_id = ""
        try:
            account_id = self._sts.get_caller_identity()["Account"]
        except Exception:
            log.warning(
                "bootstrap_components: STS get_caller_identity failed — "
                "IRSA role ARNs will be empty if not set in auth_config.irsa_roles"
            )

        # VPC ID — required by aws-load-balancer-controller when EC2 IMDS
        # is unavailable (e.g. Fargate). The controller's auto-discovery
        # path calls the IMDS mac/vpc-id endpoint, which times out on
        # Fargate pods. Provide it explicitly from DescribeCluster so the
        # controller starts without any IMDS dependency.
        # Override via auth_config["vpc_id"] for non-standard setups.
        vpc_id: str = auth_cfg.get("vpc_id", "")
        if not vpc_id:
            try:
                resp = self._eks.describe_cluster(name=eks_cluster_name)
                vpc_id = resp["cluster"]["resourcesVpcConfig"].get("vpcId", "")
            except Exception:
                log.warning(
                    "bootstrap_components: EKS describe_cluster failed — "
                    "vpcId will not be set; ALB controller may fail on Fargate"
                )

        # backendSecurityGroup — the node *shared* SG. Pinning it avoids the
        # controller's tag-based discovery, which fails when the cluster SG
        # and the node shared SG are both tagged owned on the same ENI
        # (FailedNetworkReconcile: expected exactly one securityGroup tagged
        # with kubernetes.io/cluster/...). Override via
        # auth_config["backend_security_group"] for non-standard setups.
        backend_sg: str = auth_cfg.get("backend_security_group", "")
        if not backend_sg:
            backend_sg = self._discover_backend_sg(eks_cluster_name)

        def _irsa_arn(key: str) -> str:
            if key in irsa_overrides:
                return irsa_overrides[key]
            if account_id:
                return f"arn:aws:iam::{account_id}:role/{eks_cluster_name}-{key}"
            return ""

        def _sa_with_irsa(key: str) -> dict:
            arn = _irsa_arn(key)
            annotations = {"eks.amazonaws.com/role-arn": arn} if arn else {}
            return {"create": True, "annotations": annotations}

        alb_values: dict = {
            "clusterName": eks_cluster_name,
            # Fargate doesn't expose EC2 IMDS; supply these explicitly so
            # the controller never falls back to instance metadata.
            "awsRegion": self._config.region,
            # Pin the SA name (chart default) so the IRSA trust subject the
            # platform mints (astrolift-system:aws-load-balancer-controller) is
            # deterministic and doesn't drift with the Flux release name (#1044).
            "serviceAccount": {**_sa_with_irsa("aws-load-balancer-controller"), "name": "aws-load-balancer-controller"},
        }
        if vpc_id:
            alb_values["vpcId"] = vpc_id
        if backend_sg:
            alb_values["backendSecurityGroup"] = backend_sg
            # When backendSecurityGroup is explicitly set we manage the SG
            # rule ourselves (or let bootstrap set it once). The TGB
            # reconciler's ENI-tag lookup fails when both the EKS cluster SG
            # and the node shared SG carry kubernetes.io/cluster/<name>:owned,
            # producing a FailedNetworkReconcile loop that blocks target-group
            # health on every Ingress reconcile (e.g. auth-gate toggle).
            alb_values["manageBackendSecurityGroupRules"] = False

        # Knative Serving post-install objects (opt-in knative-serving
        # component below). The component is chart-free: instead of a
        # HelmRelease it applies the vendored official Knative Operator YAML
        # (KNATIVE_OPERATOR_MANIFESTS — the operator + its operator.knative.dev
        # CRDs) followed by these two objects, which drive the operator to
        # stand Knative Serving up. The install path sorts foundational kinds
        # (Namespace / CRD) first, so the operator's CRDs register before the
        # KnativeServing CR lands. Static — no cluster-specific values — but
        # built fresh per call so the frozen component never shares a mutable
        # list across driver instances.
        knative_serving_namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "knative-serving",
                "labels": {"astrolift.io/managed-by": "platform"},
            },
        }
        knative_serving_cr = {
            "apiVersion": "operator.knative.dev/v1beta1",
            "kind": "KnativeServing",
            "metadata": {
                "name": "knative-serving",
                "namespace": "knative-serving",
                "labels": {"astrolift.io/managed-by": "platform"},
            },
            "spec": {
                # Kourier is Knative's lightweight Envoy gateway — no Istio
                # dependency. Selecting it here also requires pointing the
                # default ingress class at Kourier (below), otherwise
                # Knative keeps expecting Istio and Routes never program.
                "ingress": {"kourier": {"enabled": True}},
                "config": {
                    "network": {
                        "ingress-class": "kourier.ingress.networking.knative.dev",
                    },
                    # config-observability keys (the operator strips the
                    # ``config-`` prefix, so the block name is
                    # ``observability``). Pre-enable request logging for the
                    # future invocation pipeline; the template emits one JSON
                    # line per request capturing method/path/status/duration/
                    # revision.
                    "observability": {
                        "logging.enable-request-log": "true",
                        "logging.request-log-template": _KNATIVE_REQUEST_LOG_TEMPLATE,
                    },
                },
            },
        }

        return [
            BootstrapComponent(
                key="aws-load-balancer-controller",
                title="AWS Load Balancer Controller",
                default_enabled=True,
                rationale=(
                    "Renders Kubernetes Ingress as AWS ALBs and Service "
                    "type=LoadBalancer as NLBs. EKS doesn't ship a default "
                    "Service controller; without this, ingress doesn't work. "
                    "Bound to its IRSA role via ServiceAccount annotation."
                ),
                helm_values=alb_values,
                requires=["irsa:aws-load-balancer-controller"],
                options=[],
                chart_name="aws-load-balancer-controller",
                chart_repo_url="https://aws.github.io/eks-charts",
                chart_repo_type="default",
                chart_version="1.9.2",
                install_timeout="10m",
            ),
            BootstrapComponent(
                key="external-dns",
                title="external-dns (Route53)",
                default_enabled=True,
                rationale=(
                    "Auto-creates Route53 records from Ingress + Service "
                    "annotations. IRSA-bound; the install workflow scopes "
                    "the role's permissions to the operator's Route53 "
                    "hosted zone."
                ),
                helm_values={
                    "provider": "aws",
                    # policy=sync (the chart defaults to upsert-only) so that
                    # when an app's Ingress/Service is deleted on teardown,
                    # external-dns reaps the A + owner-TXT records it created.
                    # Under upsert-only it never deletes, orphaning Route53
                    # records on every deregister and breaking verify-clean.
                    "policy": "sync",
                    # Scope sync deletes to records THIS cluster owns. Required
                    # whenever installs can share one hosted zone (the N-installs
                    # topology): without a unique owner id, one cluster's
                    # external-dns would treat another's records as orphans and
                    # delete them. txt registry is on by chart default.
                    "txtOwnerId": eks_cluster_name,
                    "sources": ["service", "ingress"],
                    # external-dns defaults aws-evaluate-target-health to true.
                    # On an ALB alias that makes Route53 answer NODATA whenever
                    # it can't positively see target health — a freshly deployed
                    # app reads as NXDOMAIN even with a healthy target group,
                    # and resolvers then negative-cache the miss. The ALB is
                    # already the health boundary; DNS must always answer.
                    # Kingpin negation form: "--flag=false" is a fatal parse
                    # error ("unexpected false") that crashloops the pod.
                    "extraArgs": ["--no-aws-evaluate-target-health"],
                    # Pin the SA name (chart default) so the IRSA trust subject
                    # the platform mints (astrolift-system:external-dns) is
                    # deterministic, not coupled to the Flux release name (#1044).
                    "serviceAccount": {**_sa_with_irsa("external-dns"), "name": "external-dns"},
                },
                requires=["irsa:external-dns", "route53_zone_id"],
                options=[],
                chart_name="external-dns",
                chart_repo_url="https://kubernetes-sigs.github.io/external-dns/",
                chart_repo_type="default",
                chart_version="1.14.5",
            ),
            BootstrapComponent(
                key="metrics-server",
                title="metrics-server (HPA + kubectl top)",
                default_enabled=True,
                rationale=(
                    "Required for HorizontalPodAutoscaler. EKS doesn't ship "
                    "it as a managed addon (the upstream chart is what we use)."
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
                key="aws-ebs-csi-driver",
                title="AWS EBS CSI Driver (persistent volumes)",
                default_enabled=True,
                rationale=(
                    "Provisions EBS-backed PersistentVolumes so StatefulSet "
                    "PVCs bind. astrolift-eks ships without it, so claims hang "
                    "Pending forever (#1024). Installed in-cluster via Helm "
                    "with its own platform-minted IRSA role — self-sufficient, "
                    "no EKS managed addon or out-of-band Terraform. EBS attaches "
                    "to EC2 nodegroup instances only: this helps nodegroup-backed "
                    "StatefulSets; Fargate-only workloads need EFS instead."
                ),
                # The controller SA is IRSA-bound so the driver can call the EC2
                # volume APIs. Name pinned to the chart default (ebs-csi-controller-sa)
                # so it matches the astrolift-system:ebs-csi-controller-sa subject the
                # platform scopes the IRSA trust to (IRSADriver.provision_ebs_csi_role).
                helm_values={
                    "controller": {
                        "serviceAccount": {
                            **_sa_with_irsa("aws-ebs-csi-driver"),
                            "name": "ebs-csi-controller-sa",
                        },
                    },
                    # Default gp3 StorageClass so a StatefulSet PVC that omits
                    # storage_class binds (#1024): the cluster ships with only
                    # the in-tree gp2 (not marked default), so claims hang
                    # Pending. is-default-class lets #1023's autostamp pick it.
                    # The chart creates the SC as part of the HelmRelease the
                    # install applies (render_storage_class isn't in the apply
                    # set, which only applies HelmReleases).
                    "storageClasses": [
                        {
                            "name": "gp3",
                            "annotations": {
                                "storageclass.kubernetes.io/is-default-class": "true",
                            },
                            "parameters": {"type": "gp3", "encrypted": "true"},
                            "reclaimPolicy": "Delete",
                            "volumeBindingMode": "WaitForFirstConsumer",
                            "allowVolumeExpansion": True,
                        },
                    ],
                },
                requires=["irsa:aws-ebs-csi-driver"],
                options=[],
                chart_name="aws-ebs-csi-driver",
                chart_repo_url="https://kubernetes-sigs.github.io/aws-ebs-csi-driver",
                chart_repo_type="default",
                chart_version="2.35.1",
                install_timeout="10m",
            ),
            BootstrapComponent(
                key="aws-mountpoint-s3-csi-driver",
                title="Mountpoint S3 CSI Driver (S3 buckets as pod volumes)",
                default_enabled=False,
                rationale=(
                    "Mounts S3 buckets into pods as static CSI volumes (#1675) "
                    "— the S3 object-store binding's mount_path option depends "
                    "on it. Read-heavy semantics only: sequential writes to new "
                    "objects, no appends, renames, or POSIX locking; EFS stays "
                    "the answer for a real shared filesystem. Opt-in because "
                    "driver-level IRSA grants every mount the shared "
                    "platform-bucket scope."
                ),
                # Driver-level IRSA on the node DaemonSet's SA. Name pinned to
                # the chart default (s3-csi-driver-sa) so it matches the
                # astrolift-system:s3-csi-driver-sa subject the platform scopes
                # the IRSA trust to (IRSADriver.provision_s3_csi_role).
                helm_values={
                    "node": {
                        "serviceAccount": {
                            **_sa_with_irsa("aws-mountpoint-s3-csi-driver"),
                            "name": "s3-csi-driver-sa",
                        },
                    },
                },
                requires=["irsa:aws-mountpoint-s3-csi-driver"],
                options=[],
                chart_name="aws-mountpoint-s3-csi-driver",
                chart_repo_url="https://awslabs.github.io/mountpoint-s3-csi-driver",
                chart_repo_type="default",
                chart_version="2.7.0",
                install_timeout="10m",
            ),
            BootstrapComponent(
                key="kube-prometheus-stack",
                title="Prometheus + Grafana + Alertmanager",
                default_enabled=True,
                rationale=(
                    "Metrics scraping + dashboarding for the platform UI's "
                    "cluster-status charts. EBS can't attach to Fargate pods "
                    "(no underlying EC2 host). The default storage mode is "
                    "ephemeral (emptyDir) — 24h retention, TSDB resets on "
                    "pod restart. Select 'efs_persistent' to get 30d "
                    "retention backed by an EFS Access Point; that requires "
                    "the aws-efs-csi-driver managed addon and the "
                    "'efs-prometheus' StorageClass (apply the manifest from "
                    "Terraform output before running this recipe). "
                    "node-exporter is disabled — DaemonSets with hostNetwork "
                    "don't schedule on Fargate."
                ),
                helm_values={
                    "nodeExporter": {"enabled": False},
                    "prometheus": {
                        "prometheusSpec": {
                            "retention": "24h",
                            "storageSpec": {},
                            # Watch ALL ServiceMonitors/PodMonitors, not just
                            # ones carrying this release's label — sibling
                            # addons (cloudwatch-exporter, ingress-nginx on
                            # other recipes) ship their own monitors and the
                            # edge-metrics contract (spec 08 §6.1) requires
                            # they get scraped without per-monitor labeling.
                            "serviceMonitorSelectorNilUsesHelmValues": False,
                            "podMonitorSelectorNilUsesHelmValues": False,
                            # Fargate sizes pods from requests — without them
                            # Prometheus gets the 0.25vCPU/512Mi default and
                            # OOM-loops under 24h retention. Matches the
                            # tuning proven on the SMD prd cluster.
                            "resources": {
                                "requests": {"cpu": "200m", "memory": "512Mi"},
                                "limits": {"memory": "1500Mi"},
                            },
                        },
                    },
                    "grafana": {"enabled": True, "persistence": {"enabled": False}},
                },
                # storageclass:efs-prometheus must be present when the operator
                # selects the efs_persistent storage mode. Apply the StorageClass
                # manifest emitted by the Terraform output before running this
                # recipe. The install activity enforces this via
                # _assert_storage_class_preflight (#772): if the StorageClass is
                # absent, the workflow surfaces an actionable error before Flux
                # tries to bind the PVC.
                requires=[],
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
                                "efs_persistent",
                                "Persistent EFS — 30d retention, survives restarts; "
                                "requires aws-efs-csi-driver addon + efs-prometheus StorageClass",
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
                depends_on=["aws-load-balancer-controller"],
            ),
            BootstrapComponent(
                key="cloudwatch-exporter",
                title="CloudWatch exporter (ALB edge metrics)",
                default_enabled=True,
                rationale=(
                    "ALB-fronted clusters have no Prometheus-native ingress "
                    "metrics — request rate / errors / latency live in "
                    "CloudWatch. YACE republishes AWS/ApplicationELB metrics "
                    "into the cluster's Prometheus, discovering the ALB "
                    "controller's load balancers by their cluster tag and "
                    "exporting the ingress.k8s.aws/stack tag that joins each "
                    "ALB back to its app namespace. This is what feeds the "
                    "edge-sourced golden signals (spec 08 §6.1) on the "
                    "aws_alb_controller ingress variant. The ServiceAccount "
                    "name is pinned so the platform-minted IRSA trust subject "
                    "(astrolift-system:cloudwatch-exporter) is deterministic."
                ),
                helm_values={
                    "serviceAccount": {
                        **_sa_with_irsa("cloudwatch-exporter"),
                        "name": "cloudwatch-exporter",
                    },
                    "serviceMonitor": {"enabled": True},
                    "config": (
                        "apiVersion: v1alpha1\n"
                        "discovery:\n"
                        "  exportedTagsOnMetrics:\n"
                        "    AWS/ApplicationELB:\n"
                        "      - ingress.k8s.aws/stack\n"
                        "  jobs:\n"
                        "    - type: AWS/ApplicationELB\n"
                        "      regions:\n"
                        f"        - {self._config.region}\n"
                        "      searchTags:\n"
                        "        - key: elbv2.k8s.aws/cluster\n"
                        f"          value: {eks_cluster_name}\n"
                        "      period: 60\n"
                        "      length: 300\n"
                        "      metrics:\n"
                        "        - name: RequestCount\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: HTTPCode_Target_2XX_Count\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: HTTPCode_Target_3XX_Count\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: HTTPCode_Target_5XX_Count\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: HTTPCode_Target_4XX_Count\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: HTTPCode_ELB_5XX_Count\n"
                        "          statistics: [Sum]\n"
                        "          nilToZero: true\n"
                        "        - name: TargetResponseTime\n"
                        "          statistics: [p50, p90, p95, p99]\n"
                    ),
                },
                requires=["irsa:cloudwatch-exporter"],
                options=[],
                chart_name="yet-another-cloudwatch-exporter",
                chart_repo_url="https://nerdswords.github.io/helm-charts",
                chart_repo_type="default",
                chart_version="0.38.0",
                install_timeout="10m",
                # The chart's ServiceMonitor needs the monitoring.coreos.com
                # CRDs kube-prometheus-stack registers.
                depends_on=["kube-prometheus-stack"],
            ),
            BootstrapComponent(
                key="cert-manager",
                title="cert-manager (in-cluster TLS, internal mTLS)",
                default_enabled=False,
                rationale=(
                    "EKS operators typically use ACM via ALB annotations for "
                    "public TLS — no in-cluster cert controller needed. Enable "
                    "cert-manager only when you need internal mTLS, webhook "
                    "certificates, or non-ALB cert flows."
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
                            ("self_signed", "Self-signed (internal / mTLS)"),
                            ("acme_letsencrypt_prod", "Let's Encrypt prod (Route53 DNS-01)"),
                            ("acme_letsencrypt_staging", "Let's Encrypt staging"),
                        ],
                        default="self_signed",
                    ),
                ],
                chart_name="cert-manager",
                chart_repo_url="https://charts.jetstack.io",
                chart_repo_type="default",
                chart_version="v1.16.3",
                # ALB controller registers a MutatingWebhookConfiguration;
                # cert-manager install creates Services that hit that webhook.
                # Wait until the ALB controller is ready to avoid "no endpoints"
                # failures on the webhook call.
                depends_on=["aws-load-balancer-controller"],
            ),
            BootstrapComponent(
                key="knative-serving",
                title="Knative Serving (function / serverless workloads)",
                default_enabled=False,
                rationale=(
                    "Applies the vendored official Knative Operator install "
                    "manifests (v1.16.0) plus a KnativeServing instance so "
                    "kind=function workloads — rendered as serving.knative.dev "
                    "Services with scale-to-zero — can run. Chart-free: the "
                    "operator's OCI Helm chart isn't publicly pullable, so the "
                    "operator YAML ships as post-install manifests rather than a "
                    "HelmRelease. Opt-in: Knative is heavyweight (operator + "
                    "activator + autoscaler + Kourier ingress gateway), so "
                    "enable it only on clusters that host functions; clusters "
                    "without functions skip it. Kourier is the ingress (no Istio "
                    "dependency) and request logging is pre-enabled so the "
                    "invocation pipeline can record method/path/status/duration/"
                    "revision per call. The operator's CRDs land before the "
                    "KnativeServing CR via the install path's foundational-kinds-"
                    "first ordering."
                ),
                helm_values={},
                requires=[],
                options=[],
                # Chart-free component: no HelmRelease is emitted. The Knative
                # Operator Helm chart is published only as an OCI artifact in the
                # knative-releases Artifact Registry, which 403s unauthenticated
                # pulls — so Flux can't install it. The operator is applied
                # directly from the vendored official YAML in post_install below.
                chart_name="",
                chart_repo_url="",
                chart_version="",
                install_timeout="15m",
                # Kourier's external gateway is a Service type=LoadBalancer; it
                # needs the AWS LB controller present to get an NLB address, so
                # gate the install on the controller being Ready (same rationale
                # as kube-prometheus-stack / cert-manager).
                depends_on=["aws-load-balancer-controller"],
                # Vendored operator (Namespace + operator.knative.dev CRDs +
                # Deployments/RBAC/webhooks) first, then the knative-serving
                # Namespace + the KnativeServing CR. The install path sorts
                # foundational kinds (Namespace / CRD) ahead of the CR, so the
                # KnativeServing CRD is registered before the CR is applied.
                post_install_manifests=[
                    *KNATIVE_OPERATOR_MANIFESTS,
                    knative_serving_namespace,
                    knative_serving_cr,
                ],
            ),
            # The central auth host (#1539). Astrolift's tenant runtime
            # is EKS, so the cluster that actually serves tenant apps is
            # the one that most needs it -- offering it only in the
            # vanilla-k8s recipe is why the first install hand-assembled
            # oauth2-proxy with Flux. Disabled until the cluster carries a
            # complete oidc_auth_config, and it needs an nginx-family
            # controller to gate against, so it is inert on an ALB-only
            # cluster rather than harmful.
            central_auth_component(getattr(cluster, "oidc_auth_config", None)),
        ]

    # ---- exec_plugin token materialization (#309) -----------------
    #
    # The k8s_native backend's ``build_api_client`` handles
    # ``kubeconfig`` + ``service_account_token`` auth natively but
    # raises on ``exec_plugin`` — EKS-specific token minting is the
    # cloud driver's job. We mint the bearer token via the
    # AWS-IAM-Authenticator protocol, fetch the cluster's endpoint +
    # CA via DescribeCluster, synthesize an in-memory kubeconfig
    # blob, and rewrite the auth payload so the shared backend sees
    # a plain ``kubeconfig`` row. The kubeconfig branch is the more
    # tolerant path (carries endpoint, CA, and bearer in one YAML
    # doc) and is what ``aws eks get-token`` users actually consume
    # via their kubeconfig file — so the resolver path matches the
    # operator's local kubectl path exactly.
    #
    # Two helpers because ClusterContext (for bring/probe) and
    # ClusterAuth (for list_pods/stream_logs) are different frozen
    # dataclasses — dataclasses.replace is type-specific. Both
    # converge on ``_synthesize_kubeconfig`` for the actual work.

    def _resolve_eks_target(
        self,
        cluster_name: str | None,
        region: str | None,
    ) -> tuple[str, str]:
        """Resolve the (cluster_name, region) to use for token mint +
        describe. Falls back to the driver's configured values for
        legacy TenantCluster rows that pre-date auto-discovery."""
        return (
            cluster_name or self._config.cluster_name,
            region or self._config.region,
        )

    def _cached_token(self, cluster_name: str, region: str) -> str:
        """Mint-or-return-cached bearer token for the (cluster, region).

        Tokens are cached for ``exec_plugin_token_ttl_seconds`` (13 min
        by default) against the monotonic clock so resolver hot-loops
        don't re-sign on every call. Re-minted on miss / expiry."""
        key = (cluster_name, region)
        now = self._clock()
        entry = self._token_cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.token
        try:
            token = self._token_minter(cluster_name, region)
        except Exception as exc:
            raise map_client_error(exc) from exc
        self._token_cache[key] = _TokenCacheEntry(
            token=token,
            expires_at=now + float(self._config.exec_plugin_token_ttl_seconds),
        )
        return token

    def _cached_describe(self, cluster_name: str) -> _DescribeCacheEntry:
        """Endpoint + base64-CA for the cluster, cached after first hit.

        ``ClusterAuthError`` from the shared backend is what the
        resolver layer expects on auth failure, but at this layer we
        raise via ``map_client_error`` so the caller sees a typed
        ``NotFoundError`` / ``ProviderError`` and can decide whether
        to log + swallow or surface."""
        cached = self._describe_cache.get(cluster_name)
        if cached is not None:
            return cached
        try:
            response = self._eks.describe_cluster(name=cluster_name)
        except Exception as exc:
            raise map_client_error(exc) from exc
        cluster_payload = response["cluster"]
        entry = _DescribeCacheEntry(
            endpoint=cluster_payload["endpoint"],
            ca_data=cluster_payload["certificateAuthority"]["data"],
        )
        self._describe_cache[cluster_name] = entry
        return entry

    @driver_op(cloud="aws", driver="cluster", heartbeat=False)
    def invalidate_describe_cache(self, cluster_name: str | None = None) -> None:
        """Drop a cached DescribeCluster entry. Operators rotating
        the cluster CA can call this; the next observability call
        re-fetches. Passing None clears the whole cache."""
        if cluster_name is None:
            self._describe_cache.clear()
        else:
            self._describe_cache.pop(cluster_name, None)

    def _synthesize_token_auth(
        self,
        *,
        cluster_name: str,
        region: str,
    ) -> tuple[dict[str, Any], str]:
        """Build ``(auth_config, endpoint)`` for a ``service_account_token``-
        shaped ClusterAuth.

        Returns the bearer token + base64 CA in the shape
        ``build_api_client``'s ``service_account_token`` branch consumes
        (``{"token": <bearer>, "ca_cert": <pem-or-base64>}``) plus the
        cluster API endpoint so the caller can stamp it onto the
        replaced ClusterAuth / ClusterContext.

        Why not ``kubeconfig``: ``kubernetes.config.load_kube_config_from_dict``
        populates ``Configuration.api_key["authorization"]`` correctly,
        but the ApiClient's request dispatch silently drops the
        Authorization header for inline-``token`` users when the
        kubeconfig has no exec stanza — every request goes out unauthed
        and EKS returns 401. The bypass-kubeconfig pattern (direct
        Configuration object with ``host``/``ssl_ca_cert``/``api_key``)
        is what the python kubernetes-client actually ships working for
        EKS Bearer auth.
        """
        describe = self._cached_describe(cluster_name)
        token = self._cached_token(cluster_name, region)
        return (
            {"token": token, "ca_cert": describe.ca_data},
            describe.endpoint,
        )

    def _resolve_eks_auth_context(self, cluster: ClusterContext) -> ClusterContext:
        if cluster.auth_method != "exec_plugin":
            return cluster
        import dataclasses

        cfg = cluster.auth_config or {}
        cluster_name, region = self._resolve_eks_target(
            cfg.get("cluster_name"),
            cfg.get("region"),
        )
        auth_config, endpoint = self._synthesize_token_auth(
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            cluster,
            auth_method="service_account_token",
            auth_config=auth_config,
            endpoint=endpoint,
        )

    def _resolve_eks_auth(self, auth: ClusterAuth) -> ClusterAuth:
        if auth.auth_method != "exec_plugin":
            return auth
        import dataclasses

        cfg = auth.auth_config or {}
        cluster_name, region = self._resolve_eks_target(
            cfg.get("cluster_name"),
            cfg.get("region"),
        )
        auth_config, endpoint = self._synthesize_token_auth(
            cluster_name=cluster_name,
            region=region,
        )
        return dataclasses.replace(
            auth,
            auth_method="service_account_token",
            auth_config=auth_config,
            endpoint=endpoint,
        )

    # ---- Cluster health (#68 slice 1) -----------------------------

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception as exc:
            log.warning(
                "list_pod_phase_summary: k8s client build failed for cluster=%s: %s",
                cluster.slug,
                exc,
            )
            return []
        return pod_phase_summary_from_client(
            client,
            namespaces=default_namespaces(namespaces),
        )

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception as exc:
            log.warning(
                "list_events: k8s client build failed for cluster=%s: %s",
                cluster.slug,
                exc,
            )
            return []
        return events_from_client(
            client,
            namespaces=default_namespaces(namespaces),
            event_type=event_type,
            limit=limit,
        )

    @driver_op(cloud="aws", driver="cluster")
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
        except Exception as exc:
            log.warning(
                "list_workload_health: k8s client build failed for cluster=%s: %s",
                cluster.slug,
                exc,
            )
            return []
        return workload_health_from_client(
            client,
            namespaces=default_namespaces(namespaces),
        )

    @driver_op(cloud="aws", driver="cluster")
    def get_alb_http_metrics(
        self,
        cluster: ClusterContext,
        *,
        app_namespace: str,
        start_unix: int,
        end_unix: int,
        step_seconds: int,
    ) -> dict[str, list[tuple[float, float]]]:
        """CloudWatch ALB HTTP metrics for an app namespace.

        Returns a dict with keys 'rps', 'error_rate', 'latency_p50',
        'latency_p95', 'latency_p99'. Values are (unix_ts, value) lists.
        All lists are empty on any failure (driver degrades gracefully).

        Looks up the ALB by finding the Kubernetes Ingress in the app
        namespace and resolving its load-balancer hostname to an ALB ARN
        via elbv2 DescribeLoadBalancers. CloudWatch is then queried for
        RequestCount, HTTPCode_Target_5XX_Count, and TargetResponseTime.
        """
        from aws.timeseries_cloudwatch import (
            alb_arn_for_app_namespace,
            error_rate,
            latency,
            request_rate,
        )

        empty: dict[str, list[tuple[float, float]]] = {
            "rps": [],
            "error_rate": [],
            "latency_p50": [],
            "latency_p95": [],
            "latency_p99": [],
        }

        try:
            k8s = self._k8s(cluster.slug)
        except Exception as exc:
            log.warning("get_alb_http_metrics: k8s build failed cluster=%s: %s", cluster.slug, exc)
            return empty

        region = self._config.region
        _cred = self._config.credential
        elbv2 = aws_client("elbv2", region=region, credential=_cred)
        cw = aws_client("cloudwatch", region=region, credential=_cred)

        alb_arn = alb_arn_for_app_namespace(
            k8s_client=k8s,
            elbv2_client=elbv2,
            namespace=app_namespace,
        )
        if not alb_arn:
            log.info(
                "get_alb_http_metrics: no ALB found for ns=%s cluster=%s",
                app_namespace,
                cluster.slug,
            )
            return empty

        period = max(step_seconds, 60)
        kwargs = dict(
            cw=cw,
            alb_arn=alb_arn,
            start_unix=start_unix,
            end_unix=end_unix,
            period=period,
        )
        return {
            "rps": request_rate(**kwargs),
            "error_rate": error_rate(**kwargs),
            "latency_p50": latency(**kwargs, stat="p50"),
            "latency_p95": latency(**kwargs, stat="p95"),
            "latency_p99": latency(**kwargs, stat="p99"),
        }

    # ---- region / Cognito discovery (#860 / #859) -----------------

    @driver_op(cloud="aws", driver="cluster")
    def list_regions(self) -> list[RegionInfo]:
        """Live ``ec2:DescribeRegions`` for the cluster-register picker
        (#860).

        Lists the regions enabled on the account (``AllRegions=False``
        — the default — so disabled / opt-in-not-yet-enabled regions
        don't clutter the picker with regions a deploy can't land in).
        On any failure (no credentials, throttle, network) falls back
        to ``_AWS_FALLBACK_REGIONS`` so the picker is never empty —
        the frontend keeps free-entry on top of this anyway. Results
        are sorted by slug for a stable, scannable list.
        """
        try:
            resp = self._ec2.describe_regions()
            slugs = sorted(r["RegionName"] for r in resp.get("Regions", []) if r.get("RegionName"))
            if not slugs:
                raise ValueError("describe_regions returned no regions")
        except Exception as exc:
            log.warning(
                "list_regions: ec2:DescribeRegions failed (%s) — falling back to static list",
                exc,
            )
            slugs = list(_AWS_FALLBACK_REGIONS)
        return [_region_info(s) for s in slugs]

    @driver_op(cloud="aws", driver="cluster")
    def list_cognito_user_pools(self) -> list[CognitoUserPoolInfo]:
        """Live ``cognito-idp:ListUserPools`` for the auth-gate picker
        (#859).

        ListUserPools returns only ``Id`` + ``Name`` per pool, so the
        ARN is composed from the caller's account id + region + pool
        id, and the hosted domain is read from a per-pool
        DescribeUserPool. Paginates the full pool list (``MaxResults``
        caps at 60). Raises on credential / API failure — the resolver
        swallows it into an empty list so the picker degrades to
        free-entry. A failed per-pool DescribeUserPool degrades that
        single pool to an empty domain rather than dropping it.
        """
        region = self._config.region
        client = self._cognito_idp_client()

        # Account id for ARN composition — Cognito pool ARNs are
        # arn:aws:cognito-idp:<region>:<account>:userpool/<pool-id>.
        try:
            account_id = self._sts.get_caller_identity()["Account"]
        except Exception as exc:
            raise map_client_error(exc) from exc

        pools: list[CognitoUserPoolInfo] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"MaxResults": 60}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                resp = client.list_user_pools(**kwargs)
            except Exception as exc:
                raise map_client_error(exc) from exc
            for p in resp.get("UserPools", []):
                pool_id = p.get("Id", "")
                if not pool_id:
                    continue
                pools.append(
                    CognitoUserPoolInfo(
                        pool_id=pool_id,
                        pool_arn=f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{pool_id}",
                        name=p.get("Name", ""),
                        domain=self._cognito_pool_domain(client, pool_id),
                        region=region,
                    ),
                )
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return pools

    @driver_op(cloud="aws", driver="cluster")
    def list_cognito_user_pool_clients(self, pool_id: str) -> list[CognitoUserPoolClientInfo]:
        """Live ``cognito-idp:ListUserPoolClients`` for ``pool_id`` (#859).

        Paginates the full client list. Raises on credential / API
        failure — the resolver swallows it into an empty list so the
        dependent client picker degrades to free-entry.
        """
        client = self._cognito_idp_client()
        clients: list[CognitoUserPoolClientInfo] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"UserPoolId": pool_id, "MaxResults": 60}
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                resp = client.list_user_pool_clients(**kwargs)
            except Exception as exc:
                raise map_client_error(exc) from exc
            for c in resp.get("UserPoolClients", []):
                client_id = c.get("ClientId", "")
                if not client_id:
                    continue
                clients.append(
                    CognitoUserPoolClientInfo(
                        client_id=client_id,
                        client_name=c.get("ClientName", ""),
                    ),
                )
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return clients

    def _cognito_pool_domain(self, client: Any, pool_id: str) -> str:
        """Best-effort hosted-domain lookup for ``pool_id`` via
        DescribeUserPool. Returns the bare domain prefix (what the ALB
        auth annotation wants — without the
        ``.auth.<region>.amazoncognito.com`` suffix). A pool with no
        hosted domain, or a failed describe, yields an empty string so
        the caller still surfaces the pool."""
        try:
            resp = client.describe_user_pool(UserPoolId=pool_id)
        except Exception as exc:
            log.warning(
                "list_cognito_user_pools: describe_user_pool failed for %s (%s) — domain blank",
                pool_id,
                exc,
            )
            return ""
        return resp.get("UserPool", {}).get("Domain", "") or ""

    def _cognito_idp_client(self) -> Any:
        """Lazily build (and cache) the cognito-idp boto3 client in the
        cluster's region. Tests inject the client in the constructor;
        production builds it here on first use via the ambient
        credential chain (same as the eks/sts/ec2 clients)."""
        if self._cognito_idp is None:
            self._cognito_idp = aws_client(
                "cognito-idp", region=self._config.region, credential=self._config.credential
            )
        return self._cognito_idp

    # ---- certificate discovery (#858) ------------------------------

    @driver_op(cloud="aws", driver="cluster")
    def list_certificates(self, cluster: ClusterContext) -> list[CertificateInfo]:
        """List ACM certificates available in the cluster's region (#858).

        Backs the SNI / custom-domain cert picker: rather than make the
        operator paste an ACM ARN from the console, the UI offers the
        certs the platform's IAM role can already see. Paginates
        ``acm:ListCertificates`` filtered to ``ISSUED`` status (only
        usable certs can terminate TLS), then ``acm:DescribeCertificate``
        per cert to read the primary domain name. Returns newest-listed
        first; the ARN is the identifier the platform persists.

        ``cluster`` is accepted for protocol symmetry with the other
        cloud-read methods but isn't needed — ACM is account+region
        scoped, and the region comes from ``EKSConfig``. The describe
        call is best-effort per cert: a transient describe failure on
        one cert falls back to the list-level domain name rather than
        dropping the cert from the picker.
        """
        del cluster  # ACM is account/region-scoped; region from config
        acm = self._acm_client()
        out: list[CertificateInfo] = []
        try:
            paginator = acm.get_paginator("list_certificates")
            pages = paginator.paginate(CertificateStatuses=["ISSUED"])
        except Exception as exc:
            log.warning(
                "list_certificates: ListCertificates failed region=%s: %s",
                self._config.region,
                exc,
            )
            return out
        for page in pages:
            for summary in page.get("CertificateSummaryList", []) or []:
                arn = summary.get("CertificateArn", "")
                if not arn:
                    continue
                domain_name = summary.get("DomainName", "")
                status = summary.get("Status", "ISSUED")
                label = domain_name
                try:
                    desc = acm.describe_certificate(CertificateArn=arn)
                    cert = desc.get("Certificate", {}) or {}
                    domain_name = cert.get("DomainName", domain_name) or domain_name
                    status = cert.get("Status", status) or status
                    sans = cert.get("SubjectAlternativeNames", []) or []
                    label = f"{domain_name} (+{len(sans) - 1})" if domain_name and len(sans) > 1 else domain_name
                except Exception as exc:
                    log.warning(
                        "list_certificates: DescribeCertificate failed arn=%s: %s",
                        arn,
                        exc,
                    )
                out.append(
                    CertificateInfo(
                        arn=arn,
                        name=label or arn,
                        domain_name=domain_name,
                        status=status,
                    )
                )
        return out

    # ---- managed model (Bedrock auto-wire) ------------------------

    @driver_op(cloud="aws", driver="cluster", heartbeat=False)
    def agent_model_env(self, *, region: str, provider_config: dict[str, Any]) -> dict[str, str]:
        """Bedrock model env for the managed-model agent path.

        Returns the env a Claude Code runner reads to target Bedrock
        instead of an ANTHROPIC_API_KEY: ``CLAUDE_CODE_USE_BEDROCK=1`` +
        ``AWS_REGION`` + the Opus / Haiku model ids. The ids default to
        a cross-region inference-profile pair (:data:`_DEFAULT_BEDROCK_MODEL_ID`
        / :data:`_DEFAULT_BEDROCK_SMALL_FAST_MODEL_ID`) and are overridable
        via ``provider_config["bedrock_model_id"]`` /
        ``["bedrock_small_fast_model_id"]``, then ``ANTHROPIC_MODEL`` /
        ``ANTHROPIC_SMALL_FAST_MODEL`` on the worker. ``region`` falls back
        to the driver's configured region. No cloud call.
        """
        provider_config = provider_config or {}
        model_id = _first_nonblank(
            provider_config.get("bedrock_model_id"),
            os.environ.get("ANTHROPIC_MODEL"),
            _DEFAULT_BEDROCK_MODEL_ID,
        )
        small_fast = _first_nonblank(
            provider_config.get("bedrock_small_fast_model_id"),
            os.environ.get("ANTHROPIC_SMALL_FAST_MODEL"),
            _DEFAULT_BEDROCK_SMALL_FAST_MODEL_ID,
        )
        return {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": region or self._config.region,
            "ANTHROPIC_MODEL": model_id,
            "ANTHROPIC_SMALL_FAST_MODEL": small_fast,
        }

    @driver_op(cloud="aws", driver="cluster", audit=True, sensitive_kind="cluster.ensure_agent_model_identity")
    def ensure_agent_model_identity(
        self,
        *,
        namespace: str,
        service_account: str,
        provider_config: dict[str, Any],
    ) -> str:
        """Idempotently mint (or reuse) the IRSA role the managed-model
        pod's ServiceAccount assumes to call ``bedrock:InvokeModel``.

        Reuses the platform's proven IAM idempotency pattern (create →
        EntityAlreadyExists → update trust + re-put inline policy). The
        trust policy binds the cluster's EKS OIDC issuer to the
        ``system:serviceaccount:<namespace>:<service_account>`` subject
        with ``aud = sts.amazonaws.com``; the inline policy grants
        ``bedrock:InvokeModel`` + ``bedrock:InvokeModelWithResponseStream``
        (``Resource "*"`` by default, narrowable via
        ``provider_config["bedrock_model_arns"]``). Returns the role ARN.

        Operator override: when ``provider_config["agent_model_role_arn"]``
        is set the driver uses that ARN verbatim and mints nothing — for
        installs whose role is provisioned out-of-band (Terraform / the
        control-plane task role can't ``iam:CreateRole``).
        """
        import json

        provider_config = provider_config or {}
        # Operator-provisioned override wins — skip minting entirely.
        explicit = str(provider_config.get("agent_model_role_arn") or "").strip()
        if explicit:
            return explicit

        issuer = self._oidc_issuer()
        if not issuer:
            raise ManagedModelNotSupportedError(
                f"cluster {self._config.cluster_name!r}: could not resolve the EKS OIDC "
                "issuer (DescribeCluster returned none); the cluster's IAM OIDC provider "
                "must exist before a managed-model role can be minted",
            )

        account_id = str(provider_config.get("account_id") or "").strip()
        if not account_id:
            try:
                account_id = self._sts.get_caller_identity()["Account"]
            except Exception as exc:  # no account id ⇒ can't build the ARN
                raise map_client_error(exc) from exc

        role_name = iam_role_name("astrolift", "agent-model", self._config.cluster_name, namespace)
        oidc_arn = f"arn:aws:iam::{account_id}:oidc-provider/{issuer}"
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": oidc_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{issuer}:aud": "sts.amazonaws.com",
                            f"{issuer}:sub": f"system:serviceaccount:{namespace}:{service_account}",
                        },
                    },
                },
            ],
        }
        # Least-privilege escape hatch: narrow the invoke Resource to
        # specific foundation-model / inference-profile ARNs via
        # provider_config; default "*" keeps the common case zero-config.
        resources = provider_config.get("bedrock_model_arns") or "*"
        invoke_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": resources,
                },
            ],
        }

        iam = self._iam_client()
        try:
            response = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                # ASCII-only Description — IAM rejects non-Latin-1 (#1026).
                Description=(
                    f"Astrolift managed-model (Bedrock) role for "
                    f"{namespace}:{service_account} on {self._config.cluster_name}"
                ),
                # Tag like every platform-minted role so the orphan scan
                # (#995) can reap it if a teardown is interrupted.
                Tags=[{"Key": "astrolift.io/managed-by", "Value": "platform"}],
            )
            role_arn = response["Role"]["Arn"]
        except iam.exceptions.EntityAlreadyExistsException:
            # Idempotent: the role exists — reconcile its trust (the OIDC
            # issuer or SA subject may have changed) and re-read the ARN.
            # put_role_policy below is idempotent too.
            iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust_policy),
            )
            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        except Exception as exc:  # surface a typed provider error
            raise map_client_error(exc) from exc

        try:
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="bedrock-invoke",
                PolicyDocument=json.dumps(invoke_policy),
            )
        except Exception as exc:  # surface a typed provider error
            raise map_client_error(exc) from exc
        return role_arn

    # ---- internals ------------------------------------------------

    def _acm_client(self) -> Any:
        """Lazily build (and cache) the ACM boto3 client for the cluster's
        region. Mirrors the eks/sts/ec2 client construction; injectable
        via the ``acm_client`` ctor kwarg for moto tests."""
        if self._acm is None:
            self._acm = aws_client("acm", region=self._config.region, credential=self._config.credential)
        return self._acm

    def _iam_client(self) -> Any:
        """Lazily build (and cache) the IAM boto3 client. IAM is a global
        service; the region is cosmetic but kept consistent with the
        cluster's other clients. Injectable via the ``iam_client`` ctor
        kwarg for moto tests (mirrors ``_acm_client``)."""
        if self._iam is None:
            self._iam = aws_client("iam", region=self._config.region, credential=self._config.credential)
        return self._iam

    def _oidc_issuer(self) -> str:
        """Resolve the cluster's EKS OIDC issuer (host + path, no scheme)
        via DescribeCluster, or ``""`` when it can't be read.

        The IAM ``oidc-provider/<issuer>`` principal + the ``<issuer>:sub``
        / ``<issuer>:aud`` trust conditions are built from this, so an empty
        value means "can't bind workload identity yet" — the caller raises
        rather than mint a broken trust. Uses the driver's injected EKS
        client so moto / fake-client tests resolve it without a live
        cluster (mirrors ``aws.identity_irsa.discover_oidc_issuer`` but
        reuses ``self._eks`` rather than a fresh boto3 client)."""
        try:
            cluster = self._eks.describe_cluster(name=self._config.cluster_name)["cluster"]
        except Exception as exc:  # unreadable issuer ⇒ empty, caller decides
            log.warning(
                "agent_model_identity: DescribeCluster failed for %s: %s",
                self._config.cluster_name,
                exc,
            )
            return ""
        issuer = (((cluster.get("identity") or {}).get("oidc") or {}).get("issuer")) or ""
        return issuer.removeprefix("https://")

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
        # Shared with the observability path's ``_cached_describe`` so
        # both the apply-manifests boto3 flow and the synthesize-
        # kubeconfig flow read the same canonical endpoint + CA pair.
        # ca_data is base64-encoded by EKS; the kubernetes client
        # handles the decode per its config shape.
        entry = self._cached_describe(self._config.cluster_name)
        return entry.endpoint, entry.ca_data

    def _eks_token(self) -> str:
        """Generate a short-lived EKS bearer token via the
        AWS-IAM-Authenticator protocol (presigned STS GetCallerIdentity
        URL with ``x-k8s-aws-id`` header). Re-minted per operation
        rather than cached; STS rejects URLs older than 15 minutes,
        so we ask for the EKS ceiling (``sts_token_lifetime_seconds``,
        default 900) to avoid spurious mid-operation auth failures
        on first-rollout deploys (#359)."""
        try:
            return mint_eks_token(
                cluster_name=self._config.cluster_name,
                region=self._config.region,
                expires_in_seconds=self._config.sts_token_lifetime_seconds,
            )
        except Exception as exc:
            raise map_client_error(exc) from exc


# ---- Internal client + exception types -----------------------------
#
# The DynamicClient wrapper + helpers (``_split_kind``,
# ``_DEFAULT_API_VERSION_FOR_KIND``, ``_PortForwardHandle``) used to
# live in this module. They were extracted into
# ``_sdk/k8s_dynamic_client.py`` (the audit-recommended shared helper,
# closes #566 / #567 / #568) so every cloud cluster driver can wire its
# auth shim to the same body without copy-pasting 400 lines of
# DynamicClient plumbing per cloud.
#
# What remains AWS-specific in this file is the bearer-token mint
# (``_eks_token`` above — presigned STS GetCallerIdentity) and the
# base64-encoded CA shape EKS hands back from DescribeCluster (the
# shared helper handles the decode for the EKS / GKE wire shape).

# Re-exports (``_RealK8sClient``, ``_DEFAULT_API_VERSION_FOR_KIND``,
# ``_NotFoundError``, ``_PortForwardHandle``, ``_split_kind``) live at
# the top of the file alongside the rest of the imports — kept here
# as a comment so future readers find the alias map without grep.
#
# Callers in this module catch ``_NotFoundError`` (== the shared
# helper's ``NotFoundError``) on resolver paths.


def _build_k8s_client(
    *,
    endpoint: str,
    ca_data: str,
    token_provider: Callable[[], str],
) -> Any:
    """Default factory that returns a real kubernetes client wrapper.

    Tests inject their own factory via EKSClusterDriver's
    ``k8s_client_factory`` arg so we don't need a live apiserver. The
    EKS wire hands us ``ca_data`` base64-encoded; the shared helper
    handles the decode.
    """
    return _RealK8sClient(
        endpoint=endpoint,
        ca_data=ca_data,
        token_provider=token_provider,
    )
