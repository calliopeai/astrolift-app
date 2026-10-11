"""Exact ACM validation records: read matching foreign records, never overwrite."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from _sdk.domain_route import DomainValidationObservation
from aws.domain_route import _HOST, _ZONE, DomainRouteError, _call


def observe(
    client: Any, *, zone_id: str, zone: str, name: str, value: str, checkpoint: Callable[[], None]
) -> DomainValidationObservation:
    if (
        not isinstance(zone_id, str)
        or not _ZONE.fullmatch(zone_id)
        or not isinstance(zone, str)
        or not _HOST.fullmatch(zone)
        or not isinstance(name, str)
        or not re.fullmatch(r"_[a-f0-9]{1,64}", name.partition(".")[0])
        or not _HOST.fullmatch(name.partition(".")[2])
        or not (name.partition(".")[2] == zone or name.partition(".")[2].endswith("." + zone))
        or not isinstance(value, str)
        or not re.fullmatch(r"_[a-f0-9]{1,64}\.(?:[a-z0-9-]{1,128}\.)?acm-validations\.aws", value)
    ):
        raise DomainRouteError("INVALID_NATIVE_CERTIFICATE_VALIDATION")
    response = _call(client, "get_hosted_zone", checkpoint, Id=zone_id)
    native = response.get("HostedZone")
    if not isinstance(native, dict) or (
        native.get("Id", "").removeprefix("/hostedzone/") != zone_id
        or native.get("Name") != zone + "."
        or native.get("Config", {}).get("PrivateZone") is not False
    ):
        raise DomainRouteError("NATIVE_ZONE_CHANGED")
    response = _call(
        client, "list_resource_record_sets", checkpoint, HostedZoneId=zone_id, StartRecordName=name + ".", MaxItems="16"
    )
    rows = response.get("ResourceRecordSets")
    if not isinstance(rows, list) or len(rows) > 16 or any(not isinstance(r, dict) for r in rows):
        raise DomainRouteError("NATIVE_DNS_MALFORMED")
    if response.get("IsTruncated") not in (True, False) or (
        response["IsTruncated"] and (not rows or rows[-1].get("Name") == name + ".")
    ):
        raise DomainRouteError("NATIVE_DNS_INVENTORY_INCOMPLETE")
    matching = [r for r in rows if r.get("Name") == name + "."]
    expected = {"Name": name + ".", "Type": "CNAME", "TTL": 300, "ResourceRecords": [{"Value": value + "."}]}
    # An identical existing validation CNAME can validate ACM, but no returned
    # ownership or deletion entitlement is inferred from that coincidence.
    if any(type(r.get("TTL")) is not int or not 0 <= r["TTL"] <= 2147483647 for r in matching):
        raise DomainRouteError("NATIVE_DNS_MALFORMED")
    semantic = [dict(r, TTL=300) for r in matching]
    return DomainValidationObservation(bool(matching), semantic == [expected])


def create(
    client: Any,
    *,
    zone_id: str,
    zone: str,
    name: str,
    value: str,
    checkpoint: Callable[[], None],
    record_ack: Callable[[DomainValidationObservation], None],
) -> DomainValidationObservation:
    if not callable(record_ack):
        raise DomainRouteError("DURABLE_DNS_ACK_REQUIRED")
    observed = observe(client, zone_id=zone_id, zone=zone, name=name, value=value, checkpoint=checkpoint)
    if observed.present:
        raise DomainRouteError("NATIVE_VALIDATION_RECORD_ALREADY_EXISTS")
    checkpoint()
    try:
        result = client.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                "Changes": [
                    {
                        "Action": "CREATE",
                        "ResourceRecordSet": {
                            "Name": name + ".",
                            "Type": "CNAME",
                            "TTL": 300,
                            "ResourceRecords": [{"Value": value + "."}],
                        },
                    }
                ]
            },
        )
    except Exception:
        raise DomainRouteError("NATIVE_DNS_ACK_UNKNOWN") from None
    change = result.get("ChangeInfo") if isinstance(result, dict) else None
    if (
        not isinstance(change, dict)
        or not re.fullmatch(r"/change/C[A-Z0-9]{1,64}", str(change.get("Id", "")))
        or change.get("Status") not in ("PENDING", "INSYNC")
    ):
        raise DomainRouteError("NATIVE_DNS_ACK_UNKNOWN")
    observation = DomainValidationObservation(False, False, change["Id"], change["Status"])
    record_ack(observation)
    checkpoint()
    return observation


def observe_change(client: Any, *, change_id: str, checkpoint: Callable[[], None]) -> str:
    if not isinstance(change_id, str) or not re.fullmatch(r"/change/C[A-Z0-9]{1,64}", change_id):
        raise DomainRouteError("INVALID_NATIVE_DNS_CHANGE")
    change = _call(client, "get_change", checkpoint, Id=change_id).get("ChangeInfo")
    if (
        not isinstance(change, dict)
        or change.get("Id") != change_id
        or change.get("Status") not in ("PENDING", "INSYNC")
    ):
        raise DomainRouteError("ORIGINAL_NATIVE_DNS_CHANGE_UNKNOWN")
    return str(change["Status"])
