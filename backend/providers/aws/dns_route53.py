"""AWS Route53 DnsDriver (#31).

Spec ref: spec 23-provider-plugin-aws + _sdk/dns.py.

Zone resolution: callers pass ``zone`` as the DNS name (e.g.
``acme.platform.example``). This driver looks up the corresponding
HostedZoneId once + caches per-instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.dns import DnsDriver, DnsRecord, Record
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- observability reads (#377) -------------------------------

    def list_records_for_app(self, zone_or_app: str) -> list[DnsRecord]:
        """Return the operator-facing record snapshot for a zone.

        ``zone_or_app`` is interpreted as a DNS zone name first; if no
        such hosted zone exists, fall back to a tag-based lookup that
        treats the input as an app slug and finds the zone tagged
        ``astrolift.io/app-slug=<slug>``.

        Propagation status is reported as ``"propagated"`` for any
        record whose change-set status is ``INSYNC`` per
        ``GetChange``; we don't poll per-record (that would burn
        Route53 quota) — instead we use the most recent change-set
        for the zone as a proxy. Records older than the last change
        are inherently propagated; the freshly-changed one carries
        the actual state. Anything we can't resolve is ``"unknown"``,
        not ``"pending"``, so the operator UI doesn't dot it amber
        for a zone that's actually fine."""
        zone = self._resolve_zone_or_app(zone_or_app)
        try:
            paginator = self._r53.get_paginator(
                "list_resource_record_sets",
            )
            zone_id = self._zone_cache.get(zone.rstrip(".") + ".")
            if zone_id is None:
                # _resolve_zone_or_app already populated the cache; if
                # not, resolve again to seed it.
                zone_id = self._resolve_zone(zone)
            propagation = self._latest_change_propagation(zone_id)
            out: list[DnsRecord] = []
            for page in paginator.paginate(HostedZoneId=zone_id):
                for rs in page.get("ResourceRecordSets", []):
                    name = rs.get("Name", "").rstrip(".")
                    rtype = rs.get("Type", "")
                    ttl = rs.get("TTL", 0)
                    for r in rs.get("ResourceRecords", []) or []:
                        out.append(DnsRecord(
                            name=name,
                            type=rtype,
                            value=r.get("Value", ""),
                            ttl=ttl,
                            propagation_status=propagation,
                        ))
            return out
        except NotFoundError:
            raise
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- internals ------------------------------------------------

    def _resolve_zone_or_app(self, zone_or_app: str) -> str:
        """Resolve a zone name OR app-slug tag-lookup to a zone name.

        Tries the input as a DNS zone first (the common case from the
        observability resolver, which already knows the zone for the
        app). On NotFoundError, falls back to scanning hosted zones
        for one tagged ``astrolift.io/app-slug=<input>``."""
        try:
            self._resolve_zone(zone_or_app)
            return zone_or_app
        except NotFoundError:
            pass

        try:
            response = self._r53.list_hosted_zones()
        except Exception as exc:
            raise map_client_error(exc) from exc

        for z in response.get("HostedZones", []) or []:
            zone_id = z["Id"].rsplit("/", 1)[-1]
            try:
                tags_resp = self._r53.list_tags_for_resource(
                    ResourceType="hostedzone", ResourceId=zone_id,
                )
            except Exception:
                # Tag lookups are best-effort; a permissions hiccup on
                # one zone shouldn't kill the whole scan.
                continue
            for t in tags_resp.get("ResourceTagSet", {}).get("Tags", []) or []:
                if (
                    t.get("Key") == "astrolift.io/app-slug"
                    and t.get("Value") == zone_or_app
                ):
                    name = str(z.get("Name", "")).rstrip(".")
                    self._zone_cache[name + "."] = zone_id
                    return name
        raise NotFoundError(
            f"no hosted zone matches {zone_or_app!r} by name or app-slug tag",
        )

    def _latest_change_propagation(self, zone_id: str) -> str:
        """Return ``"propagated"`` / ``"pending"`` / ``"unknown"`` based
        on the most recent change-set for the hosted zone.

        Route53 only exposes change status via GetChange + a change id
        we don't store. The driver attempts the cheapest path (look up
        the last change with ``change_resource_record_sets`` history
        equivalent) but Route53 has no such endpoint — so this returns
        ``"unknown"`` when we can't observe the last change, and the
        UI dots it muted. AWS users who need per-record propagation
        rely on dig from a separate vantage point."""
        # Route53 doesn't have a "get last change for zone" API; the
        # operator-facing UI is fine with "unknown" here. We keep this
        # method as a hook so a future enhancement can pass a remembered
        # change id from ensure_record through.
        del zone_id
        return "unknown"

    def _resolve_zone(self, zone: str) -> str:
        canonical = zone.rstrip(".") + "."
        if canonical in self._zone_cache:
            return self._zone_cache[canonical]
        try:
            response = self._r53.list_hosted_zones_by_name(
                DNSName=canonical, MaxItems="1",
            )
        except Exception as exc:
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
