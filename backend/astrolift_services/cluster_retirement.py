"""Cluster retirement must preserve the transport needed to remove shared models."""


def has_cluster_owned_models(cluster_id: int) -> bool:
    from astrolift_services.models import ManagedService

    return ManagedService.objects.filter(
        tenant_cluster_id=cluster_id,
        organization__isnull=False,
        kind="model_endpoint",
        variant="vllm",
    ).exists()


MODEL_CLEANUP_REQUIRED = "Remove all shared model deployments before retiring this cluster."
