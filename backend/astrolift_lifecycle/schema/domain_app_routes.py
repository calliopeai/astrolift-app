"""Typed reviewed managed-domain to app routing contract (#2299)."""

from __future__ import annotations

from datetime import datetime

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class ManagedDomainAppRouteTargetInput:
    domain_id: GUID
    expected_domain_version: int
    environment_id: GUID
    expected_environment_version: int
    workload_id: GUID
    hostname: str


@strawberry.input
class ManagedDomainAppRouteWriteInput(ManagedDomainAppRouteTargetInput):
    review_sha256: str
    request_id: GUID


@strawberry.type
class ManagedDomainAppRouteSupport:
    domain_id: GUID
    version: int
    allowed: bool
    reason: str
    writer: str


@strawberry.type
class ManagedDomainAppRouteTarget:
    kind: str
    app_id: GUID
    app_name: str
    app_version: int
    environment_id: GUID
    environment_name: str
    environment_version: int
    workload_id: GUID
    workload_name: str
    workload_version: int
    cluster_id: GUID
    cluster_name: str
    cluster_version: int


@strawberry.type
class ManagedDomainAppRouteTargetsPage:
    items: list[ManagedDomainAppRouteTarget]
    next_cursor: str | None
    has_next_page: bool


@strawberry.type
class ManagedDomainAppRouteRecord:
    name: str
    type: str
    target: str | None
    target_zone_id: str | None


@strawberry.type
class ManagedDomainAppRouteReview:
    kind: str
    app_id: GUID
    app_version: int
    domain_id: GUID
    domain_version: int
    environment_id: GUID
    environment_version: int
    workload_id: GUID
    workload_version: int
    cluster_id: GUID
    cluster_version: int
    hostname: str
    review_sha256: str
    writer: str
    records: list[ManagedDomainAppRouteRecord]
    certificate_status: str
    allocation_state: str
    api_id: str | None
    api_endpoint: str | None
    stage: str | None
    function_arn: str | None
    source_fingerprint: str | None
    can_prepare: bool
    can_attach: bool
    reason: str


@strawberry.type
class ManagedDomainAppRouteOperation:
    kind: str
    app_id: GUID
    cluster_id: GUID
    allocation_state: str
    records: list[ManagedDomainAppRouteRecord]
    observed_at: datetime | None
    id: GUID
    version: int
    request_id: GUID
    domain_id: GUID
    environment_id: GUID
    workload_id: GUID
    custom_domain_id: GUID | None
    hostname: str
    purpose: str
    status: str
    reason: str
    dns_state: str
    ingress_state: str
    tls_state: str
    can_reconcile: bool


def _target_args(input):
    return {
        "domain_id": input.domain_id,
        "domain_version": input.expected_domain_version,
        "environment_id": input.environment_id,
        "environment_version": input.expected_environment_version,
        "workload_id": input.workload_id,
        "hostname": input.hostname,
    }


def _query(fn):
    try:
        return fn()
    except Exception as exc:
        from _sdk.domain_route_http_api import HttpApiRouteUnavailable
        from aws.domain_route import DomainRouteError

        from astrolift_lifecycle.domain_app_route import DomainAppRouteError
        from core.permissions import PermissionDenied

        if isinstance(exc, (DomainAppRouteError, DomainRouteError, HttpApiRouteUnavailable)):
            reason = str(exc)
        elif isinstance(exc, PermissionDenied):
            reason = "CURRENT_ROUTE_AUTHORITY_UNAVAILABLE"
        else:
            reason = "CURRENT_ROUTE_SOURCE_UNAVAILABLE"
        raise GraphQLError("Managed-domain route is unavailable.", extensions={"code": reason}) from None


@strawberry.type
class ManagedDomainAppRouteQuery:
    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def managed_domain_app_route_support(
        self, info: Info, domain_id: GUID, expected_domain_version: int
    ) -> ManagedDomainAppRouteSupport:
        from astrolift_lifecycle import domain_app_route as service

        return _query(lambda: service.support(info, domain_id, expected_domain_version))

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def managed_domain_app_targets_page(
        self,
        info: Info,
        domain_id: GUID,
        expected_domain_version: int,
        first: int = 20,
        after: str | None = None,
    ) -> ManagedDomainAppRouteTargetsPage:
        from astrolift_lifecycle import domain_app_route as service

        return _query(lambda: service.targets_page(info, domain_id, expected_domain_version, first, after))

    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def managed_domain_app_route_review(
        self, info: Info, input: ManagedDomainAppRouteTargetInput
    ) -> ManagedDomainAppRouteReview:
        from astrolift_lifecycle import domain_app_route as service

        def run():
            snapshot, reviewed, _reference = service.review(info, **_target_args(input))
            edge = snapshot["edge"]
            reason = (
                "ROUTE_REVIEWED"
                if snapshot["can_attach"]
                else (
                    "DOMAIN_PREPARATION_REQUIRED"
                    if snapshot["can_prepare"]
                    else (
                        "READ_ONLY_PROVIDER_MANUAL_DNS_REQUIRED"
                        if snapshot["writer"] == "MANUAL"
                        else "ROUTE_SETUP_OR_OWNERSHIP_REQUIRED"
                    )
                )
            )
            return ManagedDomainAppRouteReview(
                kind=snapshot["target_kind"],
                app_id=GUID(snapshot["app_id"]),
                app_version=snapshot["app_version"],
                domain_id=input.domain_id,
                domain_version=snapshot["domain_version"],
                environment_id=input.environment_id,
                environment_version=snapshot["environment_version"],
                workload_id=input.workload_id,
                workload_version=snapshot["workload_version"],
                cluster_id=GUID(snapshot["cluster_id"]),
                cluster_version=snapshot["cluster_version"],
                hostname=snapshot["hostname"],
                review_sha256=reviewed,
                writer=snapshot["writer"],
                records=[
                    ManagedDomainAppRouteRecord(
                        name=snapshot["hostname"],
                        type="A_ALIAS",
                        target=edge["dns_name"],
                        target_zone_id=edge["canonical_zone_id"],
                    )
                ],
                certificate_status=edge["certificate_status"],
                allocation_state=edge["allocation_state"],
                api_id=snapshot.get("native_api", {}).get("target", {}).get("api_id"),
                api_endpoint=snapshot.get("native_api", {}).get("api_endpoint"),
                stage=snapshot.get("native_api", {}).get("stage"),
                function_arn=snapshot.get("native_api", {}).get("target", {}).get("function_arn"),
                source_fingerprint=snapshot.get("native_api", {}).get("source_fingerprint"),
                can_prepare=snapshot["can_prepare"],
                can_attach=snapshot["can_attach"],
                reason=reason,
            )

        return _query(run)


@strawberry.input
class ManagedDomainAppRouteOperationInput:
    domain_id: GUID
    request_id: GUID


@strawberry.input
class ManagedDomainAppRouteReconcileInput:
    operation_id: GUID
    expected_version: int
    request_id: GUID


def operation_type(row, *, can_reconcile=False):
    states = row.native_receipts.get("states", {})
    snapshot = row.target_snapshot
    edge = snapshot["edge"]
    allocation = row.native_receipts.get("api-domain", {}).get("ack")
    native_target = allocation.get("regional_target") if allocation else edge.get("dns_name")
    native_zone = allocation.get("hosted_zone_id") if allocation else edge.get("canonical_zone_id")
    records = (
        [
            ManagedDomainAppRouteRecord(
                name=row.hostname, type="A_ALIAS", target=native_target, target_zone_id=native_zone
            )
        ]
        if row.purpose == "connect"
        else []
    )
    return ManagedDomainAppRouteOperation(
        kind=snapshot["target_kind"],
        app_id=GUID(snapshot["app_id"]),
        cluster_id=GUID(snapshot["cluster_id"]),
        allocation_state="OBSERVED" if native_target is not None else "PENDING_NATIVE_DOMAIN",
        records=records,
        observed_at=row.observed_at,
        id=row.guid,
        version=row.version,
        request_id=row.request_id,
        domain_id=row.managed_domain.guid,
        environment_id=row.app_environment.guid,
        workload_id=row.workload.guid,
        custom_domain_id=row.custom_domain.guid if row.custom_domain_id else None,
        hostname=row.hostname,
        purpose=row.purpose,
        status=row.status,
        reason=row.reason,
        dns_state=states.get("dns", "UNKNOWN"),
        ingress_state=states.get("ingress", "UNKNOWN"),
        tls_state=states.get("tls", "UNKNOWN"),
        can_reconcile=can_reconcile,
    )


def admitted_operation(info, *, domain_id, request_id):
    from astrolift_lifecycle import domain_app_route as source
    from astrolift_lifecycle import domain_route_authority as authority
    from astrolift_lifecycle.models import ManagedDomainAppBinding
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    row = (
        ManagedDomainAppBinding.objects.select_related(
            "managed_domain", "app_environment__tenant_cluster", "registered_app", "workload", "custom_domain"
        )
        .filter(
            organization_id=tenant.organization_id,
            managed_domain__guid=source._guid(domain_id),
            request_id=source._guid(request_id),
            registered_app__organization_id=tenant.organization_id,
            registered_app__deleted_at__isnull=True,
            app_environment__deleted_at__isnull=True,
        )
        .first()
    )
    if row is None:
        return None
    with authority.current(
        authority.capture(info.context.request),
        environment=row.app_environment,
        permissions=source.permissions_for(row.app_environment, read=True),
    ):
        return row


def can_reconcile(info, row):
    """Fresh current viewer and original accepted actor, never a borrowed hint."""
    from astrolift_lifecycle import domain_app_route as source
    from astrolift_lifecycle import domain_app_route_store as store
    from astrolift_lifecycle import domain_route_authority as authority

    try:
        with authority.current(
            authority.capture(info.context.request),
            environment=row.app_environment,
            permissions=source.permissions_for(row.app_environment),
        ):
            with store.locked(row.guid):
                pass
        return True
    except Exception:
        return False


def _dispatch(row, *, recovery=None):
    from astrolift_workflows.client import start_workflow_once
    from astrolift_workflows.domain_app_route_inputs import ManagedDomainAppRouteInput

    identity = row.workflow_id if recovery is None else row.workflow_id + "-" + str(recovery)
    start_workflow_once(
        "ManagedDomainAppRouteWorkflow", [ManagedDomainAppRouteInput(str(row.guid))], workflow_id=identity
    )


def _write(info, input, purpose):
    from astrolift_graphql import failure, success
    from astrolift_lifecycle import domain_app_route as source
    from astrolift_lifecycle import domain_app_route_store as store
    from astrolift_lifecycle import domain_route_authority as authority

    row = admitted_operation(info, domain_id=input.domain_id, request_id=input.request_id)
    if row is not None:
        expected = row.target_snapshot
        if (
            row.purpose != purpose
            or row.review_sha256 != input.review_sha256
            or any(
                str(expected[k]) != str(v)
                for k, v in (("environment_id", input.environment_id), ("workload_id", input.workload_id))
            )
            or expected["domain_version"] != input.expected_domain_version
            or expected["environment_version"] != input.expected_environment_version
            or expected["hostname"] != source.scoped_hostname(input.hostname, row.managed_domain.zone)
        ):
            return failure("ORIGINAL_ROUTE_REQUEST_CHANGED", "The original route request cannot be replaced.")
        with authority.current(
            authority.capture(info.context.request),
            environment=row.app_environment,
            permissions=source.permissions_for(row.app_environment),
        ):
            with store.locked(row.guid):
                pass
    else:
        snapshot, reviewed, reference = source.review(info, **_target_args(input))
        if reviewed != input.review_sha256:
            return failure("ROUTE_REVIEW_CHANGED", "The native route changed. Review it again.")
        row = store.reserve(
            snapshot=snapshot,
            review_sha256=reviewed,
            reference=reference,
            request_id=input.request_id,
            purpose=purpose,
        )
    can_reconcile_now = can_reconcile(info, row)
    try:
        _dispatch(row)
        store.dispatched(row.guid, confirmed=True)
    except Exception:
        # Reservation remains accepted. Recovery joins the exact original start.
        # It must not be reported as a rejected write or silently repeated.
        try:
            store.dispatched(row.guid, confirmed=False)
        except Exception:
            pass
    row.refresh_from_db()
    return success(operation_type(row, can_reconcile=can_reconcile_now))


def _mutation(fn):
    from _sdk.domain_route_http_api import HttpApiRouteUnavailable

    from astrolift_graphql import failure
    from astrolift_lifecycle.domain_app_route import DomainAppRouteError
    from astrolift_lifecycle.domain_app_route_store import DomainRouteStoreError
    from core.permissions import PermissionDenied

    try:
        return fn()
    except (DomainAppRouteError, DomainRouteStoreError, HttpApiRouteUnavailable) as exc:
        return failure(str(exc), "The current reviewed domain route is unavailable.")
    except PermissionDenied:
        return failure("CURRENT_ROUTE_AUTHORITY_UNAVAILABLE", "Current route authority is unavailable.")
    except Exception:
        return failure("CURRENT_ROUTE_SOURCE_UNAVAILABLE", "The current route source is unavailable.")


@strawberry.type
class ManagedDomainAppRouteMutation:
    @strawberry.mutation
    @mutation_audit(action="domain.route.prepare")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def prepare_managed_domain_app_route(
        self, info: Info, input: ManagedDomainAppRouteWriteInput
    ) -> MutationResultType[ManagedDomainAppRouteOperation]:
        return _mutation(lambda: _write(info, input, "prepare"))

    @strawberry.mutation
    @mutation_audit(action="domain.route.connect")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def connect_managed_domain_app_route(
        self, info: Info, input: ManagedDomainAppRouteWriteInput
    ) -> MutationResultType[ManagedDomainAppRouteOperation]:
        return _mutation(lambda: _write(info, input, "connect"))

    @strawberry.mutation
    @mutation_audit(action="domain.route.reconcile")
    @require_permission(
        Permission.PROVIDER_PLUGIN_CONFIGURE,
        scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    )
    @tenant_scoped()
    def reconcile_managed_domain_app_route(
        self, info: Info, input: ManagedDomainAppRouteReconcileInput
    ) -> MutationResultType[ManagedDomainAppRouteOperation]:
        from astrolift_graphql import failure, success
        from astrolift_lifecycle import domain_app_route as source
        from astrolift_lifecycle import domain_app_route_store as store
        from astrolift_lifecycle import domain_route_authority as authority
        from astrolift_lifecycle.models import ManagedDomainAppBinding
        from core.tenancy import get_current_tenant

        def run():
            candidate = ManagedDomainAppBinding.objects.filter(
                guid=source._guid(input.operation_id), organization_id=get_current_tenant().organization_id
            ).first()
            if candidate is None:
                return failure("NOT_FOUND", "Route operation not found.")
            row = admitted_operation(
                info, domain_id=candidate.managed_domain.guid, request_id=candidate.request_id
            )
            if row is None:
                return failure("NOT_FOUND", "Route operation not found.")
            if type(input.expected_version) is not int or row.version != input.expected_version:
                return failure(
                    "VERSION_MISMATCH",
                    "Read the current route operation before recovery.",
                    current_version=row.version,
                )
            with authority.current(
                authority.capture(info.context.request),
                environment=row.app_environment,
                permissions=source.permissions_for(row.app_environment),
            ):
                with store.locked(row.guid):
                    pass
            can_reconcile_now = can_reconcile(info, row)
            try:
                _dispatch(row, recovery=source._guid(input.request_id))
            except Exception:
                pass
            row.refresh_from_db()
            return success(operation_type(row, can_reconcile=can_reconcile_now))

        return _mutation(run)


@strawberry.type
class ManagedDomainAppRouteOperationQuery:
    @strawberry.field
    @require_permission(
        Permission.PROVIDER_PLUGIN_READ, scope=cluster_catalog_org_scope(Permission.PROVIDER_PLUGIN_READ)
    )
    @tenant_scoped()
    def managed_domain_app_route_operation(
        self, info: Info, input: ManagedDomainAppRouteOperationInput
    ) -> ManagedDomainAppRouteOperation | None:
        def run():
            row = admitted_operation(info, domain_id=input.domain_id, request_id=input.request_id)
            if row is None:
                return None
            return operation_type(row, can_reconcile=can_reconcile(info, row))

        return _query(run)
