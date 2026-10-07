"""Per-app access on the Envoy edge (#2132).

Any user of a cluster's identity pool could sign in to every gated app on it.
An app now lists who may enter (groups of the IdP, users by email), and the
edge enforces it before the app sees the request.

The rules live in the one shared SecurityPolicy, so single sign-on stays
(``providers.k8s_native.edge_gateway.edge_authorization``). That policy is
cluster-wide and rendered by the recipe install, so the control plane keeps
``TenantCluster.edge_access_rules`` current and re-applies the edge when it
changes:

* a deploy records the hostnames an environment actually serves,
* an access change (UI, CLI or ``[ingress.access]``) records the grant,
* a teardown removes the environment's entry.

An app with no rule is open to every signed-in user, as before.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ACCESS_SOURCES = ("ui", "manifest")


def normalize_access(groups: Any, users: Any) -> dict[str, list[str]]:
    """Sorted, deduplicated grants. Emails compare case-insensitively."""
    return {
        "groups": sorted({str(g).strip() for g in groups or [] if str(g).strip()}),
        "users": sorted({str(u).strip().lower() for u in users or [] if str(u).strip()}),
    }


def app_access(app: Any) -> dict[str, list[str]]:
    access = getattr(app, "edge_access", None) or {}
    return normalize_access(access.get("groups"), access.get("users"))


def is_restricted(access: dict[str, list[str]]) -> bool:
    return bool(access["groups"] or access["users"])


def _key(app: Any, namespace: str) -> str:
    # The app's guid, not its slug: two orgs can each have an app of the same
    # slug on one shared cluster, and a slug prefix would match both.
    return f"{app.guid}/{namespace}"


def _hosts_from_rendered(rendered: list[dict[str, Any]], namespace: str) -> list[str]:
    """The hostnames this environment's edge routes serve."""
    from providers.k8s_native.edge_gateway import ROUTE_NAMESPACE_LABEL

    hosts: set[str] = set()
    for manifest in rendered:
        if manifest.get("kind") != "HTTPRoute":
            continue
        labels = (manifest.get("metadata") or {}).get("labels") or {}
        if labels.get(ROUTE_NAMESPACE_LABEL) != namespace:
            continue
        if labels.get("astrolift.dev/custom-domain"):
            continue  # Independent first-party policies never enter the shared central policy.
        hosts.update(str(h) for h in (manifest.get("spec") or {}).get("hostnames") or [])
    return sorted(hosts)


def _save_rules(cluster: Any, rules: dict[str, Any]) -> bool:
    """Persist ``rules`` if they changed, re-applying the edge. Returns changed."""
    if rules == (cluster.edge_access_rules or {}):
        return False
    cluster.edge_access_rules = rules
    cluster.save(update_fields=["edge_access_rules", "updated_at", "version"])
    reapply_edge(cluster)
    return True


def record_environment(cluster: Any, app: Any, namespace: str, rendered: list[dict[str, Any]]) -> bool:
    """After an apply: the environment's hosts and the app's grant.

    ``rendered`` empty (a teardown) removes the entry. Never raises: the
    apply already succeeded, and the next deploy or access change retries.
    """
    try:
        rules = dict(cluster.edge_access_rules or {})
        key = _key(app, namespace)
        access = app_access(app)
        hosts = _hosts_from_rendered(rendered, namespace)
        from providers.k8s_native.edge_gateway import custom_domain_hostname

        custom_routes = [
            {
                "name": item["metadata"]["name"],
                "hostname": custom_domain_hostname(item),
                "labels": item["metadata"]["labels"],
            }
            for item in rendered
            if item.get("kind") == "SecurityPolicy"
            and item.get("metadata", {}).get("labels", {}).get("astrolift.dev/namespace") == namespace
            and item.get("metadata", {}).get("labels", {}).get("astrolift.dev/custom-domain")
        ]
        if (hosts and is_restricted(access)) or custom_routes:
            rules[key] = {
                "name": f"{app.slug}-{namespace}",
                "hosts": hosts,
                **access,
                **({"custom_routes": custom_routes} if custom_routes else {}),
            }
        else:
            rules.pop(key, None)
        return _save_rules(cluster, rules)
    except Exception:
        logger.exception("edge access: recording %s on %s failed", getattr(app, "slug", app), cluster.slug)
        return False


def set_app_access(app: Any, *, groups: Any, users: Any, source: str) -> dict[str, list[str]]:
    """Set who may enter ``app``, and re-render every edge it is on.

    Hostnames come from each environment's last deploy, so an environment
    never deployed on the edge gains its rule on that first deploy.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment

    if source not in ACCESS_SOURCES:
        raise ValueError(f"source must be one of {ACCESS_SOURCES}")
    access = normalize_access(groups, users)
    app.edge_access = {**access, "source": source} if is_restricted(access) else {}
    app.save(update_fields=["edge_access", "updated_at", "version"])

    prefix = f"{app.guid}/"
    # Only the clusters this app's own environments run on.
    cluster_ids = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).values_list(
        "tenant_cluster_id", flat=True
    )
    for cluster in TenantCluster.objects.filter(
        pk__in=list(cluster_ids), ingress_class="envoy", deleted_at__isnull=True
    ):
        rules = dict(cluster.edge_access_rules or {})
        ours = {k: v for k, v in rules.items() if k.startswith(prefix)}
        if not ours:
            continue
        for key, entry in ours.items():
            if is_restricted(access) or entry.get("custom_routes"):
                rules[key] = {**entry, **access}
            else:
                rules.pop(key)
        _save_rules(cluster, rules)
    return access


def reapply_edge(cluster: Any) -> bool:
    """Re-apply the edge so its shared policy carries the current rules.

    The same additive recipe run the control plane uses to bring the edge up
    (#2130): it applies the edge's manifests and deletes nothing. On a
    cluster held to the minimal RBAC contract that run is refused, so only
    the access policies are written (:func:`_apply_access_policies`).
    """
    from core.install_restrictions import cluster_scope_refusal

    if cluster_scope_refusal(cluster):
        return _apply_access_policies(cluster)
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, InstallClusterPrereqsInput
    from providers.k8s_native.edge_gateway import EDGE_COMPONENT_KEY

    try:
        start_workflow(
            "InstallClusterPrereqsWorkflow",
            args=[
                InstallClusterPrereqsInput(
                    cluster_id=cluster.pk,
                    actor=Actor(kind="system", display="edge-access"),
                    selected_components=(EDGE_COMPONENT_KEY,),
                    option_overrides={},
                    organization_id=cluster.organization_id,
                    additive=True,
                ),
            ],
            workflow_id=f"InstallClusterPrereqsWorkflow-{cluster.guid}-edge",
        )
    except Exception:
        logger.exception("edge access: re-applying the edge on %s failed", cluster.slug)
        return False
    return True


def _apply_access_policies(cluster: Any) -> bool:
    """Write the edge's access policies, and nothing else (calliope-installer#447).

    The recipe run that renders the whole edge writes a GatewayClass, the
    EnvoyProxy and a HelmRelease, none of which the minimal RBAC contract
    grants, so it is refused. The SecurityPolicies that carry per-app access
    live in the edge namespace and are granted; the edge itself is the
    cluster owner's.
    """
    from core.cluster_management import _driver_for_cluster  # type: ignore[attr-defined]
    from providers.k8s_native.edge_gateway import EDGE_NAMESPACE, edge_access_manifests

    manifests = edge_access_manifests(
        getattr(cluster, "oidc_auth_config", None), list((cluster.edge_access_rules or {}).values())
    )
    if not manifests:
        return True
    try:
        result = _driver_for_cluster(cluster).apply_manifests(cluster.slug, EDGE_NAMESPACE, manifests)
    except Exception:
        logger.exception("edge access: writing the access policies on %s failed", cluster.slug)
        return False
    if not result.ok:
        logger.error(
            "edge access: writing the access policies on %s failed: %s",
            cluster.slug,
            "; ".join(str(e) for e in result.errors),
        )
        return False
    return True


def access_preview(app: Any, cluster: Any, *, groups: Any, users: Any) -> dict[str, Any]:
    """Who a proposed rule would let in, from the cluster's IdP (#2132).

    ``{"allowed": n, "total": n, "losing": [emails]}``; ``None`` counts when
    the IdP is not one Astrolift can list.
    """
    from astrolift_clusters.schema.auth_users import identity_users_driver

    access = normalize_access(groups, users)
    driver, _reason = identity_users_driver(cluster)
    if driver is None:
        return {"allowed": None, "total": None, "losing": []}
    people = driver.list_users(limit=60)
    current = app_access(app)

    def allowed(person, rule) -> bool:
        if not is_restricted(rule):
            return True
        return bool(set(person.groups) & set(rule["groups"])) or person.email.lower() in rule["users"]

    losing = [p.email for p in people if allowed(p, current) and not allowed(p, access)]
    return {"allowed": sum(1 for p in people if allowed(p, access)), "total": len(people), "losing": losing}
