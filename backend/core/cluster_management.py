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
from typing import TYPE_CHECKING, Any

from astrolift_drivers.registry import DriverNotFound, plugins
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


__all__ = [
    "ClusterManagementError",
    "ClusterObservabilityError",  # re-exported so callers have one import
    "bootstrap_components_dispatch",
    "bring_cluster_into_management",
    "cluster_alb_http_metrics_dispatch",
    "cluster_health_dispatch",
    "cluster_workload_health_dispatch",
    "probe_cluster_capabilities_dispatch",
    "reset_management_backend_for_tests",
    "set_management_backend_for_tests",
    "teardown_cluster_dispatch",
]
