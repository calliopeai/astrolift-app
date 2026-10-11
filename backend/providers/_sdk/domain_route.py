"""Native edge facts for an exact reviewed hostname, separate from DNS writes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainRouteEdge:
    account_id: str
    region: str
    alb_group: str
    load_balancer_arn: str
    dns_name: str
    canonical_zone_id: str
    ingress_uid: str
    certificate_arn: str
    certificate_status: str
    listener_certificate_present: bool


@dataclass(frozen=True)
class DomainRouteCertificate:
    arn: str
    status: str
    validation_records: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DomainValidationObservation:
    present: bool
    matches: bool
    change_id: str = ""
    change_status: str = "UNKNOWN"
