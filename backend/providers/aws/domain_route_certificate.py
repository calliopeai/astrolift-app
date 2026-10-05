"""One-attempt reviewed ACM request and bounded exact certificate observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
from uuid import UUID

from _sdk.domain_route import DomainRouteCertificate
from aws.domain_route import _HOST, DomainRouteError, _call
from aws.tls_acm import _cert_covers_host


def _arn(value: str, *, region: str, account: str) -> None:
    if not re.fullmatch(rf"arn:aws:acm:{re.escape(region)}:{account}:certificate/[a-f0-9-]{{36}}", value):
        raise DomainRouteError("NATIVE_CERTIFICATE_UNKNOWN")


def request_certificate(
    client: Any,
    *,
    zone: str,
    region: str,
    account: str,
    operation_id: str,
    hostname: str = "",
    checkpoint: Callable[[], None],
    record_ack: Callable[[str], None],
) -> str:
    if not _HOST.fullmatch(zone) or len(zone) > 253 or not re.fullmatch(r"[0-9]{12}", account):
        raise DomainRouteError("INVALID_CERTIFICATE_TARGET")
    hostname = hostname or zone
    if not _HOST.fullmatch(hostname) or len(hostname) > 253 or not (hostname == zone or hostname.endswith("." + zone)):
        raise DomainRouteError("INVALID_CERTIFICATE_TARGET")
    try:
        operation = UUID(operation_id)
        if operation.int == 0:
            raise ValueError
        token = operation.hex
    except (ValueError, TypeError, AttributeError):
        raise DomainRouteError("INVALID_CERTIFICATE_OPERATION") from None
    if not callable(record_ack):
        raise DomainRouteError("DURABLE_CERTIFICATE_ACK_REQUIRED")
    checkpoint()
    try:
        response = client.request_certificate(
            DomainName=zone,
            SubjectAlternativeNames=sorted({"*." + zone, hostname} - {zone}),
            ValidationMethod="DNS",
            IdempotencyToken=token,
            Tags=[{"Key": "astrolift.io/domain-route-operation", "Value": operation_id}],
        )
    except Exception:
        raise DomainRouteError("NATIVE_CERTIFICATE_ACK_UNKNOWN") from None
    arn = response.get("CertificateArn") if isinstance(response, dict) else None
    if not isinstance(arn, str):
        raise DomainRouteError("NATIVE_CERTIFICATE_ACK_UNKNOWN")
    _arn(arn, region=region, account=account)
    record_ack(arn)
    checkpoint()
    return arn


def observe_certificate(
    client: Any, *, zone: str, hostname: str, region: str, account: str, arn: str, checkpoint: Callable[[], None]
) -> DomainRouteCertificate:
    _arn(arn, region=region, account=account)
    cert = _call(client, "describe_certificate", checkpoint, CertificateArn=arn).get("Certificate")
    if not isinstance(cert, dict) or cert.get("CertificateArn") != arn:
        raise DomainRouteError("NATIVE_CERTIFICATE_UNKNOWN")
    names = cert.get("SubjectAlternativeNames")
    if (
        not isinstance(names, list)
        or len(names) > 100
        or any(not isinstance(n, str) or len(n) > 253 for n in names)
        or not _cert_covers_host(hostname, names)
    ):
        raise DomainRouteError("NATIVE_CERTIFICATE_HOST_MISMATCH")
    status = cert.get("Status")
    if status not in (
        "PENDING_VALIDATION",
        "ISSUED",
        "FAILED",
        "VALIDATION_TIMED_OUT",
        "EXPIRED",
        "REVOKED",
        "INACTIVE",
    ):
        raise DomainRouteError("NATIVE_CERTIFICATE_UNKNOWN")
    if status == "ISSUED":
        now = datetime.now(UTC)
        if (
            not isinstance(cert.get("NotBefore"), datetime)
            or not isinstance(cert.get("NotAfter"), datetime)
            or not cert["NotBefore"] <= now < cert["NotAfter"]
        ):
            raise DomainRouteError("NATIVE_CERTIFICATE_EXPIRED")
    options = cert.get("DomainValidationOptions", [])
    if not isinstance(options, list) or len(options) > 100 or any(not isinstance(o, dict) for o in options):
        raise DomainRouteError("NATIVE_CERTIFICATE_MALFORMED")
    records: set[tuple[str, str]] = set()
    for option in options:
        record = option.get("ResourceRecord")
        if record is None:
            continue
        if not isinstance(record, dict) or record.get("Type") != "CNAME":
            raise DomainRouteError("NATIVE_CERTIFICATE_MALFORMED")
        name, value = record.get("Name"), record.get("Value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise DomainRouteError("NATIVE_CERTIFICATE_MALFORMED")
        name, value = name.rstrip(".").lower(), value.rstrip(".").lower()
        suffix = name.partition(".")[2]
        if (
            not re.fullmatch(r"_[a-f0-9]{1,64}", name.partition(".")[0])
            or not _HOST.fullmatch(suffix)
            or not (suffix == zone or suffix.endswith("." + zone))
            or not re.fullmatch(r"_[a-f0-9]{1,64}\.(?:[a-z0-9-]{1,128}\.)?acm-validations\.aws", value)
        ):
            raise DomainRouteError("NATIVE_CERTIFICATE_VALIDATION_OUTSIDE_ZONE")
        records.add((name, value))
    return DomainRouteCertificate(arn, status, tuple(sorted(records)))
