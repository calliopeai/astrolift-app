"""Typed policy-governed app connection intake, review and current-owner finalization."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
from uuid import UUID

import strawberry
from django.db import IntegrityError, transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, PageType, failure, numbered_page, success
from astrolift_graphql.results import version_mismatch
from astrolift_identity import abac
from astrolift_identity.operation_visibility import visible_operation_rows
from astrolift_identity.scopes import identity_organization_scope
from astrolift_lifecycle.scopes import app_scope_via, environment_app_scope
from astrolift_services.cluster_models import live_cluster_model_by_guid
from astrolift_services.model_connection_policy import (
    admission,
    check_connection_permission,
    effective_policy,
    fresh_actor,
    locked_organization,
)
from astrolift_services.model_connection_requests import (
    ConnectionUnavailable,
    approver_admission,
    eligible_vote_count,
    locked_request,
    locked_targets,
    request_input,
    request_rows,
    reviewed_versions,
    source_current,
    stale_request,
    vote_credential,
)
from astrolift_services.models import ModelConnectionApproval, ModelConnectionPolicy, ModelConnectionRequest
from astrolift_services.schema.cluster_model_mutations import SubscribeClusterModelInput, _subscribe_model
from astrolift_services.schema.cluster_models import _environment_rows, _subscription_targets_page
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from astrolift_services.schema.model_types import ModelSubscriptionTargetType
from core.current_credential import current_dispatch_credential
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant


@strawberry.enum
class ModelConnectionMode(Enum):
    AUTO = "AUTO"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


@strawberry.enum
class ModelConnectionAction(Enum):
    AUTO = "AUTO"
    REQUEST = "REQUEST"
    DENY = "DENY"


@strawberry.enum
class ModelConnectionRequestStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STALE = "stale"


@strawberry.type(name="ModelConnectionPolicy")
class ModelConnectionPolicyType:
    id: GUID | None
    version: int
    mode: ModelConnectionMode
    required_approvals: int
    allow_self_approval: bool


@strawberry.type(name="ModelConnectionEligibility")
class ModelConnectionEligibilityType:
    action: ModelConnectionAction
    reason: str
    policy_version: str | None
    required_approvals: int | None
    allow_self_approval: bool | None


@strawberry.type(name="ModelConnectionDestination")
class ModelConnectionDestinationType(ModelSubscriptionTargetType):
    app_version: int
    action: ModelConnectionAction
    policy_version: str | None
    required_approvals: int | None
    allow_self_approval: bool | None


@strawberry.type(name="ModelConnectionRequest")
class ModelConnectionRequestType:
    id: GUID
    version: int
    status: ModelConnectionRequestStatus
    organization_id: GUID
    model_deployment_id: GUID
    app_id: GUID
    app_environment_id: GUID
    cluster_id: GUID
    provider_id: GUID
    alias: str
    model_name: str | None
    app_name: str | None
    environment_name: str | None
    requester_username: str | None
    policy_version: str
    required_approvals: int
    approval_count: int
    can_approve: bool
    can_reject: bool
    can_cancel: bool
    can_finalize: bool
    subscription_id: GUID | None
    created_at: datetime
    decided_at: datetime | None
    finalized_at: datetime | None


@strawberry.input
class ModelConnectionPlacementInput:
    organization_id: GUID
    model_deployment_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID


@strawberry.input
class ModelConnectionTargetInput:
    organization_id: GUID
    model_deployment_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    app_environment_id: GUID


@strawberry.input
class RequestModelConnectionInput(SubscribeClusterModelInput):
    if_match_app_version: int
    policy_version: str
    idempotency_key: GUID


@strawberry.input
class DecideModelConnectionRequestInput:
    id: GUID
    if_match_version: int


@strawberry.input
class UpdateOrganizationModelConnectionPolicyInput:
    organization_id: GUID
    if_match_version: int
    mode: ModelConnectionMode
    required_approvals: int
    allow_self_approval: bool


@strawberry.input
class SetModelConnectionRestrictionInput(UpdateOrganizationModelConnectionPolicyInput):
    model_deployment_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_deployment_version: int


def _request_scope(permission):
    return app_scope_via(
        "astrolift_services.ModelConnectionRequest",
        "input.id",
        app_path="app_environment__registered_app",
        permission=permission,
    )


def _request_operation(args):
    from core.scope_args import read_guid

    identity = read_guid(args, "input.id")
    row = request_rows().filter(guid=identity).first() if identity else None
    from astrolift_identity.operation_context import OperationContext, environment_context

    if row is None:
        return (OperationContext(approvals=0),)
    return (environment_context(row.app_environment, approvals=0),)


def _target_operation(args):
    from astrolift_identity.operation_context import OperationContext, environment_context
    from astrolift_lifecycle.models import AppEnvironment

    value = args.get("input")
    tenant = get_current_tenant()
    row = (
        AppEnvironment.objects.select_related("tenant_cluster")
        .filter(
            guid=str(getattr(value, "app_environment_id", "")),
            registered_app__organization_id=tenant.organization_id if tenant else None,
        )
        .first()
    )
    return (environment_context(row, approvals=0),) if row else (OperationContext(approvals=0),)


def _org_admission(info):
    actor = fresh_actor(info)
    with current_dispatch_credential(Permission.ORG_UPDATE):
        with abac.request_attributes(
            replace(
                abac.attributes_from_request(
                    SimpleNamespace(
                        META=getattr(info.context.request, "META", {}),
                        session=info.context.request._model_connection_auth_session,
                    ),
                    actor.pk,
                ),
                cache={},
                approval_request=False,
            )
        ):
            check_connection_permission(
                Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE)({})
            )
    return actor


def _automatic_admission(info, env):
    # Mirror legacy direct-subscribe prerequisites; request intake does not need ORG_READ.
    from astrolift_services.schema.cluster_model_mutations import _recheck_authority

    _recheck_authority(info, Permission.APP_UPDATE, env.tenant_cluster, environment=env)


def _policy_type(row):
    if row is None:
        from constance import config

        return ModelConnectionPolicyType(
            id=None,
            version=0,
            mode=ModelConnectionMode(config.MODEL_CONNECTION_DEFAULT_MODE),
            required_approvals=config.MODEL_CONNECTION_DEFAULT_QUORUM,
            allow_self_approval=config.MODEL_CONNECTION_ALLOW_SELF_APPROVAL,
        )
    return ModelConnectionPolicyType(
        id=GUID(str(row.guid)),
        version=row.version,
        mode=ModelConnectionMode(row.mode),
        required_approvals=row.required_approvals,
        allow_self_approval=row.allow_self_approval,
    )


def _request_type(row, *, approve=False, cancel=False, finalize=False, approval_count=None):
    return ModelConnectionRequestType(
        id=GUID(str(row.guid)),
        version=row.version,
        status=ModelConnectionRequestStatus(row.status),
        organization_id=GUID(str(row.organization.guid)),
        model_deployment_id=GUID(str(row.model_deployment.guid)),
        app_id=GUID(str(row.registered_app.guid)),
        app_environment_id=GUID(str(row.app_environment.guid)),
        cluster_id=GUID(str(row.tenant_cluster.guid)),
        provider_id=GUID(str(row.provider_plugin.guid)),
        alias=row.alias,
        model_name=row.model_deployment.name[:128] if row.model_deployment.deleted_at is None else None,
        app_name=row.registered_app.name[:128] if row.registered_app.deleted_at is None else None,
        environment_name=row.app_environment.name[:128] if row.app_environment.deleted_at is None else None,
        requester_username=(
            row.requester.username[:128]
            if row.requester.is_active and "@" not in row.requester.username
            else None
        ),
        policy_version=row.policy_version,
        required_approvals=row.required_approvals,
        approval_count=eligible_vote_count(row, row.app_environment)
        if approval_count is None
        else approval_count,
        can_approve=approve,
        can_reject=approve,
        can_cancel=cancel,
        can_finalize=finalize,
        subscription_id=GUID(str(row.subscription.guid)) if row.subscription_id else None,
        created_at=row.created_at,
        decided_at=row.decided_at,
        finalized_at=row.finalized_at,
    )


def _mismatch(row, input):
    if row.version != input.if_match_version:
        return version_mismatch(
            current_version=row.version,
            requested_version=input.if_match_version,
            kind="Model connection request",
        )
    return None


def _readable_requests():
    with abac.operation_attributes(approval_request=True):
        return visible_operation_rows(
            request_rows(), Permission.APP_UPDATE, environment_path="app_environment"
        )


@contextmanager
def _fresh_visibility(info, permission):
    with current_dispatch_credential(permission):
        actor = fresh_actor(info)
        attrs = replace(
            abac.attributes_from_request(
                SimpleNamespace(
                    META=getattr(info.context.request, "META", {}),
                    session=info.context.request._model_connection_auth_session,
                ),
                actor.pk,
            ),
            cache={},
            approval_request=permission == Permission.APP_UPDATE,
            approvals=0,
        )
        with abac.request_attributes(attrs):
            yield


@strawberry.type
class ModelConnectionsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=identity_organization_scope(Permission.ORG_READ))
    @tenant_scoped()
    def organization_model_connection_policy(
        self, info: Info, organization_id: GUID
    ) -> ModelConnectionPolicyType:
        tenant = get_current_tenant()
        from astrolift_identity.models import Organization

        if not Organization.objects.filter(pk=tenant.organization_id, guid=str(organization_id)).exists():
            raise PermissionDenied(Permission.ORG_READ, None, "organization is unavailable")
        return _policy_type(
            ModelConnectionPolicy.objects.filter(
                organization_id=tenant.organization_id, model_deployment_id__isnull=True
            ).first()
        )

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def model_connection_restriction(
        self, info: Info, input: ModelConnectionPlacementInput
    ) -> ModelConnectionPolicyType:
        """Read the model overlay itself; absence is a neutral version-zero overlay."""
        from astrolift_services.schema.cluster_model_mutations import _locked_model, _ModelIdentity
        from astrolift_services.schema.hf_connections import require_host_admin
        from core.permissions import check_platform_operator

        with current_dispatch_credential(Permission.ORG_UPDATE):
            check_platform_operator(fresh_actor(info), gate=Permission.ORG_UPDATE)
        with transaction.atomic():
            locked_organization()
            service = _locked_model(
                _ModelIdentity(
                    input.model_deployment_id,
                    input.organization_id,
                    input.expected_cluster_id,
                    input.expected_provider_id,
                )
            )
            if service is None:
                raise PermissionDenied(Permission.ORG_UPDATE, None, "model deployment is unavailable")
            row = (
                ModelConnectionPolicy.objects.select_for_update()
                .filter(organization=service.organization, model_deployment=service)
                .first()
            )
            _org_admission(info)
            require_host_admin(info, service.tenant_cluster)
            with current_dispatch_credential(Permission.ORG_UPDATE):
                check_platform_operator(fresh_actor(info), gate=Permission.ORG_UPDATE)
            if row is None:
                return ModelConnectionPolicyType(
                    id=None,
                    version=0,
                    mode=ModelConnectionMode.AUTO,
                    required_approvals=1,
                    allow_self_approval=True,
                )
            return _policy_type(row)

    @strawberry.field
    @require_permission(Permission.APP_UPDATE, any_scope=True, approval_request=True)
    @tenant_scoped()
    def model_connection_targets_page(
        self,
        info: Info,
        input: ModelConnectionPlacementInput,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[ModelConnectionDestinationType]:
        """Metadata-only intake: never invent votes or admit subscription effects."""
        from astrolift_identity.models import Organization

        actor = fresh_actor(info)
        tenant = get_current_tenant()
        service = (
            live_cluster_model_by_guid(input.model_deployment_id)
            if Organization.objects.filter(
                pk=tenant.organization_id, guid=str(input.organization_id)
            ).exists()
            else None
        )
        if service is not None and (
            str(service.tenant_cluster.guid) != str(input.expected_cluster_id)
            or str(service.tenant_cluster.provider_plugin.guid) != str(input.expected_provider_id)
        ):
            service = None

        def projection_factory(environments):
            from astrolift_services.model_connection_projection import PageAuthority, policy_page_facts
            from astrolift_services.schema.model_reads import _catalogue_audience

            authority = PageAuthority(info, environments)
            facts = policy_page_facts(tenant.organization_id)
            source_available = service is not None and source_current(service)
            try:
                _catalogue_audience(info)
                automatic_audience = True
            except Exception:
                automatic_audience = False

            return lambda env, target: project(
                env, target, authority, facts, source_available, automatic_audience
            )

        def project(env, target, authority, facts, source_available, automatic_audience):
            values = {name: getattr(target, name) for name in ModelSubscriptionTargetType.__annotations__}
            action = ModelConnectionAction.DENY
            policy = None
            if target.eligible and source_available:
                try:
                    minimum = authority.admission(env)
                    candidate = effective_policy(service, approval_minimum=minimum, page_facts=facts)
                    if candidate.mode == "AUTO":
                        if not automatic_audience:
                            raise PermissionDenied(
                                Permission.APP_UPDATE, None, "authentication is unavailable"
                            )
                        authority.admission(env, request_only=False)
                        authority.organization(Permission.ORG_READ, env)
                    if candidate.mode != "DENY":
                        policy = candidate
                        action = (
                            ModelConnectionAction.AUTO
                            if candidate.mode == "AUTO"
                            else ModelConnectionAction.REQUEST
                        )
                except PermissionDenied:
                    pass
            values["eligible"] = action != ModelConnectionAction.DENY
            if not values["eligible"]:
                values["reason"] = target.reason or "Model connection is unavailable or denied."
            return ModelConnectionDestinationType(
                **values,
                app_version=env.registered_app.version,
                action=action,
                policy_version=policy.version if policy else None,
                required_approvals=policy.required_approvals if policy else None,
                allow_self_approval=policy.allow_self_approval if policy else None,
            )

        with current_dispatch_credential(Permission.APP_UPDATE):
            attrs = replace(
                abac.attributes_from_request(
                    SimpleNamespace(
                        META=getattr(info.context.request, "META", {}),
                        session=info.context.request._model_connection_auth_session,
                    ),
                    actor.pk,
                ),
                cache={},
                approval_request=True,
                approvals=0,
            )
            with abac.request_attributes(attrs):
                return _subscription_targets_page(
                    service,
                    _environment_rows(Permission.APP_UPDATE),
                    search=search,
                    page=page,
                    page_size=page_size,
                    projection_factory=projection_factory,
                )

    @strawberry.field
    @require_permission(
        Permission.APP_UPDATE,
        scope=environment_app_scope("input.app_environment_id", permission=Permission.APP_UPDATE),
        operation=_target_operation,
        approval_request=True,
    )
    @tenant_scoped()
    def model_connection_action(
        self, info: Info, input: ModelConnectionTargetInput
    ) -> ModelConnectionEligibilityType:
        try:
            with transaction.atomic():
                _, env, policy = locked_targets(info, input)
                if policy.mode == "AUTO":
                    _automatic_admission(info, env)
                action = (
                    ModelConnectionAction.AUTO if policy.mode == "AUTO" else ModelConnectionAction.REQUEST
                )
                return ModelConnectionEligibilityType(
                    action=action,
                    reason="Automatic connection is permitted."
                    if action == ModelConnectionAction.AUTO
                    else "Approval is required before connecting.",
                    policy_version=policy.version,
                    required_approvals=policy.required_approvals,
                    allow_self_approval=policy.allow_self_approval,
                )
        except (ConnectionUnavailable, PermissionDenied):
            return ModelConnectionEligibilityType(
                action=ModelConnectionAction.DENY,
                reason="Model connection is unavailable or denied.",
                policy_version=None,
                required_approvals=None,
                allow_self_approval=None,
            )

    @strawberry.field
    @require_permission(Permission.APP_UPDATE, any_scope=True, approval_request=True)
    @tenant_scoped()
    def model_connection_requests_page(
        self, info: Info, organization_id: GUID, page: int = 1, page_size: int = 25
    ) -> PageType[ModelConnectionRequestType]:
        with _fresh_visibility(info, Permission.APP_UPDATE):
            rows = _readable_requests().filter(organization__guid=str(organization_id))
            page_result = numbered_page(
                rows, order_by=["-created_at", "-guid"], page=page, page_size=page_size
            )
            from astrolift_services.model_connection_projection import project_request_page

            return PageType(
                items=project_request_page(info, page_result.rows),
                page=page_result.page,
                page_size=page_result.page_size,
                total_count=page_result.total_count,
            )

    @strawberry.field
    @require_permission(
        Permission.APP_UPDATE,
        scope=_request_scope(Permission.APP_UPDATE),
        operation=_request_operation,
        approval_request=True,
    )
    @tenant_scoped()
    def model_connection_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> ModelConnectionRequestType | None:
        try:
            with transaction.atomic():
                row, service, env, policy = locked_request(info, input)
                stale_request(row, service, env, policy)
                own = row.requester_id == get_current_tenant().actor_user_id
                approve = False
                if row.status == "pending" and (not own or row.allow_self_approval):
                    try:
                        approver_admission(info, env)
                        approve = True
                    except PermissionDenied:
                        pass
                return _request_type(
                    row,
                    approve=approve,
                    cancel=own and row.status in ("pending", "approved") and row.subscription_id is None,
                    finalize=own
                    and row.status == "approved"
                    and row.subscription_id is None
                    and eligible_vote_count(row, env) >= row.required_approvals,
                )
        except (ConnectionUnavailable, PermissionDenied):
            return None

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def model_connection_approval_requests_page(
        self, info: Info, organization_id: GUID, page: int = 1, page_size: int = 25
    ) -> PageType[ModelConnectionRequestType]:
        _org_admission(info)
        with _fresh_visibility(info, Permission.APP_APPROVE_DEPLOY):
            rows = visible_operation_rows(
                request_rows(),
                Permission.APP_APPROVE_DEPLOY,
                app_path="registered_app",
                environment_path="app_environment",
            ).filter(organization__guid=str(organization_id))
            page_result = numbered_page(
                rows, order_by=["-created_at", "-guid"], page=page, page_size=page_size
            )
            from astrolift_services.model_connection_projection import project_request_page

            return PageType(
                items=project_request_page(info, page_result.rows, review=True),
                page=page_result.page,
                page_size=page_result.page_size,
                total_count=page_result.total_count,
            )

    @strawberry.field
    @require_permission(
        Permission.APP_APPROVE_DEPLOY,
        scope=_request_scope(Permission.APP_APPROVE_DEPLOY),
        operation=_request_operation,
    )
    @tenant_scoped()
    def model_connection_review_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> ModelConnectionRequestType | None:
        try:
            with transaction.atomic():
                row, service, env, policy = locked_request(
                    info, input, permission=Permission.APP_APPROVE_DEPLOY
                )
                actor = approver_admission(info, env)
                stale_request(row, service, env, policy)
                return _request_type(
                    row,
                    approve=row.status == "pending"
                    and (row.requester_id != actor.pk or row.allow_self_approval),
                )
        except (ConnectionUnavailable, PermissionDenied):
            return None


@strawberry.type
class ModelConnectionsMutation:
    @strawberry.field
    @model_mutation_audit(action="model.connection_policy.update")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def update_organization_model_connection_policy(
        self, info: Info, input: UpdateOrganizationModelConnectionPolicyInput
    ) -> MutationResultType[ModelConnectionPolicyType]:
        with transaction.atomic():
            org = locked_organization()
            actor = _org_admission(info)
            if str(org.guid) != str(input.organization_id):
                return failure("NOT_FOUND", "Organization is unavailable.")
            if type(input.required_approvals) is not int or not 1 <= input.required_approvals <= 16:
                return failure("VALIDATION", "Approval quorum must be between 1 and 16.")
            row = (
                ModelConnectionPolicy.objects.select_for_update()
                .filter(organization=org, model_deployment_id__isnull=True)
                .first()
            )
            version = row.version if row else 0
            if version != input.if_match_version:
                return version_mismatch(
                    current_version=version,
                    requested_version=input.if_match_version,
                    kind="Model connection policy",
                )
            if row is None:
                row = ModelConnectionPolicy(organization=org, created_by=actor)
            row.mode = input.mode.value
            row.required_approvals = input.required_approvals
            row.allow_self_approval = input.allow_self_approval
            row.updated_by = _org_admission(info)
            row.save()
            return success(_policy_type(row))

    @strawberry.field
    @model_mutation_audit(action="model.connection_restriction.update")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def set_model_connection_restriction(
        self, info: Info, input: SetModelConnectionRestrictionInput
    ) -> MutationResultType[ModelConnectionPolicyType]:
        from astrolift_services.schema.cluster_model_mutations import _locked_model, _ModelIdentity
        from astrolift_services.schema.hf_connections import require_host_admin
        from core.permissions import check_platform_operator

        with current_dispatch_credential(Permission.ORG_UPDATE):
            check_platform_operator(fresh_actor(info), gate=Permission.ORG_UPDATE)

        with transaction.atomic():
            locked_organization()
            service = _locked_model(
                _ModelIdentity(
                    input.model_deployment_id,
                    input.organization_id,
                    input.expected_cluster_id,
                    input.expected_provider_id,
                )
            )
            if service is None:
                return failure("NOT_FOUND", "Model deployment is unavailable.")
            require_host_admin(info, service.tenant_cluster)
            actor = _org_admission(info)
            if service.version != input.if_match_deployment_version:
                return version_mismatch(
                    current_version=service.version,
                    requested_version=input.if_match_deployment_version,
                    kind="Model deployment",
                )
            if type(input.required_approvals) is not int or not 1 <= input.required_approvals <= 16:
                return failure("VALIDATION", "Approval quorum must be between 1 and 16.")
            row = (
                ModelConnectionPolicy.objects.select_for_update()
                .filter(organization=service.organization, model_deployment=service)
                .first()
            )
            version = row.version if row else 0
            if version != input.if_match_version:
                return version_mismatch(
                    current_version=version,
                    requested_version=input.if_match_version,
                    kind="Model connection restriction",
                )
            row = row or ModelConnectionPolicy(
                organization=service.organization, model_deployment=service, created_by=actor
            )
            row.mode = input.mode.value
            row.required_approvals = input.required_approvals
            row.allow_self_approval = input.allow_self_approval
            row.updated_by = actor
            require_host_admin(info, service.tenant_cluster)
            with current_dispatch_credential(Permission.ORG_UPDATE):
                check_platform_operator(fresh_actor(info), gate=Permission.ORG_UPDATE)
            row.save()
            return success(_policy_type(row))

    @strawberry.field
    @model_mutation_audit(action="model.connection.request")
    @require_permission(
        Permission.APP_UPDATE,
        scope=environment_app_scope("input.app_environment_id", permission=Permission.APP_UPDATE),
        operation=_target_operation,
        approval_request=True,
    )
    @tenant_scoped()
    def request_model_connection(
        self, info: Info, input: RequestModelConnectionInput
    ) -> MutationResultType[ModelConnectionRequestType]:
        from astrolift_services.cluster_models import model_binding_prefix

        try:
            model_binding_prefix(input.alias)
            key = UUID(str(input.idempotency_key))
            if not key.int:
                raise ValueError
            with transaction.atomic():
                service, env, policy = locked_targets(info, input)
                from astrolift_services.model_subscriptions import validate_destination

                validate_destination(env, input.alias)
                actor = fresh_actor(info)
                versions = reviewed_versions(service, env)
                expected = {
                    "model": input.if_match_version,
                    "app": input.if_match_app_version,
                    "environment": input.if_match_environment_version,
                }
                if (
                    any(versions[k] != value for k, value in expected.items())
                    or policy.version != input.policy_version
                ):
                    return failure("PRECONDITION", "Connection targets or policy changed. Review again.")
                if policy.mode != "REQUIRE_APPROVAL":
                    return failure(
                        "PRECONDITION",
                        "This target does not accept approval requests under its current policy.",
                    )
                prior = (
                    request_rows()
                    .select_for_update(of=("self",))
                    .filter(requester=actor, idempotency_key=key)
                    .first()
                )
                if prior:
                    # A duplicate's request row can also block after target admission.
                    minimum = admission(info, env, request_only=True)
                    from astrolift_services.model_connection_policy import effective_policy

                    policy = effective_policy(service, approval_minimum=minimum)
                    if policy.mode != "REQUIRE_APPROVAL" or policy.version != input.policy_version:
                        return failure("PRECONDITION", "Connection policy changed. Review again.")
                    if (
                        prior.model_deployment_id != service.pk
                        or prior.app_environment_id != env.pk
                        or prior.alias != input.alias
                        or prior.policy_version != input.policy_version
                        or prior.reviewed_versions != versions
                    ):
                        return failure(
                            "CONFLICT", "The idempotency key belongs to a different reviewed request."
                        )
                    return success(_request_type(prior))
                row = ModelConnectionRequest.objects.create(
                    organization=service.organization,
                    model_deployment=service,
                    registered_app=env.registered_app,
                    app_environment=env,
                    tenant_cluster=service.tenant_cluster,
                    provider_plugin=service.tenant_cluster.provider_plugin,
                    requester=actor,
                    alias=input.alias,
                    idempotency_key=key,
                    reviewed_versions=versions,
                    policy_version=policy.version,
                    required_approvals=policy.required_approvals,
                    allow_self_approval=policy.allow_self_approval,
                    created_by=actor,
                    updated_by=actor,
                )
                return success(_request_type(row, cancel=True))
        except (ConnectionUnavailable, ValueError):
            return failure(
                "PRECONDITION", "Model connection request is unavailable. Review its targets and policy."
            )
        except IntegrityError:
            return failure("CONFLICT", "This idempotency key is already in use.")

    @strawberry.field
    @model_mutation_audit(action="model.connection.approve")
    @require_permission(
        Permission.APP_APPROVE_DEPLOY,
        scope=_request_scope(Permission.APP_APPROVE_DEPLOY),
        operation=_request_operation,
    )
    @tenant_scoped()
    def approve_model_connection_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> MutationResultType[ModelConnectionRequestType]:
        try:
            with transaction.atomic():
                row, service, env, policy = locked_request(
                    info, input, permission=Permission.APP_APPROVE_DEPLOY
                )
                actor = approver_admission(info, env)
                if stale_request(row, service, env, policy):
                    return success(_request_type(row))
                if row.requester_id == actor.pk and not row.allow_self_approval:
                    return failure("PERMISSION_DENIED", "This policy requires a distinct reviewer.")
                prior_vote = ModelConnectionApproval.all_objects.filter(request=row, voter=actor).first()
                if prior_vote is not None:
                    if prior_vote.deleted_at is not None:
                        return failure(
                            "PRECONDITION", "This reviewer vote was withdrawn. Request a new review."
                        )
                    return success(_request_type(row))
                mismatch = _mismatch(row, input)
                if mismatch:
                    return mismatch
                if row.status != "pending":
                    return failure("PRECONDITION", "This request is no longer pending.")
                ModelConnectionApproval.objects.create(
                    request=row, voter=actor, created_by=actor, updated_by=actor, **vote_credential(info)
                )
                if eligible_vote_count(row, env) >= row.required_approvals:
                    row.status = "approved"
                    row.decided_at = timezone.now()
                row.updated_by = actor
                row.save(update_fields=["status", "decided_at", "updated_by", "updated_at", "version"])
                return success(_request_type(row))
        except ConnectionUnavailable:
            return failure("PRECONDITION", "Model connection request is unavailable.")

    @strawberry.field
    @model_mutation_audit(action="model.connection.reject")
    @require_permission(
        Permission.APP_APPROVE_DEPLOY,
        scope=_request_scope(Permission.APP_APPROVE_DEPLOY),
        operation=_request_operation,
    )
    @tenant_scoped()
    def reject_model_connection_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> MutationResultType[ModelConnectionRequestType]:
        return _close_request(info, input, "rejected")

    @strawberry.field
    @model_mutation_audit(action="model.connection.cancel")
    @require_permission(
        Permission.APP_UPDATE,
        scope=_request_scope(Permission.APP_UPDATE),
        operation=_request_operation,
        approval_request=True,
    )
    @tenant_scoped()
    def cancel_model_connection_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> MutationResultType[ModelConnectionRequestType]:
        return _close_request(info, input, "cancelled")

    @strawberry.field
    @model_mutation_audit(action="model.connection.finalize")
    @require_permission(
        Permission.APP_UPDATE,
        scope=_request_scope(Permission.APP_UPDATE),
        operation=_request_operation,
        approval_request=True,
    )
    @tenant_scoped()
    def finalize_model_connection_request(
        self, info: Info, input: DecideModelConnectionRequestInput
    ) -> MutationResultType[ModelConnectionRequestType]:
        try:
            with transaction.atomic():
                row, service, env, policy = locked_request(info, input)
                if row.requester_id != get_current_tenant().actor_user_id:
                    return failure(
                        "PERMISSION_DENIED", "Only the current requester may finalize this connection."
                    )
                if row.subscription_id:
                    admission(info, env, approvals=eligible_vote_count(row, env))
                    return success(_request_type(row))
                if stale_request(row, service, env, policy):
                    return success(_request_type(row))
                mismatch = _mismatch(row, input)
                if mismatch:
                    return mismatch
                if row.status != "approved":
                    return failure("PRECONDITION", "Current reviewer quorum is required before connecting.")
                count = eligible_vote_count(row, env)
                if count < row.required_approvals:
                    row.status = "stale"
                    row.decided_at = timezone.now()
                    row.save(update_fields=["status", "decided_at", "updated_at", "version"])
                    return success(_request_type(row))
                with abac.operation_attributes(approvals=count, approval_request=False):
                    admission(info, env, approvals=count)
                    result = _subscribe_model(info, request_input(row), connection_request=row)
                if not result.ok:
                    return result
                from astrolift_services.models import ManagedServiceAttachment

                row.subscription = ManagedServiceAttachment.objects.get(
                    guid=str(result.data.subscription.id),
                    managed_service=service,
                    managed_service__organization_id=get_current_tenant().organization_id,
                    app_environment=env,
                    app_environment__registered_app__organization_id=get_current_tenant().organization_id,
                )
                row.finalized_at = timezone.now()
                row.updated_by = fresh_actor(info)
                row.save(
                    update_fields=["subscription", "finalized_at", "updated_by", "updated_at", "version"]
                )
                return success(_request_type(row))
        except ConnectionUnavailable:
            return failure("PRECONDITION", "Model connection request is unavailable.")


def _close_request(info, input, status):
    try:
        with transaction.atomic():
            permission = Permission.APP_APPROVE_DEPLOY if status == "rejected" else Permission.APP_UPDATE
            row, service, env, policy = locked_request(info, input, permission=permission)
            actor = approver_admission(info, env) if status == "rejected" else fresh_actor(info)
            if status == "cancelled" and row.requester_id != actor.pk:
                return failure("PERMISSION_DENIED", "Only the requester may cancel this request.")
            if row.status == status:
                return success(_request_type(row))
            if stale_request(row, service, env, policy):
                return success(_request_type(row))
            mismatch = _mismatch(row, input)
            if mismatch:
                return mismatch
            if row.status not in ("pending", "approved") or row.subscription_id:
                return failure("PRECONDITION", "This request cannot be closed.")
            row.status = status
            row.decided_at = timezone.now()
            row.updated_by = actor
            row.save(update_fields=["status", "decided_at", "updated_by", "updated_at", "version"])
            return success(_request_type(row))
    except ConnectionUnavailable:
        return failure("PRECONDITION", "Model connection request is unavailable.")
