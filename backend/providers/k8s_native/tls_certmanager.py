"""cert-manager TlsDriver for k8s-native plugin (#50 + #9).

Emits cert-manager Certificate CRDs. The cluster's installed
ClusterIssuer (Let's Encrypt or self-signed) issues the cert
into a k8s Secret which the ingress reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.tls import Certificate, TlsDriver


@dataclass(frozen=True)
class CertManagerConfig:
    cluster_issuer: str = "letsencrypt-prod"
    """ClusterIssuer name. Operator pre-configures the issuer
    on the cluster; this driver just references it."""

    target_namespace: str = "default"
    """Where Certificate CRDs land. Production usually emits one
    Certificate per app namespace; the driver binds to the
    namespace at apply time via ClusterDriver."""

    cluster_driver: Any | None = None


# Map CRD strategy → cert-manager Issuer kind.
STRATEGY_TO_ISSUER = {
    "letsencrypt": "ClusterIssuer",
    "provided": "Issuer",
}


class CertManagerDriver(TlsDriver):
    def __init__(self, *, config: CertManagerConfig | None = None) -> None:
        self._config = config or CertManagerConfig()

    @driver_op(cloud="k8s_native", driver="tls", audit=True, sensitive_kind="tls.mint")
    def ensure_certificate(
        self,
        domain: str,
        *,
        sans: list[str] | None = None,
        strategy: str = "letsencrypt",
    ) -> Certificate:
        if strategy not in STRATEGY_TO_ISSUER:
            raise ValueError(
                f"unknown strategy {strategy!r} for cert-manager",
            )
        cert_name = self._cert_name(domain=domain)
        manifest = self._render_certificate(
            cert_name=cert_name,
            domain=domain,
            sans=sans or [],
            strategy=strategy,
        )
        if self._config.cluster_driver is not None:
            result = self._config.cluster_driver.apply_manifests(
                "default",
                self._config.target_namespace,
                [manifest],
            )
            if not result.ok:
                raise RuntimeError(
                    f"failed to apply Certificate: {result.summary()}",
                )

        # cert-manager fills in not_before/not_after asynchronously.
        # Return immediately with status=pending; caller polls
        # via get_certificate.
        return Certificate(
            id=cert_name,
            domain=domain,
            sans=list(sans or []),
            strategy=strategy,
            status="pending",
            not_before=None,
            not_after=None,
        )

    @driver_op(cloud="k8s_native", driver="tls")
    def get_certificate(self, certificate_id: str) -> Certificate:
        """Without a cluster_driver bound, returns a stub with
        status=unknown. With one, queries the actual Certificate
        CRD's status block."""
        if self._config.cluster_driver is None:
            return Certificate(
                id=certificate_id,
                domain="",
                sans=[],
                strategy="letsencrypt",
                status="unknown",
                not_before=None,
                not_after=None,
            )
        try:
            cert = self._config.cluster_driver._k8s("default").get(
                kind="Certificate",
                namespace=self._config.target_namespace,
                name=certificate_id,
            )
        except Exception:
            return Certificate(
                id=certificate_id,
                domain="",
                sans=[],
                strategy="letsencrypt",
                status="unknown",
                not_before=None,
                not_after=None,
            )
        spec = cert.get("spec", {})
        status = cert.get("status", {})
        return Certificate(
            id=certificate_id,
            domain=spec.get("commonName", ""),
            sans=spec.get("dnsNames", []) or [],
            strategy="letsencrypt",
            status=_summarize_conditions(status.get("conditions", [])),
            not_before=status.get("notBefore"),
            not_after=status.get("notAfter"),
        )

    @driver_op(cloud="k8s_native", driver="tls", audit=True, sensitive_kind="tls.revoke")
    def revoke_certificate(self, certificate_id: str) -> None:
        if self._config.cluster_driver is None:
            return  # render-only mode
        stub = {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {
                "name": certificate_id,
                "namespace": self._config.target_namespace,
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            "default",
            self._config.target_namespace,
            [stub],
        )
        if result.errors:
            raise RuntimeError(
                f"failed to delete Certificate: {result.summary()}",
            )

    # ---- internals ------------------------------------------------

    def _cert_name(self, *, domain: str) -> str:
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in domain.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:253]

    def _render_certificate(
        self,
        *,
        cert_name: str,
        domain: str,
        sans: list[str],
        strategy: str,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "secretName": f"{cert_name}-tls",
            "commonName": domain,
            "dnsNames": [domain, *list(sans)],
            "issuerRef": {
                "name": self._config.cluster_issuer,
                "kind": STRATEGY_TO_ISSUER[strategy],
                "group": "cert-manager.io",
            },
        }
        return {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {
                "name": cert_name,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/domain": domain.replace(
                        "*",
                        "wildcard",
                    ),
                },
            },
            "spec": spec,
        }

    def render_certificate(
        self,
        *,
        domain: str,
        sans: list[str] | None = None,
        strategy: str = "letsencrypt",
    ) -> dict[str, Any]:
        """Public render path for callers without a cluster_driver."""
        return self._render_certificate(
            cert_name=self._cert_name(domain=domain),
            domain=domain,
            sans=list(sans or []),
            strategy=strategy,
        )


def _summarize_conditions(conditions: list[dict[str, Any]]) -> str:
    """cert-manager exposes Ready / Issuing conditions; we
    project the most recent into a string the platform's UI uses."""
    for cond in reversed(conditions):
        if cond.get("type") == "Ready":
            if cond.get("status") == "True":
                return "issued"
            return cond.get("reason", "pending").lower()
    return "pending"
