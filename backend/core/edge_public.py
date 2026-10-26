"""Per-app public access on the Envoy edge (#2504).

An app's platform hostname can answer without the edge login when both sides
agree: the cluster allows public apps (``TenantCluster.allow_public_apps``,
on by default) and the app asked (``RegisteredApp.edge_public``, off by
default). Turning it on is a step-up action (``app.edge.make_public``).

A public app is public because its own HTTPRoute goes without the gate label
(``providers.k8s_native.edge_gateway.app_route_gated``). The shared
``edge-oidc`` SecurityPolicy is never loosened for it: on 2026-10-25 a change
to that one policy opened every gated app on a cluster at once (#2526).

A brain can be public too; its ``/mcp`` route keeps its own token policy
whatever the app's state. Only the Envoy edge honours the switch. On any
other edge, and wherever the cluster refuses, the app stays gated. Custom
domains keep their own ``CustomDomain.edge_auth_enabled``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Why an app is or is not public on one cluster, as the API reports it.
PUBLIC = "public"
GATED = "gated"
NO_GATE = "no_gate"
NOT_ENVOY = "not_envoy"
CLUSTER_DISALLOWS = "cluster_disallows"
RESTRICTED = "restricted"


def _restricted(app: Any) -> bool:
    from core.edge_access import app_access, is_restricted

    return is_restricted(app_access(app))


def route_gated(app: Any, cluster: Any) -> bool:
    """Whether ``app``'s platform route on ``cluster``'s Envoy edge carries the gate."""
    from core.app_deploy import oidc_auth_for_cluster
    from providers.k8s_native.edge_gateway import app_route_gated

    return app_route_gated(
        cluster_gated=oidc_auth_for_cluster(cluster) is not None,
        allow_public_apps=bool(getattr(cluster, "allow_public_apps", True)),
        edge_public=bool(getattr(app, "edge_public", False)),
        restricted=_restricted(app),
    )


def public_state(app: Any, cluster: Any) -> str:
    """``app``'s state on ``cluster``: one of the module's state strings."""
    from core.app_deploy import oidc_auth_for_cluster

    if oidc_auth_for_cluster(cluster) is None:
        return NO_GATE
    if not getattr(app, "edge_public", False):
        return GATED
    if getattr(cluster, "ingress_class", "") != "envoy":
        return NOT_ENVOY
    if not getattr(cluster, "allow_public_apps", True):
        return CLUSTER_DISALLOWS
    if _restricted(app):
        return RESTRICTED
    return PUBLIC if not route_gated(app, cluster) else GATED


def make_public_refusal(app: Any) -> str | None:
    """Why ``app`` may not ask to be public at all, or ``None``."""
    if _restricted(app):
        return "this app has an access list, which only applies behind the login; clear it first"
    return None


def app_clusters(app: Any) -> list[Any]:
    """The live clusters ``app``'s environments run on."""
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment

    ids = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).values_list(
        "tenant_cluster_id", flat=True
    )
    return list(TenantCluster.objects.filter(pk__in=list(ids), deleted_at__isnull=True).order_by("slug"))


def set_edge_public(app: Any, public: bool) -> list[str]:
    """Record the app's choice and move its live routes now. Returns errors."""
    if bool(app.edge_public) != public:
        app.edge_public = public
        app.save(update_fields=["edge_public", "updated_at", "version"])
    return reapply_app_routes(app)


def reapply_app_routes(app: Any, clusters: list[Any] | None = None) -> list[str]:
    """Re-render ``app``'s platform routes on each Envoy edge and apply them.

    A deploy would do the same, but a person making an app private again
    should not have to wait for one. The workloads, hostnames and ports come
    from the routes the edge already serves, so nothing about the app other
    than its gate can change here. A paused environment is left alone: its
    route answers 503 whatever its gate, and resuming redeploys it.

    Returns one message per cluster the change could not reach; it reaches
    them on the app's next deploy.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from core.app_deploy import envoy_edge_routes, namespace_for_environment
    from core.cluster_management import _driver_for_cluster  # type: ignore[attr-defined]
    from providers.k8s_native.edge_gateway import EDGE_NAMESPACE, ROUTE_NAMESPACE_LABEL

    wanted = {c.pk for c in clusters} if clusters is not None else None
    errors: list[str] = []
    environments = (
        AppEnvironment.objects.filter(
            registered_app=app, deleted_at__isnull=True, tenant_cluster__ingress_class="envoy"
        )
        .select_related("tenant_cluster")
        .order_by("pk")
    )
    for env in environments:
        cluster = env.tenant_cluster
        if (
            cluster is None
            or cluster.deleted_at is not None
            or (wanted is not None and cluster.pk not in wanted)
        ):
            continue
        if env.ingress_paused:
            continue
        namespace = namespace_for_environment(env)
        try:
            driver = _driver_for_cluster(cluster)
            live = driver.list_manifests(
                cluster.slug, EDGE_NAMESPACE, "gateway.networking.k8s.io/v1/HTTPRoute"
            )
            workloads: dict[str, tuple[list[str], int]] = {}
            names: set[str] = set()
            for route in live:
                labels = (route.get("metadata") or {}).get("labels") or {}
                if (
                    labels.get("astrolift.dev/app") != app.slug
                    or labels.get(ROUTE_NAMESPACE_LABEL) != namespace
                    or labels.get("astrolift.dev/managed-subdomain") != "true"
                    or labels.get("astrolift.dev/custom-domain")
                ):
                    continue
                rules = (route.get("spec") or {}).get("rules") or []
                if len(rules) != 1 or rules[0].get("matches"):
                    continue  # A brain's /mcp token route keeps its own policy, public or not.
                refs = rules[0].get("backendRefs") or []
                if len(refs) != 1:
                    continue
                hosts = [str(h) for h in (route.get("spec") or {}).get("hostnames") or []]
                workloads[str(refs[0]["name"])] = (hosts, int(refs[0]["port"]))
                names.add(route["metadata"]["name"])
            if not workloads:
                continue
            rendered = [
                m
                for m in envoy_edge_routes(
                    app, namespace=namespace, workloads=workloads, cluster=cluster, paused=False
                )
                if m.get("kind") == "HTTPRoute" and m["metadata"]["name"] in names
            ]
            result = driver.apply_manifests(cluster.slug, EDGE_NAMESPACE, rendered)
            if not result.ok:
                errors.append(f"{cluster.slug}: " + "; ".join(str(e) for e in result.errors))
        except Exception as exc:  # noqa: BLE001 - reported to the caller, the next deploy retries
            logger.exception("edge public: re-applying %s on %s failed", app.slug, cluster.slug)
            errors.append(f"{cluster.slug}: {exc}")
    return errors


def reapply_cluster(cluster: Any) -> list[str]:
    """After ``allow_public_apps`` changes: every app on ``cluster`` that asked to be public."""
    from astrolift_registry.models import RegisteredApp

    if getattr(cluster, "ingress_class", "") != "envoy":
        return []
    apps = RegisteredApp.objects.filter(
        edge_public=True,
        deleted_at__isnull=True,
        environments__tenant_cluster=cluster,
        environments__deleted_at__isnull=True,
    ).distinct()
    errors: list[str] = []
    for app in apps:
        errors.extend(reapply_app_routes(app, [cluster]))
    return errors
