"""AWS ALB IngressDriver (#30).

Spec ref: spec 23-provider-plugin-aws + _sdk/ingress.py.

The AWS Load Balancer Controller (LBC) translates standard k8s
Ingress resources annotated with `alb.ingress.kubernetes.io/*`
into Application Load Balancers. This driver renders those
annotations from the platform's hostname + TLS context.

The driver doesn't talk to AWS APIs directly for ALB ops — that's
the LBC's job. The driver's job is to emit the right Ingress YAML
shape so the LBC can do the rest. Updates/deletes mutate the
in-cluster Ingress via the ClusterDriver (#29).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.ingress import IngressDriver, Manifest


@dataclass(frozen=True)
class ALBConfig:
    """AWS-specific ALB config bound from cluster's plugin config."""

    region: str

    scheme: str = "internet-facing"
    """internet-facing | internal. internet-facing puts the ALB on
    public subnets; internal keeps it on private subnets only."""

    target_type: str = "ip"
    """ip | instance. 'ip' is required for Fargate + IPv6; 'instance'
    routes via NodePort on EC2-backed clusters."""

    listen_ports: tuple[int, ...] = (80, 443)
    """LBC translates this into the ALB's listeners. 80 + 443 is
    the default; operators add custom ports when needed."""

    ssl_redirect: bool = True
    """When True, port 80 returns a 301 redirect to https. When
    False, port 80 routes through (used for ACME HTTP-01)."""

    certificate_arn: str | None = None
    """ACM cert ARN for HTTPS termination. When None, the LBC
    discovers via the cert-manager CRD or DNS-validated SDS."""

    healthcheck_path: str = "/healthz"
    """Default healthcheck path. Per-app override via the manifest's
    ingress block."""

    cluster_driver: Any | None = None
    """Optional ClusterDriver instance for update/delete operations.
    When None, the driver renders manifests but can't mutate live
    state — caller passes one in for full lifecycle ops."""


class ALBIngressDriver(IngressDriver):
    def __init__(self, *, config: ALBConfig) -> None:
        self._config = config

    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        """Render the Ingress + (optional) IngressClassParams.

        ``tls_strategy`` values:
        - ``acm_dns_validated`` — ALB does TLS termination with
          ACM cert (HTTPS-only listener; the LBC reads the
          alb.ingress.kubernetes.io/certificate-arn annotation)
        - ``letsencrypt`` — cert-manager + ACME issues, ALB still
          terminates but the cert lives in a k8s Secret. LBC reads
          via the discovery annotation.
        - ``provided`` — operator pre-uploaded a cert; ARN must
          be in config.certificate_arn.
        """
        annotations = self._render_annotations(
            tls_strategy=tls_strategy,
        )

        rules = []
        for hostname in hostnames:
            rules.append({
                "host": hostname,
                "http": {
                    "paths": [{
                        "path": "/",
                        "pathType": "Prefix",
                        "backend": {
                            "service": {
                                "name": workload,
                                "port": {"number": 80},
                            },
                        },
                    }],
                },
            })

        ingress: Manifest = {
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
            "spec": {
                "ingressClassName": "alb",
                "rules": rules,
            },
        }

        # TLS block only when a cert source is configured. LBC reads
        # the alb.ingress.kubernetes.io/certificate-arn annotation
        # when present rather than the ingress.spec.tls block.
        if tls_strategy in ("acm_dns_validated", "provided"):
            ingress["spec"]["tls"] = [{
                "hosts": list(hostnames),
            }]

        return [ingress]

    def update_ingress_host(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
        new_hostname: str,
    ) -> None:
        """Mutate the live Ingress to the new host. Delegates to
        the ClusterDriver for the actual k8s patch."""
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "update_ingress_host requires cluster_driver in ALBConfig",
            )
        manifests = self.render_ingress(
            app=app,
            workload=workload,
            hostnames=[new_hostname],
            tls_strategy="acm_dns_validated",
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster, namespace, manifests,
        )
        if not result.ok:
            raise RuntimeError(
                f"failed to update ingress: {result.errors}",
            )

    def delete_ingress(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
    ) -> None:
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "delete_ingress requires cluster_driver in ALBConfig",
            )
        # Render a stub manifest just for the metadata; cluster
        # driver only needs name + namespace + kind to delete
        manifest: Manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{app}-{workload}",
                "namespace": namespace,
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            cluster, namespace, [manifest],
        )
        if not result.ok:
            raise RuntimeError(
                f"failed to delete ingress: {result.errors}",
            )

    # ---- internals ------------------------------------------------

    def _render_annotations(
        self, *, tls_strategy: str,
    ) -> dict[str, str]:
        """Translate config + tls strategy into LBC annotations."""
        annotations: dict[str, str] = {
            "alb.ingress.kubernetes.io/scheme": self._config.scheme,
            "alb.ingress.kubernetes.io/target-type": self._config.target_type,
            "alb.ingress.kubernetes.io/healthcheck-path": (
                self._config.healthcheck_path
            ),
            "alb.ingress.kubernetes.io/listen-ports": _render_listen_ports(
                self._config.listen_ports,
            ),
        }
        if self._config.ssl_redirect and 443 in self._config.listen_ports:
            annotations["alb.ingress.kubernetes.io/ssl-redirect"] = "443"
        if (
            tls_strategy in ("acm_dns_validated", "provided")
            and self._config.certificate_arn
        ):
            annotations[
                "alb.ingress.kubernetes.io/certificate-arn"
            ] = self._config.certificate_arn
        return annotations


def _render_listen_ports(ports: tuple[int, ...]) -> str:
    """ALB LBC expects a JSON-encoded list of {protocol: port} maps."""
    import json

    return json.dumps([
        {"HTTPS" if p == 443 else "HTTP": p}
        for p in ports
    ])
