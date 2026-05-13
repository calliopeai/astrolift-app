"""AWS Route53 DnsDriver (#31).

Spec ref: spec 23-provider-plugin-aws + _sdk/dns.py.

Zone resolution: callers pass ``zone`` as the DNS name (e.g.
``acme.platform.example``). This driver looks up the corresponding
HostedZoneId once + caches per-instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _sdk.dns import DnsDriver, Record

from aws._errors import NotFoundError, map_client_error


@dataclass
class Route53Config:
    region: str = "us-east-1"
    """Route53 is a global service but boto3 still wants a region."""


class Route53Driver(DnsDriver):
    def __init__(
        self,
        *,
        config: Route53Config | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config or Route53Config()
        if client is not None:
            self._r53 = client
        else:
            import boto3

            self._r53 = boto3.client(
                "route53", region_name=self._config.region,
            )
        self._zone_cache: dict[str, str] = {}

    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record:
        zone_id = self._resolve_zone(zone)
        fqdn = self._fqdn(name=name, zone=zone)
        try:
            self._r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Comment": "managed by astrolift",
                    "Changes": [{
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": fqdn,
                            "Type": type,
                            "TTL": ttl,
                            "ResourceRecords": [{"Value": value}],
                        },
                    }],
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        return Record(zone=zone, name=name, type=type, value=value, ttl=ttl)

    def delete_record(self, zone: str, name: str, type: str) -> None:
        zone_id = self._resolve_zone(zone)
        fqdn = self._fqdn(name=name, zone=zone)

        # Route53's DELETE requires the EXACT current record set;
        # fetch it first.
        try:
            response = self._r53.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=fqdn,
                StartRecordType=type,
                MaxItems="1",
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

        records = response.get("ResourceRecordSets", []) or []
        match = next(
            (
                rs for rs in records
                if rs.get("Name", "").rstrip(".") == fqdn.rstrip(".")
                and rs.get("Type") == type
            ),
            None,
        )
        if match is None:
            raise NotFoundError(
                f"record {name} ({type}) in zone {zone} not found",
            )

        try:
            self._r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Changes": [{
                        "Action": "DELETE",
                        "ResourceRecordSet": match,
                    }],
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    def list_records(self, zone: str) -> list[Record]:
        zone_id = self._resolve_zone(zone)
        try:
            paginator = self._r53.get_paginator(
                "list_resource_record_sets",
            )
            out: list[Record] = []
            for page in paginator.paginate(HostedZoneId=zone_id):
                for rs in page.get("ResourceRecordSets", []):
                    name = rs.get("Name", "").rstrip(".")
                    rtype = rs.get("Type", "")
                    ttl = rs.get("TTL", 0)
                    for r in rs.get("ResourceRecords", []) or []:
                        out.append(Record(
                            zone=zone,
                            name=self._strip_zone(fqdn=name, zone=zone),
                            type=rtype,
                            value=r.get("Value", ""),
                            ttl=ttl,
                        ))
            return out
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    # ---- internals ------------------------------------------------

    def _resolve_zone(self, zone: str) -> str:
        canonical = zone.rstrip(".") + "."
        if canonical in self._zone_cache:
            return self._zone_cache[canonical]
        try:
            response = self._r53.list_hosted_zones_by_name(
                DNSName=canonical, MaxItems="1",
            )
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

        zones = response.get("HostedZones", []) or []
        match = next(
            (z for z in zones if z.get("Name") == canonical),
            None,
        )
        if match is None:
            raise NotFoundError(f"hosted zone {zone} not found")
        zone_id = match["Id"].rsplit("/", 1)[-1]
        self._zone_cache[canonical] = zone_id
        return zone_id

    def _fqdn(self, *, name: str, zone: str) -> str:
        zone = zone.rstrip(".")
        if name == "" or name == "@":
            return f"{zone}."
        if name.endswith(zone):
            return name.rstrip(".") + "."
        return f"{name}.{zone}."

    def _strip_zone(self, *, fqdn: str, zone: str) -> str:
        zone = zone.rstrip(".")
        fqdn = fqdn.rstrip(".")
        if fqdn == zone:
            return "@"
        if fqdn.endswith("." + zone):
            return fqdn[: -(len(zone) + 1)]
        return fqdn
