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


def workload_identity_role_name(app: RegisteredApp) -> str:
    """Canonical IAM/identity role + ServiceAccount name the platform
    issues per app for workload identity (IRSA / GKE WI / AKS federated).

    Single source of truth so provision (``ensure_workload_identity``),
    deprovision (``deprovision_app_identity_role``) and the deploy render
    (which emits the annotated ServiceAccount) all agree on the name —
    otherwise teardown would leak the role and the SA annotation would
    point at a role that was never created. ``astrolift-<org>-<app>`` (or
    ``astrolift-<app>`` when the app has no org) fits IAM's 64-char limit
    for any reasonable slug.
    """
    from providers.aws._naming import iam_role_name

    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    # Sanitized + length-bounded (#994): this name is the IAM role name AND
    # the workload-identity ServiceAccount name, so it must satisfy both IAM
    # (≤64) and the K8s DNS-label limit (≤63). iam_role_name is deterministic,
    # so create / SA annotation / deprovision keep agreeing.
    if org_slug:
        return iam_role_name("astrolift", org_slug, app.slug)
    return iam_role_name("astrolift", app.slug)


def build_identity_role_name(app: RegisteredApp) -> str:
    """Canonical IAM role + ServiceAccount name for an app's platform build
    (#978).

    Distinct from ``workload_identity_role_name``: the build role grants ECR
    *push* to the app's repo and is bound to the kaniko build SA in the
    platform namespace, whereas the workload-identity role grants the app's
    *runtime* managed-service access. Single source of truth so the build
    activity (which mints + binds it) and the deregister/teardown path (which
    deletes it) agree on the name — otherwise teardown leaks the build role.
    ``astrolift-build-<org>-<app>`` (or ``astrolift-build-<app>``) stays under
    IAM's 64-char limit and the K8s DNS-label limit it doubles as.
    """
    from providers.aws._naming import iam_role_name

    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    if org_slug:
        return iam_role_name("astrolift-build", org_slug, app.slug)
    return iam_role_name("astrolift-build", app.slug)


# Kinds whose pod template gets the workload-identity ServiceAccount.
_POD_TEMPLATE_KINDS = {"Deployment", "StatefulSet", "ReplicaSet", "DaemonSet", "Job"}


def _inject_workload_identity(
    resources: list[dict[str, Any]],
    *,
    sa_name: str,
    role_arn: str,
    namespace: str,
) -> list[dict[str, Any]]:
    """Set ``serviceAccountName`` on every pod template in ``resources`` and
    append a ServiceAccount annotated with the IRSA role ARN (#1011).

    The EKS pod-identity webhook reads ``eks.amazonaws.com/role-arn`` off the
    SA and injects STS credentials into pods that use it — so IAM-authed
    managed services (S3) work without static keys. The caller gates this on
    the app actually having managed services + a resolvable AWS account.
    """
    for r in resources:
        kind = r.get("kind", "")
        if kind == "CronJob":
            pod_spec = (
                r.setdefault("spec", {})
                .setdefault("jobTemplate", {})
                .setdefault("spec", {})
                .setdefault("template", {})
                .setdefault("spec", {})
            )
            pod_spec["serviceAccountName"] = sa_name
        elif kind in _POD_TEMPLATE_KINDS:
            pod_spec = r.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
            pod_spec["serviceAccountName"] = sa_name

    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": sa_name,
            "namespace": namespace,
            "labels": {"astrolift.io/managed-by": "platform"},
            "annotations": {"eks.amazonaws.com/role-arn": role_arn},
        },
    }
    return sorted(
        [*resources, service_account],
        key=lambda r: (r.get("kind", ""), r["metadata"]["name"]),
    )


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
                role_path=str(pc.get("irsa_role_path", "/")),
            )
        if capability == "secrets":
            from aws.secrets import SecretsConfig

            return SecretsConfig(
                region=region,
                kms_key_id=(pc.get("kms_key_id") or None),
                secrets_manager_prefix=str(
                    pc.get("secrets_manager_prefix", "astrolift"),
                ),
                ssm_prefix=str(pc.get("ssm_prefix", "/astrolift")),
            )

    # Fallback: cluster-level config (EKSConfig / GKEConfig / AKSConfig) for
    # dns, tls, and any capability without a dedicated entry above.
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


def _stamp_storage_class_for_claims(deployment: Deployment, resources: list[dict[str, Any]]) -> None:
    """Stamp a ``storageClassName`` on StatefulSet volumeClaimTemplates that
    omit one, by discovering the cluster's StorageClass (#1023).

    A claim with no ``storageClassName`` only binds when the cluster has a
    default-annotated SC; astrolift-eks ships ``gp2`` un-defaulted, so the
    PVC would hang Pending forever. Prefer the default SC, else the sole SC;
    if several exist with none default we can't safely guess, so we leave it
    unset (and log) rather than risk the wrong tier. Best-effort: a list
    failure leaves claims as-is (same as before).
    """
    pending = [
        vct
        for r in resources
        if r.get("kind") == "StatefulSet"
        for vct in (r.get("spec", {}) or {}).get("volumeClaimTemplates", []) or []
        if not ((vct.get("spec", {}) or {}).get("storageClassName"))
    ]
    if not pending:
        return
    try:
        driver, ctx, _ns = driver_for_deployment(deployment)
        scs = driver.list_storage_classes(ctx.slug)
    except Exception:
        log.warning("storage-class autostamp: list failed; leaving claims unset", exc_info=True)
        return
    if not scs:
        return
    chosen = next((s.name for s in scs if s.is_default), None)
    if chosen is None and len(scs) == 1:
        chosen = scs[0].name
    if chosen is None:
        log.warning(
            "storage-class autostamp: %d StorageClasses, none default — "
            "leaving claim unset (set storage_class in the manifest)",
            len(scs),
        )
        return
    for vct in pending:
        vct.setdefault("spec", {})["storageClassName"] = chosen


def render_resources_for_deployment(deployment: Deployment) -> list[dict[str, Any]]:
    """Re-render the deployment's manifests against the stored TOML.

    Activities call this rather than threading the render output through
    workflow state — manifests are deterministic given the stored
    ``RegisteredApp.manifest_raw`` + Deployment.image_tag, and re-rendering
    keeps activities idempotent across retries (a stored payload could
    drift across temporal-history compactions).

    Includes the managed-subdomain Ingress when the AppEnvironment has a
    ManagedDomain FK and a TenantCluster. Caller must ensure both
    relations are prefetched (``app_environment__managed_domain`` and
    ``app_environment__tenant_cluster``).
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
    namespace = namespace_for_app(app)

    # envFrom: operator secret bundles + the platform-synthesized
    # managed-service bindings Secret (astrolift-bindings-<slug>, created by
    # update_secrets). apply_manifests renders via THIS function, so the
    # env only reaches workloads if it's wired here — the render_manifests
    # Temporal activity computes the same env_from but its output isn't what
    # gets applied (#1003). update_secrets creates the bindings Secret
    # between apply_manifests and poll_rollout, so it exists before rollout.
    from astrolift_services.models import AppSecretBundleRef, ManagedService

    env_from = sorted(
        AppSecretBundleRef.objects.filter(
            registered_app=app,
            app_environment=env,
            deleted_at__isnull=True,
        ).values_list("secret_bundle__slug", flat=True),
    )
    has_managed = ManagedService.objects.filter(
        registered_app=app,
        app_environment=env,
        deleted_at__isnull=True,
    ).exists()
    if has_managed:
        env_from.append(f"astrolift-bindings-{app.slug}")

    resources = _render(
        manifest,
        app_slug=app.slug,
        namespace=namespace,
        image_tag=deployment.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        environment_name=env.name,
        env_from_secret_refs=env_from,
    )

    # Fold in the managed-subdomain Ingress if the environment has a
    # platform domain assigned. This mirrors the logic in the
    # render_manifests Temporal activity so apply_manifests always has
    # the full resource set regardless of which code path produced it.
    managed_domain = getattr(env, "managed_domain", None)
    cluster = getattr(env, "tenant_cluster", None)
    _ingress_count = 0
    if managed_domain is not None and cluster is not None:
        ingress_resources = (
            _render_managed_subdomain_ingress(deployment, manifest, namespace, managed_domain, cluster) or []
        )
        _ingress_count = len(ingress_resources)
        if ingress_resources:
            resources = sorted(
                [*resources, *ingress_resources],
                key=lambda r: (r.get("kind", ""), r["metadata"]["name"]),
            )

    # StatefulSet PVC binding (#1023): if a volumeClaimTemplate omits
    # storageClassName and the cluster has no default-annotated SC, the PVC
    # never binds → the pod hangs Pending. Discover the cluster's SC and stamp
    # it so stateful apps bind without the operator knowing the SC name.
    _stamp_storage_class_for_claims(deployment, resources)

    # Workload identity (#1011): apps with managed services get a
    # ServiceAccount annotated with their IRSA role ARN so IAM-authed
    # services (S3) get STS credentials without static keys. The role +
    # OIDC trust are created by the ensure_workload_identity activity,
    # which uses the same name (workload_identity_role_name) and the same
    # account_id/role_path config, so this ARN matches IRSADriver._role_arn.
    # Only AWS clusters with a known account_id can form the ARN; others
    # skip (postgres/redis bind via password env and don't need IRSA).
    if has_managed and cluster is not None:
        pc = getattr(cluster, "provider_config", None) or {}
        account_id = str(pc.get("account_id", ""))
        plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
        if plugin_slug == "aws" and account_id:
            role_path = str(pc.get("irsa_role_path", "/")).strip("/")
            seg = f"{role_path}/" if role_path else ""
            sa_name = workload_identity_role_name(app)
            role_arn = f"arn:aws:iam::{account_id}:role/{seg}{sa_name}"
            resources = _inject_workload_identity(
                resources,
                sa_name=sa_name,
                role_arn=role_arn,
                namespace=namespace,
            )

    log.info(
        "render_resources_for_deployment: app=%s env=%s md_id=%s cluster_id=%s "
        "base+ingress=%d ingress=%d kinds=%s",
        app.slug,
        getattr(env, "name", None),
        getattr(env, "managed_domain_id", None),
        getattr(env, "tenant_cluster_id", None),
        len(resources),
        _ingress_count,
        [r.get("kind") for r in resources],
        extra={"deployment_id": getattr(deployment, "pk", None)},
    )
    return resources


def _render_managed_subdomain_ingress(
    deployment: Deployment,
    manifest: Any,
    namespace: str,
    managed_domain: Any,
    cluster: Any,
) -> list[dict[str, Any]]:
    """Return K8s Ingress resources for the platform-assigned subdomain."""
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames

    app = deployment.registered_app
    org_slug = app.organization.slug if app.organization_id else "none"

    log.info(
        "render_managed_subdomain_ingress: app=%s org_slug=%s managed_domain=%s ingress_class=%s",
        app.slug,
        org_slug,
        getattr(managed_domain, "zone", None),
        getattr(cluster, "ingress_class", None),
    )

    computed = compute_hostnames(
        manifest,
        HostnameInputs(
            app_slug=app.slug,
            org_slug=org_slug,
            base_zone=managed_domain.zone,
        ),
    )
    log.info("render_managed_subdomain_ingress: computed hostnames=%s", [h.hostname for h in computed])
    if not computed:
        return []

    cert_arn: str | None = (
        managed_domain.dns_config.get("certificate_arn") if managed_domain.dns_config else None
    )
    ingress_paused = bool(getattr(deployment.app_environment, "ingress_paused", False))
    ingress_state_label = "paused" if ingress_paused else "live"
    out: list[dict[str, Any]] = []

    # Backend Service port per workload. The Service exposes the primary
    # container's port (render._render_service), so the Ingress backend must
    # reference that exact number — not a hardcoded 80, or the LB controller
    # rejects the Ingress ("unable to find port 80 on service") and never
    # provisions an ALB (#996).
    def _backend_port(workload_slug: str) -> int:
        for w in manifest.workloads:
            if w.name != workload_slug:
                continue
            primary = next(
                (c for c in w.containers if getattr(c, "is_primary", False)),
                w.containers[0] if w.containers else None,
            )
            if primary is not None and getattr(primary, "port", 0):
                return int(primary.port)
        return 80

    # ALB target-group health-check path per workload. The default was a
    # hardcoded ``/healthz`` (ALBConfig), but apps declare their own probe
    # path (``[healthcheck] value`` in the manifest, e.g. ``/health``). A
    # mismatch makes the ALB health check 404 → targets unhealthy → with an
    # ``EvaluateTargetHealth`` alias the Route53 record returns NO answer, so
    # the app never resolves (#997). Use the manifest's http probe path,
    # falling back to ``/`` (not ``/healthz``).
    def _healthcheck_path(workload_slug: str) -> str:
        for w in manifest.workloads:
            if w.name != workload_slug:
                continue
            primary = next(
                (c for c in w.containers if getattr(c, "is_primary", False)),
                w.containers[0] if w.containers else None,
            )
            if primary is not None and getattr(primary, "healthcheck_kind", "none") == "http":
                val = getattr(primary, "healthcheck_value", "") or ""
                if val:
                    return val
        return "/"

    if getattr(cluster, "ingress_class", None) == "alb":
        from providers.aws.ingress_alb import ALBConfig, ALBIngressDriver, CognitoAuthConfig

        cognito_auth = None
        alb_auth_cfg = getattr(cluster, "alb_auth_config", None)
        if alb_auth_cfg and all(
            k in alb_auth_cfg for k in ("user_pool_arn", "user_pool_client_id", "user_pool_domain")
        ):
            cognito_auth = CognitoAuthConfig(
                user_pool_arn=alb_auth_cfg["user_pool_arn"],
                user_pool_client_id=alb_auth_cfg["user_pool_client_id"],
                user_pool_domain=alb_auth_cfg["user_pool_domain"],
            )

        driver = ALBIngressDriver(
            config=ALBConfig(
                region=cluster.region or "us-east-1",
                certificate_arn=cert_arn,
                cognito_auth=cognito_auth,
                healthcheck_path=_healthcheck_path(computed[0].workload_slug) if computed else "/",
            )
        )
        tls_strategy = "acm_dns_validated" if cert_arn else "letsencrypt"
        by_workload: dict[str, list[str]] = {}
        for wh in computed:
            by_workload.setdefault(wh.workload_slug, []).append(wh.hostname)
        for workload_slug, hostnames in by_workload.items():
            for rendered in driver.render_ingress(
                app=app.slug,
                workload=workload_slug,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                port=_backend_port(workload_slug),
            ):
                rendered.setdefault("metadata", {})["namespace"] = namespace
                rendered["metadata"].setdefault("labels", {})["astrolift.dev/managed-subdomain"] = "true"
                rendered["metadata"]["labels"]["astrolift.dev/ingress-state"] = ingress_state_label
                out.append(rendered)
    else:
        from providers.k8s_native.ingress import (
            K8sIngressConfig,
            K8sIngressDriver,
            OIDCAuthConfig,
        )

        oidc_auth = None
        oidc_auth_cfg = getattr(cluster, "oidc_auth_config", None)
        if oidc_auth_cfg and all(
            k in oidc_auth_cfg for k in ("discovery_url", "client_id", "auth_proxy_host")
        ):
            oidc_auth = OIDCAuthConfig(
                auth_proxy_host=oidc_auth_cfg["auth_proxy_host"],
            )

        ingress_class = getattr(cluster, "ingress_class", "nginx")
        # Map the cluster's ingress_class to the driver variant. "nginx" and
        # "ingress-nginx" both map to nginx_ingress; "traefik", "kong" pass
        # through; anything else (e.g. "haproxy") falls back to nginx_ingress.
        _variant_map = {
            "nginx": "nginx_ingress",
            "ingress-nginx": "nginx_ingress",
            "traefik": "traefik",
            "kong": "kong",
        }
        variant = _variant_map.get(ingress_class, "nginx_ingress")

        k8s_driver = K8sIngressDriver(
            config=K8sIngressConfig(
                variant=variant,
                ingress_class_name=ingress_class,
                cert_manager_issuer="letsencrypt-prod",
                oidc_auth=oidc_auth,
            )
        )
        tls_strategy = "letsencrypt" if not cert_arn else "provided"
        by_workload: dict[str, list[str]] = {}
        for wh in computed:
            by_workload.setdefault(wh.workload_slug, []).append(wh.hostname)
        for workload_slug, hostnames in by_workload.items():
            for rendered in k8s_driver.render_ingress(
                app=app.slug,
                workload=workload_slug,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                port=_backend_port(workload_slug),
            ):
                rendered.setdefault("metadata", {})["namespace"] = namespace
                rendered["metadata"].setdefault("labels", {})["astrolift.dev/managed-subdomain"] = "true"
                rendered["metadata"]["labels"]["astrolift.dev/ingress-state"] = ingress_state_label
                if ingress_paused:
                    rendered["metadata"].setdefault("annotations", {})[
                        "nginx.ingress.kubernetes.io/server-snippet"
                    ] = 'return 503 "Astrolift: app is paused";'
                out.append(rendered)

    # Return the rendered Ingress set. Without this the function fell through
    # to an implicit ``return None``, and the caller's ``if ingress_resources:``
    # (None → falsy) silently dropped the managed Ingress — so is_public apps
    # deployed with no ALB/DNS while the deploy still reported success (#992).
    return out


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
    "workload_identity_role_name",
    "render_resources_for_deployment",
    "workloads_from_resources",
]
