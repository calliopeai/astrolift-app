"""Resolver-facing entry for runtime pod observability (#299).

Builds a ``ClusterAuth`` payload from a ``TenantCluster`` row,
looks up the right provider plugin's ``ClusterDriver``, and
dispatches ``list_pods`` / ``stream_logs`` through it. The
kubernetes-client SDK lives in the provider package now — the
lifecycle module no longer imports it directly.

Why this lives in ``core`` instead of ``astrolift_drivers``: the
function shape (``list_app_pods(*, cluster, namespace, app_slug)``)
is the resolver contract — it predates the protocol move and the
existing tests assert against it. Moving the file under
``astrolift_drivers`` would force a bigger test-rename churn for
no upside.

Pluggable test backends:
The k8s_native driver (and its EKS/GKE/AKS subclasses) take
``pod_backend`` and ``log_backend`` constructor kwargs that swap
the live kubernetes-client path for a deterministic fake. Tests
go through :func:`set_pod_backend_for_tests` /
:func:`set_log_backend_for_tests` here so the resolver-facing
contract stays narrow.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from astrolift_drivers.registry import DriverNotFound, plugins

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)


class ClusterObservabilityError(Exception):
    """A cluster row can't be turned into a usable driver — either
    the plugin isn't loaded, the driver doesn't implement the
    observability methods, or the auth payload is malformed. The
    resolver layer maps this to an empty UI state."""


# ---- Test-injectable backends -------------------------------------
#
# Same pattern the old core.k8s.{pods,logs} modules used: tests
# install a fake, run their assertion, and reset on teardown. The
# real driver path is the default; tests opt in.
#
# Type-imported lazily so importing this module doesn't pull in
# astrolift-providers (which the schema-export command runs without).

_POD_BACKEND_OVERRIDE: Any = None
_LOG_BACKEND_OVERRIDE: Any = None


def set_pod_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic pod backend.

    Backend interface: ``backend.list_pods(*, auth, namespace,
    app_slug) -> list[PodInfo]``. Reset with
    :func:`reset_pod_backend_for_tests`.
    """
    global _POD_BACKEND_OVERRIDE
    _POD_BACKEND_OVERRIDE = backend


def reset_pod_backend_for_tests() -> None:
    global _POD_BACKEND_OVERRIDE
    _POD_BACKEND_OVERRIDE = None


def set_log_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic log backend.

    Backend interface: ``backend.stream(*, auth, namespace, ...) ->
    AsyncIterator[PodLogLine]``. Reset with
    :func:`reset_log_backend_for_tests`.
    """
    global _LOG_BACKEND_OVERRIDE
    _LOG_BACKEND_OVERRIDE = backend


def reset_log_backend_for_tests() -> None:
    global _LOG_BACKEND_OVERRIDE
    _LOG_BACKEND_OVERRIDE = None


# ---- Public resolver API ------------------------------------------


def namespace_for_app(app: Any) -> str:
    """Per-app namespace, mirroring what the manifest renderer uses.

    Resolution order:
      1. ``app.k8s_namespace`` (explicit override on the row)
      2. ``f"{org_slug}-{app_slug}"`` (renderer default)
    """
    explicit = (getattr(app, "k8s_namespace", "") or "").strip()
    if explicit:
        return explicit
    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    return f"{org_slug}-{app.slug}"


def _auth_for_cluster(cluster: TenantCluster) -> Any:
    """Build a ``ClusterAuth`` payload from a ``TenantCluster`` row.

    The provider package owns the dataclass shape; we import it
    lazily so the schema-export command (which doesn't have the
    provider plugins installed) still works."""
    from _sdk.cluster import ClusterAuth

    return ClusterAuth(
        slug=cluster.slug,
        auth_method=cluster.auth_method,
        auth_config=cluster.auth_config or {},
        endpoint=cluster.endpoint or "",
        ca_cert=cluster.ca_cert or "",
        namespace_prefix=cluster.default_namespace_prefix or "",
    )


class _OverrideDriver:
    """Test-only driver — bypasses provider-plugin lookup and
    dispatches list_pods / stream_logs straight to the installed
    test backends. Lets the test fixtures use any
    ``TenantCluster.provider_plugin`` row (even one not in the
    plugin registry) without forcing the real driver path."""

    def __init__(self, pod_backend: Any, log_backend: Any) -> None:
        self._pod_backend = pod_backend
        self._log_backend = log_backend

    def list_pods(self, *, auth: Any, namespace: str, app_slug: str) -> list[Any]:
        if self._pod_backend is None:
            raise ClusterObservabilityError(
                "list_pods called without a pod backend override; set_pod_backend_for_tests was not called",
            )
        return self._pod_backend.list_pods(
            auth=auth,
            namespace=namespace,
            app_slug=app_slug,
        )

    def stream_logs(
        self,
        *,
        auth: Any,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> Any:
        if self._log_backend is None:
            raise ClusterObservabilityError(
                "stream_logs called without a log backend override; set_log_backend_for_tests was not called",
            )
        return self._log_backend.stream(
            auth=auth,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )


def _driver_for_cluster(cluster: TenantCluster) -> Any:
    """Look up the ``ClusterDriver`` class for ``cluster.provider_plugin``
    and instantiate it with the right test backend overrides (if any).

    The driver classes ship config-required constructors (EKSConfig,
    GKEConfig, AKSConfig, K8sNativeConfig) but the observability path
    doesn't use that config — it lands in ``auth`` instead. We pass
    a sentinel placeholder so the constructor doesn't refuse to
    build, and the pluggable backends do the real work.

    When a test backend is installed we skip the registry lookup
    entirely — the test fixtures use synthetic ``provider_plugin``
    rows that aren't loaded as Python plugins, so the registry path
    would error on every test. The override driver dispatches
    straight to the test backend.
    """
    if _POD_BACKEND_OVERRIDE is not None or _LOG_BACKEND_OVERRIDE is not None:
        return _OverrideDriver(
            pod_backend=_POD_BACKEND_OVERRIDE,
            log_backend=_LOG_BACKEND_OVERRIDE,
        )

    plugin_slug = cluster.provider_plugin.slug
    try:
        driver_cls = plugins.get(plugin_slug, "cluster")
    except DriverNotFound as exc:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: provider plugin {plugin_slug!r} does not register a 'cluster' driver",
        ) from exc

    cfg = _config_for(plugin_slug, cluster)
    try:
        return driver_cls(config=cfg)
    except TypeError as exc:
        # Constructor signature didn't accept config= or kwargs. This
        # signals a plugin mismatch — surface as observability error
        # rather than blowing up the resolver.
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: driver {plugin_slug!r} constructor rejected config payload: {exc}",
        ) from exc


def _config_for(plugin_slug: str, cluster: TenantCluster) -> Any:
    """Per-plugin config dataclass instantiation.

    Each driver has a tiny ``*Config`` dataclass keyed by cloud
    identifiers (region/project/subscription). For the observability
    path we just need *something* that doesn't crash the constructor;
    the row's ``provider_config`` holds whatever the operator pinned
    so use that, falling back to empty strings.
    """
    pc = cluster.provider_config or {}
    if plugin_slug == "k8s_native":
        from k8s_native.cluster import K8sNativeConfig

        return K8sNativeConfig(
            kubeconfig_path=str(pc.get("kubeconfig_path", "")),
            context=str(pc.get("context", "")),
            in_cluster=bool(pc.get("in_cluster", False)),
        )
    if plugin_slug == "aws":
        from aws.cluster_eks import EKSConfig

        return EKSConfig(
            region=str(pc.get("region", cluster.region or "")),
            cluster_name=str(pc.get("cluster_name", cluster.slug)),
        )
    if plugin_slug == "gcp":
        from gcp.cluster_gke import GKEConfig

        return GKEConfig(
            project_id=str(pc.get("project_id", "")),
            location=str(pc.get("location", cluster.region or "")),
            cluster_name=str(pc.get("cluster_name", cluster.slug)),
        )
    if plugin_slug == "azure":
        from azure.cluster_aks import AKSConfig

        return AKSConfig(
            subscription_id=str(pc.get("subscription_id", "")),
            resource_group=str(pc.get("resource_group", "")),
            cluster_name=str(pc.get("cluster_name", cluster.slug)),
        )
    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no config builder wired for plugin {plugin_slug!r}",
    )


def list_app_pods(
    *,
    cluster: TenantCluster,
    namespace: str,
    app_slug: str,
) -> list[Any]:
    """Resolver-facing entry. Returns a list of ``PodInfo`` (from
    the provider SDK). Resolver layer is responsible for catching
    :class:`ClusterObservabilityError` and rendering the empty UI."""
    driver = _driver_for_cluster(cluster)
    auth = _auth_for_cluster(cluster)
    return driver.list_pods(auth=auth, namespace=namespace, app_slug=app_slug)


def stream_app_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    pod_name: str,
    container: str | None,
    tail_lines: int = 100,
    follow: bool = True,
) -> AsyncIterator[Any]:
    """Resolver-facing entry for the log subscription. Returns an
    async iterator that yields ``PodLogLine`` (from the provider
    SDK). The subscription layer wraps this with an explicit
    ``aclose()`` finally — see :mod:`astrolift_lifecycle.schema.subscriptions`.
    """
    driver = _driver_for_cluster(cluster)
    auth = _auth_for_cluster(cluster)
    return driver.stream_logs(
        auth=auth,
        namespace=namespace,
        pod_name=pod_name,
        container=container,
        tail_lines=tail_lines,
        follow=follow,
    )
