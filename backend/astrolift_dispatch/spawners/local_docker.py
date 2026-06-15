"""Local Docker spawn backend for agent task dispatch — dev mode (#49)."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from astrolift_dispatch.spawners.base import ContainerSpawner, SpawnResult, TaskStatus

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask

logger = logging.getLogger(__name__)


class LocalDockerSpawner(ContainerSpawner):
    """Spawn agent tasks via local Docker (for dev/test without a cluster)."""

    def spawn(self, task: AgentTask) -> SpawnResult:
        from astrolift_dispatch.brief_injector import brief_env_vars
        from astrolift_dispatch.snapshot_injector import snapshot_env_vars
        from astrolift_dispatch.spawners.k8s_job import _resolve_base_image, _vnc_image

        workload = task.agent_definition
        spec = getattr(task, "environment_spec", None)
        # Honour the runtime catalog / explicit image_tag precedence, same as
        # the K8s spawner; fall back to ubuntu only when nothing resolves.
        image = _resolve_base_image(workload, spec) if (workload or spec) else "ubuntu:22.04"
        if getattr(task, "vnc_enabled", False):
            image = _vnc_image(image)

        container_name = f"agent-task-{str(task.guid)[:12]}"

        # Brief identity + (for VNC tasks) the snapshot PUT URL env vars.
        env_vars = brief_env_vars(task) + snapshot_env_vars(task)
        env_args = []
        for ev in env_vars:
            env_args += ["-e", f"{ev['name']}={ev['value']}"]

        try:
            cmd = ["docker", "run", "-d", "--name", container_name] + env_args + [image]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return SpawnResult(external_id=container_name, ok=False, error=result.stderr)
            container_id = result.stdout.strip()
            logger.info("local_docker_spawner: started container %s (%s)", container_name, container_id[:12])
            return SpawnResult(external_id=container_name)
        except Exception as exc:
            return SpawnResult(external_id=container_name, ok=False, error=str(exc))

    def status(self, external_id: str) -> TaskStatus:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", external_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return TaskStatus(failed=True, error_message="container not found")
            parts = result.stdout.strip().split()
            state = parts[0] if parts else "unknown"
            exit_code = int(parts[1]) if len(parts) > 1 else None
            return TaskStatus(
                running=state == "running",
                succeeded=state == "exited" and exit_code == 0,
                failed=state == "exited" and exit_code != 0,
                exit_code=exit_code,
            )
        except Exception as exc:
            return TaskStatus(failed=True, error_message=str(exc))

    def stop(self, external_id: str) -> None:
        try:
            subprocess.run(["docker", "rm", "-f", external_id], capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass
