"""Encrypted org-owned DNS connections. All projections omit credentials."""

from datetime import datetime
from functools import wraps

import strawberry
from strawberry.types import Info

from astrolift_clusters import cloudflare_dns_oauth as oauth
from astrolift_clusters import dns_provider_connections as service
from astrolift_clusters.cloudflare_dns_transport import DnsConnectionError
from astrolift_clusters.models import DnsProviderConnection, ManagedDomain
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, PageType, failure, numbered_page, success
from core.decorators import tenant_scoped
from core.mutations import MutationResult, mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission


def dns_mutation(action):
    """Safe complete envelope before the audit wrapper can log unexpected bodies."""

    def decorate(fn):
        @wraps(fn)
        def safe(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except PermissionDenied:
                return failure("PERMISSION_DENIED", "Current DNS connection authority is unavailable.")
            except DnsConnectionError as exc:
                return failure(exc.reason, "DNS connection operation is unavailable.")
            except Exception:
                return failure("INTERNAL", "DNS connection operation is unavailable.")

        audited = mutation_audit(action=action)(safe)

        @wraps(audited)
        def complete(*args, **kwargs):
            result = audited(*args, **kwargs)
            if isinstance(result, MutationResult):
                return failure(str(result.errors[0].code), "DNS connection operation is unavailable.")
            return result

        return complete

    return decorate


@strawberry.type
class DnsProviderConnectionType:
    id: GUID
    version: int
    name: str
    provider: str
    auth_method: str
    state: str
    revocation_state: str
    verified_at: datetime | None
    expires_at: datetime | None
    dns_writes_supported: bool = False


def connection_type(row):
    state = (
        "EXPIRED"
        if row.state == "ACTIVE" and row.expires_at and row.expires_at <= service.timezone.now()
        else row.state
    )
    return DnsProviderConnectionType(
        id=GUID(str(row.guid)),
        version=row.version,
        name=row.name,
        provider=row.provider,
        auth_method=row.auth_method,
        state=state,
        revocation_state=row.revocation_state,
        verified_at=row.verified_at,
        expires_at=row.expires_at,
    )


@strawberry.type
class DnsProviderConnectionSupport:
    allowed: bool
    reason: str
    api_token_supported: bool
    oauth_configured: bool
    oauth_setup_reason: str
    dns_writes_supported: bool = False


@strawberry.type
class CloudflareDnsZone:
    id: str
    name: str
    account_id: str
    status: str
    name_servers: list[str]


@strawberry.type
class CloudflareDnsRecord:
    id: str
    name: str
    type: str
    content: str
    ttl: int
    proxied: bool
    priority: int | None


@strawberry.type
class CloudflareDnsZonesInventory:
    complete: bool
    reason: str
    items: list[CloudflareDnsZone]


@strawberry.type
class CloudflareDnsRecordsInventory:
    complete: bool
    reason: str
    items: list[CloudflareDnsRecord]


@strawberry.input
class CloudflareDnsConnectionInput:
    connection_id: GUID
    expected_version: int


@strawberry.input
class CloudflareDnsRecordsInput:
    connection_id: GUID
    expected_version: int
    zone_id: str
    zone_name: str


@strawberry.input
class ConnectCloudflareDnsTokenInput:
    organization_id: GUID
    name: str
    token: str


@strawberry.input
class BeginCloudflareDnsOAuthInput:
    organization_id: GUID
    name: str


@strawberry.type
class CloudflareDnsOAuthStart:
    authorization_url: str


@strawberry.input
class AttachCloudflareDnsZoneInput:
    domain_id: GUID
    expected_domain_version: int
    connection_id: GUID
    expected_connection_version: int
    zone_id: str
    zone_name: str


@strawberry.input
class RegisterCloudflareDnsZoneInput:
    connection_id: GUID
    expected_connection_version: int
    zone_id: str
    zone_name: str


@strawberry.type
class ManagedDomainDnsConnectionBinding:
    domain_id: GUID
    domain_version: int
    connection_id: GUID
    connection_version: int
    zone: CloudflareDnsZone
    verification_state: str
    verification_record_name: str | None
    verification_record_value: str | None
    dns_writes_supported: bool = False


@strawberry.type
class ManagedDomainDnsBindingReference:
    domain_id: GUID
    domain_version: int
    state: str
    connection_id: GUID | None
    connection_version: int | None
    current_connection_version: int | None
    zone_id: str | None
    zone_name: str
    can_verify: bool = False
    dns_writes_supported: bool = False


def binding_type(domain, zone):
    return ManagedDomainDnsConnectionBinding(
        domain_id=GUID(str(domain.guid)),
        domain_version=domain.version,
        connection_id=GUID(str(domain.dns_provider_connection.guid)),
        connection_version=domain.dns_provider_connection_version,
        zone=CloudflareDnsZone(**zone),
        verification_state=domain.verification_state,
        verification_record_name="_astrolift-challenge." + domain.zone if domain.verification_token else None,
        verification_record_value=domain.verification_token or None,
    )


def safe_reason(exc):
    return (
        exc.reason
        if isinstance(exc, DnsConnectionError)
        else "PERMISSION_DENIED"
        if isinstance(exc, PermissionDenied)
        else "UPSTREAM_UNAVAILABLE"
    )


@strawberry.type
class DnsProviderConnectionsQuery:
    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def dns_provider_domain_binding(
        self, info: Info, domain_id: GUID
    ) -> ManagedDomainDnsBindingReference | None:
        with service.admitted(info.context.request) as (org, _):
            row = ManagedDomain.objects.filter(guid=service.guid(domain_id), organization=org).first()
            if row is None:
                return None
            connection = DnsProviderConnection.objects.filter(
                pk=row.dns_provider_connection_id, organization=org, provider="CLOUDFLARE"
            ).first()
            state = "UNBOUND"
            if row.dns_provider_connection_id:
                state = (
                    "UNAVAILABLE"
                    if connection is None
                    else "DISCONNECTED"
                    if connection.state != "ACTIVE"
                    else "EXPIRED"
                    if connection.expires_at and connection.expires_at <= service.timezone.now()
                    else "CONNECTION_CHANGED"
                    if connection.version != row.dns_provider_connection_version
                    else "CURRENT"
                )
            can_verify = False
            if row.verification_state == ManagedDomain.VerificationState.PENDING:
                try:
                    with service.admitted(info.context.request, write=True):
                        can_verify = True
                except Exception:
                    pass
            return ManagedDomainDnsBindingReference(
                domain_id=GUID(str(row.guid)),
                domain_version=row.version,
                state=state,
                connection_id=GUID(str(connection.guid)) if connection else None,
                connection_version=row.dns_provider_connection_version if connection else None,
                current_connection_version=connection.version if connection else None,
                zone_id=row.dns_provider_zone_id if connection else None,
                zone_name=row.zone,
                can_verify=can_verify,
            )

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def dns_provider_connection_support(self, info: Info) -> DnsProviderConnectionSupport:
        try:
            with service.admitted(info.context.request, write=True):
                pass
        except Exception:
            return DnsProviderConnectionSupport(
                allowed=False,
                reason="PLATFORM_OPERATOR_REQUIRED",
                api_token_supported=False,
                oauth_configured=False,
                oauth_setup_reason="",
            )
        try:
            service.oauth_configuration()
            configured, reason = True, ""
        except DnsConnectionError as exc:
            configured, reason = False, exc.reason
        return DnsProviderConnectionSupport(
            allowed=True,
            reason="",
            api_token_supported=True,
            oauth_configured=configured,
            oauth_setup_reason=reason,
        )

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def dns_provider_connections_page(
        self, info: Info, page: int = 1, page_size: int = 20
    ) -> PageType[DnsProviderConnectionType]:
        with service.admitted(info.context.request) as (org, _):
            result = numbered_page(
                DnsProviderConnection.objects.filter(organization=org),
                order_by=["guid"],
                page=page,
                page_size=page_size,
                max_page_size=50,
            )
            return PageType(
                items=[connection_type(x) for x in result.rows],
                total_count=result.total_count,
                page=result.page,
                page_size=result.page_size,
            )

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def cloudflare_dns_zones(
        self, info: Info, input: CloudflareDnsConnectionInput
    ) -> CloudflareDnsZonesInventory:
        try:
            _, client, current = service.observe_connection(
                info.context.request, input.connection_id, input.expected_version
            )
            rows = client.zones()
            current()
            return CloudflareDnsZonesInventory(
                complete=True, reason="", items=[CloudflareDnsZone(**x) for x in rows]
            )
        except Exception as exc:
            return CloudflareDnsZonesInventory(complete=False, reason=safe_reason(exc), items=[])

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def cloudflare_dns_records(
        self, info: Info, input: CloudflareDnsRecordsInput
    ) -> CloudflareDnsRecordsInventory:
        try:
            _, client, current = service.observe_connection(
                info.context.request, input.connection_id, input.expected_version
            )
            rows = client.records(input.zone_id, input.zone_name)
            current()
            return CloudflareDnsRecordsInventory(
                complete=True, reason="", items=[CloudflareDnsRecord(**x) for x in rows]
            )
        except Exception as exc:
            return CloudflareDnsRecordsInventory(complete=False, reason=safe_reason(exc), items=[])


@strawberry.type
class DnsProviderConnectionsMutation:
    @strawberry.mutation
    @dns_mutation("dns.connection.connect")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def connect_cloudflare_dns_token(
        self, info: Info, input: ConnectCloudflareDnsTokenInput
    ) -> MutationResultType[DnsProviderConnectionType]:
        return success(
            connection_type(
                service.connect_token(info.context.request, input.organization_id, input.name, input.token)
            )
        )

    @strawberry.mutation
    @dns_mutation("dns.connection.retest")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def retest_dns_provider_connection(
        self, info: Info, input: CloudflareDnsConnectionInput
    ) -> MutationResultType[DnsProviderConnectionType]:
        return success(
            connection_type(service.retest(info.context.request, input.connection_id, input.expected_version))
        )

    @strawberry.mutation
    @dns_mutation("dns.connection.disconnect")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def disconnect_dns_provider_connection(
        self, info: Info, input: CloudflareDnsConnectionInput
    ) -> MutationResultType[DnsProviderConnectionType]:
        return success(
            connection_type(
                service.disconnect(info.context.request, input.connection_id, input.expected_version)
            )
        )

    @strawberry.mutation
    @dns_mutation("dns.connection.oauth.begin")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def begin_cloudflare_dns_o_auth(
        self, info: Info, input: BeginCloudflareDnsOAuthInput
    ) -> MutationResultType[CloudflareDnsOAuthStart]:
        return success(
            CloudflareDnsOAuthStart(
                authorization_url=oauth.begin(info.context.request, input.organization_id, input.name)
            )
        )

    @strawberry.mutation
    @dns_mutation("dns.connection.zone.attach")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def attach_cloudflare_dns_zone(
        self, info: Info, input: AttachCloudflareDnsZoneInput
    ) -> MutationResultType[ManagedDomainDnsConnectionBinding]:
        domain, zone = service.attach_zone(
            info.context.request,
            input.domain_id,
            input.expected_domain_version,
            input.connection_id,
            input.expected_connection_version,
            input.zone_id,
            input.zone_name,
        )
        return success(binding_type(domain, zone))

    @strawberry.mutation
    @dns_mutation("dns.connection.zone.register")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def register_cloudflare_dns_zone(
        self, info: Info, input: RegisterCloudflareDnsZoneInput
    ) -> MutationResultType[ManagedDomainDnsConnectionBinding]:
        domain, zone = service.register_zone(
            info.context.request,
            input.connection_id,
            input.expected_connection_version,
            input.zone_id,
            input.zone_name,
        )
        return success(binding_type(domain, zone))
