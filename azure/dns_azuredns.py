"""Azure DNS DnsDriver (#44)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.dns import DnsDriver, Record

from azure._errors import NotFoundError, map_api_error


_TYPE_RECORD_FIELD = {
    "A": "a_records",
    "AAAA": "aaaa_records",
    "CNAME": "cname_record",
    "TXT": "txt_records",
    "MX": "mx_records",
    "NS": "ns_records",
    "SRV": "srv_records",
}


@dataclass(frozen=True)
class AzureDNSConfig:
    subscription_id: str
    resource_group: str
    client: Any | None = None


class AzureDNSDriver(DnsDriver):
    def __init__(self, *, config: AzureDNSConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.dns import DnsManagementClient

            self._client = DnsManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )

    def ensure_record(
        self,
        zone: str,
        name: str,
        type: str,
        value: str,
        *,
        ttl: int = 300,
    ) -> Record:
        params = self._record_params(type=type, value=value, ttl=ttl)
        try:
            self._client.record_sets.create_or_update(
                resource_group_name=self._config.resource_group,
                zone_name=zone,
                relative_record_set_name=self._relative_name(
                    name=name, zone=zone,
                ),
                record_type=type,
                parameters=params,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        return Record(zone=zone, name=name, type=type, value=value, ttl=ttl)

    def delete_record(self, zone: str, name: str, type: str) -> None:
        record_type = type
        try:
            self._client.record_sets.delete(
                resource_group_name=self._config.resource_group,
                zone_name=zone,
                relative_record_set_name=self._relative_name(
                    name=name, zone=zone,
                ),
                record_type=record_type,
            )
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"record {name} ({record_type}) in zone {zone} "
                    f"not found",
                ) from exc
            raise map_api_error(exc) from exc

    def list_records(self, zone: str) -> list[Record]:
        try:
            iterator = self._client.record_sets.list_by_dns_zone(
                resource_group_name=self._config.resource_group,
                zone_name=zone,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_api_error(exc) from exc
        out: list[Record] = []
        for rs in iterator:
            rtype = getattr(rs, "type", "").rsplit("/", 1)[-1]
            ttl = getattr(rs, "ttl", 0) or 0
            for value in self._extract_values(rs, rtype=rtype):
                out.append(Record(
                    zone=zone, name=getattr(rs, "name", "") or "@",
                    type=rtype, value=value, ttl=ttl,
                ))
        return out

    def _record_params(
        self, *, type: str, value: str, ttl: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"ttl": ttl}
        if type == "A":
            params["a_records"] = [{"ipv4_address": value}]
        elif type == "AAAA":
            params["aaaa_records"] = [{"ipv6_address": value}]
        elif type == "CNAME":
            params["cname_record"] = {"cname": value}
        elif type == "TXT":
            params["txt_records"] = [{"value": [value]}]
        else:
            raise ValueError(f"unsupported record type {type!r}")
        return params

    def _relative_name(self, *, name: str, zone: str) -> str:
        if name in ("", "@"):
            return "@"
        zone = zone.rstrip(".")
        if name.endswith(zone):
            stripped = name[: -(len(zone) + 1)].rstrip(".")
            return stripped or "@"
        return name

    def _extract_values(self, rs: Any, *, rtype: str) -> list[str]:
        if rtype == "A":
            return [r.ipv4_address for r in getattr(rs, "a_records", []) or []]
        if rtype == "AAAA":
            return [
                r.ipv6_address for r in getattr(rs, "aaaa_records", []) or []
            ]
        if rtype == "CNAME":
            cname = getattr(rs, "cname_record", None)
            return [cname.cname] if cname else []
        if rtype == "TXT":
            out: list[str] = []
            for r in getattr(rs, "txt_records", []) or []:
                out.extend(getattr(r, "value", []) or [])
            return out
        return []
