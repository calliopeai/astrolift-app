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


@dataclass(frozen=True)
class CertificateInfo:
    """Operator-facing cert snapshot returned by ``TlsDriver.list_certificates``.

    Distinct from :class:`Certificate` (the create/get DTO) because the
    observability surface needs days-until-expiry and renewal status —
    derived properties the create call doesn't carry. ``not_after`` is
    an ISO-8601 string, not a datetime, so the dataclass stays
    JSON-serializable (the GraphQL layer maps str → string in the
    schema without an extra coerce step)."""

    id: str
    hostname: str
    issuer: str
    not_after: str
    days_until_expiry: int
    renewal_status: str = "unknown"
    """One of ``"auto"``, ``"manual"``, ``"failed"``, ``"unknown"``.
    ACM with DNS-01 validation in place is ``"auto"``; an operator-
    uploaded BYO cert is ``"manual"``; a renewal that the controller
    surfaced an error on is ``"failed"``; anything else is
    ``"unknown"``. The UI dots renewal_status to colour the chip."""


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

    # ---- observability reads (default: not implemented) --------------
    #
    # See the same note on DnsDriver — additive, raises by default so
    # non-AWS drivers stay shape-compatible without forced stubs. AWS
    # overrides in ``aws/tls_acm.py``.

    def list_certificates(
        self,
        filter_hostname: str | None = None,
    ) -> list[CertificateInfo]:
        """Return the certs the driver knows about, optionally
        filtered to a hostname (substring match — the driver decides
        what "match" means; ACM matches against ``DomainName`` +
        ``SubjectAlternativeNames``). The default raises so non-AWS
        drivers stay shape-compatible."""
        raise NotImplementedError("not implemented for this driver")
