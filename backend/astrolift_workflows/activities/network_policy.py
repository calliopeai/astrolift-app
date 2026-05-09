"""
Per-app NetworkPolicy generation (#24, spec 12 §7.1).

Pure-Python renderer. The deploy workflow asks this module for
the NetworkPolicy objects to apply alongside the workload manifests.

The generated policy enforces:
* Ingress: deny by default; allow only from the ingress controller
  namespace.
* Egress: deny by default; allow only to:
  - Cluster DNS (kube-dns / CoreDNS)
  - The app's bound managed services (resolved CIDRs)
  - The app's allow-listed FQDNs / CIDRs from #72 EgressPolicy
  - HTTPS to public internet (when ``allow_internet_https=True``)

Pairs with #72 ``EgressPolicy`` (the egress allow-list is computed
there; this module renders it as NetworkPolicy YAML/dict).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from astrolift_clusters.egress import EgressPolicy, render as render_egress


# Standard cluster DNS port + protocol — covers CoreDNS / kube-dns.
_DNS_PORTS = (
    {"protocol": "UDP", "port": 53},
    {"protocol": "TCP", "port": 53},
)

# Default ingress controller selector — any namespace labeled
# 'astrolift.io/ingress=true'. Operators can override per-cluster
# via the cluster's ``provider_config['ingress_namespace_selector']``.
DEFAULT_INGRESS_NS_LABEL = "astrolift.io/ingress"


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceCIDR:
    """A bound managed service's reachable network. The driver
    resolves these from the service handle (e.g. RDS subnet
    group → CIDR list)."""

    name: str
    cidr: str
    port: int


def render_network_policy(
    *,
    namespace: str,
    app_label: str,
    egress_policy: EgressPolicy,
    managed_service_cidrs: Sequence[ManagedServiceCIDR] = (),
    allow_internet_https: bool = True,
    ingress_namespace_label: str = DEFAULT_INGRESS_NS_LABEL,
) -> dict:
    """Build the NetworkPolicy object for a workload.

    The policy applies to pods labeled ``app=<app_label>`` in
    ``namespace``. The deploy activity emits one of these per app
    (not per workload — pods of the same app share network
    posture).
    """
    if not namespace or not app_label:
        raise ValueError("namespace and app_label are required")

    egress_decision = render_egress(egress_policy)

    egress_rules: list[dict] = []

    # 1. Cluster DNS — always allow. Pods can't resolve anything
    # without it.
    egress_rules.append({
        "to": [{"namespaceSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
        "ports": list(_DNS_PORTS),
    })

    # 2. Managed service CIDRs.
    for ms in managed_service_cidrs:
        egress_rules.append({
            "to": [{"ipBlock": {"cidr": ms.cidr}}],
            "ports": [{"protocol": "TCP", "port": ms.port}],
        })

    # 3. Per-app allow-listed CIDRs (from #72 egress policy).
    for cidr in egress_decision.allow_cidrs:
        egress_rules.append({
            "to": [{"ipBlock": {"cidr": cidr}}],
        })

    # 4. Public HTTPS internet (when enabled). Excludes the deny
    # floor of internal CIDRs via NetworkPolicy 'except' clauses
    # so pods can't reach control-plane DB / other tenants.
    if allow_internet_https:
        egress_rules.append({
            "to": [{
                "ipBlock": {
                    "cidr": "0.0.0.0/0",
                    "except": list(egress_decision.deny_cidrs),
                },
            }],
            "ports": [{"protocol": "TCP", "port": 443}],
        })

    # Ingress rules: only the ingress controller namespace.
    ingress_rules = [{
        "from": [{
            "namespaceSelector": {
                "matchLabels": {ingress_namespace_label: "true"},
            },
        }],
    }]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"astrolift-{app_label}",
            "namespace": namespace,
            "labels": {
                "app": app_label,
                "managed-by": "astrolift",
            },
        },
        "spec": {
            "podSelector": {"matchLabels": {"app": app_label}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": ingress_rules,
            "egress": egress_rules,
        },
    }


# ---- TLS / HSTS edge defaults --------------------------------------


# Spec 12 §7.2 edge security defaults. The ingress driver consumes
# these as annotations on the Ingress object; per-driver translation
# (nginx vs alb vs gateway-api) lives in the driver.
EDGE_TLS_DEFAULTS: dict[str, str] = {
    # TLS 1.3 minimum
    "tls_min_version": "1.3",
    # HSTS 1 year, includeSubDomains
    "hsts_max_age": "31536000",
    "hsts_include_subdomains": "true",
    # HTTP -> HTTPS redirect
    "http_to_https_redirect": "true",
}


def edge_annotations(
    *,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Spec 12 §7.2 edge defaults as annotation map. The ingress
    driver translates these to its native annotation keys
    (e.g. ``alb.ingress.kubernetes.io/ssl-policy`` vs
    ``nginx.ingress.kubernetes.io/ssl-protocols``)."""
    out = dict(EDGE_TLS_DEFAULTS)
    if overrides:
        out.update(overrides)
    return out
