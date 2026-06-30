"""GCP IngressDriver — GKE Gateway API + GCE Ingress (#37).

Two variants:
- gce_ingress: classic GKE Ingress with kubernetes.io/ingress.class
- gateway_api: GKE Gateway with gke-l7-* GatewayClasses
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.ingress import IngressDriver, Manifest

SUPPORTED_VARIANTS = ("gce_ingress", "gateway_api")


@dataclass(frozen=True)
class GCPIngressConfig:
    variant: str = "gce_ingress"
    static_ip_name: str | None = None
    """Pre-allocated GCP global static IP. Operator creates via
    gcloud + this driver references it via annotation."""

    managed_cert_name: str | None = None
    """Pre-provisioned ManagedCertificate (from gcp/tls_managed.py)."""

    gateway_class: str = "gke-l7-global-external-managed"
    """For gateway_api variant. The other common option is
    gke-l7-regional-external-managed."""

    cluster_driver: Any | None = None


class GCPIngressDriver(IngressDriver):
    def __init__(self, *, config: GCPIngressConfig) -> None:
        if config.variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"variant {config.variant!r} not in {SUPPORTED_VARIANTS}",
            )
        self._config = config

    @driver_op(cloud="gcp", driver="ingress")
    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        port: int = 80,
    ) -> list[Manifest]:
        if self._config.variant == "gce_ingress":
            return self._render_gce(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                port=port,
            )
        if self._config.variant == "gateway_api":
            return self._render_gateway_api(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        raise ValueError(f"unhandled variant {self._config.variant!r}")

    @driver_op(cloud="gcp", driver="ingress")
    def update_ingress_host(
        self,
        cluster,
        namespace,
        app,
        workload,
        new_hostname,
    ):
        if self._config.cluster_driver is None:
            raise RuntimeError("update_ingress_host requires cluster_driver")
        manifests = self.render_ingress(
            app=app,
            workload=workload,
            hostnames=[new_hostname],
            tls_strategy="gcp_managed_cert",
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster,
            namespace,
            manifests,
        )
        if not result.ok:
            raise RuntimeError(f"update failed: {result.summary()}")

    @driver_op(cloud="gcp", driver="ingress")
    def delete_ingress(self, cluster, namespace, app, workload):
        if self._config.cluster_driver is None:
            raise RuntimeError("delete_ingress requires cluster_driver")
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
            cluster,
            namespace,
            stubs,
        )
        if result.errors:
            raise RuntimeError(f"delete failed: {result.summary()}")

    def _render_gce(
        self,
        *,
        app,
        workload,
        hostnames,
        tls_strategy,
        port: int = 80,
    ) -> list[Manifest]:
        annotations: dict[str, str] = {
            "kubernetes.io/ingress.class": "gce",
        }
        if self._config.static_ip_name:
            annotations["kubernetes.io/ingress.global-static-ip-name"] = self._config.static_ip_name
        if tls_strategy == "gcp_managed_cert" and self._config.managed_cert_name:
            annotations["networking.gke.io/managed-certificates"] = self._config.managed_cert_name

        rules = [
            {
                "host": h,
                "http": {
                    "paths": [
                        {
                            "path": "/*",
                            "pathType": "ImplementationSpecific",
                            "backend": {
                                "service": {
                                    "name": workload,
                                    "port": {"number": port},
                                },
                            },
                        }
                    ],
                },
            }
            for h in hostnames
        ]

        return [
            {
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
            }
        ]

    def _render_gateway_api(
        self,
        *,
        app,
        workload,
        hostnames,
        tls_strategy,
    ) -> list[Manifest]:
        return [
            {
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
                    "parentRefs": [
                        {
                            "name": "astrolift-gateway",
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
        ]
