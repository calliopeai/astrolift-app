"""Safe shared-model metadata; no configuration, handles or credential values."""

from datetime import datetime

import strawberry

from astrolift_graphql import GUID
from astrolift_services.cluster_models import model_binding_prefix
from astrolift_services.models import ManagedService


@strawberry.type(name="ModelResources")
class ModelResourcesType:
    cpu_request: str | None
    memory_request: str | None
    gpu_count: int | None
    replicas: int | None
    cpu_kv_cache_gi_b: int | None


def model_resources(config):
    config = config if isinstance(config, dict) else {}
    return ModelResourcesType(
        cpu_request=config.get("cpu") if isinstance(config.get("cpu"), str) else None,
        memory_request=config.get("memory") if isinstance(config.get("memory"), str) else None,
        gpu_count=config.get("gpu") if type(config.get("gpu")) is int else None,
        replicas=config.get("replicas") if type(config.get("replicas")) is int else None,
        cpu_kv_cache_gi_b=config.get("cpu_kv_cache_gib")
        if type(config.get("cpu_kv_cache_gib")) is int
        else None,
    )


@strawberry.type(name="ClusterModelDeployment")
class ClusterModelDeploymentType:
    id: GUID
    version: int
    name: str
    organization_id: GUID
    cluster_id: GUID
    provider_id: GUID
    cluster_slug: str
    cluster_name: str
    model_repo: str
    revision_sha: str | None
    compute_mode: str | None
    subscriptions_enabled: bool
    runtime_supported: bool | None
    runtime_reason: str | None
    status: str
    reason: str | None
    ready: bool | None
    readiness_observed_at: datetime | None
    readiness_generation: int | None
    desired_subscription_revision: int
    applied_subscription_revision: int
    operation_id: str | None
    operation_started_at: datetime | None
    operation_completed_at: datetime | None
    desired_resources: ModelResourcesType
    applied_resources: ModelResourcesType | None
    source_kind: str = "unknown"
    local_artifact_id: GUID | None = None
    local_artifact_version: int | None = None
    local_manifest_sha256: str | None = None


def cluster_model_to_type(service):
    import re

    from k8s_native.managed.shared_model_runtime import shared_runtime

    from astrolift_services.model_admission import canonical_model_handle

    config = service.config if isinstance(service.config, dict) else {}
    cluster = service.tenant_cluster
    provider = cluster.provider_plugin
    available = cluster.is_active and cluster.lifecycle == "managed" and provider.is_enabled
    runtime_supported, runtime_reason = None, None
    try:
        shared_runtime(
            (cluster.provider_config if isinstance(cluster.provider_config, dict) else {}).get(
                "vllm_shared_runtimes", {}
            ),
            config,
            config.get("frontend", ""),
        )
        runtime_supported = True
    except (TypeError, ValueError):
        runtime_supported = False
        runtime_reason = "Supported shared model runtime admission is unavailable for the stored request."
    source_kind, artifact_id, artifact_version, manifest_sha256 = "unknown", None, None, None
    if config.get("model_source") == "local_artifact":
        from _sdk.local_model_artifact import local_source_identity

        try:
            source_id, artifact_version, manifest_sha256 = local_source_identity(config)
            artifact_id = GUID(source_id)
            source_kind = "local_artifact"
        except ValueError:
            artifact_id, artifact_version, manifest_sha256 = None, None, None
    elif not config.get("model_source") and isinstance(config.get("model"), str):
        source_kind = "huggingface"
    revision = config.get("model_revision")
    revision = revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision) else None
    ready = (
        available
        and service.status == ManagedService.Status.ACTIVE
        and service.applied_config is not None
        and service.model_ready_observed_at is not None
        and type(service.model_ready_generation) is int
        and service.model_ready_generation > 0
        and service.model_ready_provider_guid == provider.guid
        and service.model_ready_auth_revision == service.subscription_revision
        and service.applied_subscription_revision == service.subscription_revision
        and bool(service.backend_ref)
        and service.model_ready_backend_ref == service.backend_ref
        and service.backend_ref == canonical_model_handle(service)
        and isinstance(service.applied_config, dict)
        and type(service.applied_config.get("replicas", 1)) is int
        and service.applied_config.get("replicas", 1) > 0
    )
    return ClusterModelDeploymentType(
        id=GUID(str(service.guid)),
        version=service.version,
        name=service.name,
        organization_id=GUID(str(service.organization.guid)),
        cluster_id=GUID(str(cluster.guid)),
        provider_id=GUID(str(provider.guid)),
        cluster_slug=cluster.slug,
        cluster_name=cluster.name,
        model_repo=str(config.get("model") or ""),
        revision_sha=revision,
        source_kind=source_kind,
        local_artifact_id=artifact_id,
        local_artifact_version=artifact_version,
        local_manifest_sha256=manifest_sha256,
        compute_mode=config.get("compute_mode") if config.get("compute_mode") in ("cpu", "gpu") else None,
        subscriptions_enabled=config.get("allow_subscriptions") is True,
        runtime_supported=runtime_supported,
        runtime_reason=runtime_reason,
        status=service.status,
        reason="Model placement is unavailable."
        if not available
        else (
            "Model reconciliation failed; operator review is required."
            if service.status == "failed"
            else None
        ),
        ready=ready,
        readiness_observed_at=service.model_ready_observed_at,
        readiness_generation=service.model_ready_generation,
        desired_subscription_revision=service.subscription_revision,
        applied_subscription_revision=service.applied_subscription_revision,
        operation_id=service.operation_workflow_id or None,
        operation_started_at=service.operation_started_at,
        operation_completed_at=service.operation_completed_at,
        desired_resources=model_resources(config),
        applied_resources=model_resources(service.applied_config)
        if service.applied_config is not None
        else None,
    )


@strawberry.type(name="ModelSubscription")
class ModelSubscriptionType:
    id: GUID
    version: int
    model_deployment_id: GUID
    app_id: GUID
    app_slug: str
    app_name: str
    environment_id: GUID
    environment_name: str
    alias: str
    binding_prefix: str
    status: str
    can_revoke: bool
    desired_enabled: bool
    desired_revision: int
    applied_revision: int
    reason: str | None
    reconcile_started_at: datetime | None
    reconciled_at: datetime | None


def model_subscription_to_type(row, *, can_revoke=False):
    env = row.app_environment
    app = env.registered_app
    return ModelSubscriptionType(
        id=GUID(str(row.guid)),
        version=row.version,
        model_deployment_id=GUID(str(row.managed_service.guid)),
        app_id=GUID(str(app.guid)),
        app_slug=app.slug,
        app_name=app.name,
        environment_id=GUID(str(env.guid)),
        environment_name=env.name,
        alias=row.binding_alias,
        binding_prefix=model_binding_prefix(row.binding_alias),
        status=row.subscription_status,
        can_revoke=can_revoke,
        desired_enabled=row.desired_enabled,
        desired_revision=row.desired_revision,
        applied_revision=row.applied_revision,
        reason="Subscription reconciliation failed; retry or operator review is required."
        if row.subscription_status == "failed"
        else None,
        reconcile_started_at=row.reconcile_started_at,
        reconciled_at=row.reconciled_at,
    )


@strawberry.type(name="ModelSubscriptionOperation")
class ModelSubscriptionOperationType:
    deployment: ClusterModelDeploymentType
    subscription: ModelSubscriptionType
    restart_required: bool = True


@strawberry.type(name="ModelSubscriptionTarget")
class ModelSubscriptionTargetType:
    environment_id: GUID
    environment_version: int
    app_id: GUID
    app_slug: str
    app_name: str
    environment_name: str
    cluster_id: GUID
    eligible: bool
    reason: str | None


@strawberry.type(name="ModelRuntimeAdmission")
class ModelRuntimeAdmissionType:
    eligible: bool
    reason: str | None
    runtime_version: str | None
    architecture: str | None
    hardware_admission: str = "unknown"
