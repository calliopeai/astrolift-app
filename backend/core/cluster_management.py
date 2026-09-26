"""Resolver-facing entry for the cluster bring-into-management path (#316).

Mirrors ``core/cluster_observability.py``: builds a ``ClusterContext``
from a ``TenantCluster`` row, resolves the right provider plugin's
``ClusterDriver``, and dispatches ``bring_into_management`` /
``probe_capabilities`` through it.

The driver classes ship with config-required constructors (EKSConfig,
GKEConfig, AKSConfig, K8sNativeConfig); the management path uses the
same per-plugin ``_config_for`` helper that the observability path
does — exported here so both paths stay in sync as new plugins ship.

Pluggable test backend:
The k8s_native driver (and its subclasses) take a ``management_backend``
constructor kwarg that swaps the live kubernetes-client path for a
deterministic fake. Tests go through :func:`set_management_backend_for_tests`
so the workflow activities can run against a record-only fake without
contacting a real apiserver.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from astrolift_drivers.registry import DriverNotFound, plugins
from core.cluster_credentials import CREDENTIAL_REFUSALS, assert_credential_supported
from core.cluster_observability import (
    ClusterObservabilityError,
    _config_for,  # type: ignore[attr-defined]
)

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)


class ClusterManagementError(Exception):
    """A cluster row can't be turned into a usable management driver
    — either the plugin isn't loaded or the driver doesn't implement
    the management methods. The workflow activity maps this to
    ``mark_error`` so the operator sees a useful error in the UI."""


# ---- Test-injectable backend --------------------------------------


_MANAGEMENT_BACKEND_OVERRIDE: Any = None


def set_management_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic management backend.

    Backend interface mirrors the provider's ``ManagementBackend``
    protocol (``apply_manifest``, ``list_cluster_crds``,
    ``list_namespaced_pods``, ``list_storage_classes``,
    ``run_preflight_job``). Reset with
    :func:`reset_management_backend_for_tests`.
    """
    global _MANAGEMENT_BACKEND_OVERRIDE
    _MANAGEMENT_BACKEND_OVERRIDE = backend


def reset_management_backend_for_tests() -> None:
    global _MANAGEMENT_BACKEND_OVERRIDE
    _MANAGEMENT_BACKEND_OVERRIDE = None


# ---- Public resolver / activity entry -----------------------------


def _context_for_cluster(cluster: TenantCluster) -> Any:
    """Build a ``ClusterContext`` payload from a TenantCluster row.

    Lazy import so the schema-export command (which doesn't have
    provider plugins installed) still loads this module without
    importing the SDK."""
    from _sdk.cluster import ClusterContext

    return ClusterContext(
        slug=cluster.slug,
        auth_method=cluster.auth_method,
        auth_config=cluster.auth_config or {},
        endpoint=cluster.endpoint or "",
        ca_cert=cluster.ca_cert or "",
        ingress_class=cluster.ingress_class or "",
        provider_plugin_slug=cluster.provider_plugin.slug if cluster.provider_plugin_id else "",
        oidc_auth_config=cluster.oidc_auth_config or {},
    )


class _OverrideManagementDriver:
    """Test-only driver — bypasses provider-plugin lookup and runs
    the canonical k8s_native management orchestrator with the
    injected backend. Mirrors ``_OverrideDriver`` in observability —
    keeps test fixtures from needing a real provider plugin loaded."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def bring_into_management(self, cluster: Any, *, run_preflight: bool = True) -> Any:
        from k8s_native.management import run_bring_into_management

        return run_bring_into_management(
            backend=self._backend,
            cluster=cluster,
            run_preflight=run_preflight,
        )

    def probe_capabilities(self, cluster: Any) -> dict[str, Any]:
        from k8s_native.management import probe_cluster_capabilities

        return probe_cluster_capabilities(backend=self._backend, cluster=cluster)

    def read_job_status(self, cluster: Any, *, namespace: str, job_name: str) -> Any:
        from k8s_native.management import read_cluster_job_status

        return read_cluster_job_status(
            backend=self._backend,
            cluster=cluster,
            namespace=namespace,
            job_name=job_name,
        )


def _driver_for_cluster(cluster: TenantCluster) -> Any:
    """Resolve the ``ClusterDriver`` for ``cluster.provider_plugin``.

    Test-installed backend short-circuits the registry lookup so
    synthetic ProviderPlugin rows used in tests work without a real
    plugin being loaded. Otherwise we go through the same plugin
    registry the observability path uses."""
    if _MANAGEMENT_BACKEND_OVERRIDE is not None:
        return _OverrideManagementDriver(backend=_MANAGEMENT_BACKEND_OVERRIDE)

    plugin_slug = cluster.provider_plugin.slug
    try:
        assert_credential_supported(cluster, capability="cluster")
    except CREDENTIAL_REFUSALS as exc:
        raise ClusterManagementError(str(exc)) from exc
    try:
        driver_cls = plugins.get(plugin_slug, "cluster")
    except DriverNotFound as exc:
        raise ClusterManagementError(
            f"cluster {cluster.slug}: provider plugin {plugin_slug!r} does not register a 'cluster' driver",
        ) from exc

    cfg = _config_for(plugin_slug, cluster)
    try:
        return driver_cls(config=cfg)
    except TypeError as exc:
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver {plugin_slug!r} constructor rejected config payload: {exc}",
        ) from exc


def _config_for_plugin_slug(plugin_slug: str) -> Any:
    """Build a minimal driver config from a plugin slug alone — no
    TenantCluster row.

    Used by the region-picker path (#860), which runs before any
    cluster exists (the operator is filling in the register dialog).
    The region listing doesn't need cluster-identifying config:
      - AWS: ``ec2:DescribeRegions`` only needs a boto3 client, which
        needs *a* region to construct (regions are account-global).
        ``us-east-1`` is the conventional bootstrap region. The
        placeholder ``cluster_name`` is never read by ``list_regions``.
      - GCP / Azure / k8s_native: ``list_regions`` is a static list, so
        empty-default configs suffice.

    Raises :class:`ClusterManagementError` for an unknown slug so the
    resolver can fall back rather than 500.
    """
    if plugin_slug == "aws":
        from aws.cluster_eks import EKSConfig

        return EKSConfig(region="us-east-1", cluster_name="_region_probe")
    if plugin_slug == "gcp":
        from gcp.cluster_gke import GKEConfig

        return GKEConfig(project_id="", location="", cluster_name="_region_probe")
    if plugin_slug == "azure":
        from azure.cluster_aks import AKSConfig

        return AKSConfig(subscription_id="", resource_group="", cluster_name="_region_probe")
    if plugin_slug == "k8s_native":
        from k8s_native.cluster import K8sNativeConfig

        return K8sNativeConfig()
    raise ClusterManagementError(f"no config builder wired for plugin {plugin_slug!r}")


def _driver_for_plugin_slug(plugin_slug: str) -> Any:
    """Resolve a ``ClusterDriver`` from a plugin slug with a minimal
    bootstrap config (#860). Mirrors :func:`_driver_for_cluster` but
    for the no-cluster region-picker path. Raises
    :class:`ClusterManagementError` when the plugin isn't loaded or its
    constructor rejects the config."""
    try:
        driver_cls = plugins.get(plugin_slug, "cluster")
    except DriverNotFound as exc:
        raise ClusterManagementError(
            f"provider plugin {plugin_slug!r} does not register a 'cluster' driver",
        ) from exc
    cfg = _config_for_plugin_slug(plugin_slug)
    try:
        return driver_cls(config=cfg)
    except TypeError as exc:
        raise ClusterManagementError(
            f"driver {plugin_slug!r} constructor rejected config payload: {exc}",
        ) from exc


def provider_regions_dispatch(*, provider_plugin_slug: str) -> list[dict[str, str]]:
    """Driver-backed region listing for the cluster-register picker
    (#860).

    Resolves a driver from the plugin slug alone and calls
    ``list_regions()``. Returns a list of plain dicts
    ``[{"id", "label", "continent"}, ...]`` so the resolver layer
    converts without strawberry-side coercion. The driver's own
    ``list_regions`` falls back to a static list on live-API failure
    (AWS) or is static to begin with (GCP/Azure); this dispatch raises
    :class:`ClusterManagementError` only when the *driver* can't be
    built (plugin not loaded), which the resolver maps to an empty
    list so the picker degrades to free-entry.
    """
    driver = _driver_for_plugin_slug(provider_plugin_slug)
    if not hasattr(driver, "list_regions"):
        return []
    try:
        regions = driver.list_regions()
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"plugin {provider_plugin_slug!r}: list_regions raised {exc}",
        ) from exc
    return [{"id": r.id, "label": r.label, "continent": r.continent} for r in regions]


def cognito_user_pools_dispatch(*, cluster: TenantCluster) -> list[dict[str, str]]:
    """Driver-backed Cognito user-pool listing for the ingress
    auth-gate picker (#859).

    Delegates to the driver's ``list_cognito_user_pools`` (AWS-only;
    other drivers inherit the SDK default that returns an empty list).
    Returns plain dicts ``[{"pool_id", "pool_arn", "name", "domain",
    "region"}, ...]``. Raises :class:`ClusterManagementError` on driver
    build / call failure (no creds, unreachable, plugin missing); the
    resolver swallows it into an empty list so the picker degrades to
    free-entry.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "list_cognito_user_pools"):
        return []
    try:
        pools = driver.list_cognito_user_pools()
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_cognito_user_pools raised {exc}",
        ) from exc
    return [
        {
            "pool_id": p.pool_id,
            "pool_arn": p.pool_arn,
            "name": p.name,
            "domain": p.domain,
            "region": p.region,
        }
        for p in pools
    ]


def cognito_user_pool_clients_dispatch(
    *,
    cluster: TenantCluster,
    pool_id: str,
) -> list[dict[str, str]]:
    """Driver-backed Cognito app-client listing for a user pool (#859).

    Delegates to the driver's ``list_cognito_user_pool_clients``
    (AWS-only). Returns plain dicts ``[{"client_id", "client_name"},
    ...]``. Same error contract as
    :func:`cognito_user_pools_dispatch`.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "list_cognito_user_pool_clients"):
        return []
    try:
        clients = driver.list_cognito_user_pool_clients(pool_id)
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_cognito_user_pool_clients raised {exc}",
        ) from exc
    return [{"client_id": c.client_id, "client_name": c.client_name} for c in clients]


def bring_cluster_into_management(
    *,
    cluster: TenantCluster,
    run_preflight: bool = True,
) -> Any:
    """Run the bring-into-management orchestration against ``cluster``.

    Returns the driver's ``ManagementReport``. Raises
    :class:`ClusterManagementError` when the driver can't be
    constructed — the workflow activity catches and surfaces that
    as ``mark_error``.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "bring_into_management"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement bring_into_management",
        )
    ctx = _context_for_cluster(cluster)
    return driver.bring_into_management(ctx, run_preflight=run_preflight)


def probe_cluster_capabilities_dispatch(
    *,
    cluster: TenantCluster,
) -> dict[str, Any]:
    """Run the read-only capability probe against ``cluster``.

    Used by the verify-reachability + probe activities; raises
    :class:`ClusterManagementError` on driver-resolution failure and
    bubbles auth / network errors so the workflow can record them
    in ``last_management_error``.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "probe_capabilities"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement probe_capabilities",
        )
    ctx = _context_for_cluster(cluster)
    return driver.probe_capabilities(ctx)


def teardown_cluster_dispatch(*, cluster: TenantCluster, delete_cloud_infra: bool) -> Any:
    """Run the driver's ``teardown_cluster`` against ``cluster``.

    Returns the driver's ``TeardownReport``. Raises
    :class:`ClusterManagementError` when the driver can't be
    constructed (plugin missing, auth invalid, ...). When the driver
    returns ``success=False`` with a non-empty ``error``, the workflow
    layer flips the row to error lifecycle and surfaces the message.
    Drivers handle "already gone" idempotently; the activity does NOT
    treat that as failure.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "teardown_cluster"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement teardown_cluster",
        )
    ctx = _context_for_cluster(cluster)
    return driver.teardown_cluster(ctx, delete_cloud_infra=delete_cloud_infra)


def bootstrap_components_dispatch(*, cluster: TenantCluster) -> list[Any]:
    """Return the driver's bootstrap recipe for ``cluster``.

    Read-only — no cluster API calls; the recipe is a static
    declaration each driver provides. Returns a list of
    ``BootstrapComponent`` dataclasses (from ``_sdk.cluster``); the
    resolver layer converts them to GraphQL types. Falls back to an
    empty list when the driver doesn't implement
    ``bootstrap_components`` (older providers); the UI renders
    "no recipe available for this provider" rather than crashing.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "bootstrap_components"):
        return []
    ctx = _context_for_cluster(cluster)
    try:
        return list(driver.bootstrap_components(ctx))
    except Exception as exc:
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver bootstrap_components raised {exc}",
        ) from exc


# ---- Keep-alive agent deploy (#873) -------------------------------

# Namespace the keep-alive agent runs in. Same one the bootstrap
# prereqs land in, so the agent sits alongside cert-manager / ingress.
AGENT_NAMESPACE = "astrolift-system"

# The agent reads heartbeat_url + agent_key from this Secret. The
# Deployment references it by name via secretKeyRef; the mutation does
# NOT create it (the raw agent key is surfaced exactly once at
# issueClusterAgentKey and never persisted, so the control plane has no
# key to put in the Secret). The operator applies the Secret from the
# snippet in the cluster settings agent card before deploying.
AGENT_SECRET_NAME = "astrolift-agent"

# Container image for the keep-alive agent. Override via env so a
# private-registry mirror or a pinned digest can be swapped in without a
# code change. Defaults to the public Docker Hub image published by the
# astrolift-agents fleet (calliopeai/astrolift-agent-keepalive) — public so
# any tenant cluster, on any cloud, pulls it without a pull secret. The old
# ghcr `astrolift-agent` name was never built (ImagePullBackOff).
AGENT_IMAGE = os.environ.get(
    "ASTROLIFT_AGENT_IMAGE",
    "docker.io/calliopeai/astrolift-agent-keepalive:latest",
)


def build_agent_manifests(cluster: TenantCluster) -> list[dict[str, Any]]:
    """Render the keep-alive agent's Namespace + Deployment manifests.

    Pure — no cluster API calls, no driver — so it's unit-testable in
    isolation and the apply path stays a thin wrapper. The Deployment
    pulls ``heartbeat_url`` + ``agent_key`` from the pre-created
    ``astrolift-agent`` Secret (see :data:`AGENT_SECRET_NAME`) and pulses
    on the cluster's configured ``heartbeat_interval_seconds``.

    Ordering matters: the Namespace manifest is first so a server-side
    apply that creates the namespace and the Deployment in one pass lands
    the namespace before the workload that targets it.
    """
    labels = {"app": "astrolift-agent", "astrolift.io/managed-by": "platform"}
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": AGENT_NAMESPACE,
                "labels": {"astrolift.io/managed-by": "platform"},
            },
        },
        # Read-only RBAC so the agent can collect the live telemetry snapshot
        # (node/pod/service inventory + metrics-server usage) for the
        # heartbeat payload (#112). Cluster-scoped get/list only — the agent
        # never mutates anything.
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": "astrolift-agent",
                "namespace": AGENT_NAMESPACE,
                "labels": labels,
            },
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": "astrolift-agent", "labels": labels},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["nodes", "pods", "services"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": ["metrics.k8s.io"],
                    "resources": ["nodes", "pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["ingresses"],
                    "verbs": ["get", "list"],
                },
                # Deliberately NO cluster-wide grant on secrets (#2064
                # security review). The agent's test-prompt relay needs to
                # read exactly one Secret per hosted model -- that grant is
                # a namespaced Role + RoleBinding the vLLM driver renders
                # alongside the service itself, restricted by
                # `resourceNames` to that one Secret (see
                # `k8s_native.managed.model_endpoint_vllm._agent_test_rbac`).
                # A cluster-wide `get` on secrets would have let the agent
                # (and so a compromised control plane naming an arbitrary
                # Secret) read any Secret in any tenant namespace.
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "astrolift-agent", "labels": labels},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "astrolift-agent",
            },
            "subjects": [
                {
                    "kind": "ServiceAccount",
                    "name": "astrolift-agent",
                    "namespace": AGENT_NAMESPACE,
                }
            ],
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "astrolift-agent",
                "namespace": AGENT_NAMESPACE,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "astrolift-agent"}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "serviceAccountName": "astrolift-agent",
                        "automountServiceAccountToken": True,
                        "containers": [
                            {
                                "name": "agent",
                                "image": AGENT_IMAGE,
                                "env": [
                                    {
                                        "name": "HEARTBEAT_URL",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": AGENT_SECRET_NAME,
                                                "key": "heartbeat_url",
                                            }
                                        },
                                    },
                                    {
                                        "name": "AGENT_KEY",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": AGENT_SECRET_NAME,
                                                "key": "agent_key",
                                            }
                                        },
                                    },
                                    {
                                        "name": "INTERVAL_SECONDS",
                                        "value": str(cluster.heartbeat_interval_seconds),
                                    },
                                ],
                            }
                        ],
                    },
                },
            },
        },
    ]


def deploy_agent_dispatch(*, cluster: TenantCluster) -> Any:
    """Apply the keep-alive agent manifests to ``cluster``.

    Resolves the cluster's ``ClusterDriver`` and applies the Namespace +
    Deployment via ``apply_manifests`` (server-side apply, idempotent —
    re-running converges the Deployment to the rendered spec). Returns
    the driver's ``ApplyResult`` so the resolver can branch on ``ok`` and
    surface structured errors.

    Raises :class:`ClusterManagementError` when the driver can't be
    constructed or doesn't implement ``apply_manifests`` (older provider)
    — the resolver maps that to an INTERNAL MutationResult with the
    message persisted to ``last_management_error``.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "apply_manifests"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement apply_manifests",
        )
    ctx = _context_for_cluster(cluster)
    manifests = build_agent_manifests(cluster)
    return driver.apply_manifests(ctx.slug, AGENT_NAMESPACE, manifests)


def apply_manifests_dispatch(
    *, cluster: TenantCluster, namespace: str, manifests: list[dict[str, Any]]
) -> Any:
    """Server-side apply platform-owned ``manifests`` to ``cluster``.

    The same driver path :func:`deploy_agent_dispatch` uses, for other
    platform components (the Zentinelle gateway, #1887). Returns the
    driver's ``ApplyResult``; raises :class:`ClusterManagementError` when
    the driver can't be built or has no ``apply_manifests``.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "apply_manifests"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement apply_manifests",
        )
    ctx = _context_for_cluster(cluster)
    return driver.apply_manifests(ctx.slug, namespace, manifests)


def delete_manifests_dispatch(
    *, cluster: TenantCluster, namespace: str, manifests: list[dict[str, Any]]
) -> Any:
    """Delete platform-owned ``manifests`` from ``cluster`` by kind and name.

    Returns the driver's ``DeleteResult``, where an object that is already
    gone lands in ``not_found`` rather than ``errors``. Raises
    :class:`ClusterManagementError` like :func:`apply_manifests_dispatch`.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "delete_manifests"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement delete_manifests",
        )
    ctx = _context_for_cluster(cluster)
    return driver.delete_manifests(ctx.slug, namespace, manifests)


def cluster_health_dispatch(
    *,
    cluster: TenantCluster,
    namespaces: list[str] | None = None,
    event_limit: int = 50,
) -> dict[str, Any]:
    """Driver-backed pod-phase rollup + recent Warning events (#68
    slice 1). Returns a dict ``{"pods": [...], "events": [...]}``
    with the dataclasses converted to plain dicts so the GraphQL
    resolver layer can return them without strawberry-side coercion.

    Per-driver failure to resolve a kube client (no exec_plugin
    creds, unreachable apiserver) yields empty lists rather than an
    exception — the UI surfaces "no data" instead of erroring out
    the whole status tab.
    """
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    try:
        pods = driver.list_pod_phase_summary(ctx, namespaces=namespaces)
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_pod_phase_summary raised {exc}",
        ) from exc
    try:
        events = driver.list_events(
            ctx,
            namespaces=namespaces,
            limit=event_limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_events raised {exc}",
        ) from exc
    return {
        "pods": [
            {
                "namespace": p.namespace,
                "phase": p.phase,
                "count": p.count,
            }
            for p in pods
        ],
        "events": [
            {
                "namespace": e.namespace,
                "name": e.name,
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "first_seen": e.first_seen,
                "last_seen": e.last_seen,
                "involved_object": e.involved_object,
            }
            for e in events
        ],
    }


def cluster_workload_health_dispatch(
    *,
    cluster: TenantCluster,
    namespaces: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Driver-backed per-Deployment health rollup for the Status tab
    (#362). Returns a list of plain dicts so the resolver layer can
    convert without strawberry-side coercion:

        [{"namespace", "name", "desired_replicas", "ready_replicas",
          "restart_count_24h", "last_image_deployed_at"}, ...]

    Drivers that can't reach the apiserver (no creds, network) return
    an empty list rather than raising — the UI surfaces "no workload
    data available" without taking the whole Status tab down. Driver-
    resolution failure (plugin missing, protocol gap) raises
    ``ClusterManagementError``; the resolver swallows that into the
    same empty-list outcome.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "list_workload_health"):
        raise ClusterManagementError(
            f"cluster {cluster.slug}: driver does not implement list_workload_health",
        )
    ctx = _context_for_cluster(cluster)
    try:
        rows = driver.list_workload_health(ctx, namespaces=namespaces)
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_workload_health raised {exc}",
        ) from exc
    return [
        {
            "namespace": w.namespace,
            "name": w.name,
            "desired_replicas": int(w.desired_replicas),
            "ready_replicas": int(w.ready_replicas),
            "restart_count_24h": int(w.restart_count_24h),
            "last_image_deployed_at": w.last_image_deployed_at,
        }
        for w in rows
    ]


def cluster_alb_http_metrics_dispatch(
    *,
    cluster: TenantCluster,
    app_namespace: str,
    start_unix: int,
    end_unix: int,
    step_seconds: int,
) -> dict[str, list[tuple[Any, Any]]]:
    """Return CloudWatch ALB HTTP metrics for *app_namespace* on *cluster*.

    Delegates to the driver's ``get_alb_http_metrics`` method when available
    (currently only ``EKSClusterDriver``). Returns an empty dict of lists on
    any failure or when the driver doesn't implement the method — callers
    treat missing data as "metrics not yet flowing" rather than an error.
    """
    driver = _driver_for_cluster(cluster)
    if not hasattr(driver, "get_alb_http_metrics"):
        return {"rps": [], "error_rate": [], "latency_p50": [], "latency_p95": [], "latency_p99": []}
    ctx = _context_for_cluster(cluster)
    try:
        return driver.get_alb_http_metrics(
            ctx,
            app_namespace=app_namespace,
            start_unix=start_unix,
            end_unix=end_unix,
            step_seconds=step_seconds,
        )
    except Exception as exc:
        raise ClusterManagementError(
            f"cluster {cluster.slug}: get_alb_http_metrics raised {exc}",
        ) from exc


def cluster_certificates_dispatch(*, cluster: TenantCluster) -> dict[str, Any]:
    """Return the cluster provider's available TLS certificates (#858).

    Backs the SNI / custom-domain cert picker. Delegates to the driver's
    ``list_certificates`` method (currently only ``EKSClusterDriver`` via
    ACM). Returns::

        {"supported": bool, "certificates": [{"arn", "name",
          "domain_name", "status"}, ...]}

    ``supported`` is ``False`` (with an empty list) when the cluster's
    provider driver doesn't implement cert listing yet (GCP / Azure /
    k8s_native) OR when the driver can't be built (plugin missing,
    config invalid) — the UI falls back to a free-text ARN field in
    both cases. A driver that *does* implement the method but fails the
    cloud API call (no creds, throttled) is still ``supported=True``
    with an empty list: the capability exists, the data just isn't
    reachable, so the UI keeps the picker (with an empty state) rather
    than silently reverting to manual entry.
    """
    try:
        driver = _driver_for_cluster(cluster)
    except ClusterManagementError:
        return {"supported": False, "certificates": []}
    if not hasattr(driver, "list_certificates"):
        return {"supported": False, "certificates": []}
    ctx = _context_for_cluster(cluster)
    try:
        certs = driver.list_certificates(ctx)
    except Exception as exc:  # noqa: BLE001
        raise ClusterManagementError(
            f"cluster {cluster.slug}: list_certificates raised {exc}",
        ) from exc
    return {
        "supported": True,
        "certificates": [
            {
                "arn": c.arn,
                "name": c.name,
                "domain_name": c.domain_name,
                "status": c.status,
            }
            for c in certs
        ],
    }


__all__ = [
    "ClusterManagementError",
    "ClusterObservabilityError",  # re-exported so callers have one import
    "bootstrap_components_dispatch",
    "bring_cluster_into_management",
    "cluster_alb_http_metrics_dispatch",
    "cluster_certificates_dispatch",
    "cluster_health_dispatch",
    "cluster_workload_health_dispatch",
    "cognito_user_pool_clients_dispatch",
    "cognito_user_pools_dispatch",
    "probe_cluster_capabilities_dispatch",
    "provider_regions_dispatch",
    "reset_management_backend_for_tests",
    "set_management_backend_for_tests",
    "teardown_cluster_dispatch",
]
