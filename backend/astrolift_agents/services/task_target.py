"""Persist agent placement and serialize spawn/stop across worker processes."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager


class TaskControlBusy(RuntimeError):
    pass


@contextmanager
def task_control_lock(task_pk: int):
    from django.db import connection

    if connection.vendor != "postgresql":
        raise RuntimeError("Agent task control requires PostgreSQL locking")
    # A session lock spans short commits: task identity must remain durable
    # even if the worker dies during an external container create.
    key = -(1 << 62) + int(task_pk)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        if not cursor.fetchone()[0]:
            raise TaskControlBusy("Task dispatch or control is still in progress; retry cleanup")
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def docker_daemon_id() -> str:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ID}}"], capture_output=True, text=True, timeout=5
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot verify the Docker daemon for this task")
    return result.stdout.strip()


def resolve_task_target(task):
    from astrolift_clusters.models import TenantCluster

    target = task.dispatch_target
    if not isinstance(target, dict) or target.get("version") != 1:
        raise RuntimeError("Task has no verified dispatch target; placement must be recovered before cleanup")
    backend, namespace = target.get("backend"), target.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("Task dispatch target has no namespace")
    if backend == "local_docker":
        if not target.get("docker_daemon_id") or target["docker_daemon_id"] != docker_daemon_id():
            raise RuntimeError("Task belongs to a different Docker daemon")
        return backend, None, namespace
    if backend != "k8s_job" or not target.get("cluster_guid"):
        raise RuntimeError("Task dispatch backend or cluster is unknown")
    cluster = TenantCluster.all_objects.filter(guid=target["cluster_guid"]).first()
    if cluster is None or cluster.organization_id not in (None, task.organization_id):
        raise RuntimeError("Task dispatch cluster is missing or belongs to another organization")
    if cluster.endpoint != target.get("cluster_endpoint"):
        raise RuntimeError("Task dispatch cluster endpoint changed; refusing to use replacement placement")
    return backend, cluster, namespace


def freeze_task_target(task, *, backend: str, cluster, namespace: str):
    if task.dispatch_target:
        return resolve_task_target(task)
    if backend not in {"k8s_job", "local_docker"}:
        raise RuntimeError(f"Unsupported agent dispatch backend {backend!r}")
    if backend == "k8s_job" and (
        cluster is None or cluster.organization_id not in (None, task.organization_id)
    ):
        raise RuntimeError("Agent dispatch cluster is missing or belongs to another organization")
    task.dispatch_target = {
        "version": 1,
        "backend": backend,
        "namespace": namespace,
        "cluster_guid": str(cluster.guid) if cluster is not None else "",
        "cluster_endpoint": cluster.endpoint if cluster is not None else "",
        "docker_daemon_id": docker_daemon_id() if backend == "local_docker" else "",
        "planned_external_id": f"agent-task-{str(task.guid).replace('-', '')}",
    }
    task.save(update_fields=["dispatch_target", "updated_at", "version"])
    return backend, cluster, namespace


def spawner_for_task(task):
    from astrolift_dispatch.spawners.registry import get_spawner

    backend, cluster, namespace = resolve_task_target(task)
    return get_spawner(backend, cluster=cluster, namespace=namespace)
