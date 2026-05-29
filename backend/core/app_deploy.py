"""Deploy-side dispatch helpers — Django model → provider driver bridge.

Workflow activities live in ``astrolift_workflows/activities/app_lifecycle.py``
and run in a Temporal worker process. Each activity loads a Deployment /
RegisteredApp / AppEnvironment row, resolves the cluster + namespace +
provider driver, then dispatches the actual work through the
``astrolift_drivers`` registry.

The helpers in this module concentrate the model-to-driver glue in one
place so activities stay thin and the test fixtures for any activity can
override the same surface used by every other activity.

Pattern mirrors ``core.cluster_management`` — the bring-into-management
activities and the deploy activities both want the same
``_driver_for_cluster(...)`` resolver but differ in payload shape, so we
re-use the helper rather than duplicate plugin-registry lookup logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.cluster_management import (
    ClusterManagementError,
    _context_for_cluster,
    _driver_for_cluster,
)
from core.cluster_observability import _config_for  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.models import RegisteredApp

log = logging.getLogger(__name__)


class AppDeployError(Exception):
    """Raised when the deploy pipeline can't resolve the cluster, the
    driver, or apply the rendered manifests. Workflow activities catch
    this and surface it through mark_error so the deployment status row
    carries the operator-facing message."""


def namespace_for_app(app: RegisteredApp) -> str:
    """Per-app Kubernetes namespace.

    Apps may pin ``k8s_namespace`` for legacy migrations; otherwise the
    canonical name is ``<org-slug>-<app-slug>``. The same logic lives in
    ``render_manifests`` — kept in lock-step so apply targets exactly the
    namespace the renderer wrote into.
    """
    if app.k8s_namespace:
        return str(app.k8s_namespace)
    return f"{app.organization.slug}-{app.slug}"


def cluster_for_deployment(deployment: Deployment) -> TenantCluster:
    """Resolve the TenantCluster the deployment lands on.

    The cluster lives on ``AppEnvironment.tenant_cluster``; raising here
    short-circuits the activity with a clear message rather than letting
    a downstream ``AttributeError`` mask the missing FK.
    """
    env = deployment.app_environment
    if env is None or env.tenant_cluster_id is None:
        raise AppDeployError(
            f"deployment {deployment.pk} environment {env.name if env else '?'} has no tenant_cluster bound — "
            "register the app's environment against an adopted cluster before deploying",
        )
    return env.tenant_cluster


def driver_for_deployment(deployment: Deployment) -> tuple[Any, Any, str]:
    """Return ``(driver, cluster_context, namespace)`` for a deployment.

    The driver implements ``ClusterDriver`` from
    ``astrolift-providers._sdk.cluster`` (apply_manifests, ensure_namespace,
    poll_rollout, get_workload_status). The context carries auth material.
    The namespace is the app's per-app namespace string.
    """
    cluster = cluster_for_deployment(deployment)
    try:
        driver = _driver_for_cluster(cluster)
    except ClusterManagementError as exc:
        raise AppDeployError(str(exc)) from exc
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_app(deployment.registered_app)
    return driver, ctx, namespace


def _config_for_capability(plugin_slug: str, cluster: TenantCluster, capability: str) -> Any:
    """Build the driver-specific config dataclass for (plugin, capability).

    Non-cluster capabilities (registry, identity, secrets, dns, tls) need
    their own config type — the cluster-level ``EKSConfig`` / ``GKEConfig``
    returned by ``_config_for`` only carries what the *cluster driver*
    needs (kubeconfig / cluster name), not the AWS account ID or OIDC
    issuer the ECR / IRSA drivers require.  Falls back to ``_config_for``
    for any combination not explicitly listed here.
    """
    pc = cluster.provider_config or {}
    ac = cluster.auth_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))

    if plugin_slug == "aws":
        if capability == "registry":
            from aws.registry_ecr import ECRConfig

            return ECRConfig(
                region=region,
                account_id=str(pc.get("account_id", "")),
                image_scanning_enabled=bool(pc.get("image_scanning_enabled", True)),
                image_tag_mutability=str(pc.get("image_tag_mutability", "IMMUTABLE")),
            )
        if capability == "identity":
            from aws.identity_irsa import IRSAConfig

            return IRSAConfig(
                region=region,
                account_id=str(pc.get("account_id", "")),
                cluster_oidc_issuer=str(ac.get("cluster_oidc_issuer", "")),
                role_path=str(pc.get("irsa_role_path", "/astrolift/")),
            )

    # Fallback: cluster-level config (EKSConfig / GKEConfig / AKSConfig) for
    # secrets, dns, tls, and any capability without a dedicated entry above.
    return _config_for(plugin_slug, cluster)


def driver_for_capability(cluster: TenantCluster, capability: str) -> Any:
    """Resolve a non-cluster driver (``secrets``, ``dns``, ``registry``,
    ``tls``, ``identity``) for the cluster's provider plugin.

    Mirrors ``_driver_for_cluster`` but takes the capability key the
    plugin's ``drivers`` dict is indexed by. Raises ``AppDeployError``
    when the plugin doesn't register a driver for ``capability`` so the
    activity surface gets a clear error rather than ``DriverNotFound``.
    """
    from astrolift_drivers.registry import DriverNotFound, plugins

    plugin_slug = cluster.provider_plugin.slug
    try:
        driver_cls = plugins.get(plugin_slug, capability)
    except DriverNotFound as exc:
        raise AppDeployError(
            f"cluster {cluster.slug}: provider plugin {plugin_slug!r} does not register a {capability!r} driver",
        ) from exc
    cfg = _config_for_capability(plugin_slug, cluster, capability)
    try:
        return driver_cls(config=cfg)
    except TypeError as exc:
        raise AppDeployError(
            f"cluster {cluster.slug}: {capability} driver constructor rejected config: {exc}",
        ) from exc


def driver_for_target_cluster(
    deployment: Deployment,
    target_cluster_id: int,
) -> tuple[Any, Any, str]:
    """Return ``(driver, cluster_context, namespace)`` for applying a
    deployment to an arbitrary cluster — used by the migration workflow
    to apply against the *target* cluster rather than the env's currently
    bound source cluster.

    Reads the target ``TenantCluster`` row by id and builds the driver
    + context against it. Namespace is still derived from the app — the
    namespace name is cluster-agnostic.
    """
    from astrolift_clusters.models import TenantCluster

    try:
        cluster = TenantCluster.all_objects.get(pk=target_cluster_id)
    except TenantCluster.DoesNotExist as exc:
        raise AppDeployError(f"target cluster {target_cluster_id} not found") from exc
    if cluster.lifecycle != cluster.Lifecycle.MANAGED.value:
        raise AppDeployError(
            f"target cluster {cluster.slug!r} lifecycle is {cluster.lifecycle!r}, not managed — "
            "bring it into management before migrating apps to it",
        )
    try:
        driver = _driver_for_cluster(cluster)
    except ClusterManagementError as exc:
        raise AppDeployError(str(exc)) from exc
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_app(deployment.registered_app)
    return driver, ctx, namespace


def render_resources_for_deployment(deployment: Deployment) -> list[dict[str, Any]]:
    """Re-render the deployment's manifests against the stored TOML.

    Activities call this rather than threading the render output through
    workflow state — manifests are deterministic given the stored
    ``RegisteredApp.manifest_raw`` + Deployment.image_tag, and re-rendering
    keeps activities idempotent across retries (a stored payload could
    drift across temporal-history compactions).
    """
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.render import render_manifests as _render

    app = deployment.registered_app
    env = deployment.app_environment
    if not (app.manifest_raw or "").strip():
        raise AppDeployError(
            f"app {app.slug!r} has no saved manifest — open the Manifest tab and paste astrolift.toml first",
        )
    manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())
    return _render(
        manifest,
        namespace=namespace_for_app(app),
        image_tag=deployment.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        environment_name=env.name,
    )


def workloads_from_resources(
    resources: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Return ``[(kind, name), ...]`` for every Deployment / StatefulSet /
    DaemonSet in the rendered resource list.

    These are the workload kinds the cluster driver's ``poll_rollout`` +
    ``get_workload_status`` can speak to. Other resource kinds
    (Services, Ingresses, Secrets, ConfigMaps) don't have a "rollout"
    semantic and are skipped — they're applied in-line and considered
    healthy once apply_manifests reports them ok.
    """
    WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
    out: list[tuple[str, str]] = []
    for r in resources:
        kind = r.get("kind", "")
        if kind not in WORKLOAD_KINDS:
            continue
        meta = r.get("metadata") or {}
        name = meta.get("name", "")
        if name:
            out.append((kind, name))
    return out


__all__ = [
    "AppDeployError",
    "cluster_for_deployment",
    "driver_for_capability",
    "driver_for_deployment",
    "driver_for_target_cluster",
    "namespace_for_app",
    "render_resources_for_deployment",
    "workloads_from_resources",
]
