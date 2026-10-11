"""Versioned model settings preserve the admitted source and credential owner."""

from enum import Enum

import strawberry

from astrolift_graphql import GUID
from astrolift_services.model_runtime_settings import ModelDtype


@strawberry.enum
class ModelSharingMode(Enum):
    SHARED = "shared"
    DEDICATED = "dedicated"


@strawberry.input
class UpdateClusterModelInput:
    organization_id: GUID
    id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int
    allow_subscriptions: bool
    cpu_request: str
    memory_request: str
    gpu_count: int
    cpu_kv_cache_gi_b: int | None = None
    name: str | None = None
    sharing_mode: ModelSharingMode | None = None
    dedicated_app_id: GUID | None = None
    if_match_dedicated_app_version: int | None = None
    dtype: ModelDtype | None = None
    max_model_len: int | None = None
    max_num_seqs: int | None = None


def update_placement(service, input):
    from astrolift_services.schema.cluster_models import ProvisionClusterModelInput

    config = service.config or {}
    source = {}
    if config.get("model_source") == "local_artifact":
        from _sdk.local_model_artifact import local_source_identity

        artifact_id, version, _ = local_source_identity(config)
        source.update(local_artifact_id=GUID(artifact_id), expected_artifact_version=version)
    elif config.get("model_source") in (None, "huggingface"):
        source.update(model_repo=config.get("model", ""), revision_sha=config.get("model_revision", ""))
    else:
        raise ValueError("Stored model source is unavailable. Refresh and retry.")
    return ProvisionClusterModelInput(
        organization_id=input.organization_id,
        cluster_id=input.expected_cluster_id,
        expected_provider_id=input.expected_provider_id,
        name=service.name if input.name is None else input.name,
        compute_mode=config.get("compute_mode", ""),
        cpu_request=input.cpu_request,
        memory_request=input.memory_request,
        gpu_count=input.gpu_count,
        allow_subscriptions=input.allow_subscriptions,
        cpu_kv_cache_gi_b=input.cpu_kv_cache_gi_b,
        dtype=input.dtype,
        max_model_len=input.max_model_len,
        max_num_seqs=input.max_num_seqs,
        **source,
    )


def validate_updated_source(service, placement, *, locked=False):
    from astrolift_services.model_admission import validate_cluster_request

    config, runtime = validate_cluster_request(placement, service.tenant_cluster, lock_source=locked)
    stored = service.config or {}
    if stored.get("model_source") == "local_artifact":
        if config.get("model_artifact_manifest_sha256") != stored.get("model_artifact_manifest_sha256"):
            raise ValueError("Stored model artifact identity changed. Refresh and retry.")
    elif service.model_hf_connection_id is not None:
        from astrolift_services.models import HuggingFaceConnection

        rows = HuggingFaceConnection.objects.filter(
            pk=service.model_hf_connection_id,
            organization_id=service.organization_id,
            version=service.model_hf_connection_version,
            organization__deleted_at__isnull=True,
        )
        if locked:
            rows = rows.select_for_update()
        if not rows.exists():
            raise ValueError("Stored Hugging Face connection changed or is unavailable. Refresh and retry.")
        expected = f"services/{service.organization.guid}/{service.guid}/huggingface#token"
        if stored.get("hf_token_secret_ref") != expected:
            raise ValueError("Stored Hugging Face credential owner is unavailable.")
        config["hf_token_secret_ref"] = expected
    elif stored.get("hf_token_secret_ref") is not None:
        raise ValueError("Stored Hugging Face credential owner is unavailable.")
    # Resource edits must not reset the runtime's other reviewed settings.
    from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver
    from k8s_native.managed.shared_model_runtime import shared_runtime

    preserved = dict(stored)
    for key in ("cpu", "memory", "gpu", "allow_subscriptions", "cpu_kv_cache_gib"):
        if key in config:
            preserved[key] = config[key]
        else:
            preserved.pop(key, None)
    for key in ("dtype", "max_model_len", "max_num_seqs"):
        if getattr(placement, key) is not None or key not in stored:
            if key in config:
                preserved[key] = config[key]
    if "runtime_controls_revision" in config:
        preserved["runtime_controls_revision"] = config["runtime_controls_revision"]
    config = VLLMDriver(config=VLLMConfig())._normalize(preserved)
    runtime = shared_runtime(
        (service.tenant_cluster.provider_config or {}).get("vllm_shared_runtimes", {}), config, "python"
    )
    return config, runtime


def dedicated_app(service):
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners

    config = service.config or {}
    if config.get("sharing_mode", "shared") != "dedicated":
        return None
    from uuid import UUID

    try:
        guid = UUID(str(config.get("dedicated_app_id")))
    except (TypeError, ValueError):
        return None
    return (
        live_app_owners(RegisteredApp.objects.filter(organization_id=service.organization_id, guid=guid))
        .filter(
            environments__tenant_cluster_id=service.tenant_cluster_id, environments__deleted_at__isnull=True
        )
        .exclude(provisioning_status__in=("tearing_down", "deregistered"))
        .first()
    )


def allows_app(service, app):
    config = service.config or {}
    if config.get("sharing_mode", "shared") == "shared":
        return app.organization_id == service.organization_id
    selected = dedicated_app(service)
    return selected is not None and selected.pk == app.pk


def sharing_config(service, input, *, locked=False):
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners

    stored = service.config or {}
    if input.sharing_mode is None:
        if input.dedicated_app_id is not None or input.if_match_dedicated_app_version is not None:
            raise ValueError("Select a sharing mode when changing the dedicated app.")
        mode = stored.get("sharing_mode", "shared")
        guid = stored.get("dedicated_app_id")
        expected_version = None
    else:
        mode = input.sharing_mode.value
        guid = input.dedicated_app_id
        expected_version = input.if_match_dedicated_app_version
    if mode == "shared":
        if guid is not None or expected_version is not None:
            raise ValueError("Shared models cannot select a dedicated app.")
        return {"sharing_mode": "shared"}
    if mode != "dedicated" or guid is None:
        raise ValueError("Dedicated models require a current app in this organization.")
    from uuid import UUID

    try:
        guid = UUID(str(guid))
    except (TypeError, ValueError):
        raise ValueError("Dedicated app is unavailable.") from None
    rows = live_app_owners(RegisteredApp.objects.filter(organization_id=service.organization_id, guid=guid))
    rows = rows.exclude(provisioning_status__in=("tearing_down", "deregistered"))
    if locked:
        rows = rows.select_for_update(of=("self",))
    app = rows.first()
    if app is None or input.sharing_mode is not None and app.version != expected_version:
        raise ValueError("Dedicated app changed or is unavailable. Refresh and retry.")
    from astrolift_lifecycle.models import AppEnvironment

    environments = AppEnvironment.objects.filter(
        registered_app=app, tenant_cluster_id=service.tenant_cluster_id
    )
    if locked:
        environments = environments.select_for_update(of=("self",))
    if environments.first() is None:
        raise ValueError("Dedicated app requires an environment in this model's cluster.")
    from django.db.models import F

    from astrolift_services.models import ManagedServiceAttachment

    if (
        ManagedServiceAttachment.all_objects.filter(managed_service=service, model_subscription=True)
        .exclude(app_environment__registered_app_id=app.pk)
        .exclude(
            subscription_status="revoked",
            desired_enabled=False,
            applied_revision=F("desired_revision"),
            reconciled_at__isnull=False,
        )
        .exists()
    ):
        raise ValueError(
            "Revoke other apps' subscriptions and confirm reconciliation before dedicating this model."
        )
    return {"sharing_mode": "dedicated", "dedicated_app_id": str(app.guid)}
