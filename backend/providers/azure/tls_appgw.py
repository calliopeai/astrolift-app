"""Azure Application Gateway managed-cert TlsDriver (#44).

Two paths:
- App Gateway with Key Vault-backed cert: operator uploads PFX to
  Key Vault, App Gateway references it via SSL profile. Driver
  exposes the AKV secret URI to the ingress driver.
- Front Door / App Gateway managed certs: Azure auto-provisions
  + auto-renews when the gateway's frontend domain is verified.

This driver shells the Key Vault path (most common with AKS + AGIC)
and emits a Certificate stub for the Front Door managed-cert path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.tls import Certificate, TlsDriver

from azure._errors import NotFoundError, map_api_error


_SUPPORTED_STRATEGIES = (
    "azure_managed_cert",
    "akv_referenced",
    "letsencrypt",
    "provided",
)


@dataclass(frozen=True)
class AppGatewayTlsConfig:
    subscription_id: str
    resource_group: str
    vault_url: str
    """Key Vault URL where PFX certs land."""

    cert_name_prefix: str = "astrolift"
    secret_client: Any | None = None
    """SecretClient used to read AKV secret IDs."""

    cert_client: Any | None = None
    """CertificateClient — production CRUD for Key Vault certs."""


class AzureAppGatewayTlsDriver(TlsDriver):
    def __init__(self, *, config: AppGatewayTlsConfig) -> None:
        self._config = config
        self._secret_client = config.secret_client
        self._cert_client = config.cert_client

    @driver_op(cloud="azure", driver="tls", audit=True, sensitive_kind="tls.mint")
    def ensure_certificate(
        self,
        domain: str,
        *,
        sans: list[str] | None = None,
        strategy: str = "azure_managed_cert",
    ) -> Certificate:
        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(
                f"unknown strategy {strategy!r} for Azure App Gateway TLS",
            )
        cert_name = self._cert_name(domain=domain)
        if strategy == "azure_managed_cert":
            # Front-Door-style managed cert: no driver-side
            # provisioning — Azure auto-issues once the domain is
            # frontend-bound. Return a 'pending' marker.
            return Certificate(
                id=cert_name,
                domain=domain,
                sans=list(sans or []),
                strategy=strategy,
                status="pending",
                not_before=None,
                not_after=None,
            )
        if strategy == "akv_referenced" and self._cert_client is not None:
            try:
                cert = self._cert_client.get_certificate(cert_name)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "ResourceNotFoundError":
                    return Certificate(
                        id=cert_name,
                        domain=domain,
                        sans=list(sans or []),
                        strategy=strategy,
                        status="pending",
                        not_before=None,
                        not_after=None,
                    )
                raise map_api_error(exc) from exc
            return self._certificate_from_akv(
                cert=cert,
                domain=domain,
                sans=list(sans or []),
                strategy=strategy,
            )
        # letsencrypt + provided defer to operator-driven flows
        return Certificate(
            id=cert_name,
            domain=domain,
            sans=list(sans or []),
            strategy=strategy,
            status="pending",
            not_before=None,
            not_after=None,
        )

    @driver_op(cloud="azure", driver="tls")
    def get_certificate(self, certificate_id: str) -> Certificate:
        if self._cert_client is None:
            raise NotFoundError(
                f"certificate {certificate_id} (no cert_client wired)",
            )
        try:
            cert = self._cert_client.get_certificate(certificate_id)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"certificate {certificate_id} not found",
                ) from exc
            raise map_api_error(exc) from exc
        return self._certificate_from_akv(
            cert=cert,
            domain="",
            sans=[],
            strategy="akv_referenced",
        )

    @driver_op(cloud="azure", driver="tls", audit=True, sensitive_kind="tls.revoke")
    def revoke_certificate(self, certificate_id: str) -> None:
        if self._cert_client is None:
            raise NotFoundError(
                f"certificate {certificate_id} (no cert_client wired)",
            )
        try:
            poller = self._cert_client.begin_delete_certificate(
                certificate_id,
            )
            if hasattr(poller, "result"):
                poller.result()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"certificate {certificate_id} not found",
                ) from exc
            raise map_api_error(exc) from exc

    def _certificate_from_akv(
        self,
        *,
        cert: Any,
        domain: str,
        sans: list[str],
        strategy: str,
    ) -> Certificate:
        attrs = getattr(cert, "properties", None) or cert
        return Certificate(
            id=getattr(cert, "name", "") or "",
            domain=domain,
            sans=sans,
            strategy=strategy,
            status="issued",
            not_before=getattr(attrs, "not_before", None),
            not_after=getattr(attrs, "expires_on", None),
        )

    def _cert_name(self, *, domain: str) -> str:
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in domain.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        return f"{self._config.cert_name_prefix}-{clean.strip('-')[:80]}"
