"""DnsDriver protocol -- create/update/delete DNS records on a target zone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Record:
    zone: str
    name: str
    type: str
    value: str
    ttl: int


@dataclass(frozen=True)
class DnsRecord:
    """Operator-facing record snapshot returned by ``DnsDriver.list_records``.

    Distinct from :class:`Record` (the create/update DTO) because the
    observability surface needs propagation status — a runtime
    property the create call doesn't carry. Field order matches the
    DNS-card column order so consumers can iterate ``dataclasses.fields``
    if they ever want a generic table renderer.
    """

    name: str
    type: str
    value: str
    ttl: int
    propagation_status: str = "unknown"
    """One of ``"propagated"``, ``"pending"``, ``"unknown"``. Drivers that
    can't observe propagation cheaply (e.g. the cloud's API doesn't
    distinguish authoritative-write from worldwide-visibility) return
    ``"unknown"``; the operator-facing UI dots that as muted."""


class DnsDriver(Protocol):
    """Protocol for managing DNS records.

    Implementations: route53, cloud_dns, azure_dns, cloudflare, ns1,
    external_dns_proxy.
    """

    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record: ...

    def delete_record(self, zone: str, name: str, type: str) -> None: ...

    def list_records(self, zone: str) -> list[Record]: ...

    # ---- observability reads (default: not implemented) --------------
    #
    # Per #377, the operator-facing observability cards on the app detail
    # page need a read surface that's distinct from the existing
    # ``list_records`` (which is zone-scoped + uses the create/update
    # ``Record`` shape). New methods land here with a default that raises
    # NotImplementedError so non-AWS drivers compile + the FE can degrade
    # to a "not yet supported on this cloud" empty state. AWS overrides
    # in ``aws/dns_route53.py``.

    def list_records_for_app(
        self,
        zone_or_app: str,
    ) -> list[DnsRecord]:
        """Return the records relevant to the given zone or app slug.

        Drivers may interpret ``zone_or_app`` as either a DNS zone name
        (``"acme.platform.example"``) or an app slug — whichever the
        driver can resolve via its tag scheme. The default implementation
        raises :class:`UnsupportedOperationError` so non-AWS drivers stay
        shape-compatible without forcing every plugin to ship a stub.
        Resolvers translate this exception to a "not supported on this
        cloud" user-facing message (#619).
        """
        # Local import keeps the protocol module free of the runtime
        # exception class until call time. Circular-import safe.
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            f"dns.list_records_for_app({zone_or_app=}) not supported on this driver",
        )

    def provision_zone(self, zone: str) -> dict[str, Any]:
        """Provision a new hosted zone for the given domain. Returns zone metadata including the authoritative nameservers to set at the registrar."""
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            f"dns.provision_zone({zone=}) not supported on this driver",
        )

    def request_wildcard_cert(self, zone: str, zone_id: str) -> dict[str, Any]:
        """Request a wildcard cert for *.<zone> via the cloud cert service. Returns cert metadata and DNS validation records to write into the zone."""
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            f"dns.request_wildcard_cert({zone=}, {zone_id=}) not supported on this driver",
        )

    def poll_cert_status(self, zone: str, cert_id: str) -> dict[str, Any]:
        """Poll the issuance status of a cert by its cert_id. Returns status ('pending'/'issued'/'failed') and the final cert identifier once issued."""
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            f"dns.poll_cert_status({zone=}, {cert_id=}) not supported on this driver",
        )

    def revoke_cert(self, zone: str, cert_id: str) -> None:
        """Delete / revoke the cert identified by cert_id so a fresh one can be
        requested. Called by the reissue path when the operator clicks
        'Reissue certificate' to force re-validation.

        Implementations: AWS → ACM delete_certificate; GCP → delete managed
        cert; Azure → delete Key Vault cert; k8s-native → delete Certificate CR.
        """
        from _sdk import UnsupportedOperationError
        raise UnsupportedOperationError(
            f"dns.revoke_cert({zone=}, {cert_id=}) not supported on this driver",
        )
