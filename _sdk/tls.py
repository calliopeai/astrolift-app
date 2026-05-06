"""TlsDriver protocol -- provision and manage TLS certificates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Certificate:
    id: str
    domain: str
    sans: list[str]
    strategy: str
    status: str
    not_before: str | None
    not_after: str | None


class TlsDriver(Protocol):
    """Protocol for provisioning, retrieving, and revoking TLS certificates.

    Strategies: letsencrypt (cert-manager + ACME), acm_dns_validated (AWS),
    gcp_managed_cert, azure_managed_cert, provided (operator-uploaded).
    """

    def ensure_certificate(
        self,
        domain: str,
        *,
        sans: list[str] | None = None,
        strategy: str = "letsencrypt",
    ) -> Certificate: ...

    def get_certificate(self, certificate_id: str) -> Certificate: ...

    def revoke_certificate(self, certificate_id: str) -> None: ...
