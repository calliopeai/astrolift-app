"""K8s Job spawn backend for agent task dispatch (#49).

Spawns agent workloads as batch/v1 Jobs in the target cluster.
One Job per AgentTask. Job name: ``agent-task-<task_guid_prefix>``.

The pod uses the Workload's image + the Brief env vars injected by
brief_injector.inject_brief_into_job_spec().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from astrolift_dispatch.spawners.base import ContainerSpawner, SpawnResult, TaskStatus

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask

logger = logging.getLogger(__name__)

# Container port the agent's raw RFB server (x11vnc) listens on when VNC
# is enabled (mirrors core.schema.vnc_ws.VNC_PORT, the relay's
# port-forward target). The -vnc image serves raw RFB here; there is no
# noVNC/websockify in the pod — the control-plane relay is the websockify.
VNC_PORT = 5900


class K8sJobSpawner(ContainerSpawner):
    """Spawn agent tasks as batch/v1 K8s Jobs."""

    def __init__(self, cluster, namespace: str) -> None:
        self._cluster = cluster
        self._namespace = namespace

    def spawn(self, task: AgentTask) -> SpawnResult:
        """Create a K8s Job for the given AgentTask."""
        from astrolift_dispatch.brief_injector import inject_brief_into_job_spec
        from astrolift_dispatch.snapshot_injector import inject_snapshot_into_job_spec
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        workload = task.agent_definition
        if workload is None:
            return SpawnResult(external_id="", ok=False, error="task has no agent_definition")

        job_name = f"agent-task-{str(task.guid).replace('-', '')[:12]}"

        # Build a minimal Job manifest from the agent workload
        job_manifest = _render_agent_job(
            job_name=job_name,
            workload=workload,
            namespace=self._namespace,
            task=task,
        )

        # Inject Brief env vars
        job_manifest = inject_brief_into_job_spec(job_manifest, task)
        # Provision + inject the per-task VNC snapshot PUT URL (no-op for
        # non-VNC tasks and when no blob store is configured).
        job_manifest = inject_snapshot_into_job_spec(job_manifest, task)

        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            result = driver.apply_manifests(ctx.slug, self._namespace, [job_manifest])
            if not getattr(result, "ok", False):
                error = result.summary() if hasattr(result, "summary") else "apply failed"
                logger.warning("k8s_job_spawner: apply failed for Job %s: %s", job_name, error)
                return SpawnResult(external_id=job_name, ok=False, error=str(error))
            logger.info("k8s_job_spawner: created Job %s for task %s", job_name, task.guid)
            return SpawnResult(external_id=job_name)
        except Exception as exc:
            logger.exception("k8s_job_spawner: failed to create Job %s", job_name)
            return SpawnResult(external_id=job_name, ok=False, error=str(exc))

    def status(self, external_id: str) -> TaskStatus:
        """Poll the K8s Job status."""
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            ws = driver.get_workload_status(ctx.slug, self._namespace, "Job", external_id)
            conditions = ws.conditions or []

            succeeded = any(
                str(c.get("type")) == "Complete" and str(c.get("status")) == "True"
                for c in conditions
            )
            failed = any(
                str(c.get("type")) == "Failed" and str(c.get("status")) == "True"
                for c in conditions
            )

            return TaskStatus(
                running=not succeeded and not failed,
                succeeded=succeeded,
                failed=failed,
            )
        except Exception as exc:
            return TaskStatus(failed=True, error_message=str(exc))

    def stop(self, external_id: str) -> None:
        """Delete the K8s Job (and its pod) for a running task."""
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        job_ref = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": external_id, "namespace": self._namespace},
        }
        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            driver.delete_manifests(ctx.slug, self._namespace, [job_ref])
            logger.info("k8s_job_spawner: deleted Job %s", external_id)
        except Exception:  # noqa: BLE001
            logger.exception("k8s_job_spawner: failed to delete Job %s", external_id)


def _vnc_image(image: str) -> str:
    """Return the ``-vnc`` variant of an image ref.

    Appends ``-vnc`` to the repository component while preserving the tag
    or digest, e.g.::

        docker.io/calliopeai/astrolift-agent-claude:latest  ->
        docker.io/calliopeai/astrolift-agent-claude-vnc:latest

        docker.io/calliopeai/agent@sha256:abcd  ->
        docker.io/calliopeai/agent-vnc@sha256:abcd

    A digest-pinned ref (``...@sha256:...``) must be split on ``@``: the
    digest itself contains a ``:`` so an rpartition on ``:`` would slice
    inside the digest and yield a corrupt, unpullable ref. Otherwise the
    tag separator is the last ``:`` that is not part of a registry
    ``host:port`` (it must come after the last ``/``). Idempotent: an
    already ``-vnc`` repo is returned unchanged.
    """
    # Digest-pinned ref: everything before "@" is the repo, the rest
    # (including its internal ":") is the digest and must stay intact.
    if "@" in image:
        repo, sep, digest = image.partition("@")
        if repo.endswith("-vnc"):
            return image
        return f"{repo}-vnc{sep}{digest}"

    repo, sep, tag = image.rpartition(":")
    # A ":" before the final "/" is a registry port, not a tag separator.
    if not sep or "/" not in repo or "/" in tag:
        repo, sep, tag = image, "", ""
    if repo.endswith("-vnc"):
        return image
    return f"{repo}-vnc{sep}{tag}"


def _resolve_base_image(workload, spec) -> str:
    """Resolve the base agent image, honouring the runtime catalog.

    Precedence (most specific wins):
      1. An explicit ``spec.image_tag`` — a pinned private/ECR ref.
      2. The spec's ``runtime`` short-name resolved through the public
         runtime catalog (``docker.io/calliopeai/astrolift-agent-<name>``).
      3. The workload's primary container image.
      4. A distroless placeholder when the workload has no container.

    The ``-vnc`` watchable variant is applied by the caller on top of this
    base, never here.
    """
    if spec is not None and spec.image_tag:
        return spec.image_tag
    if spec is not None and getattr(spec, "runtime", ""):
        from astrolift_agents.runtime_catalog import (
            is_known_runtime,
            resolve_runtime_image,
        )

        if is_known_runtime(spec.runtime):
            return resolve_runtime_image(spec.runtime)
    primary_container = workload.containers.filter(is_primary=True).first() if workload else None
    if primary_container and primary_container.image_ref:
        return primary_container.image_ref
    return "gcr.io/distroless/base"


def _render_agent_job(*, job_name: str, workload, namespace: str, task) -> dict:
    """Build a minimal batch/v1 Job manifest for an agent workload."""
    primary_container = workload.containers.filter(is_primary=True).first()
    port = primary_container.port if primary_container else 0

    spec = getattr(task, "environment_spec", None)
    image = _resolve_base_image(workload, spec)

    # VNC-capable runs swap to the -vnc image variant and expose the
    # raw RFB port (5900) so the ASGI relay can port-forward into it.
    vnc = bool(getattr(task, "vnc_enabled", False))
    if vnc:
        image = _vnc_image(image)

    ports = [{"containerPort": port}] if port else []
    if vnc:
        ports = [p for p in ports if p["containerPort"] != VNC_PORT]
        ports.append({"containerPort": VNC_PORT})

    # Honour the primary container's command/args overrides. Both are
    # JSONField(default=list); only set the corresponding Job keys when
    # non-empty so an unconfigured container preserves the image's own
    # ENTRYPOINT/CMD (omitting the keys is not the same as setting them
    # to []). ``command`` maps to the pod's ``command`` (ENTRYPOINT) and
    # ``args`` to ``args`` (CMD).
    command = list(primary_container.command or []) if primary_container else []
    args = list(primary_container.args or []) if primary_container else []

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "astrolift.dev/workload-kind": "agent",
                "astrolift.dev/task-id": str(task.guid),
            },
        },
        "spec": {
            "backoffLimit": 0,  # No retries — AgentTask handles retry logic
            "completions": 1,
            "template": {
                "metadata": {
                    "labels": {
                        "astrolift.dev/task-id": str(task.guid),
                        "astrolift.dev/workload-kind": "agent",
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "agent",
                            "image": image,
                            "env": [],
                            **({"command": command} if command else {}),
                            **({"args": args} if args else {}),
                            **({"ports": ports} if ports else {}),
                        }
                    ],
                },
            },
        },
    }
