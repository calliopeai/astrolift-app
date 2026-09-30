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


def request_config(input):
    from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver

    cfg = {
        "model": input.model_repo,
        "model_revision": input.revision_sha,
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
    if input.cpu_kv_cache_gi_b is not None:
        cfg["cpu_kv_cache_gib"] = input.cpu_kv_cache_gi_b
    if (
        not isinstance(input.name, str)
        or not 1 <= len(input.name.strip()) <= 128
        or any(ord(char) < 32 for char in input.name)
    ):
        raise ValueError("Model deployment name must contain 1 to 128 visible characters.")
    return VLLMDriver(config=VLLMConfig())._normalize(cfg)


def validate_cluster_request(input, cluster):
    from k8s_native.managed.shared_model_runtime import shared_runtime

    config = request_config(input)
    provider_config = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    runtime = shared_runtime(provider_config.get("vllm_shared_runtimes", {}), config, "python")
    return config, runtime
