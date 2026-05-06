"""Vanilla Kubernetes provider plugin manifest.

This plugin targets kind, minikube, k3d, k3s, and any standards-
compliant Kubernetes cluster without cloud-managed services. It
implements the minimal driver set needed for local development and
bare-metal deployments:

- ClusterDriver (kubectl / client-go)
- IngressDriver (nginx-ingress or traefik)
- LogStreamDriver (kubelet stream API)
- WorkloadIdentityDriver (projected SA tokens)

Managed services, DNS, TLS, and other capabilities are expected to
be composed from external controllers (cert-manager, external-dns,
CNPG, etc.) rather than this plugin.
"""

from _sdk.base import ProviderPlugin


class K8sNativeProviderPlugin:
    """Vanilla Kubernetes provider plugin -- stub."""

    def __init__(self) -> None:
        raise NotImplementedError("k8s_native provider plugin is not yet implemented")


PLUGIN = ProviderPlugin(
    id="k8s_native",
    display_name="Kubernetes (vanilla)",
    drivers={},
    managed_service_drivers={},
    config_schema={},
)
