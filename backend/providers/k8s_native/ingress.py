"""k8s-native IngressDriver — multi-variant (#49 + #8).

Supports the common in-cluster ingress controllers:
- nginx-ingress (host-based + path-based routing)
- Gateway API (HTTPRoute + Gateway, the upstream "next" model)
- Traefik (router CRDs)
- Kong (Ingress + KongPlugin CRDs)
- Istio Gateway / VirtualService

The driver dispatches per ``variant`` to the appropriate render
path. Mutations (update/delete) delegate to ClusterDriver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.ingress import IngressDriver, Manifest

SUPPORTED_VARIANTS = (
    "nginx_ingress",
    "gateway_api",
    "traefik",
    "kong",
    "istio_gateway",
)


@dataclass(frozen=True)
class K8sIngressConfig:
    variant: str = "nginx_ingress"
    """One of SUPPORTED_VARIANTS. Determines which annotations +
    CRDs the driver renders."""

    ingress_class_name: str = "nginx"
    """For nginx_ingress + traefik. Matches the ingress controller
    deployed on the cluster."""

    gateway_class_name: str = "istio"
    """For gateway_api + istio_gateway."""

    cert_manager_issuer: str = "letsencrypt-prod"
    """ClusterIssuer name for cert-manager-driven cert provisioning."""

    cluster_driver: Any | None = None


class K8sIngressDriver(IngressDriver):
    def __init__(self, *, config: K8sIngressConfig) -> None:
        if config.variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"variant {config.variant!r} not in {SUPPORTED_VARIANTS}",
            )
        self._config = config

    @driver_op(cloud="k8s_native", driver="ingress")
    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        if self._config.variant == "nginx_ingress":
            return self._render_nginx(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        if self._config.variant == "traefik":
            return self._render_nginx_style(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                ingress_class="traefik",
            )
        if self._config.variant == "gateway_api":
            return self._render_gateway_api(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        if self._config.variant == "kong":
            return self._render_nginx_style(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                ingress_class="kong",
            )
        if self._config.variant == "istio_gateway":
            return self._render_istio(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        raise ValueError(f"unhandled variant {self._config.variant!r}")

    @driver_op(cloud="k8s_native", driver="ingress")
    def update_ingress_host(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
        new_hostname: str,
    ) -> None:
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "update_ingress_host requires cluster_driver",
            )
        manifests = self.render_ingress(
            app=app,
            workload=workload,
            hostnames=[new_hostname],
            tls_strategy="letsencrypt",
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster,
            namespace,
            manifests,
        )
        if not result.ok:
            raise RuntimeError(
                f"failed to update ingress: {result.summary()}",
            )

    @driver_op(cloud="k8s_native", driver="ingress")
    def delete_ingress(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
    ) -> None:
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "delete_ingress requires cluster_driver",
            )
        # Delete via best-effort manifest stubs covering all
        # variants the driver could've rendered. ClusterDriver
        # treats not-found as "not_found" not error.
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
            {
                "apiVersion": "networking.istio.io/v1",
                "kind": "VirtualService",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
        ]
        result = self._config.cluster_driver.delete_manifests(
            cluster,
            namespace,
            stubs,
        )
        if result.errors:
            raise RuntimeError(
                f"failed to delete ingress: {result.summary()}",
            )

    # ---- per-variant renderers -----------------------------------

    def _render_nginx(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        return self._render_nginx_style(
            app=app,
            workload=workload,
            hostnames=hostnames,
            tls_strategy=tls_strategy,
            ingress_class=self._config.ingress_class_name,
        )

    def _render_nginx_style(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        ingress_class: str,
    ) -> list[Manifest]:
        """Generic Ingress shape works for nginx, traefik, kong."""
        annotations: dict[str, str] = {}
        if tls_strategy == "letsencrypt":
            annotations["cert-manager.io/cluster-issuer"] = self._config.cert_manager_issuer

        rules = [
            {
                "host": h,
                "http": {
                    "paths": [
                        {
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": workload,
                                    "port": {"number": 80},
                                },
                            },
                        }
                    ],
                },
            }
            for h in hostnames
        ]

        ingress: Manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
                "annotations": annotations,
            },
            "spec": {
                "ingressClassName": ingress_class,
                "rules": rules,
            },
        }
        if tls_strategy in ("letsencrypt", "provided"):
            ingress["spec"]["tls"] = [
                {
                    "hosts": list(hostnames),
                    "secretName": f"{app}-{workload}-tls",
                }
            ]
        return [ingress]

    def _render_gateway_api(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        """Gateway API HTTPRoute. Cluster owns the Gateway separately."""
        route: Manifest = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "parentRefs": [
                    {
                        "name": self._config.gateway_class_name,
                        "namespace": "gateway-system",
                    }
                ],
                "hostnames": list(hostnames),
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "backendRefs": [
                            {
                                "name": workload,
                                "port": 80,
                            }
                        ],
                    }
                ],
            },
        }
        return [route]

    def _render_istio(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        """Istio Gateway + VirtualService."""
        gateway: Manifest = {
            "apiVersion": "networking.istio.io/v1",
            "kind": "Gateway",
            "metadata": {
                "name": f"{app}-{workload}-gw",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "selector": {"istio": "ingressgateway"},
                "servers": [
                    {
                        "port": {
                            "number": 443 if tls_strategy != "off" else 80,
                            "protocol": "HTTPS" if tls_strategy != "off" else "HTTP",
                            "name": "https" if tls_strategy != "off" else "http",
                        },
                        "hosts": list(hostnames),
                        "tls": (
                            {
                                "mode": "SIMPLE",
                                "credentialName": f"{app}-{workload}-tls",
                            }
                            if tls_strategy != "off"
                            else None
                        ),
                    }
                ],
            },
        }
        # Drop None TLS field
        if gateway["spec"]["servers"][0]["tls"] is None:
            del gateway["spec"]["servers"][0]["tls"]

        virtualservice: Manifest = {
            "apiVersion": "networking.istio.io/v1",
            "kind": "VirtualService",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "hosts": list(hostnames),
                "gateways": [f"{app}-{workload}-gw"],
                "http": [
                    {
                        "route": [
                            {
                                "destination": {
                                    "host": workload,
                                    "port": {"number": 80},
                                },
                            }
                        ],
                    }
                ],
            },
        }
        return [gateway, virtualservice]


def _labels(*, app: str, workload: str) -> dict[str, str]:
    return {
        "astrolift.io/app": app,
        "astrolift.io/workload": workload,
        "astrolift.io/managed-by": "platform",
    }
