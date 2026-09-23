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
"""

from __future__ import annotations

from astrolift_clusters.egress import DEFAULT_INTERNAL_CIDRS, EgressPolicy

FENCE_NAME = "astrolift-agent-fence"

_IPV6_INTERNAL = ("fc00::/7", "fe80::/10")

# GKE Workload Identity serves pod credentials from the metadata server;
# GKE's network-policy guidance allows these so identity keeps working.
_GKE_METADATA_RULES = (
    {"to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}], "ports": [{"protocol": "TCP", "port": 80}]},
    {"to": [{"ipBlock": {"cidr": "169.254.169.252/32"}}], "ports": [{"protocol": "TCP", "port": 988}]},
)


def _allow_cidrs(raw: str) -> tuple[str, ...]:
    cidrs = tuple(part.strip() for part in raw.split(",") if part.strip())
    # EgressPolicy validates each CIDR and raises a readable error.
    return EgressPolicy(allowed_cidrs=cidrs).allowed_cidrs


def render_agent_fence(*, namespace: str, provider_slug: str = "", allow_cidrs: str = "") -> dict:
    ipv4_internal = [cidr for cidr in DEFAULT_INTERNAL_CIDRS if ":" not in cidr]
    egress: list[dict] = [
        {
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
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
                    {"key": "astrolift.dev/workload-kind", "operator": "In", "values": ["agent", "agent-box"]}
                ]
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": egress,
        },
    }


def agent_fence_manifests(cluster, namespace: str) -> list[dict]:
    """The fence for ``namespace`` as a one-item list, or empty when off."""
    from constance import config

    if not config.AGENT_NETWORK_FENCE:
        return []
    plugin = getattr(cluster, "provider_plugin", None)
    return [
        render_agent_fence(
            namespace=namespace,
            provider_slug=str(getattr(plugin, "slug", "") or ""),
            allow_cidrs=str(config.AGENT_EGRESS_ALLOW_CIDRS or ""),
        )
    ]
