"""Permission-gated NVKE site metadata and write-only connection credentials."""

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from functools import wraps

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_sites as service
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, PageType, failure, keyset_page, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission


def nvke_errors(fn):
    @wraps(fn)
    def safe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PermissionDenied:
            return failure("PERMISSION_DENIED", "NVKE authority is unavailable")
        except service.NvkeSiteError as exc:
            return failure(exc.code, str(exc))
        except Exception:
            # Credential-bearing inputs and backend exception messages must not
            # escape into the audit decorator or GraphQL error diagnostics.
            return failure("INTERNAL", "NVKE configuration could not be saved")

    return safe


@strawberry.enum
class NvkeSurface(Enum):
    CONTROLLER = "controller"
    TENANT = "tenant"
    OPERATOR = "operator"
    DATASTORE = "datastore"


@strawberry.type
class NvkeConnectionType:
    id: GUID
    version: int
    surface: NvkeSurface
    endpoint: str
    is_enabled: bool
    allow_http: bool
    timeout_seconds: int
    has_credentials: bool
    verified_at: datetime | None
    verification_code: str


@strawberry.type
class NvkeSiteType:
    id: GUID
    version: int
    name: str
    slug: str
    organization_namespace: str
    is_active: bool
    upstream_revision: str
    connections: list[NvkeConnectionType]
    created_at: datetime
    updated_at: datetime


def connection_type(row):
    return NvkeConnectionType(
        id=GUID(str(row.guid)),
        version=row.version,
        surface=NvkeSurface(row.surface),
        endpoint=row.endpoint,
        is_enabled=row.is_enabled,
        allow_http=row.allow_http,
        timeout_seconds=row.timeout_seconds,
        has_credentials=bool(row.credential_ciphertext),
        verified_at=row.verified_at,
        verification_code=row.verification_code,
    )


def site_type(row):
    return NvkeSiteType(
        id=GUID(str(row.guid)),
        version=row.version,
        name=row.name,
        slug=row.slug,
        organization_namespace=row.organization_namespace,
        is_active=row.is_active,
        upstream_revision=row.upstream_revision,
        connections=[connection_type(connection) for connection in row.connections.all()],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@strawberry.input
class CreateNvkeSiteInput:
    name: str
    slug: str
    organization_namespace: str


@strawberry.input
class NvkeCredentialsInput:
    token: str = ""
    ca_pem: str = ""
    client_certificate_pem: str = ""
    client_key_pem: str = ""


@strawberry.input
class ConfigureNvkeConnectionInput:
    site_id: GUID
    expected_site_version: int
    surface: NvkeSurface
    expected_connection_version: int
    endpoint: str
    credentials: NvkeCredentialsInput | None = None
    allow_http: bool = False
    timeout_seconds: int = 30


@strawberry.input
class SetNvkeSiteEnabledInput:
    site_id: GUID
    expected_site_version: int
    enabled: bool


@strawberry.input
class RemoveNvkeSiteInput:
    site_id: GUID
    expected_site_version: int


@strawberry.type
class NvkeQuery:
    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_sites(
        self, info: Info, after: str | None = None, limit: int = 50
    ) -> PageType[NvkeSiteType]:
        return keyset_page(
            service.list_sites().prefetch_related("connections"),
            cursor=after,
            limit=limit,
            cursor_scope=f"nvke-sites:{service.authorized(Permission.NVKE_READ)[0].guid}",
        ).map(site_type)

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_site(self, info: Info, site_id: GUID) -> NvkeSiteType | None:
        try:
            return site_type(service.read_site(str(site_id)))
        except service.NvkeSiteError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise


@strawberry.type
class NvkeMutation:
    @strawberry.mutation
    @mutation_audit(action="nvke.site_create")
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def create_nvke_site(self, info: Info, input: CreateNvkeSiteInput) -> MutationResultType[NvkeSiteType]:
        return success(site_type(service.create_site(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.connection_configure", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def configure_nvke_connection(
        self, info: Info, input: ConfigureNvkeConnectionInput
    ) -> MutationResultType[NvkeSiteType]:
        service.put_connection(
            site_id=str(input.site_id),
            expected_site_version=input.expected_site_version,
            surface=input.surface.value,
            expected_connection_version=input.expected_connection_version,
            endpoint=input.endpoint,
            credentials=asdict(input.credentials) if input.credentials is not None else None,
            allow_http=input.allow_http,
            timeout_seconds=input.timeout_seconds,
        )
        # Configuration requires admin, not an additional read grant. Resolve
        # the same authorized org rather than calling the read-only API gate.
        org, _actor = service.authorized(Permission.NVKE_ADMIN)
        return success(site_type(service.site_row(str(input.site_id), org)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.site_enable", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def set_nvke_site_enabled(
        self, info: Info, input: SetNvkeSiteEnabledInput
    ) -> MutationResultType[NvkeSiteType]:
        return success(site_type(service.set_site_enabled(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.site_remove", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def remove_nvke_site(self, info: Info, input: RemoveNvkeSiteInput) -> MutationResultType[NvkeSiteType]:
        return success(site_type(service.remove_site(**asdict(input))))
