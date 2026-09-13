"""Local Docker spawn backend for agent task dispatch — dev mode (#49)."""

from __future__ import annotations

import logging
import re
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

        # UUIDv7 prefixes contain only time; truncating them collides across parallel tasks.
        container_name = f"agent-task-{str(task.guid).replace('-', '')}"

        # Brief identity + (for VNC tasks) the snapshot PUT URL env vars.
        env_vars = brief_env_vars(task) + snapshot_env_vars(task)
        env_args = []
        for ev in env_vars:
            env_args += ["-e", f"{ev['name']}={ev['value']}"]

        try:
            cmd = (
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--label",
                    f"astrolift.dev/task-id={task.guid}",
                ]
                + env_args
                + [image]
            )
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

    def stop(self, external_id: str, *, expected_task_guid: str | None = None) -> None:
        delete_id = external_id
        if expected_task_guid is not None:
            inspected = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{.Id}} {{ index .Config.Labels "astrolift.dev/task-id" }}',
                    external_id,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if inspected.returncode:
                if self._is_missing(inspected, external_id):
                    return
                raise RuntimeError("Cannot verify ownership of the Docker container")
            fields = inspected.stdout.strip().split()
            if len(fields) != 2 or fields[1] != expected_task_guid:
                raise RuntimeError("Docker container belongs to a different agent task")
            delete_id = fields[0]
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", delete_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"local container deletion failed: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "local container deletion failed")

    @staticmethod
    def _is_missing(result, external_id: str) -> bool:
        return bool(
            re.fullmatch(
                rf"(?:Error:|Error response from daemon:) No such (?:object|container): {re.escape(external_id)}",
                result.stderr.strip(),
            )
        )

    def confirm_stopped(self, external_id: str) -> bool:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", external_id],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return False
        if self._is_missing(result, external_id):
            return True
        raise RuntimeError("Cannot confirm Docker container deletion")
