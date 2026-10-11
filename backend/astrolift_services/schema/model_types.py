"""Safe shared-model metadata; no configuration, handles or credential values."""

from datetime import datetime

import strawberry

from astrolift_graphql import GUID
from astrolift_services.cluster_models import model_binding_prefix
from astrolift_services.model_settings import ModelSharingMode, dedicated_app
from astrolift_services.models import ManagedService
from astrolift_services.schema.bedrock_model_types import NativeModelConnectionSourceType
from astrolift_services.schema.native_model_types import NativeModelConnectionType, NativeModelFamily


@strawberry.type(name="ModelResources")
class ModelResourcesType:
    cpu_request: str | None
    memory_request: str | None
    gpu_count: int | None
    replicas: int | None
    cpu_kv_cache_gi_b: int | None
    dtype: str | None = None
    max_model_len: int | None = None
    max_num_seqs: int | None = None


def model_resources(config):
    config = config if isinstance(config, dict) else {}
    return ModelResourcesType(
        dtype=config.get("dtype") if isinstance(config.get("dtype"), str) else None,
        max_model_len=config.get("max_model_len") if type(config.get("max_model_len")) is int else None,
        max_num_seqs=config.get("max_num_seqs") if type(config.get("max_num_seqs")) is int else None,
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
    native_source: NativeModelConnectionSourceType | None = None
    native_connection: NativeModelConnectionType | None = None
    source_kind: str = "unknown"
    local_artifact_id: GUID | None = None
    local_artifact_version: int | None = None
    local_manifest_sha256: str | None = None
    sharing_mode: ModelSharingMode = ModelSharingMode.SHARED
    dedicated_app_id: GUID | None = None
    dedicated_app_version: int | None = None
    dedicated_app_name: str | None = None
    dedicated_app_slug: str | None = None


_DEDICATED_APP_UNSET = object()


def dedicated_apps_for_services(services, *, permission=None):
    """Resolve coherent current dedicated owners without per-service fan-out."""
    from uuid import UUID

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners
    from astrolift_registry.visibility import visible_registry_apps

    identities = {}
    for service in services:
        config = service.config or {}
        if config.get("sharing_mode") == "dedicated":
            try:
                identities[service.pk] = UUID(str(config.get("dedicated_app_id")))
            except (TypeError, ValueError):
                pass
    if not identities:
        return {service.pk: None for service in services}
    rows = live_app_owners(RegisteredApp.objects.filter(guid__in=identities.values())).exclude(
        provisioning_status__in=("tearing_down", "deregistered")
    )
    if permission is not None:
        rows = visible_registry_apps(rows, permission)
    apps = {(app.organization_id, app.guid): app for app in rows} if identities else {}
    placements = (
        set(
            AppEnvironment.objects.filter(
                registered_app_id__in=[app.pk for app in apps.values()],
                tenant_cluster_id__in=[service.tenant_cluster_id for service in services],
            ).values_list("registered_app_id", "tenant_cluster_id")
        )
        if apps
        else set()
    )
    selected = {}
    for service in services:
        app = apps.get((service.organization_id, identities.get(service.pk)))
        if app is not None and (app.pk, service.tenant_cluster_id) not in placements:
            app = None
        selected[service.pk] = app
    return selected


def cluster_models_to_types(services):
    """Project one page with the scalar dedicated-app privacy boundary."""
    from core.permissions import Permission

    apps = dedicated_apps_for_services(services, permission=Permission.APP_READ)
    return [cluster_model_to_type(service, dedicated=apps.get(service.pk)) for service in services]


def cluster_model_to_type(service, *, dedicated=_DEDICATED_APP_UNSET):
    import re

    from k8s_native.managed.shared_model_runtime import shared_runtime

    from astrolift_services.model_admission import canonical_model_handle

    config = service.config if isinstance(service.config, dict) else {}
    cluster = service.tenant_cluster
    provider = cluster.provider_plugin
    available = cluster.is_active and cluster.lifecycle == "managed" and provider.is_enabled
    runtime_supported, runtime_reason = None, None
    from astrolift_services.native_model_projection import native_connection_to_type

    native_connection = native_connection_to_type(service)
    native = native_connection is not None
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
    elif config.get("model_source") in (None, "huggingface") and isinstance(config.get("model"), str):
        source_kind = "huggingface"
    revision = config.get("model_revision")
    app = dedicated_app(service) if dedicated is _DEDICATED_APP_UNSET else dedicated
    if app is not None and dedicated is _DEDICATED_APP_UNSET:
        from astrolift_registry.models import RegisteredApp
        from astrolift_registry.visibility import visible_registry_apps
        from core.permissions import Permission

        if not visible_registry_apps(RegisteredApp.objects.filter(pk=app.pk), Permission.APP_READ).exists():
            app = None
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
    native_source = None
    if native:
        native_source = (
            native_connection.source if native_connection.family == NativeModelFamily.BEDROCK else None
        )
        ready = runtime_supported = None
        runtime_reason = (
            "Native Bedrock connection; Kubernetes model runtime is inapplicable."
            if native_connection.family == NativeModelFamily.BEDROCK
            else "Native connection; Kubernetes model runtime is inapplicable."
        )
        stored_native = config.get("native_connection")
        stored_kind = stored_native.get("source_kind") if isinstance(stored_native, dict) else None
        source_kind = (
            "bedrock_"
            + (
                native_source.source_kind.value
                if native_source is not None
                else stored_kind
                if stored_kind in ("foundation_model", "inference_profile")
                else "unknown"
            )
            if native_connection.family == NativeModelFamily.BEDROCK
            else native_connection.source_kind.value
        )

    return ClusterModelDeploymentType(
        native_source=native_source,
        native_connection=native_connection,
        id=GUID(str(service.guid)),
        version=service.version,
        name=service.name,
        organization_id=GUID(str(service.organization.guid)),
        cluster_id=GUID(str(cluster.guid)),
        provider_id=GUID(str(provider.guid)),
        cluster_slug=cluster.slug,
        cluster_name=cluster.name,
        model_repo="" if native else str(config.get("model") or ""),
        revision_sha=None if native else revision,
        source_kind=source_kind,
        local_artifact_id=None if native else artifact_id,
        local_artifact_version=None if native else artifact_version,
        local_manifest_sha256=None if native else manifest_sha256,
        sharing_mode=ModelSharingMode.DEDICATED
        if config.get("sharing_mode") == "dedicated"
        else ModelSharingMode.SHARED,
        dedicated_app_id=GUID(str(app.guid)) if app else None,
        dedicated_app_version=app.version if app else None,
        dedicated_app_name=app.name if app else None,
        dedicated_app_slug=app.slug if app else None,
        compute_mode=config.get("compute_mode")
        if not native and config.get("compute_mode") in ("cpu", "gpu")
        else None,
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
        readiness_observed_at=None if native else service.model_ready_observed_at,
        readiness_generation=None if native else service.model_ready_generation,
        desired_subscription_revision=service.subscription_revision,
        applied_subscription_revision=service.applied_subscription_revision,
        operation_id=service.operation_workflow_id or None,
        operation_started_at=service.operation_started_at,
        operation_completed_at=service.operation_completed_at,
        desired_resources=model_resources({} if native else config),
        applied_resources=model_resources(service.applied_config)
        if not native and service.applied_config is not None
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
