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


__all__ = [
    "ClusterManagementError",
    "ClusterObservabilityError",  # re-exported so callers have one import
    "bring_cluster_into_management",
    "probe_cluster_capabilities_dispatch",
    "reset_management_backend_for_tests",
    "set_management_backend_for_tests",
]
