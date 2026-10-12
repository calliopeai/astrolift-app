"""Network fence for agent task and box pods (#1850).

One NetworkPolicy per agent namespace, applied with every task spawn and box
start. Agent pods take no inbound pod-network traffic (exec, VNC and attach go
through the Kubernetes API, which NetworkPolicy does not see). Outbound they
reach cluster DNS and the public internet on any port (git over ssh, apt over
http), never private ranges or link-local, which covers cloud metadata
(169.254.169.254) and the control plane's own network.

Off unless ``AGENT_NETWORK_FENCE`` is on: an install whose agents reach a
private git server, an in-cluster Zentinelle, or an app's managed database
would lose them, the same reason app NetworkPolicy is opt-in. Those
destinations go in ``AGENT_EGRESS_ALLOW_CIDRS``. Turning the fence back off
does not remove an applied policy; delete ``astrolift-agent-fence`` from the
agent namespace. Enforcement needs a NetworkPolicy-capable CNI.

A cluster running the Zentinelle gateway (#1887) gets one more rule, to the
gateway's pods by label, so agents reach it without a CIDR carve-out.

Gateway mode is a boundary (#1950, Leo 2026-10-12: default-deny). A pod whose
spec routes models through the Zentinelle gateway carries
``astrolift.dev/model-gateway: "true"`` and gets its own policy, applied
whatever ``AGENT_NETWORK_FENCE`` says: cluster DNS, the gateway, and
``AGENT_GATEWAY_EGRESS_ALLOW_CIDRS`` (git hosts, package mirrors), nothing
else, so no payload, config file or multi-provider runtime reaches a model
provider directly. Policies add up, so the general fence leaves those pods
out; otherwise its internet rule would reopen what this one closes.
"""

from __future__ import annotations

import copy

from astrolift_clusters.egress import CLUSTER_DNS_PEER, DEFAULT_INTERNAL_CIDRS, EgressPolicy

FENCE_NAME = "astrolift-agent-fence"
GATEWAY_FENCE_NAME = "astrolift-agent-gateway-fence"
#: Set on gateway-mode agent pods (task Jobs and boxes).
GATEWAY_LABEL = "astrolift.dev/model-gateway"

_IPV6_INTERNAL = ("fc00::/7", "fe80::/10")

# GKE Workload Identity serves pod credentials from the metadata server;
# GKE's network-policy guidance allows these so identity keeps working.
_GKE_METADATA_RULES = (
    {"to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}], "ports": [{"protocol": "TCP", "port": 80}]},
    {"to": [{"ipBlock": {"cidr": "169.254.169.252/32"}}], "ports": [{"protocol": "TCP", "port": 988}]},
)

# The Zentinelle gateway's pods (astrolift_operations.zentinelle_connect). Spelled out
# rather than imported so this module stays free of model imports; a test
# holds the two to the same namespace, label and port.
_ZENTINELLE_GATEWAY_RULE = {
    "to": [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"}},
            "podSelector": {"matchLabels": {"app": "zentinelle-gateway"}},
        }
    ],
    "ports": [{"protocol": "TCP", "port": 8742}],
}


def _allow_cidrs(raw: str) -> tuple[str, ...]:
    cidrs = tuple(part.strip() for part in raw.split(",") if part.strip())
    # EgressPolicy validates each CIDR and raises a readable error.
    return EgressPolicy(allowed_cidrs=cidrs).allowed_cidrs


def render_agent_fence(
    *, namespace: str, provider_slug: str = "", allow_cidrs: str = "", zentinelle_gateway: bool = False
) -> dict:
    ipv4_internal = [cidr for cidr in DEFAULT_INTERNAL_CIDRS if ":" not in cidr]
    egress: list[dict] = [
        {
            "to": [dict(CLUSTER_DNS_PEER)],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
        {
            "to": [
                {"ipBlock": {"cidr": "0.0.0.0/0", "except": ipv4_internal}},
                {"ipBlock": {"cidr": "::/0", "except": list(_IPV6_INTERNAL)}},
            ]
        },
    ]
    egress.extend({"to": [{"ipBlock": {"cidr": cidr}}]} for cidr in _allow_cidrs(allow_cidrs))
    if provider_slug == "gcp":
        egress.extend(_GKE_METADATA_RULES)
    if zentinelle_gateway:
        egress.append(copy.deepcopy(_ZENTINELLE_GATEWAY_RULE))

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": FENCE_NAME,
            "namespace": namespace,
            "labels": {"managed-by": "astrolift", "astrolift.dev/component": "agents"},
        },
        "spec": {
            "podSelector": {
                "matchExpressions": [
                    {
                        "key": "astrolift.dev/workload-kind",
                        "operator": "In",
                        "values": ["agent", "agent-box"],
                    },
                    # Gateway-mode pods have their own, narrower policy (#1950).
                    {"key": GATEWAY_LABEL, "operator": "DoesNotExist"},
                ]
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": egress,
        },
    }


def render_gateway_fence(*, namespace: str, provider_slug: str = "", allow_cidrs: str = "") -> dict:
    """Default-deny egress for gateway-mode agent pods (#1950)."""
    egress: list[dict] = [
        {
            "to": [dict(CLUSTER_DNS_PEER)],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
        copy.deepcopy(_ZENTINELLE_GATEWAY_RULE),
    ]
    egress.extend({"to": [{"ipBlock": {"cidr": cidr}}]} for cidr in _allow_cidrs(allow_cidrs))
    if provider_slug == "gcp":
        egress.extend(_GKE_METADATA_RULES)
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": GATEWAY_FENCE_NAME,
            "namespace": namespace,
            "labels": {"managed-by": "astrolift", "astrolift.dev/component": "agents"},
        },
        "spec": {
            "podSelector": {"matchLabels": {GATEWAY_LABEL: "true"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": egress,
        },
    }


def agent_fence_manifests(cluster, namespace: str, *, gateway: bool = False) -> list[dict]:
    """The policies for ``namespace``: the general fence when it is on, and the
    gateway fence whenever the pod being started is in gateway mode."""
    from constance import config

    from astrolift_operations.zentinelle_connect import fenced_agents_reach_gateway

    plugin = getattr(cluster, "provider_plugin", None)
    provider_slug = str(getattr(plugin, "slug", "") or "")
    manifests = []
    if config.AGENT_NETWORK_FENCE:
        manifests.append(
            render_agent_fence(
                namespace=namespace,
                provider_slug=provider_slug,
                allow_cidrs=str(config.AGENT_EGRESS_ALLOW_CIDRS or ""),
                zentinelle_gateway=fenced_agents_reach_gateway(cluster),
            )
        )
    if gateway:
        manifests.append(
            render_gateway_fence(
                namespace=namespace,
                provider_slug=provider_slug,
                allow_cidrs=str(getattr(config, "AGENT_GATEWAY_EGRESS_ALLOW_CIDRS", "") or ""),
            )
        )
    return manifests
