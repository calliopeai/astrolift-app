"""
Ingress mode + routing policy (#64, spec 13 §4 + §4.1 + §4.2).

Pure-Python policy module:

* **Cluster-level ingress mode** — ``shared_ingress`` (one LB
  fronts all apps; per-driver group annotation) or
  ``per_app_ingress`` (one LB per app; higher cost, cleaner
  isolation for compliance-sensitive orgs).
* **Per-driver group annotation** — each ingress controller has
  its own way of declaring 'these Ingress objects share a load
  balancer': ALB ``alb.ingress.kubernetes.io/group.name``, nginx
  ``nginx.ingress.kubernetes.io/server-snippet``, Gateway API
  ``gatewayClassName`` + parent reference, etc.
* **Routing mode** — host-based (default; one host per workload)
  vs path-based (workload's host == parent app's host, path
  differs). Path-based requires the manifest's
  ``[workloads.network] expose_paths``.

Pairs with #76 status routing (catch-all rule precedence) and
#24 NetworkPolicy generation (per-app vs shared affects the
ingress namespaceSelector).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import StrEnum

from astrolift_clusters.status_routing import IngressDriver


class IngressMode(StrEnum):
    SHARED_INGRESS = "shared_ingress"
    """One ingress LB fronts all apps in the cluster. Tenant
    Ingresses opt into the shared group via driver-specific
    annotations. Default — cheaper for most orgs."""

    PER_APP_INGRESS = "per_app_ingress"
    """One ingress LB per app. Higher cost; cleaner isolation
    for compliance-sensitive orgs (each app's traffic stays in
    its own LB)."""


# ---- group annotations (shared mode) -------------------------------


_SHARED_ANNOTATIONS: dict[IngressDriver, tuple[str, str]] = {
    IngressDriver.AWS_ALB: (
        "alb.ingress.kubernetes.io/group.name",
        "alb.ingress.kubernetes.io/group.order",
    ),
    IngressDriver.NGINX: (
        # nginx-ingress shares the controller by default; all
        # Ingresses pointing at the same ingressClassName share
        # the LB. Annotation is informational.
        "kubernetes.io/ingress.class",
        "",
    ),
    IngressDriver.GCE: (
        # GCE: the parent Ingress's name is shared via the
        # `kubernetes.io/ingress.class` + `gce-shared-ingress` annotation.
        "kubernetes.io/ingress.class",
        "",
    ),
    IngressDriver.AZURE_AGW: (
        "appgw.ingress.kubernetes.io/shared",
        "",
    ),
    IngressDriver.GATEWAY_API: (
        # Gateway API: tenant HTTPRoutes attach to a shared
        # parent Gateway by name.
        "gateway.networking.k8s.io/parent-gateway",
        "",
    ),
}


@dataclasses.dataclass(frozen=True, slots=True)
class IngressAnnotations:
    """Annotations the ingress driver applies to the rendered
    Ingress object based on the cluster's mode + the app's
    grouping."""

    group_name_key: str
    group_name_value: str
    group_order_key: str
    group_order_value: str
    """Empty string when the driver doesn't use ordering."""


def shared_annotations_for(
    *,
    driver: IngressDriver,
    org_slug: str,
    app_slug: str,
    group_order: int = 100,
) -> IngressAnnotations:
    """Return the annotations to apply on a shared-mode Ingress.

    ``group_name`` is namespaced by org so two orgs in the same
    cluster get separate ALB groups (separate cloud LBs)
    even in shared_ingress mode — that's the right default for
    multi-tenant clusters: tenants share within an org, not
    across orgs.
    """
    if driver not in _SHARED_ANNOTATIONS:
        raise ValueError(f"unsupported ingress driver {driver!r}")
    name_key, order_key = _SHARED_ANNOTATIONS[driver]
    group_name_value = f"astrolift-{org_slug}"
    return IngressAnnotations(
        group_name_key=name_key,
        group_name_value=group_name_value,
        group_order_key=order_key,
        group_order_value=str(group_order) if order_key else "",
    )


def per_app_annotations(
    *,
    driver: IngressDriver,
    app_slug: str,
) -> IngressAnnotations:
    """Per-app mode: each app gets its own LB. Group name is
    unique per app."""
    if driver not in _SHARED_ANNOTATIONS:
        raise ValueError(f"unsupported ingress driver {driver!r}")
    name_key, _ = _SHARED_ANNOTATIONS[driver]
    return IngressAnnotations(
        group_name_key=name_key,
        group_name_value=f"astrolift-app-{app_slug}",
        group_order_key="",
        group_order_value="",
    )


# ---- path-based routing --------------------------------------------


_PATH_PATTERN_RE = re.compile(r"^/[A-Za-z0-9._/\-*]*$")


class RoutingError(ValueError):
    pass


def validate_expose_paths(paths: Sequence[str]) -> None:
    """Manifest ``[workloads.network] expose_paths`` validation.

    Must each start with '/'. Trailing '*' allowed for prefix
    matching (driver-specific translation). No regex chars
    beyond '*' — different ingress controllers translate
    differently and we don't want surprises.
    """
    if not paths:
        raise RoutingError("expose_paths must not be empty when set")
    for p in paths:
        if not isinstance(p, str):
            raise RoutingError(f"expose_paths entry {p!r} must be a string")
        if not _PATH_PATTERN_RE.match(p):
            raise RoutingError(
                f"path {p!r} must start with '/' and contain only "
                "[A-Za-z0-9._/-*] (use '*' suffix for prefix match)"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The output of resolving manifest routing config to ingress
    rules."""

    mode: str
    """'host' | 'path'"""

    hostname: str
    """For path mode, this is the parent app's hostname; the
    workloads share it and differ only by path."""

    paths: tuple[str, ...]
    """Empty for host-mode; populated for path-mode."""


def resolve_routing(
    *,
    app_hostname: str,
    workload_hostname: str,
    expose_paths: Sequence[str] = (),
) -> RoutingDecision:
    """Translate the manifest's hostname + expose_paths into a
    routing decision the ingress driver renders.

    Path-mode rule: ``workload_hostname`` MUST equal the parent
    ``app_hostname`` when paths are set. Otherwise the workload
    is asking for a different host AND path-based routing — that
    contradicts the spec (path-mode is opt-in to share the
    parent host).
    """
    if expose_paths:
        validate_expose_paths(expose_paths)
        if workload_hostname and workload_hostname != app_hostname:
            raise RoutingError(
                f"workload hostname {workload_hostname!r} "
                f"differs from app hostname {app_hostname!r} but "
                "expose_paths is set; path-mode requires shared "
                "hostname"
            )
        return RoutingDecision(
            mode="path",
            hostname=app_hostname,
            paths=tuple(expose_paths),
        )
    return RoutingDecision(
        mode="host",
        hostname=workload_hostname or app_hostname,
        paths=(),
    )
