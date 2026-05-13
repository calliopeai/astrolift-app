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
