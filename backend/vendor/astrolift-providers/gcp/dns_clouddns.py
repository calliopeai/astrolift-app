"""GCP Cloud DNS DnsDriver (#38)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _sdk.dns import DnsDriver, Record

from gcp._errors import NotFoundError, map_api_error


@dataclass
class CloudDNSConfig:
    project_id: str
    client: Any | None = None
    _zone_cache: dict[str, str] = field(default_factory=dict)


class CloudDNSDriver(DnsDriver):
    def __init__(self, *, config: CloudDNSConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import dns

            self._client = dns.Client(project=config.project_id)

    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record:
        managed_zone = self._resolve_zone(zone=zone)
        fqdn = self._fqdn(name=name, zone=zone)
        try:
            record_set = managed_zone.resource_record_set(
                fqdn, type, ttl, [value],
            )
            changes = managed_zone.changes()
            # Delete existing record if any (idempotent UPSERT)
            for existing in managed_zone.list_resource_record_sets():
                if existing.name == fqdn and existing.record_type == type:
                    changes.delete_record_set(existing)
            changes.add_record_set(record_set)
            changes.create()
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        return Record(zone=zone, name=name, type=type, value=value, ttl=ttl)

    def delete_record(self, zone: str, name: str, type: str) -> None:
        managed_zone = self._resolve_zone(zone=zone)
        fqdn = self._fqdn(name=name, zone=zone)
        try:
            changes = managed_zone.changes()
            target = next(
                (
                    rs for rs in managed_zone.list_resource_record_sets()
                    if rs.name == fqdn and rs.record_type == type
                ),
                None,
            )
            if target is None:
                raise NotFoundError(
                    f"record {name} ({type}) in zone {zone} not found",
                )
            changes.delete_record_set(target)
            changes.create()
        except NotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc

    def list_records(self, zone: str) -> list[Record]:
        managed_zone = self._resolve_zone(zone=zone)
        try:
            out: list[Record] = []
            for rs in managed_zone.list_resource_record_sets():
                for value in rs.rrdatas or []:
                    out.append(Record(
                        zone=zone,
                        name=self._strip_zone(fqdn=rs.name, zone=zone),
                        type=rs.record_type,
                        value=value,
                        ttl=rs.ttl or 0,
                    ))
            return out
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc

    def _resolve_zone(self, *, zone: str) -> Any:
        canonical = zone.rstrip(".") + "."
        if canonical in self._config._zone_cache:
            zone_id = self._config._zone_cache[canonical]
            return self._client.zone(zone_id)
        try:
            for managed_zone in self._client.list_zones():
                if managed_zone.dns_name == canonical:
                    self._config._zone_cache[canonical] = managed_zone.name
                    return managed_zone
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        raise NotFoundError(f"hosted zone {zone} not found")

    def _fqdn(self, *, name: str, zone: str) -> str:
        zone = zone.rstrip(".")
        if name in ("", "@"):
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
