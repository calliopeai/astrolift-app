"""Owner-authorized shared model prompts through the existing bounded agent relay."""

from functools import wraps

import strawberry
from django.db import transaction
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_clusters import agent_test_jobs
from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.abac import operation_attributes
from astrolift_identity.operation_context import OperationContext
from astrolift_services.cluster_models import cluster_model_org_scope, live_cluster_model_by_guid
from astrolift_services.model_prompt import (
    PromptReadinessState,
    shared_agent_test_target,
    shared_prompt_readiness,
)
from astrolift_services.models import ManagedService
from astrolift_services.schema.model_reads import _catalogue_audience
from astrolift_services.schema.types import ModelEndpointTestType, ModelPromptReadinessType
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, PermissionDenied, check_permission, require_permission
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


@strawberry.input
class TestSharedModelEndpointInput:
    managed_service_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    expected_version: int
    prompt: str


def _operation(field):
    def operation(args):
        service = live_cluster_model_by_guid(read_guid(args, field))
        return (
            OperationContext(
                environment=None,
                region=(service.tenant_cluster.region or None) if service else None,
                approvals=0,
            ),
        )

    return operation


def _permission_errors(resolver):
    @wraps(resolver)
    def wrapped(*args, **kwargs):
        try:
            return resolver(*args, **kwargs)
        except PermissionDenied as exc:
            raise GraphQLError(
                "Shared model prompt access is denied", extensions={"code": "PERMISSION_DENIED"}
            ) from exc

    return wrapped


def _target(guid, cluster_id, provider_id, version, *, lock=False):
    service = live_cluster_model_by_guid(guid)
    if service is None:
        return None
    if lock:
        # Configuration/subscription mutations serialize on this same owner row.
        ManagedService.objects.select_for_update().get(pk=service.pk)
        service = live_cluster_model_by_guid(guid)
        if service is None:
            return None
        cluster = TenantCluster.objects.select_for_update().get(pk=service.tenant_cluster_id)
        type(cluster.provider_plugin).objects.select_for_update().get(pk=cluster.provider_plugin_id)
        service = live_cluster_model_by_guid(guid)
        if service is None:
            return None
    if (
        type(version) is not int
        or service.version != version
        or str(service.tenant_cluster.guid) != str(cluster_id)
        or str(service.tenant_cluster.provider_plugin.guid) != str(provider_id)
    ):
        raise GraphQLError("Shared model prompt target changed", extensions={"code": "PRECONDITION"})
    return service


def _readiness_type(service):
    state = shared_prompt_readiness(service)
    return ModelPromptReadinessType(
        state=state,
        eligible=state == PromptReadinessState.READY,
        max_prompt_chars=agent_test_jobs.MAX_PROMPT_CHARS,
        max_output_tokens=agent_test_jobs.MAX_TOKENS,
        prompts_per_minute=agent_test_jobs.RATE_LIMIT_PER_MINUTE,
        max_wait_seconds=int(
            agent_test_jobs.wait_cap_seconds(service.tenant_cluster.heartbeat_interval_seconds)
        ),
    )


@strawberry.type
class SharedModelPromptQuery:
    @strawberry.field
    @_permission_errors
    @require_permission(
        Permission.CLUSTER_UPDATE,
        scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE),
        operation=_operation("id"),
    )
    @tenant_scoped()
    def astrolift_shared_model_prompt_readiness(
        self,
        info: Info,
        id: GUID,
        expected_cluster_id: GUID,
        expected_provider_id: GUID,
        expected_version: int,
    ) -> ModelPromptReadinessType | None:
        _catalogue_audience(info)
        service = _target(id, expected_cluster_id, expected_provider_id, expected_version)
        return _readiness_type(service) if service else None


@strawberry.type
class SharedModelPromptMutations:
    @strawberry.field
    @mutation_audit(
        action="cluster_model.test_prompt",
        extras=lambda result: {"status": result.data.status} if result.ok and result.data else None,
    )
    @require_permission(
        Permission.CLUSTER_UPDATE,
        scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE),
        operation=_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def test_shared_model_endpoint(
        self, info: Info, input: TestSharedModelEndpointInput
    ) -> MutationResultType[ModelEndpointTestType]:
        _catalogue_audience(info)
        prompt = input.prompt.strip()
        if not prompt or len(prompt) > agent_test_jobs.MAX_PROMPT_CHARS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"prompt must contain 1 to {agent_test_jobs.MAX_PROMPT_CHARS} characters",
                field="prompt",
            )
        tenant = get_current_tenant()
        actor = tenant.actor_user_id if tenant else None
        with transaction.atomic():
            try:
                service = _target(
                    input.managed_service_id,
                    input.expected_cluster_id,
                    input.expected_provider_id,
                    input.expected_version,
                    lock=True,
                )
            except GraphQLError:
                return gql_failure(ErrorCode.PRECONDITION.value, "Shared model prompt target changed")
            if service is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "Shared model deployment not found")
            _catalogue_audience(info)
            with operation_attributes(
                environment=None, region=service.tenant_cluster.region or None, approvals=0
            ):
                check_permission(
                    Permission.CLUSTER_UPDATE, scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE)({})
                )
            if shared_prompt_readiness(service) != PromptReadinessState.READY:
                return gql_failure(ErrorCode.PRECONDITION.value, "Shared model prompt relay is not ready")
            target = shared_agent_test_target(service)
            cluster = service.tenant_cluster
            try:
                agent_test_jobs.check_rate_limit(actor or 0)
                job_id = agent_test_jobs.enqueue(
                    cluster_guid=str(cluster.guid),
                    managed_service_guid=str(service.guid),
                    prompt=prompt,
                    model=target.model,
                    base_url=target.base_url,
                    secret_namespace=target.api_key_secret_namespace,
                    secret_name=target.api_key_secret_name,
                    secret_key=target.api_key_secret_key,
                    requested_by_user_id=actor,
                )
            except agent_test_jobs.AgentTestRateLimited:
                return gql_failure(ErrorCode.RATE_LIMITED.value, "Shared model prompt rate limit reached")
            except agent_test_jobs.AgentTestConflict:
                return gql_failure(
                    ErrorCode.CONFLICT.value, "A model prompt is already running on this cluster"
                )
            except agent_test_jobs.AgentTestUnavailable:
                return gql_failure(ErrorCode.PRECONDITION.value, "Shared model prompt relay is unavailable")
        # Release the owner row before waiting; an unavailable agent cannot hold
        # model configuration/subscription transactions for the whole wait cap.
        try:
            job = agent_test_jobs.await_result(
                job_id, heartbeat_interval_seconds=cluster.heartbeat_interval_seconds
            )
        except agent_test_jobs.AgentTestUnavailable:
            return gql_failure(ErrorCode.PRECONDITION.value, "Shared model prompt result is unavailable")
        return gql_success(
            ModelEndpointTestType(
                status="timed_out"
                if job is None
                else "succeeded"
                if job["status"] == agent_test_jobs.SUCCEEDED
                else "failed",
                reply=job["reply"] if job else "",
                latency_ms=job["latency_ms"] if job else None,
                prompt_tokens=job["prompt_tokens"] if job else None,
                completion_tokens=job["completion_tokens"] if job else None,
                total_tokens=job["total_tokens"] if job else None,
                error=job["error"]
                if job
                else "The cluster agent did not respond before the bounded wait expired",
            )
        )
