"""Bounded request validation and canonical facts for shared model operations."""

from uuid import UUID

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization
from astrolift_identity.operation_context import UNKNOWN, OperationContext
from astrolift_services.cluster_models import available_model_clusters, live_cluster_model_by_guid
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def current_org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def in_current_org(guid):
    try:
        parsed = UUID(str(guid))
    except (ValueError, TypeError, AttributeError):
        return False
    return Organization.objects.filter(pk=current_org_id(), guid=parsed, deleted_at__isnull=True).exists()


def shared_model_operation(field="input.id"):
    def load(args):
        service = live_cluster_model_by_guid(read_guid(args, field))
        return (
            (OperationContext(region=service.tenant_cluster.region or None, approvals=0),)
            if service
            else UNKNOWN
        )

    return load


def shared_cluster_operation(field="input.cluster_id"):
    def load(args):
        guid = read_guid(args, field)
        cluster = (
            available_model_clusters(TenantCluster.objects.filter(guid=guid), current_org_id()).first()
            if guid
            else None
        )
        return (OperationContext(region=cluster.region or None, approvals=0),) if cluster else UNKNOWN

    return load


def request_config(input, *, lock_source=False):
    from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver

    cfg = {
        "compute_mode": input.compute_mode,
        "frontend": "python",
        "gpu": input.gpu_count,
        "cpu": input.cpu_request,
        "memory": input.memory_request,
        "replicas": 1,
        "tensor_parallel_size": 1 if input.compute_mode == "cpu" else input.gpu_count,
        "dtype": "bfloat16" if input.compute_mode == "cpu" else "auto",
        "allow_subscriptions": input.allow_subscriptions,
    }
    if input.local_artifact_id is not None:
        if (
            input.model_repo is not None
            or input.revision_sha is not None
            or input.connection_id is not None
            or input.expected_connection_version is not None
        ):
            raise ValueError("Select either an immutable local artifact or a Hugging Face source, not both.")
        from astrolift_services.local_model_artifacts import validate_artifact_request

        cfg.update(
            validate_artifact_request(
                current_org_id(), input.local_artifact_id, input.expected_artifact_version, locked=lock_source
            )
        )
    else:
        if input.expected_artifact_version is not None:
            raise ValueError("Select a current verified local model artifact.")
        cfg.update(model=input.model_repo, model_revision=input.revision_sha)
    if input.cpu_kv_cache_gi_b is not None:
        cfg["cpu_kv_cache_gib"] = input.cpu_kv_cache_gi_b
    if (
        not isinstance(input.name, str)
        or not 1 <= len(input.name.strip()) <= 128
        or any(ord(char) < 32 for char in input.name)
    ):
        raise ValueError("Model deployment name must contain 1 to 128 visible characters.")
    return VLLMDriver(config=VLLMConfig())._normalize(cfg)


def validate_cluster_request(input, cluster, *, lock_source=False):
    from k8s_native.managed.shared_model_runtime import shared_runtime

    config = request_config(input, lock_source=lock_source)
    provider_config = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    runtime = shared_runtime(provider_config.get("vllm_shared_runtimes", {}), config, "python")
    return config, runtime


def canonical_model_handle(service):
    """One pure recorded-target contract shared by writes and readiness projection."""
    from _sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name
    from k8s_native.managed._handle import pack

    return pack(
        kind="model_endpoint",
        cluster_id=str(service.tenant_cluster.guid),
        namespace=cluster_model_namespace(
            organization_id=str(service.organization.guid),
            cluster_id=str(service.tenant_cluster.guid),
            managed_service_id=str(service.guid),
        ),
        name=cluster_model_resource_name(str(service.guid)),
    )


def with_canonical_model_handle(rows):
    """Filter recorded targets before pagination using the SDK's UUID-only naming contract.

    The three UUID namespace input always exceeds 63 characters. Its 52-character
    prefix ends in the first UUID's trailing separator, which dns_label trims.
    Regression tests compare this expression with the SDK producer.
    """
    from django.db.models import BinaryField, CharField, F, Func, Value
    from django.db.models.functions import Cast, Concat, Substr

    org = Cast(F("organization__guid"), CharField())
    cluster = Cast(F("tenant_cluster__guid"), CharField())
    service = Cast(F("guid"), CharField())
    raw = Concat(Value("astrolift-model-"), org, Value("-"), cluster, Value("-"), service)
    raw_bytes = Func(raw, Value("UTF8"), function="convert_to", output_field=BinaryField())
    digest = Func(raw_bytes, function="sha256", output_field=BinaryField())
    hex_digest = Func(digest, Value("hex"), function="encode", output_field=CharField())
    namespace = Concat(Value("astrolift-model-"), org, Value("-"), Substr(hex_digest, 1, 10))
    return rows.annotate(
        _canonical_model_handle=Concat(
            Value("model_endpoint/"), cluster, Value("/"), namespace, Value("/vllm-"), service
        )
    )
