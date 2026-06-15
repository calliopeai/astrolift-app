"""Spawner registry — maps backend names to ContainerSpawner implementations (#49)."""

from __future__ import annotations

from astrolift_dispatch.spawners.base import ContainerSpawner


def get_spawner(backend: str, *, cluster=None, namespace: str = "default") -> ContainerSpawner:
    """Return the spawner for the given backend name.

    Args:
        backend: one of "k8s_job", "local_docker"
        cluster: TenantCluster model instance (required for k8s_job)
        namespace: K8s namespace for the spawner (k8s_job only)
    """
    if backend == "k8s_job":
        from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner

        return K8sJobSpawner(cluster=cluster, namespace=namespace)
    if backend == "local_docker":
        from astrolift_dispatch.spawners.local_docker import LocalDockerSpawner

        return LocalDockerSpawner()
    raise ValueError(f"Unknown spawn backend: {backend!r}. Valid: k8s_job, local_docker")
