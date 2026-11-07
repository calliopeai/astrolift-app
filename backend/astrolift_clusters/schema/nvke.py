"""Permission-gated NVKE site metadata and write-only connection credentials."""

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from functools import wraps

import strawberry
from nvke.client import NvkeError
from nvke.contract import controller_spec, resource_schema, resources, upstream_revision
from nvke.inventory import collections, resource_actions
from nvke.operations import actions
from strawberry.scalars import JSON
from strawberry.types import Info

from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as service
from astrolift_clusters.models import NvkeOperation
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
        except (service.NvkeSiteError, NvkeError) as exc:
            return failure(exc.code, str(exc))
        except Exception:
            # Credential-bearing inputs and backend exception messages must not
            # escape into the audit decorator or GraphQL error diagnostics.
            return failure("INTERNAL", "NVKE request could not be completed")

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


@strawberry.enum
class NvkeOperationStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RECONCILING = "reconciling"
    AWAITING_READY = "awaiting_ready"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


@strawberry.type
class NvkeOperationType:
    id: GUID
    version: int
    site_id: GUID
    request_id: str
    operation_key: str
    is_inventory: bool
    status: NvkeOperationStatus
    stage: str
    generation: int
    error_code: str
    error_message: str
    cancellation_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    deadline: datetime
    created_at: datetime
    updated_at: datetime


def operation_type(row):
    return NvkeOperationType(
        id=GUID(str(row.guid)),
        version=row.version,
        site_id=GUID(str(row.site.guid)),
        request_id=str(row.request_id),
        operation_key=row.operation_key,
        is_inventory=row.authority.get("inventory") is True,
        status=NvkeOperationStatus(row.status),
        stage=row.stage,
        generation=row.generation,
        error_code=row.error_code,
        error_message=row.error_message,
        cancellation_requested_at=row.cancellation_requested_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        deadline=row.deadline,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@strawberry.type
class NvkeActionType:
    key: str
    surface: NvkeSurface
    verb: str
    resource_kind: str
    mutates: bool
    permission: str
    path: str


@strawberry.type
class NvkeResourceSchemaType:
    kind: str
    namespaced: bool
    schema: JSON


def action_type(item):
    return NvkeActionType(
        key=item.key,
        surface=NvkeSurface(item.surface),
        verb=item.verb,
        resource_kind=item.kind or (item.http.resource if item.http else ""),
        mutates=item.mutates,
        permission=item.permission,
        path=item.http.path if item.http else "",
    )


@strawberry.type
class NvkeCollectionType:
    key: str
    surface: NvkeSurface
    resource_kind: str
    namespaced: bool
    permission: str
    default_parameters_text: str
    actions: list[NvkeActionType]


def collection_type(item):
    parameters = (
        {"page_size": 500}
        if item.surface == "datastore"
        else {"query": {"pageSize": 500}}
        if item.surface != "controller"
        else {}
    )
    return NvkeCollectionType(
        key=item.key,
        surface=NvkeSurface(item.surface),
        resource_kind=item.kind,
        namespaced=item.namespaced,
        permission=item.permission,
        default_parameters_text=operations.canonical(parameters).decode(),
        actions=[action_type(selected) for selected in resource_actions(item)],
    )


@strawberry.type
class NvkeNativeConditionType:
    name: str
    status: str


@strawberry.type
class NvkeInventoryItemType:
    key: str
    identity: str
    name: str
    uid: str
    namespace: str
    resource_version: str
    generation: str
    kubernetes_version: str
    desired_count: str
    conditions: list[NvkeNativeConditionType]
    conditions_omitted: int


@strawberry.type
class NvkeInventoryPageType:
    items: list[NvkeInventoryItemType]
    next_cursor: str | None
    total_count: int
    operation_id: GUID
    observed_at: datetime | None
    collection: NvkeCollectionType
    resource_version: str
    native_page_count: int


def inventory_item_type(item):
    return NvkeInventoryItemType(
        **{
            **asdict(item),
            "conditions": [
                NvkeNativeConditionType(name=name, status=status) for name, status in item.conditions
            ],
        }
    )


@strawberry.type
class NvkeInventoryResourceType:
    item: NvkeInventoryItemType
    collection: NvkeCollectionType
    operation_id: GUID
    site_id: GUID
    observed_at: datetime | None


@strawberry.type
class NvkeContractType:
    revision: str
    actions: list[NvkeActionType]
    controller_openapi: JSON
    resources: list[NvkeResourceSchemaType]
    collections: list[NvkeCollectionType]


@strawberry.input
class RequestNvkeOperationInput:
    site_id: GUID
    expected_site_version: int
    request_id: str
    operation_key: str
    parameters: JSON
    timeout_seconds: int = 7200


@strawberry.input
class RequestNvkeOperationTextInput:
    expected_actor_id: int
    site_id: GUID
    expected_site_version: int
    request_id: str
    operation_key: str
    parameters_text: str
    timeout_seconds: int = 7200


@strawberry.input
class CancelNvkeOperationInput:
    operation_id: GUID


@strawberry.input
class ResumeNvkeOperationInput:
    operation_id: GUID
    expected_version: int
    timeout_seconds: int = 7200


@strawberry.type
class NvkeQuery:
    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_inventory_page(
        self,
        info: Info,
        operation_id: GUID,
        expected_actor_id: int,
        after: str | None = None,
        limit: int = 50,
        search: str | None = None,
    ) -> NvkeInventoryPageType:
        row, selected, items, cursor, total, data = inventory.page(
            operation_id=str(operation_id),
            expected_actor_id=expected_actor_id,
            after=after,
            limit=limit,
            search=search or "",
        )
        return NvkeInventoryPageType(
            items=[inventory_item_type(item) for item in items],
            next_cursor=cursor,
            total_count=total,
            operation_id=GUID(str(row.guid)),
            observed_at=row.finished_at,
            collection=collection_type(selected),
            resource_version=data["resource_version"],
            native_page_count=data["native_page_count"],
        )

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_inventory_resource(
        self, info: Info, operation_id: GUID, expected_actor_id: int, resource_key: str
    ) -> NvkeInventoryResourceType:
        row, selected, item, _data, _index = inventory.resource(
            operation_id=str(operation_id), expected_actor_id=expected_actor_id, resource_key=resource_key
        )
        return NvkeInventoryResourceType(
            item=inventory_item_type(item),
            collection=collection_type(selected),
            operation_id=GUID(str(row.guid)),
            site_id=GUID(str(row.site.guid)),
            observed_at=row.finished_at,
        )

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_inventory_document_text(
        self, info: Info, operation_id: GUID, expected_actor_id: int, resource_key: str
    ) -> MutationResultType[str]:
        return success(
            inventory.document(
                operation_id=str(operation_id), expected_actor_id=expected_actor_id, resource_key=resource_key
            )
        )

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_request(self, info: Info, site_id: GUID, request_id: str) -> NvkeOperationType | None:
        try:
            row = operations.read_request(site_id=str(site_id), request_id=request_id)
            return operation_type(row) if row is not None else None
        except service.NvkeSiteError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_contract(self, info: Info) -> NvkeContractType:
        service.authorized(Permission.NVKE_READ)
        return NvkeContractType(
            revision=upstream_revision(),
            actions=[
                NvkeActionType(
                    key=item.key,
                    surface=NvkeSurface(item.surface),
                    verb=item.verb,
                    resource_kind=item.kind or (item.http.resource if item.http else ""),
                    mutates=item.mutates,
                    permission=item.permission,
                    path=item.http.path if item.http else "",
                )
                for item in actions()
            ],
            controller_openapi=controller_spec(),
            resources=[
                NvkeResourceSchemaType(
                    kind=item.kind, namespaced=item.namespaced, schema=resource_schema(item.kind)
                )
                for item in resources()
            ],
            collections=[collection_type(item) for item in collections()],
        )

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operations(
        self, info: Info, site_id: GUID, after: str | None = None, limit: int = 50
    ) -> PageType[NvkeOperationType]:
        site = service.read_site(str(site_id))
        return keyset_page(
            NvkeOperation.objects.select_related("site").filter(
                site=site, organization_id=site.organization_id
            ),
            cursor=after,
            limit=limit,
            cursor_scope=f"nvke-operations:{site.guid}",
        ).map(operation_type)

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operation(self, info: Info, operation_id: GUID) -> NvkeOperationType | None:
        try:
            return operation_type(operations.read(str(operation_id)))
        except NvkeError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operation_result(self, info: Info, operation_id: GUID) -> MutationResultType[JSON]:
        return success(operations.result(str(operation_id)))

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operation_result_text(self, info: Info, operation_id: GUID) -> MutationResultType[str]:
        return success(
            operations.canonical(operations.result(str(operation_id)), limit=64 * 1024 * 1024).decode("utf-8")
        )

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
        except (service.NvkeSiteError, NvkeError) as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise


@strawberry.type
class NvkeMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.inventory_request", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @tenant_scoped()
    def request_nvke_inventory(
        self, info: Info, input: RequestNvkeOperationTextInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(operations.reserve_text(**asdict(input), inventory=True)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operation_request_text",
        target=lambda root, info, input: ("nvke_site", str(input.site_id)),
    )
    @nvke_errors
    @tenant_scoped()
    def request_nvke_operation_text(
        self, info: Info, input: RequestNvkeOperationTextInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(operations.reserve_text(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operation_request", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @tenant_scoped()
    def request_nvke_operation(
        self, info: Info, input: RequestNvkeOperationInput
    ) -> MutationResultType[NvkeOperationType]:
        # The catalog selects read/manage/admin for the exact operation. A
        # blanket manage gate here would authorize operator writes incorrectly.
        return success(operation_type(operations.reserve(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operation_cancel",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @tenant_scoped()
    def cancel_nvke_operation(
        self, info: Info, input: CancelNvkeOperationInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(operations.request_cancel(str(input.operation_id))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operation_resume",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @tenant_scoped()
    def resume_nvke_operation(
        self, info: Info, input: ResumeNvkeOperationInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(operations.resume(**asdict(input))))

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
