"""Exact reviewed Route53 alias operations; existing records are never adopted."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from _sdk.dns import AliasRouteObservation, ExactAliasRoute, ReviewedDnsHostname


class DomainRouteError(ValueError):
    pass


_ZONE = re.compile(r"Z[A-Z0-9]{1,31}\Z")
_HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _validate(route: ExactAliasRoute) -> None:
    if not _ZONE.fullmatch(route.zone_id) or not _ZONE.fullmatch(route.target_zone_id):
        raise DomainRouteError("INVALID_NATIVE_ZONE")
    if any(not _HOST.fullmatch(v) for v in (route.zone_name, route.hostname, route.target_dns_name)):
        raise DomainRouteError("INVALID_NATIVE_HOSTNAME")
    if route.hostname != route.zone_name and not route.hostname.endswith("." + route.zone_name):
        raise DomainRouteError("HOST_OUTSIDE_REVIEWED_ZONE")
    native_target = (
        route.target_kind == "ELB"
        and re.fullmatch(r"[a-z0-9-]{1,128}\.[a-z0-9-]{1,32}\.elb\.amazonaws\.com", route.target_dns_name)
    ) or (
        route.target_kind == "HTTP_API"
        and re.fullmatch(r"d-[a-z0-9]{1,64}\.execute-api\.[a-z0-9-]{1,32}\.amazonaws\.com", route.target_dns_name)
    )
    if not native_target:
        raise DomainRouteError("UNSUPPORTED_NATIVE_ALIAS_TARGET")


def _call(client: Any, method: str, checkpoint: Callable[[], None], **kwargs: Any) -> dict[str, Any]:
    checkpoint()
    try:
        result = getattr(client, method)(**kwargs)
    except Exception:
        raise DomainRouteError("NATIVE_DNS_UNAVAILABLE") from None
    checkpoint()
    if not isinstance(result, dict):
        raise DomainRouteError("NATIVE_DNS_MALFORMED")
    try:
        if len(json.dumps(result, default=str, ensure_ascii=True).encode()) > 131072:
            raise DomainRouteError("NATIVE_DNS_METADATA_LIMIT")
    except (TypeError, ValueError):
        raise DomainRouteError("NATIVE_DNS_METADATA_LIMIT") from None
    return result


def _zone(client: Any, route: ExactAliasRoute | ReviewedDnsHostname, checkpoint: Callable[[], None]) -> None:
    if (
        not _ZONE.fullmatch(route.zone_id)
        or any(not _HOST.fullmatch(v) for v in (route.zone_name, route.hostname))
        or (route.hostname != route.zone_name and not route.hostname.endswith("." + route.zone_name))
    ):
        raise DomainRouteError("INVALID_NATIVE_HOSTNAME")
    result = _call(client, "get_hosted_zone", checkpoint, Id=route.zone_id)
    zone = result.get("HostedZone")
    if not isinstance(zone, dict) or (
        zone.get("Id", "").removeprefix("/hostedzone/") != route.zone_id
        or zone.get("Name") != route.zone_name + "."
        or not isinstance(zone.get("Config"), dict)
        or zone["Config"].get("PrivateZone") is not False
    ):
        raise DomainRouteError("NATIVE_ZONE_CHANGED")


def _records(
    client: Any, route: ExactAliasRoute | ReviewedDnsHostname, checkpoint: Callable[[], None]
) -> list[dict[str, Any]]:
    result = _call(
        client,
        "list_resource_record_sets",
        checkpoint,
        HostedZoneId=route.zone_id,
        StartRecordName=route.hostname + ".",
        MaxItems="16",
    )
    records = result.get("ResourceRecordSets")
    if not isinstance(records, list) or len(records) > 16 or any(not isinstance(r, dict) for r in records):
        raise DomainRouteError("NATIVE_DNS_MALFORMED")
    matching = [r for r in records if r.get("Name") == route.hostname + "."]
    # A continuation at this hostname can hide another routing-policy record.
    if result.get("IsTruncated") not in (True, False):
        raise DomainRouteError("NATIVE_DNS_MALFORMED")
    if result["IsTruncated"] and (len(records) == 0 or records[-1].get("Name") == route.hostname + "."):
        raise DomainRouteError("NATIVE_DNS_INVENTORY_INCOMPLETE")
    return matching


def _record(route: ExactAliasRoute) -> dict[str, Any]:
    return {
        "Name": route.hostname + ".",
        "Type": "A",
        "AliasTarget": {
            "HostedZoneId": route.target_zone_id,
            "DNSName": route.target_dns_name + ".",
            "EvaluateTargetHealth": False,
        },
    }


def observe_alias(client: Any, route: ExactAliasRoute, *, checkpoint: Callable[[], None]) -> AliasRouteObservation:
    _validate(route)
    _zone(client, route, checkpoint)
    records = _records(client, route, checkpoint)
    addresses = [r for r in records if r.get("Type") in ("A", "AAAA", "CNAME")]
    exact = _record(route)
    _zone(client, route, checkpoint)
    return AliasRouteObservation(present=bool(addresses), matches=addresses == [exact])


def create_alias(
    client: Any,
    route: ExactAliasRoute,
    *,
    checkpoint: Callable[[], None],
    record_ack: Callable[[AliasRouteObservation], None],
) -> AliasRouteObservation:
    _validate(route)
    if not callable(record_ack):
        raise DomainRouteError("DURABLE_DNS_ACK_REQUIRED")
    _zone(client, route, checkpoint)
    if any(r.get("Type") in ("A", "AAAA", "CNAME") for r in _records(client, route, checkpoint)):
        raise DomainRouteError("NATIVE_HOST_ALREADY_ROUTED")
    _zone(client, route, checkpoint)
    checkpoint()
    try:
        result = client.change_resource_record_sets(
            HostedZoneId=route.zone_id,
            ChangeBatch={"Changes": [{"Action": "CREATE", "ResourceRecordSet": _record(route)}]},
        )
    except Exception:
        raise DomainRouteError("NATIVE_DNS_ACK_UNKNOWN") from None
    change = result.get("ChangeInfo")
    if not isinstance(change, dict) or not re.fullmatch(r"/change/C[A-Z0-9]{1,64}", str(change.get("Id", ""))):
        raise DomainRouteError("NATIVE_DNS_ACK_UNKNOWN")
    status = change.get("Status")
    if status not in ("PENDING", "INSYNC"):
        raise DomainRouteError("NATIVE_DNS_ACK_UNKNOWN")
    observation = AliasRouteObservation(False, False, change["Id"], status)
    record_ack(observation)
    checkpoint()
    return observation


def observe_hostname(client: Any, target: ReviewedDnsHostname, *, checkpoint: Callable[[], None]) -> bool:
    _zone(client, target, checkpoint)
    records = _records(client, target, checkpoint)
    _zone(client, target, checkpoint)
    return any(r.get("Type") in ("A", "AAAA", "CNAME") for r in records)
