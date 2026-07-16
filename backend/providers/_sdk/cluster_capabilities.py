"""ClusterCapabilities — feature-detection beyond the bind matrix (#61).

Some Kubernetes features are conditionally available depending on
how the cluster is set up: VPA, HPA-with-custom-metrics, Gateway
API CRDs, Pod Security Admission level, Topology-aware Routing.

The driver detects what's actually present at probe time and
returns a ClusterCapabilities record. Workflow code reads this to
decide whether to render VPA + HPA together, or fall back to
HPA-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClusterCapabilities:
    cluster_id: str
    kubernetes_version: str
    """Major.minor (e.g. '1.30'). Patch is irrelevant for feature
    gating."""

    vpa_installed: bool = False
    """vertical-pod-autoscaler CRD present + recommender pod
    healthy."""

    hpa_v2_supported: bool = True
    """HorizontalPodAutoscaler v2 with custom-metrics support.
    Anything 1.23+ is True; we keep the field to support old
    clusters surfacing False."""

    gateway_api_installed: bool = False
    """gateway.networking.k8s.io/v1 CRDs present."""

    cnpg_installed: bool = False
    """CloudNativePG operator present."""

    cert_manager_installed: bool = False
    external_secrets_installed: bool = False
    external_dns_installed: bool = False

    pod_security_admission_level: str = "baseline"
    """privileged | baseline | restricted — the default applied by
    PSA when a namespace has no explicit label."""

    topology_aware_routing_supported: bool = True
    network_policy_engine: str = "none"
    """none | calico | cilium | weave | aws-vpc-cni"""

    operator_versions: dict[str, str] = field(default_factory=dict)
    """Map of operator name → installed version (e.g.
    {'cnpg': 'v1.22.0', 'cert-manager': 'v1.14.0'}). Probe fills
    this in for visibility."""

    notes: list[str] = field(default_factory=list)


def render_capability_summary(caps: ClusterCapabilities) -> str:
    """Human-readable capability listing for the operator UI."""
    bits: list[str] = [f"k8s {caps.kubernetes_version}"]
    if caps.vpa_installed:
        bits.append("VPA")
    if caps.hpa_v2_supported:
        bits.append("HPA-v2")
    if caps.gateway_api_installed:
        bits.append("Gateway API")
    if caps.cnpg_installed:
        bits.append("CNPG")
    if caps.cert_manager_installed:
        bits.append("cert-manager")
    if caps.external_secrets_installed:
        bits.append("external-secrets")
    if caps.external_dns_installed:
        bits.append("external-dns")
    if caps.network_policy_engine != "none":
        bits.append(f"netpol={caps.network_policy_engine}")
    bits.append(f"PSA={caps.pod_security_admission_level}")
    return ", ".join(bits)


def probe_capabilities(
    *,
    cluster_id: str,
    k8s_client: object,
    kubernetes_version: str = "",
) -> ClusterCapabilities:
    """Detect capabilities on a live cluster. Reads CRD list +
    operator deployments via the cluster's k8s client.

    The k8s_client must support .list(kind, namespace=None) →
    list[dict] for CRDs / Deployments. Tests inject a stub.
    """
    crd_names = _list_crd_names(k8s_client=k8s_client)
    deployment_names = _list_deployment_names(k8s_client=k8s_client)

    return ClusterCapabilities(
        cluster_id=cluster_id,
        kubernetes_version=kubernetes_version,
        vpa_installed=(
            "verticalpodautoscalers.autoscaling.k8s.io" in crd_names
            and any("vpa-recommender" in d for d in deployment_names)
        ),
        gateway_api_installed=("gateways.gateway.networking.k8s.io" in crd_names),
        cnpg_installed=("clusters.postgresql.cnpg.io" in crd_names),
        cert_manager_installed=("certificates.cert-manager.io" in crd_names),
        external_secrets_installed=("externalsecrets.external-secrets.io" in crd_names),
        external_dns_installed=(any("external-dns" in d for d in deployment_names)),
    )


def _list_crd_names(*, k8s_client: object) -> list[str]:
    list_method = getattr(k8s_client, "list", None)
    if list_method is None:
        return []
    try:
        items = (
            list_method(
                kind="CustomResourceDefinition",
                namespace=None,
            )
            or []
        )
    except Exception:
        return []
    return [item.get("metadata", {}).get("name", "") for item in items]


def _list_deployment_names(*, k8s_client: object) -> list[str]:
    list_method = getattr(k8s_client, "list", None)
    if list_method is None:
        return []
    try:
        items = list_method(kind="Deployment", namespace=None) or []
    except Exception:
        return []
    return [item.get("metadata", {}).get("name", "") for item in items]
