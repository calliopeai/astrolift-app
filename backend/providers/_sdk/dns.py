"""DnsDriver protocol -- create/update/delete DNS records on a target zone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        raises so non-AWS drivers stay shape-compatible without forcing
        every plugin to ship a stub.
        """
        raise NotImplementedError("not implemented for this driver")
