"""Ensure an app's workload-identity (IRSA) role + ServiceAccount binding
exist before its pods start (#1011).

Apps that consume IAM-authed managed services (e.g. S3 object stores) need a
ServiceAccount annotated with an IAM role ARN; the EKS pod-identity webhook
then exchanges the projected SA token for STS credentials so the pod reaches
the service with no static keys. This activity:

* aggregates the IAM grants every active managed service declares in its
  driver ``binding().iam_grants`` into one inline policy,
* creates/updates the scoped IAM role (idempotent) via the cluster's
  WorkloadIdentityDriver, and
* binds it to the app's ServiceAccount (adds the ``(namespace, sa)`` subject
  to the role's OIDC trust policy).

The deploy render (``render_resources_for_deployment``) emits the matching
annotated ServiceAccount + sets ``serviceAccountName`` on the pods, using the
same name (``workload_identity_role_name``) and account/role-path config, so
the SA annotation points at exactly the role this activity creates.

Password-authed services (postgres/redis) declare no ``iam_grants``. Apps with
only those still receive an empty cloud identity because deploy rendering is
deterministic and cannot depend on an activity's transient return value.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.workload_identity")


def _permissions_from_bindings(
    bindings: list[Any],
    *,
    plugin_slug: str = "aws",
) -> list[dict[str, Any]]:
    """Translate portable binding grants into the provider identity shape.

    AWS consumes policy statements. GCP consumes predefined/custom IAM roles;
    accepting a raw GCP permission here would make provisioning appear to work
    while granting nothing, so that path fails closed.
    """
    permissions: list[dict[str, Any]] = []
    gcp_roles: set[str] = set()
    for binding in bindings:
        if binding is None:
            continue
        for grant in getattr(binding, "iam_grants", None) or []:
            actions = list(getattr(grant, "actions", None) or [])
            resource = getattr(grant, "resource", None)
            if not actions or not resource:
                continue
            if plugin_slug == "gcp":
                for action in actions:
                    if not (
                        action.startswith("roles/")
                        or (action.startswith(("projects/", "organizations/")) and "/roles/" in action)
                    ):
                        raise ValueError(
                            "GCP managed-service grants must declare IAM roles, "
                            f"not raw permissions; received {action!r} for {resource!r}",
                        )
                    if action not in gcp_roles:
                        permissions.append({"role": action})
                        gcp_roles.add(action)
                continue
            permissions.append(
                {
                    "Effect": "Allow",
                    "Action": actions,
                    "Resource": resource,
                },
            )
    return permissions


def _ensure_cluster_oidc_issuer(cluster: Any) -> None:
    """Ensure ``cluster.auth_config['cluster_oidc_issuer']`` is populated.

    IRSA's trust policy is built from the cluster's OIDC issuer. Rather than
    require it to be set out-of-band, discover it from EKS and cache it on
    the cluster row (mirrors the on-demand discovery managed-service
    networking does). No-op when already set or when the provider isn't AWS.
    """
    ac = cluster.auth_config or {}
    if ac.get("cluster_oidc_issuer"):
        return
    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return
    from aws.identity_irsa import discover_oidc_issuer

    pc = cluster.provider_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))
    cluster_name = str(pc.get("cluster_name", ac.get("cluster_name", cluster.slug)))
    issuer = discover_oidc_issuer(region, cluster_name)
    if not issuer:
        return
    new_ac = dict(ac)
    new_ac["cluster_oidc_issuer"] = issuer
    cluster.auth_config = new_ac
    cluster.save(update_fields=["auth_config"])


def _ensure_workload_identity_sync(
    registered_app_id: int,
    app_environment_id: int,
) -> dict[str, Any]:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.app_lifecycle import (
        _managed_services_for_environment,
    )
    from astrolift_workflows.activities.capability_deprovision import (
        _resolve_capability_driver,
    )
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _managed_binding_for,
    )
    from core.app_deploy import namespace_for_app, workload_identity_role_name

    app = RegisteredApp.all_objects.select_related("organization").get(
        pk=registered_app_id,
    )

    environment = AppEnvironment.all_objects.select_related(
        "registered_app",
        "tenant_cluster__provider_plugin",
    ).get(
        pk=app_environment_id,
        registered_app_id=registered_app_id,
        deleted_at__isnull=True,
    )
    services = list(
        _managed_services_for_environment(environment).select_related(
            "app_environment__tenant_cluster__provider_plugin",
            "tenant_cluster__provider_plugin",
        ),
    )

    if not services:
        return {
            "skipped": True,
            "reason": "no managed services",
            "registered_app_id": registered_app_id,
        }

    cluster = environment.tenant_cluster
    if cluster is None:
        return {
            "skipped": True,
            "reason": "no tenant cluster bound to environment",
            "registered_app_id": registered_app_id,
        }

    plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
    permissions = _permissions_from_bindings(
        [_managed_binding_for(svc) for svc in services],
        plugin_slug=plugin_slug,
    )
    # Self-sufficient: the IRSA trust policy needs the cluster's OIDC issuer.
    # Discover it from EKS + cache on the cluster row when absent, so the
    # trust isn't malformed by an empty issuer (which yields a broken
    # oidc-provider/ principal + bare :sub condition).
    _ensure_cluster_oidc_issuer(cluster)

    identity_driver = _resolve_capability_driver(cluster, "identity")
    role_name = workload_identity_role_name(app)
    namespace = namespace_for_app(app)

    # Idempotent: create_identity_role returns the existing ARN if present;
    # bind_service_account adds this (namespace, sa) subject to the trust.
    role_arn = identity_driver.create_identity_role(role_name, permissions)
    annotation = identity_driver.bind_service_account(
        cluster.slug,
        namespace,
        role_name,
        role_name,
    )
    return {
        "registered_app_id": registered_app_id,
        "role": role_name,
        "role_arn": role_arn,
        "namespace": namespace,
        "cluster_slug": cluster.slug,
        "grants": len(permissions),
        "annotation": annotation,
    }


@activity.defn(name="astrolift.workload_identity.ensure")
async def ensure_workload_identity(
    registered_app_id: int,
    app_environment_id: int,
) -> dict[str, Any]:
    """Activity entry — ensure the app's IRSA role + SA binding exist."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_ensure_workload_identity_sync)(
        registered_app_id,
        app_environment_id,
    )
    log.info(
        "ensure_workload_identity app_id=%s summary=%s",
        registered_app_id,
        summary,
    )
    return summary
