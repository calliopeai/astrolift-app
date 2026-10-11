"""Read-only managed-domain observations; refresh never changes delegation."""

import datetime as dt
from enum import Enum

import strawberry
from strawberry.types import Info

from astrolift_clusters.scopes import cluster_catalog_org_scope, domain_org_scope
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.enum
class ManagedDomainCheckState(Enum):
    OK = "OK"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"
    UNSUPPORTED = "UNSUPPORTED"


@strawberry.enum
class ManagedDomainRecordType(Enum):
    A = "A"
    AAAA = "AAAA"
    CNAME = "CNAME"
    NS = "NS"
    SOA = "SOA"
    TXT = "TXT"
    MX = "MX"
    SRV = "SRV"
    CAA = "CAA"


@strawberry.enum
class ManagedDomainProbeTool(Enum):
    LOOKUP = "LOOKUP"
    DIG = "DIG"
    PING = "PING"
    TRACEROUTE = "TRACEROUTE"
    HTTPS = "HTTPS"


@strawberry.type
class ManagedDomainActions:
    can_create: bool
    can_delete: bool = False
    can_revalidate: bool = False
    can_update: bool = False
    can_set_defaults: bool = False
    default_update_reason: str = "DOMAIN_SELECTION_REQUIRED"
    defaults_fingerprint: str | None = None


@strawberry.type
class ManagedDomainDiagnosticCheck:
    key: str
    state: ManagedDomainCheckState
    perspective: str
    checked_at: dt.datetime
    reason: str
    expected: list[str]
    observed: list[str]


@strawberry.type
class ManagedDomainRecordGeoLocation:
    continent_code: str | None = None
    country_code: str | None = None
    subdivision_code: str | None = None


@strawberry.type
class ManagedDomainRecordCoordinates:
    latitude: str
    longitude: str


@strawberry.type
class ManagedDomainRecordGeoProximity:
    aws_region: str | None = None
    local_zone_group: str | None = None
    coordinates: ManagedDomainRecordCoordinates | None = None
    bias: int | None = None


@strawberry.type
class ManagedDomainRecordCidrRouting:
    collection_id: str
    location_name: str


@strawberry.enum
class ManagedDomainRoutingPolicy(Enum):
    SIMPLE = "SIMPLE"
    WEIGHTED = "WEIGHTED"
    LATENCY = "LATENCY"
    FAILOVER = "FAILOVER"
    GEOLOCATION = "GEOLOCATION"
    MULTIVALUE = "MULTIVALUE"
    GEOPROXIMITY = "GEOPROXIMITY"
    CIDR = "CIDR"
    UNKNOWN = "UNKNOWN"


@strawberry.type
class ManagedDomainRecordRouting:
    policy: ManagedDomainRoutingPolicy
    reason: str
    set_identifier: str | None = None
    weight: int | None = None
    region: str | None = None
    failover: str | None = None
    geo_location: ManagedDomainRecordGeoLocation | None = None
    geo_proximity: ManagedDomainRecordGeoProximity | None = None
    cidr: ManagedDomainRecordCidrRouting | None = None
    multi_value_answer: bool | None = None
    health_check_id: str | None = None
    traffic_policy_instance_id: str | None = None


@strawberry.type
class ManagedDomainDiagnosticRecord:
    name: str
    type: str
    ttl: int | None
    values: list[str]
    alias_target: str | None = None
    alias_zone_id: str | None = None
    evaluate_target_health: bool | None = None
    proxied: bool | None = None
    priority: int | None = None
    routing: ManagedDomainRecordRouting | None = None


@strawberry.type
class ManagedDomainProviderZone:
    state: ManagedDomainCheckState
    reason: str
    checked_at: dt.datetime
    zone_id: str | None
    zone_name: str | None
    private_zone: bool | None
    nameservers: list[str]
    records: list[ManagedDomainDiagnosticRecord]
    truncated: bool
    binding_source: str = "NONE"


@strawberry.type
class ManagedDomainRoute:
    app_id: GUID
    app_name: str
    app_slug: str
    environment_id: GUID
    environment_name: str
    recorded_url: str
    hostname: str
    cluster_id: GUID | None
    cluster_name: str | None
    cluster_slug: str | None
    ingress_class: str | None
    observed_state: ManagedDomainCheckState


@strawberry.type
class ManagedDomainDiagnostics:
    id: GUID
    version: int
    zone: str
    verification_state: str
    provision_state: str
    checked_at: dt.datetime
    checks: list[ManagedDomainDiagnosticCheck]
    provider_zone: ManagedDomainProviderZone
    routes: list[ManagedDomainRoute]
    routes_truncated: bool
    actions: ManagedDomainActions
    provision_cluster_id: GUID | None = None


@strawberry.type
class ManagedDomainProbe:
    state: ManagedDomainCheckState
    perspective: str
    checked_at: dt.datetime
    reason: str
    hostname: str
    tool: ManagedDomainProbeTool
    record_type: ManagedDomainRecordType
    values: list[str]
    public_address: str | None = None
    http_status: int | None = None
    tls_verified: bool | None = None
    latency_ms: float | None = None


@strawberry.type
class ManagedDomainDiagnosticQuery:
    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=domain_org_scope(Permission.PROVIDER_PLUGIN_READ, "domain_id")
    )
    @tenant_scoped()
    def astrolift_managed_domain_diagnostics(
        self,
        info: Info,
        domain_id: GUID,
        expected_version: int,
        hostname: str | None = None,
        record_type: ManagedDomainRecordType = ManagedDomainRecordType.NS,
    ) -> ManagedDomainDiagnostics | None:
        from astrolift_clusters.domain_diagnostics import diagnostics

        return diagnostics(info, domain_id, expected_version, hostname, record_type)

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=domain_org_scope(Permission.PROVIDER_PLUGIN_READ, "domain_id")
    )
    @tenant_scoped()
    def astrolift_managed_domain_probe(
        self,
        info: Info,
        domain_id: GUID,
        expected_version: int,
        hostname: str,
        tool: ManagedDomainProbeTool,
        record_type: ManagedDomainRecordType = ManagedDomainRecordType.A,
    ) -> ManagedDomainProbe | None:
        from astrolift_clusters.domain_diagnostics import probe

        return probe(info, domain_id, expected_version, hostname, tool, record_type)

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def astrolift_managed_domain_actions(self, info: Info) -> ManagedDomainActions:
        from astrolift_clusters.domain_diagnostics import actions

        return actions(info)
