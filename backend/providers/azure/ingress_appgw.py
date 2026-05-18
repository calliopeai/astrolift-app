"""Azure Application Gateway IngressDriver / AGIC (#43).

Two variants:
- agic: classic Application Gateway Ingress Controller, annotated
  Ingress; AGIC reconciles AGW listener / backend pools.
- gateway_api: Gateway API HTTPRoute backed by an Azure Application
  Gateway via the AGIC-Gateway-API contrib operator (preview).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.ingress import IngressDriver, Manifest


SUPPORTED_VARIANTS = ("agic", "gateway_api")


@dataclass(frozen=True)
class AppGatewayIngressConfig:
    variant: str = "agic"
    """agic = annotated Ingress; gateway_api = HTTPRoute."""

    appgw_id: str | None = None
    """Resource ID of the Application Gateway. Used by AGIC bound
    via the AGW Subnet."""

    akv_secret_id: str | None = None
    """Key Vault secret ID for the TLS cert (PFX)."""

    cluster_driver: Any | None = None


class AzureAppGatewayIngressDriver(IngressDriver):
    def __init__(self, *, config: AppGatewayIngressConfig) -> None:
        if config.variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"variant {config.variant!r} not in {SUPPORTED_VARIANTS}",
            )
        self._config = config

    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        if self._config.variant == "agic":
            return self._render_agic(
                app=app, workload=workload,
                hostnames=hostnames, tls_strategy=tls_strategy,
            )
        if self._config.variant == "gateway_api":
            return self._render_gateway_api(
                app=app, workload=workload, hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        raise ValueError(f"unhandled variant {self._config.variant!r}")

    def update_ingress_host(
        self, cluster, namespace, app, workload, new_hostname,
    ):
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "update_ingress_host requires cluster_driver",
            )
        manifests = self.render_ingress(
            app=app, workload=workload,
            hostnames=[new_hostname],
            tls_strategy="akv_referenced",
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster, namespace, manifests,
        )
        if not result.ok:
            raise RuntimeError(f"update failed: {result.errors}")

    def delete_ingress(self, cluster, namespace, app, workload):
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "delete_ingress requires cluster_driver",
            )
        stubs = [
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "HTTPRoute",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
        ]
        result = self._config.cluster_driver.delete_manifests(
            cluster, namespace, stubs,
        )
        if result.errors:
            raise RuntimeError(f"delete failed: {result.errors}")

    def _render_agic(
        self, *, app, workload, hostnames, tls_strategy,
    ) -> list[Manifest]:
        annotations: dict[str, str] = {
            "kubernetes.io/ingress.class": "azure/application-gateway",
            "appgw.ingress.kubernetes.io/use-private-ip": "false",
        }
        if (
            tls_strategy == "akv_referenced"
            and self._config.akv_secret_id
        ):
            annotations[
                "appgw.ingress.kubernetes.io/appgw-ssl-certificate"
            ] = self._config.akv_secret_id
        rules = [{
            "host": h,
            "http": {
                "paths": [{
                    "path": "/*",
                    "pathType": "ImplementationSpecific",
                    "backend": {
                        "service": {
                            "name": workload,
                            "port": {"number": 80},
                        },
                    },
                }],
            },
        } for h in hostnames]
        return [{
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": {
                    "astrolift.io/app": app,
                    "astrolift.io/workload": workload,
                    "astrolift.io/managed-by": "platform",
                },
                "annotations": annotations,
            },
            "spec": {"rules": rules},
        }]

    def _render_gateway_api(
        self, *, app, workload, hostnames, tls_strategy,
    ) -> list[Manifest]:
        return [{
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": {
                    "astrolift.io/app": app,
                    "astrolift.io/workload": workload,
                    "astrolift.io/managed-by": "platform",
                },
            },
            "spec": {
                "parentRefs": [{
                    "name": "astrolift-gateway",
                    "namespace": "gateway-system",
                }],
                "hostnames": list(hostnames),
                "rules": [{
                    "matches": [
                        {"path": {"type": "PathPrefix", "value": "/"}},
                    ],
                    "backendRefs": [{
                        "name": workload, "port": 80,
                    }],
                }],
            },
        }]
