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

from core.cluster_credentials import CREDENTIAL_REFUSALS, assert_credential_supported
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
    from _sdk.k8s_naming import app_namespace

    return app_namespace(
        organization_slug=str(app.organization.slug),
        app_slug=str(app.slug),
    )


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


def static_build_role_name(app: RegisteredApp) -> str:
    """Canonical IAM role + ServiceAccount name for an app's in-cluster
    static-asset build/sync Job (#1010).

    Distinct from both the kaniko ``build_identity_role_name`` (ECR push) and
    the runtime ``workload_identity_role_name``: this role grants S3
    put/list/delete on the app's static origin bucket + cloudfront
    CreateInvalidation, and is bound to the static-build SA in the platform
    namespace. SSOT so the ``sync_static_assets`` activity (which mints +
    binds it) and the deregister/teardown path (which deletes it) agree on
    the name -- otherwise teardown leaks the role. ``astrolift-static-<org>-<app>``
    (or ``astrolift-static-<app>``) stays under IAM's 64-char limit and the
    K8s DNS-label limit it doubles as."""
    from providers.aws._naming import iam_role_name

    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    if org_slug:
        return iam_role_name("astrolift-static", org_slug, app.slug)
    return iam_role_name("astrolift-static", app.slug)


def ci_push_role_name(app: RegisteredApp) -> str:
    """IAM role name of the GitHub-OIDC CI push role for an app (#994/#1026).

    Mirrors ``ECRDriver.ensure_ci_push_role`` (``astrolift-<repo>-ecr-push``,
    where ``repo`` is the ECR repo name ``<org>/<app>``). SSOT so the
    deregister/teardown path can delete it by the same name the registry
    provision created — otherwise the CI push role orphans (it isn't tagged
    for the #995 scan either, so nothing else would reap it).
    """
    from providers.aws._naming import iam_role_name

    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    repo = f"{org_slug}/{app.slug}" if org_slug else app.slug
    return iam_role_name("astrolift", repo, "ecr-push")


# Kinds whose pod template gets the workload-identity ServiceAccount.
_POD_TEMPLATE_KINDS = {"Deployment", "StatefulSet", "ReplicaSet", "DaemonSet", "Job"}


def _inject_workload_identity(
    resources: list[dict[str, Any]],
    *,
    sa_name: str,
    namespace: str,
    role_arn: str | None = None,
    annotations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Inject a provider-annotated Kubernetes ServiceAccount into workloads."""
    if annotations is None:
        if not role_arn:
            raise AppDeployError("workload identity requires provider annotations")
        annotations = {"eks.amazonaws.com/role-arn": role_arn}
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
            "annotations": annotations,
        },
    }
    return sorted(
        [*resources, service_account],
        key=lambda r: (r.get("kind", ""), r["metadata"]["name"]),
    )


def _workload_identity_annotations(
    *,
    plugin_slug: str,
    provider_config: dict[str, Any],
    auth_config: dict[str, Any],
    role_name: str,
) -> dict[str, str]:
    """Build the deterministic pod-identity annotation for a cloud plugin."""
    if plugin_slug == "aws":
        account_id = str(provider_config.get("account_id", ""))
        if not account_id:
            return {}
        role_path = str(provider_config.get("irsa_role_path", "/")).strip("/")
        segment = f"{role_path}/" if role_path else ""
        return {
            "eks.amazonaws.com/role-arn": (f"arn:aws:iam::{account_id}:role/{segment}{role_name}"),
        }
    if plugin_slug == "gcp":
        project_id = str(
            provider_config.get("project_id")
            or provider_config.get("gcp_project_id")
            or auth_config.get("project_id")
            or auth_config.get("gcp_project_id")
            or ""
        )
        if not project_id:
            return {}
        from gcp.identity_wi import service_account_email_for

        return {
            "iam.gke.io/gcp-service-account": service_account_email_for(
                role_name,
                project_id,
            ),
        }
    return {}


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

    if plugin_slug == "k8s_native" and capability == "secrets":
        from k8s_native.secrets_vault import VaultConfig

        return VaultConfig(
            address=str(pc.get("vault_address", "")),
            token=str(ac.get("vault_token", "")),
            kv_mount=str(pc.get("vault_kv_mount", "secret")),
            kv_path_prefix=str(pc.get("vault_path_prefix", "astrolift")),
            namespace=str(pc.get("vault_namespace", "")),
            auth_method=str(pc.get("vault_auth_method", "token")),
            sa_role=str(pc.get("vault_sa_role", "")),
        )

    if plugin_slug == "gcp":
        project_id = str(
            pc.get("project_id")
            or pc.get("gcp_project_id")
            or ac.get("project_id")
            or ac.get("gcp_project_id")
            or ""
        )
        if not project_id:
            raise AppDeployError(
                f"cluster {cluster.slug}: GCP capability {capability!r} requires provider_config.project_id",
            )
        if capability == "registry":
            from gcp.registry_artifact import ArtifactRegistryConfig

            return ArtifactRegistryConfig(
                project_id=project_id,
                location=str(pc.get("artifact_registry_location", region)),
                repository_id=str(pc.get("artifact_registry_repo", "astrolift")),
                immutable_tags=bool(pc.get("artifact_registry_immutable_tags", True)),
                encryption_kms_key_name=(
                    str(
                        pc.get("artifact_registry_kms_key") or pc.get("kms_key") or "",
                    )
                    or None
                ),
            )
        if capability == "identity":
            from gcp.identity_wi import GCPWIConfig

            return GCPWIConfig(project_id=project_id)
        if capability == "secrets":
            from gcp.secrets import GCPSecretsConfig

            return GCPSecretsConfig(
                project_id=project_id,
                secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
                kms_key_name=(str(pc.get("secret_manager_kms_key") or pc.get("kms_key") or "") or None),
            )
        if capability == "dns":
            from gcp.dns_clouddns import CloudDNSConfig

            return CloudDNSConfig(project_id=project_id)
        if capability == "tls":
            from gcp.tls_managed import ManagedCertConfig

            return ManagedCertConfig(
                project_id=project_id,
                cert_name_prefix=str(pc.get("managed_cert_name_prefix", "astrolift")),
            )
        if capability == "ingress":
            from gcp.ingress import GCPIngressConfig

            return GCPIngressConfig(
                variant=str(pc.get("ingress_variant", "gce_ingress")),
                static_ip_name=(str(pc.get("static_ip_name") or "") or None),
                managed_cert_name=(str(pc.get("managed_cert_name") or "") or None),
                gateway_class=str(
                    pc.get("gateway_class", "gke-l7-global-external-managed"),
                ),
            )
        if capability == "notification":
            from gcp.notification_fcm import FCMConfig

            return FCMConfig(
                project_id=project_id,
                access_token=str(ac.get("fcm_access_token", "")),
                timeout_seconds=int(pc.get("fcm_timeout_seconds", 10)),
            )
        raise AppDeployError(
            f"cluster {cluster.slug}: no GCP config builder for capability {capability!r}",
        )

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

    if plugin_slug == "azure":
        subscription_id = str(pc.get("subscription_id") or ac.get("subscription_id") or "")
        resource_group = str(pc.get("resource_group") or ac.get("resource_group") or "")
        tenant_id = str(pc.get("tenant_id") or ac.get("tenant_id") or "")
        vault_url = str(pc.get("vault_url") or pc.get("keyvault_url") or "")
        if capability == "registry":
            from azure.registry_acr import ACRConfig

            registry_name = str(pc.get("registry_name") or "")
            _require_azure_config(cluster, capability, subscription_id=subscription_id)
            _require_azure_config(cluster, capability, resource_group=resource_group)
            _require_azure_config(cluster, capability, registry_name=registry_name)
            return ACRConfig(
                subscription_id=subscription_id,
                resource_group=resource_group,
                registry_name=registry_name,
                location=str(pc.get("location") or region or "eastus"),
                sku=str(pc.get("acr_sku", "Standard")),
                admin_enabled=bool(pc.get("acr_admin_enabled", False)),
                immutable_tags=bool(pc.get("acr_immutable_tags", True)),
                tenant_id=tenant_id or None,
            )
        if capability == "identity":
            from azure.identity_federated import FederatedIdentityConfig

            issuer = str(pc.get("cluster_oidc_issuer") or ac.get("cluster_oidc_issuer") or "")
            for key, value in (
                ("tenant_id", tenant_id),
                ("subscription_id", subscription_id),
                ("resource_group", resource_group),
                ("cluster_oidc_issuer", issuer),
            ):
                _require_azure_config(cluster, capability, **{key: value})
            return FederatedIdentityConfig(
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                resource_group=resource_group,
                cluster_oidc_issuer=issuer,
                location=str(pc.get("location") or region or "eastus"),
            )
        if capability == "secrets":
            from azure.secrets_keyvault import KeyVaultConfig

            _require_azure_config(cluster, capability, vault_url=vault_url)
            managed_prefixes = tuple(
                sorted(
                    {
                        str(value)
                        for key, value in pc.items()
                        if key.endswith("_secret_name_prefix") and value
                    },
                ),
            )
            try:
                return KeyVaultConfig(
                    vault_url=vault_url,
                    secret_name_prefix=str(pc.get("keyvault_secret_name_prefix", "astrolift")),
                    managed_secret_name_prefixes=managed_prefixes,
                )
            except ValueError as exc:
                raise AppDeployError(
                    f"cluster {cluster.slug}: invalid Azure Key Vault config: {exc}"
                ) from exc
        if capability == "dns":
            from azure.dns_azuredns import AzureDNSConfig

            _require_azure_config(cluster, capability, subscription_id=subscription_id)
            _require_azure_config(cluster, capability, resource_group=resource_group)
            return AzureDNSConfig(subscription_id=subscription_id, resource_group=resource_group)
        if capability == "tls":
            from azure.tls_appgw import AppGatewayTlsConfig

            _require_azure_config(cluster, capability, subscription_id=subscription_id)
            _require_azure_config(cluster, capability, resource_group=resource_group)
            _require_azure_config(cluster, capability, vault_url=vault_url)
            return AppGatewayTlsConfig(
                subscription_id=subscription_id,
                resource_group=resource_group,
                vault_url=vault_url,
                cert_name_prefix=str(pc.get("managed_cert_name_prefix", "astrolift")),
            )
        if capability == "ingress":
            from azure.ingress_appgw import AppGatewayIngressConfig

            return AppGatewayIngressConfig(
                variant=str(pc.get("ingress_variant", "agic")),
                appgw_id=(str(pc.get("appgw_id") or "") or None),
                akv_secret_id=(str(pc.get("akv_secret_id_for_tls") or "") or None),
            )
        if capability == "notification":
            from azure.notification_anh import AzureNotificationHubsConfig

            namespace = str(pc.get("notification_hubs_namespace") or "")
            hub_name = str(pc.get("notification_hub_name") or "")
            key_name = str(ac.get("notification_hubs_shared_access_key_name") or "")
            key = str(ac.get("notification_hubs_shared_access_key") or "")
            for config_key, value in (
                ("notification_hubs_namespace", namespace),
                ("notification_hub_name", hub_name),
                ("auth_config.notification_hubs_shared_access_key_name", key_name),
                ("auth_config.notification_hubs_shared_access_key", key),
            ):
                _require_azure_config(cluster, capability, **{config_key: value})
            return AzureNotificationHubsConfig(
                namespace=namespace,
                hub_name=hub_name,
                shared_access_key_name=key_name,
                shared_access_key=key,
                api_version=str(pc.get("notification_hubs_api_version", "2020-06")),
                timeout_seconds=int(pc.get("notification_hubs_timeout_seconds", 10)),
            )
        raise AppDeployError(
            f"cluster {cluster.slug}: no Azure config builder for capability {capability!r}",
        )

    # Fallback: cluster-level config (EKSConfig / GKEConfig / AKSConfig) for
    # dns, tls, and any capability without a dedicated entry above.
    return _config_for(plugin_slug, cluster)


def _require_azure_config(cluster: TenantCluster, capability: str, **values: str) -> None:
    key, value = next(iter(values.items()))
    if not value:
        raise AppDeployError(
            f"cluster {cluster.slug}: Azure capability {capability!r} requires {key}",
        )


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
        assert_credential_supported(cluster, capability=capability)
    except CREDENTIAL_REFUSALS as exc:
        raise AppDeployError(str(exc)) from exc
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


def _stamp_storage_class_for_claims(
    deployment: Deployment,
    resources: list[dict[str, Any]],
    *,
    cluster_override: TenantCluster | None = None,
) -> None:
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
        if cluster_override is None:
            driver, ctx, _ns = driver_for_deployment(deployment)
        else:
            driver = _driver_for_cluster(cluster_override)
            ctx = _context_for_cluster(cluster_override)
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


def _inject_managed_filesystem_bindings(
    deployment: Deployment,
    resources: list[dict[str, Any]],
    *,
    namespace: str,
    cluster_override: TenantCluster | None = None,
) -> list[dict[str, Any]]:
    """Preflight and attach active managed filesystem bindings."""

    from astrolift_services.filesystem_bindings import (
        FilesystemBindingError,
        inject_bindings_into_workloads,
        preflight_bindings,
    )
    from astrolift_services.models import ManagedService, ManagedServiceVolumeBinding
    from astrolift_workflows.activities.app_lifecycle import _managed_services_for_environment

    service_ids = (
        _managed_services_for_environment(deployment.app_environment)
        .filter(
            kind__in=[ManagedService.Kind.FILESYSTEM, ManagedService.Kind.NFS],
            status__in=[ManagedService.Status.ACTIVE, ManagedService.Status.UPDATING],
        )
        .values_list("pk", flat=True)
    )
    bindings = list(
        ManagedServiceVolumeBinding.objects.filter(
            managed_service_id__in=service_ids,
            deleted_at__isnull=True,
        )
        .select_related(
            "managed_service__tenant_cluster",
            "managed_service__app_environment__tenant_cluster",
        )
        .order_by("managed_service__name", "name"),
    )
    if not bindings:
        return resources

    if cluster_override is None:
        cluster_driver, ctx, _ = driver_for_deployment(deployment)
    else:
        cluster_driver = _driver_for_cluster(cluster_override)
        ctx = _context_for_cluster(cluster_override)
    try:
        preflight_bindings(
            bindings,
            cluster_driver=cluster_driver,
            cluster_slug=ctx.slug,
            namespace=namespace,
        )
        return inject_bindings_into_workloads(
            resources,
            bindings,
            namespace=namespace,
            consumer_key=str(deployment.app_environment.guid),
        )
    except FilesystemBindingError as exc:
        raise AppDeployError(str(exc)) from exc


def render_resources_for_deployment(
    deployment: Deployment,
    *,
    cluster_override: TenantCluster | None = None,
    include_managed_filesystems: bool = True,
) -> list[dict[str, Any]]:
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
    from astrolift_services.models import AppSecretBundleRef
    from astrolift_workflows.activities.app_lifecycle import (
        _managed_services_for_environment,
    )

    env_from = sorted(
        AppSecretBundleRef.objects.filter(
            registered_app=app,
            app_environment=env,
            deleted_at__isnull=True,
        ).values_list("secret_bundle__slug", flat=True),
    )
    has_managed = _managed_services_for_environment(env).exists()
    if has_managed:
        env_from.append(f"astrolift-bindings-{app.slug}")

    resources = _render(
        manifest,
        app_slug=app.slug,
        namespace=namespace,
        image_tag=deployment.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        # Pin to the digest the build recorded. This is the render that
        # apply_manifests actually ships (#1003), so without it every
        # workload runs a mutable tag and a rollback can silently land
        # different bytes than the release it names.
        image_digest=deployment.image_digest,
        environment_name=env.name,
        env_from_secret_refs=env_from,
    )

    # Fold in the managed-subdomain Ingress if the environment has a
    # platform domain assigned. This mirrors the logic in the
    # render_manifests Temporal activity so apply_manifests always has
    # the full resource set regardless of which code path produced it.
    managed_domain = getattr(env, "managed_domain", None)
    cluster = cluster_override or getattr(env, "tenant_cluster", None)
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
    _stamp_storage_class_for_claims(
        deployment,
        resources,
        cluster_override=cluster_override,
    )

    if include_managed_filesystems:
        resources = _inject_managed_filesystem_bindings(
            deployment,
            resources,
            namespace=namespace,
            cluster_override=cluster_override,
        )

    # Workload identity (#1011): IAM-authed managed services run through a
    # provider-annotated Kubernetes ServiceAccount, never static credentials.
    if has_managed and cluster is not None:
        pc = getattr(cluster, "provider_config", None) or {}
        ac = getattr(cluster, "auth_config", None) or {}
        plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
        sa_name = workload_identity_role_name(app)
        annotations = _workload_identity_annotations(
            plugin_slug=plugin_slug,
            provider_config=pc,
            auth_config=ac,
            role_name=sa_name,
        )
        if annotations:
            resources = _inject_workload_identity(
                resources,
                sa_name=sa_name,
                namespace=namespace,
                annotations=annotations,
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


def shared_ingress_annotations(cluster: Any, *, org_slug: str, app_slug: str) -> dict[str, str]:
    """ALB group annotations for a cluster in shared-ingress mode (#64).

    Empty dict for a per-app cluster and for every non-ALB ingress
    class, which is what keeps the rendering identical for clusters
    that never opt in.

    Restricted to ALB on purpose. It is the one controller where
    "shared" means a real cloud load balancer is shared and where the
    policy's annotation is an actual grouping key; an nginx-family
    controller already fronts the whole cluster with one load balancer,
    and the policy's nginx entry is the ingress-class annotation, which
    would take the Ingress away from its controller rather than group
    it.
    """
    from astrolift_clusters.ingress_modes import IngressMode, shared_annotations_for
    from astrolift_clusters.status_routing import IngressDriver

    if getattr(cluster, "ingress_mode", "") != IngressMode.SHARED_INGRESS:
        return {}
    if getattr(cluster, "ingress_class", "") != "alb":
        return {}
    ann = shared_annotations_for(
        driver=IngressDriver.AWS_ALB,
        org_slug=org_slug,
        app_slug=app_slug,
    )
    out = {ann.group_name_key: ann.group_name_value}
    if ann.group_order_key:
        out[ann.group_order_key] = ann.group_order_value
    return out


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
            # ``setAppSubdomain`` writes this field and SyncAppDomainWorkflow
            # re-applies from here (#143). Without the override the rendered
            # host stayed ``<slug>.<zone>`` forever, so a rename only ever
            # changed the URL the API reported -- never the live Ingress.
            subdomain_override=app.subdomain or "",
        ),
    )
    log.info("render_managed_subdomain_ingress: computed hostnames=%s", [h.hostname for h in computed])
    if not computed:
        return []

    # static_site and faas workloads have no Service -> the ALB/nginx controller
    # would reject an Ingress backend pointing at a non-existent Service. Both are
    # reached via a CNAME -> CloudFront written by the deploy flow, not an Ingress
    # (#1010 static_site, #1035 public faas), so drop them from the
    # hostname-to-backend maps below.
    serviceless_workloads = {w.name for w in manifest.workloads if w.kind in ("static_site", "faas")}

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

        # The ALB health-check path is a single global ALBConfig field, so it
        # must come from a REAL container workload -- never a container-less
        # static_site (which has no probe and would force the "/" fallback
        # onto the real workload's target group, re-triggering #997). Pick the
        # first non-static computed entry.
        hc_source = next(
            (wh for wh in computed if wh.workload_slug not in serviceless_workloads),
            None,
        )
        driver = ALBIngressDriver(
            config=ALBConfig(
                region=cluster.region or "us-east-1",
                certificate_arn=cert_arn,
                cognito_auth=cognito_auth,
                healthcheck_path=_healthcheck_path(hc_source.workload_slug) if hc_source else "/",
            )
        )
        tls_strategy = "acm_dns_validated" if cert_arn else "letsencrypt"
        group_annotations = shared_ingress_annotations(cluster, org_slug=org_slug, app_slug=app.slug)
        by_workload: dict[str, list[str]] = {}
        for wh in computed:
            if wh.workload_slug in serviceless_workloads:
                continue
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
                if group_annotations:
                    rendered["metadata"].setdefault("annotations", {}).update(group_annotations)
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
            if wh.workload_slug in serviceless_workloads:
                continue
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
    "shared_ingress_annotations",
    "workloads_from_resources",
]
