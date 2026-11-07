"""Permission-gated NVKE site metadata and write-only connection credentials."""

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from functools import wraps

import strawberry
from nvke.client import NvkeError
from nvke.contract import controller_spec, resource_schema, resources, upstream_revision
from nvke.inventory import collections, resource_actions
from nvke.native_workflows import workflows as native_workflows
from nvke.operations import action, actions
from strawberry.scalars import JSON
from strawberry.types import Info

from astrolift_clusters import nvke_diagnostics as diagnostics
from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_maintenance as maintenance
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_resource_operations as resource_operations
from astrolift_clusters import nvke_sites as service
from astrolift_clusters import nvke_temporal as native_temporal
from astrolift_clusters import nvke_workload_access as workload_access
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


@strawberry.type
class NvkeTemporalAuthorityType:
    id: GUID
    version: int
    site_id: GUID
    site_version: int
    address: str
    namespace: str
    task_queue: str
    server_name: str
    use_tls: bool
    is_enabled: bool
    timeout_seconds: int
    has_credentials: bool
    verified_at: datetime | None
    verification_code: str
    deleted_at: datetime | None


def temporal_authority_type(row):
    return NvkeTemporalAuthorityType(
        id=GUID(str(row.guid)),
        version=row.version,
        site_id=GUID(str(row.site.guid)),
        site_version=row.site.version,
        address=row.address,
        namespace=row.namespace,
        task_queue=row.task_queue,
        server_name=row.server_name,
        use_tls=row.use_tls,
        is_enabled=row.is_enabled,
        timeout_seconds=row.timeout_seconds,
        has_credentials=bool(row.credential_ciphertext),
        verified_at=row.verified_at,
        verification_code=row.verification_code,
        deleted_at=row.deleted_at,
    )


@strawberry.input
class NvkeTemporalCredentialsInput:
    api_key: str = ""
    ca_pem: str = ""
    client_certificate_pem: str = ""
    client_key_pem: str = ""


@strawberry.input
class ConfigureNvkeTemporalAuthorityInput:
    site_id: GUID
    expected_actor_id: int
    expected_site_version: int
    expected_version: int
    address: str
    namespace: str
    task_queue: str
    server_name: str = ""
    use_tls: bool = True
    timeout_seconds: int = 30
    credentials: NvkeTemporalCredentialsInput | None = None


@strawberry.input
class NvkeTemporalAuthorityReviewInput:
    site_id: GUID
    expected_actor_id: int
    expected_site_version: int
    expected_version: int


@strawberry.input
class SetNvkeTemporalAuthorityEnabledInput(NvkeTemporalAuthorityReviewInput):
    enabled: bool


@strawberry.type
class NvkeWorkloadAccessBindingType:
    id: GUID
    version: int
    site_id: GUID
    controller_connection_id: GUID
    source_operation_id: GUID
    cluster_id: GUID
    upstream_revision: str
    created_at: datetime
    deleted_at: datetime | None


def workload_access_type(row):
    return NvkeWorkloadAccessBindingType(
        id=GUID(str(row.guid)),
        version=row.version,
        site_id=GUID(str(row.site.guid)),
        controller_connection_id=GUID(str(row.controller_connection.guid)),
        source_operation_id=GUID(str(row.source_operation.guid)),
        cluster_id=GUID(str(row.cluster_id)),
        upstream_revision=row.upstream_revision,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


@strawberry.input
class EnrollNvkeWorkloadAccessInput:
    site_id: GUID
    expected_site_version: int
    expected_actor_id: int
    source_operation_id: GUID
    expected_source_version: int


@strawberry.input
class RevokeNvkeWorkloadAccessInput:
    site_id: GUID
    expected_site_version: int
    expected_actor_id: int
    binding_id: GUID
    expected_version: int


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
class NvkeResourceSourceType:
    operation_id: GUID
    version: int
    resource_key: str | None


@strawberry.type
class NvkeResourceEditorType:
    operation_key: str
    parameters_text: str | None
    preparation_action_key: str | None
    concurrency: str
    source: NvkeResourceSourceType


@strawberry.type
class NvkeOperationType:
    id: GUID
    version: int
    site_id: GUID
    request_id: str
    operation_key: str
    is_inventory: bool
    resource_source: NvkeResourceSourceType | None
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
    resource_source = row.authority.get("resource", {}).get("source")
    return NvkeOperationType(
        id=GUID(str(row.guid)),
        version=row.version,
        site_id=GUID(str(row.site.guid)),
        request_id=str(row.request_id),
        operation_key=row.operation_key,
        is_inventory=row.authority.get("inventory") is True,
        resource_source=NvkeResourceSourceType(
            operation_id=GUID(resource_source["operation_id"]),
            version=resource_source["version"],
            resource_key=resource_source["resource_key"],
        )
        if resource_source
        else None,
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
    operation_version: int
    site_id: GUID
    observed_at: datetime | None


@strawberry.type
class NvkeNativeWorkflowType:
    name: str
    input_type: str
    source: str
    permission: str
    input_schema: JSON


@strawberry.type
class NvkeContractType:
    revision: str
    actions: list[NvkeActionType]
    controller_openapi: JSON
    resources: list[NvkeResourceSchemaType]
    collections: list[NvkeCollectionType]
    native_workflows: list[NvkeNativeWorkflowType]


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
class RequestNvkeResourceOperationInput:
    site_id: GUID
    expected_site_version: int
    expected_actor_id: int
    request_id: str
    operation_key: str
    parameters_text: str
    source_operation_id: GUID
    expected_source_version: int
    source_resource_key: str | None = None
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
class NvkeDiagnosticCountType:
    name: str
    value: str | None


@strawberry.type
class NvkeDiagnosticFlagType:
    name: str
    value: bool | None


@strawberry.type
class NvkeDiagnosticConditionType:
    name: str
    value: str | None


@strawberry.type
class NvkeDiagnosticSummaryType:
    cluster_id: GUID
    kind: str
    configuration_id: str | None
    kubernetes_version: str | None
    requested_address: str | None
    requested_port: int | None
    counts: list[NvkeDiagnosticCountType]
    flags: list[NvkeDiagnosticFlagType]
    conditions: list[NvkeDiagnosticConditionType]


@strawberry.type
class NvkeDiagnosticItemType:
    key: str
    native_id: str | None
    name: str | None
    created_at: str | None
    is_current: bool | None
    reachable: bool | None
    has_valid_svid: bool | None
    server_address: str | None
    server_port: int | None
    svid_expires_at: str | None
    changed_fields: list[str]
    changed_field_count: str | None
    fields_omitted: str | None


@strawberry.type
class NvkeDiagnosticPageType:
    site_id: GUID
    operation_id: GUID
    operation_version: int
    observed_at: datetime | None
    summary: NvkeDiagnosticSummaryType
    items: list[NvkeDiagnosticItemType]
    next_cursor: str | None
    total_count: int


@strawberry.type
class NvkeCertificateRenewalObservationType:
    workflow_id: str | None
    state: str | None
    outcome: str | None
    forced: bool | None
    scheduled_for: str | None
    started_at: str | None
    completed_at: str | None
    failed_node_id: GUID | None
    phase: str | None
    retained_mutation_lock: bool | None


@strawberry.type
class NvkeEtcdKeyRotationObservationType:
    workflow_id: str | None
    state: str | None
    phase: str | None
    started_at: str | None
    last_progress_at: str | None
    completed_at: str | None
    failure_reason: str | None
    retained_mutation_lock: bool | None


@strawberry.type
class NvkeMaintenanceObservationType:
    site_id: GUID
    operation_id: GUID
    operation_version: int
    observed_at: datetime | None
    cluster_id: GUID
    next_certificate_renewal_due: str | None
    renewal_schedule_overdue_at_observation: bool | None
    mutation_lock_reported: bool | None
    mutation_lock_owner: str | None
    mutation_lock_started_at: str | None
    renewal: NvkeCertificateRenewalObservationType | None
    etcd_key_rotation: NvkeEtcdKeyRotationObservationType | None


@strawberry.type
class NvkeQuery:
    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_temporal_authority(
        self, info: Info, site_id: GUID
    ) -> NvkeTemporalAuthorityType | None:
        row = native_temporal.read(str(site_id))
        return temporal_authority_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_maintenance_observation(
        self, info: Info, site_id: GUID, operation_id: GUID, expected_actor_id: int, expected_version: int
    ) -> NvkeMaintenanceObservationType:
        row, data = maintenance.read(
            site_id=str(site_id),
            operation_id=str(operation_id),
            expected_actor_id=expected_actor_id,
            expected_version=expected_version,
        )
        return NvkeMaintenanceObservationType(
            site_id=GUID(str(row.site.guid)),
            operation_id=GUID(str(row.guid)),
            operation_version=row.version,
            observed_at=row.finished_at,
            **{
                **data,
                "renewal": NvkeCertificateRenewalObservationType(**data["renewal"])
                if data["renewal"] is not None
                else None,
                "etcd_key_rotation": NvkeEtcdKeyRotationObservationType(**data["etcd_key_rotation"])
                if data["etcd_key_rotation"] is not None
                else None,
            },
        )

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_workload_access_bindings(
        self, info: Info, site_id: GUID, after: str | None = None, limit: int = 50
    ) -> PageType[NvkeWorkloadAccessBindingType]:
        return keyset_page(
            workload_access.list_bindings(str(site_id)).select_related(
                "site", "controller_connection", "source_operation"
            ),
            cursor=after,
            limit=limit,
            cursor_scope=f"nvke-access:{service.authorized(Permission.NVKE_READ)[0].guid}:{site_id}",
        ).map(workload_access_type)

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_diagnostic_page(
        self,
        info: Info,
        site_id: GUID,
        operation_id: GUID,
        expected_actor_id: int,
        expected_version: int,
        after: str | None = None,
        limit: int = 50,
        search: str | None = None,
    ) -> NvkeDiagnosticPageType:
        row, summary, items, cursor, total = diagnostics.page(
            site_id=str(site_id),
            operation_id=str(operation_id),
            expected_actor_id=expected_actor_id,
            expected_version=expected_version,
            after=after,
            limit=limit,
            search=search or "",
        )
        return NvkeDiagnosticPageType(
            site_id=GUID(str(row.site.guid)),
            operation_id=GUID(str(row.guid)),
            operation_version=row.version,
            observed_at=row.finished_at,
            summary=NvkeDiagnosticSummaryType(
                **{
                    **summary,
                    "counts": [NvkeDiagnosticCountType(**value) for value in summary["counts"]],
                    "flags": [NvkeDiagnosticFlagType(**value) for value in summary["flags"]],
                    "conditions": [NvkeDiagnosticConditionType(**value) for value in summary["conditions"]],
                }
            ),
            items=[NvkeDiagnosticItemType(**item) for item in items],
            next_cursor=cursor,
            total_count=total,
        )

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_resource_editor(
        self,
        info: Info,
        site_id: GUID,
        expected_actor_id: int,
        operation_key: str,
        source_operation_id: GUID,
        expected_source_version: int,
        source_resource_key: str | None = None,
    ) -> MutationResultType[NvkeResourceEditorType]:
        reference = {
            "operation_id": str(source_operation_id),
            "version": expected_source_version,
            "resource_key": source_resource_key,
        }
        _source, _collection, selected, parameters, preparation, concurrency = resource_operations.editor(
            site_id=str(site_id),
            expected_actor_id=expected_actor_id,
            operation_key=operation_key,
            reference=reference,
        )
        return success(
            NvkeResourceEditorType(
                operation_key=selected.key,
                parameters_text=operations.canonical(parameters).decode() if parameters is not None else None,
                preparation_action_key=preparation,
                concurrency=concurrency,
                source=NvkeResourceSourceType(
                    operation_id=source_operation_id,
                    version=expected_source_version,
                    resource_key=source_resource_key,
                ),
            )
        )

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
            operation_version=row.version,
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
            native_workflows=[NvkeNativeWorkflowType(**item) for item in native_workflows()],
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
        action="nvke.temporal_configure", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def configure_nvke_temporal_authority(
        self, info: Info, input: ConfigureNvkeTemporalAuthorityInput
    ) -> MutationResultType[NvkeTemporalAuthorityType]:
        values = asdict(input)
        return success(temporal_authority_type(native_temporal.configure(**values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.temporal_enable", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def set_nvke_temporal_authority_enabled(
        self, info: Info, input: SetNvkeTemporalAuthorityEnabledInput
    ) -> MutationResultType[NvkeTemporalAuthorityType]:
        return success(temporal_authority_type(native_temporal.set_enabled(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.temporal_remove", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def remove_nvke_temporal_authority(
        self, info: Info, input: NvkeTemporalAuthorityReviewInput
    ) -> MutationResultType[NvkeTemporalAuthorityType]:
        return success(temporal_authority_type(native_temporal.set_enabled(**asdict(input), remove=True)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.temporal_verify", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def verify_nvke_temporal_authority(
        self, info: Info, input: NvkeTemporalAuthorityReviewInput
    ) -> MutationResultType[NvkeTemporalAuthorityType]:
        return success(temporal_authority_type(native_temporal.verify(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.workload_access_enroll",
        target=lambda root, info, input: ("nvke_site", str(input.site_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def enroll_nvke_workload_access(
        self, info: Info, input: EnrollNvkeWorkloadAccessInput
    ) -> MutationResultType[NvkeWorkloadAccessBindingType]:
        return success(workload_access_type(workload_access.enroll(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.workload_access_revoke",
        target=lambda root, info, input: ("nvke_site", str(input.site_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def revoke_nvke_workload_access(
        self, info: Info, input: RevokeNvkeWorkloadAccessInput
    ) -> MutationResultType[NvkeWorkloadAccessBindingType]:
        return success(workload_access_type(workload_access.revoke(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.resource_request", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @tenant_scoped()
    def request_nvke_resource_operation(
        self, info: Info, input: RequestNvkeResourceOperationInput
    ) -> MutationResultType[NvkeOperationType]:
        service.authorized(Permission(action(input.operation_key).permission))
        values = asdict(input)
        source = {
            "operation_id": str(values.pop("source_operation_id")),
            "version": values.pop("expected_source_version"),
            "resource_key": values.pop("source_resource_key"),
        }
        return success(operation_type(operations.reserve_text(**values, resource_source=source)))

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
