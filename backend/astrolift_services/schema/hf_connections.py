"""Admin-admitted write-only HF connections and separate model access checks."""

from datetime import datetime
from types import SimpleNamespace
from typing import cast

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, PageType, failure, numbered_page, success
from astrolift_graphql.pagination import NumberedPage
from astrolift_services.cluster_models import cluster_model_org_scope
from astrolift_services.hf_catalogue import HuggingFaceModel
from astrolift_services.hf_connection import (
    HuggingFaceUnavailable,
    credential,
    verified_model,
    verify_account,
)
from astrolift_services.model_admission import current_org_id, in_current_org
from astrolift_services.models import HuggingFaceConnection
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionDenied, require_permission
from core.secrets import encrypt_at_rest


def require_host_admin(info, cluster=None):
    from astrolift_services.hosting_authority import current_host_operator
    from astrolift_services.schema.cluster_model_mutations import _recheck_authority

    target = cluster or SimpleNamespace(region=None)
    with current_host_operator():
        _recheck_authority(info, Permission.ORG_UPDATE, target)
        _recheck_authority(info, Permission.CLUSTER_UPDATE, target)


def locked_connection(connection_id, expected_version):
    row = (
        HuggingFaceConnection.objects.select_for_update()
        .filter(
            guid=str(connection_id),
            organization_id=current_org_id(),
            organization__deleted_at__isnull=True,
        )
        .first()
    )
    if row is None or row.version != expected_version:
        raise HuggingFaceUnavailable("Hugging Face connection changed or is unavailable. Refresh and retry.")
    return row


@strawberry.type
class ModelHostingAction:
    allowed: bool
    reason: str | None


@strawberry.type
class HuggingFaceConnectionType:
    id: GUID
    version: int
    name: str
    account_username: str
    verified_at: datetime


def connection_to_type(row):
    return HuggingFaceConnectionType(
        id=GUID(str(row.guid)),
        version=row.version,
        name=row.name,
        account_username=row.account_username,
        verified_at=row.verified_at,
    )


@strawberry.type
class ModelSourceAccess:
    accessible: bool
    reason: str | None
    model: HuggingFaceModel | None
    observed_at: datetime


@strawberry.input
class ConnectHuggingFaceInput:
    organization_id: GUID
    name: str
    token: str


@strawberry.input
class DisconnectHuggingFaceInput:
    organization_id: GUID
    connection_id: GUID
    expected_version: int


@strawberry.type
class HuggingFaceConnectionsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ))
    @tenant_scoped()
    def model_hosting_action(self, info: Info, organization_id: GUID) -> ModelHostingAction:
        if not in_current_org(organization_id):
            return ModelHostingAction(allowed=False, reason="Organization is unavailable.")
        try:
            require_host_admin(info)
        except PermissionDenied:
            return ModelHostingAction(
                allowed=False,
                reason="Hosting requires an active platform operator with organization configuration and cluster update permission.",
            )
        return ModelHostingAction(allowed=True, reason=None)

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=cluster_model_org_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def hugging_face_connections_page(
        self,
        info: Info,
        organization_id: GUID,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[HuggingFaceConnectionType]:
        require_host_admin(info)
        rows = HuggingFaceConnection.objects.filter(organization_id=current_org_id())
        if not in_current_org(organization_id):
            rows = rows.none()
        result: NumberedPage[HuggingFaceConnection] = numbered_page(
            rows, order_by=["pk"], page=page, page_size=page_size, max_page_size=50
        )
        return PageType(
            items=[connection_to_type(row) for row in result.rows],
            page=result.page,
            page_size=result.page_size,
            total_count=result.total_count,
        )

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=cluster_model_org_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def cluster_model_source_access(
        self,
        info: Info,
        organization_id: GUID,
        model_repo: str,
        revision_sha: str,
        connection_id: GUID | None = None,
        expected_connection_version: int | None = None,
    ) -> ModelSourceAccess:
        now = timezone.now()
        if not in_current_org(organization_id):
            return ModelSourceAccess(
                accessible=False, reason="Organization is unavailable.", model=None, observed_at=now
            )
        try:
            with transaction.atomic():
                require_host_admin(info)
                row = locked_connection(connection_id, expected_connection_version) if connection_id else None
                if row is None and expected_connection_version is not None:
                    raise HuggingFaceUnavailable("Select a current Hugging Face connection.")
                require_host_admin(info)
                model = verified_model(
                    model_repo,
                    revision_sha.lower(),
                    token=credential(row) if row else None,
                    checkpoint=lambda: require_host_admin(info),
                )
                require_host_admin(info)
                return ModelSourceAccess(accessible=True, reason=None, model=model, observed_at=now)
        except HuggingFaceUnavailable as exc:
            return ModelSourceAccess(accessible=False, reason=str(exc), model=None, observed_at=now)


def connection_failure(code: str, message: str) -> MutationResultType[HuggingFaceConnectionType]:
    # Failure data is null in every specialized connection envelope.
    return cast(MutationResultType[HuggingFaceConnectionType], failure(code, message))


@strawberry.type
class HuggingFaceConnectionsMutation:
    @strawberry.field
    @model_mutation_audit(action="model.huggingface.disconnect")
    @require_permission(Permission.ORG_UPDATE, scope=cluster_model_org_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def disconnect_hugging_face(
        self,
        info: Info,
        input: DisconnectHuggingFaceInput,
    ) -> MutationResultType[HuggingFaceConnectionType]:
        from astrolift_services.models import ManagedService

        if not in_current_org(input.organization_id):
            return connection_failure("PRECONDITION", "Organization is unavailable.")
        try:
            with transaction.atomic():
                require_host_admin(info)
                row = locked_connection(input.connection_id, input.expected_version)
                require_host_admin(info)
                if ManagedService.objects.filter(model_hf_connection=row).exists():
                    return connection_failure(
                        "PRECONDITION", "Deprovision models using this connection before disconnecting it."
                    )
                row.soft_delete(by=info.context.request.user)
                return success(connection_to_type(row))
        except HuggingFaceUnavailable as exc:
            return connection_failure("PRECONDITION", str(exc))

    @strawberry.field
    @model_mutation_audit(action="model.huggingface.connect")
    @require_permission(Permission.ORG_UPDATE, scope=cluster_model_org_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def connect_hugging_face(
        self,
        info: Info,
        input: ConnectHuggingFaceInput,
    ) -> MutationResultType[HuggingFaceConnectionType]:
        if not in_current_org(input.organization_id):
            return connection_failure("PRECONDITION", "Organization is unavailable.")
        name = input.name.strip()
        if not 1 <= len(name) <= 128 or any(ord(char) < 32 for char in name):
            return connection_failure("VALIDATION", "Enter a connection name of 1–128 printable characters.")
        try:
            with transaction.atomic():
                require_host_admin(info)
                account = verify_account(input.token)
                # Authorization may change during the bounded external read.
                require_host_admin(info)
                secret = encrypt_at_rest(input.token.encode("utf-8"))
                row = HuggingFaceConnection.objects.create(
                    organization_id=current_org_id(),
                    name=name,
                    account_username=account,
                    secret_backend_kind=secret.backend_kind,
                    secret_ciphertext=secret.backend_ref,
                    verified_at=timezone.now(),
                    created_by=info.context.request.user,
                )
                return success(connection_to_type(row))
        except HuggingFaceUnavailable as exc:
            return connection_failure("VALIDATION", str(exc))
