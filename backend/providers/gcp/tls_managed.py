"""GCP Google-Managed Cert TlsDriver (#38).

Spec ref: spec 23-provider-plugin-gcp + _sdk/tls.py.

GCP managed certs (the SslCertificate resource with type=MANAGED)
are auto-provisioned + auto-renewed by Google. The driver creates
the SslCertificate via Compute Engine API; the Ingress / Gateway
references it by name.

Pre-shared / self-managed certs (type=SELF_MANAGED) are out of
scope here — that's the operator's flow if they want to bring
their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.tls import Certificate, TlsDriver

from gcp._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class ManagedCertConfig:
    project_id: str
    cert_name_prefix: str = "astrolift"
    client: Any | None = None


class GCPManagedCertDriver(TlsDriver):
    def __init__(self, *, config: ManagedCertConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import compute_v1

            self._client = compute_v1.SslCertificatesClient()

    def ensure_certificate(
        self,
        domain: str,
        *,
        sans: list[str] | None = None,
        strategy: str = "letsencrypt",
    ) -> Certificate:
        if strategy not in ("gcp_managed_cert", "letsencrypt", "provided"):
            raise ValueError(
                f"unknown strategy {strategy!r} for GCP managed cert",
            )
        cert_name = self._cert_name(domain=domain)
        domains = [domain] + list(sans or [])
        try:
            from google.cloud.compute_v1 import (
                SslCertificate,
                SslCertificateManagedSslCertificate,
            )

            cert = SslCertificate(
                name=cert_name,
                description=f"astrolift cert for {domain}",
                type_="MANAGED",
                managed=SslCertificateManagedSslCertificate(
                    domains=domains,
                ),
            )
            operation = self._client.insert(
                project=self._config.project_id,
                ssl_certificate_resource=cert,
            )
            operation.result()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "AlreadyExists":
                # Idempotent — fetch + return
                return self.get_certificate(cert_name)
            raise map_api_error(exc) from exc
        return self.get_certificate(cert_name)

    def get_certificate(self, certificate_id: str) -> Certificate:
        try:
            cert = self._client.get(
                project=self._config.project_id,
                ssl_certificate=certificate_id,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(
                    f"certificate {certificate_id} not found",
                ) from exc
            raise map_api_error(exc) from exc

        managed = getattr(cert, "managed", None)
        domains = list(managed.domains) if managed else []
        status = "issued"
        if managed:
            status = (
                managed.status.lower()
                if hasattr(managed, "status")
                else "active"
            )
        return Certificate(
            id=certificate_id,
            domain=domains[0] if domains else "",
            sans=domains[1:],
            strategy="gcp_managed_cert",
            status=status,
            not_before=None,
            not_after=None,
        )

    def revoke_certificate(self, certificate_id: str) -> None:
        try:
            operation = self._client.delete(
                project=self._config.project_id,
                ssl_certificate=certificate_id,
            )
            operation.result()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(
                    f"certificate {certificate_id} not found",
                ) from exc
            raise map_api_error(exc) from exc

    def _cert_name(self, *, domain: str) -> str:
        clean = "".join(
            c if c.isalnum() or c == "-" else "-"
            for c in domain.lower()
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return f"{self._config.cert_name_prefix}-{clean.strip('-')[:50]}"
