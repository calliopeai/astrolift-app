"""IngressDriver protocol -- render ingress resources for a cluster's ingress controller."""

from __future__ import annotations

from typing import Any, Protocol

# A Manifest is a Kubernetes resource dict (apiVersion, kind, metadata, spec, ...).
Manifest = dict[str, Any]


class IngressDriver(Protocol):
    """Protocol for rendering, updating, and deleting ingress resources.

    The driver knows which annotations, CRDs, and resource shapes to emit
    for its ingress variant (nginx, traefik, alb, gateway-api, etc.).
    Tenant manifests are agnostic -- the control plane delegates to the
    driver at render time.

    Supported variants: nginx_ingress, traefik, aws_alb_controller,
    gcp_gce, azure_app_gateway, gateway_api, istio_gateway, linkerd.
    """

    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        port: int = 80,
    ) -> list[Manifest]: ...

    def update_ingress_host(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
        new_hostname: str,
    ) -> None: ...

    def delete_ingress(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
    ) -> None: ...
