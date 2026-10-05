"""Private exact HTTP API domain port; configuration observations are not health."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class HttpApiRouteUnavailable(ValueError):
    """Fixed safe reason; no native payloads or credentials."""


@dataclass(frozen=True, slots=True)
class HttpApiRouteTarget:
    operation_id: str
    hostname: str
    account_id: str
    region: str
    partition: str
    api_id: str
    api_service_id: str
    faas_service_id: str
    function_arn: str
    certificate_arn: str = ""
    source_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class HttpApiRouteObservation:
    target: HttpApiRouteTarget
    api_endpoint: str
    route_id: str
    integration_id: str
    source_fingerprint: str
    stage: str = "$default"


@dataclass(frozen=True, slots=True)
class HttpApiDomainReceipt:
    hostname: str
    domain_name_arn: str
    regional_target: str
    hosted_zone_id: str
    certificate_arn: str
    state: str


@dataclass(frozen=True, slots=True)
class HttpApiMappingReceipt:
    hostname: str
    domain_name_arn: str
    mapping_id: str
    api_id: str
    stage: str
    mapping_key: str = ""


@dataclass(frozen=True, slots=True)
class HttpApiSubmission:
    operation_id: str
    nonce: str
    request_sha256: str


HttpApiAcknowledgement = HttpApiDomainReceipt | HttpApiMappingReceipt


@dataclass(frozen=True, slots=True)
class HttpApiEffectHooks:
    commit_sent: Callable[[dict[str, Any]], HttpApiSubmission]
    record_ack: Callable[[HttpApiSubmission, HttpApiAcknowledgement], None]


def http_api_request_sha256(payload: dict[str, Any]) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise HttpApiRouteUnavailable("HTTP_API_METADATA_INVALID") from None
    if len(raw) > 65536:
        raise HttpApiRouteUnavailable("HTTP_API_METADATA_BOUNDS")
    return hashlib.sha256(raw).hexdigest()
