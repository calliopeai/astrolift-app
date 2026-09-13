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

    def __init__(self, *, connection: dict | None = None, expected_daemon_id: str | None = None):
        self.connection = connection
        self.expected_daemon_id = expected_daemon_id

    def _run(self, args: list[str], *, timeout: int = 5):
        from astrolift_dispatch.spawners.docker_connection import run_docker

        if self.connection is not None:
            return run_docker(self.connection, args, timeout=timeout)
        try:
            return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Docker command timed out") from None

    def _verify_daemon(self) -> None:
        if self.expected_daemon_id is not None:
            result = self._run(["info", "--format", "{{.ID}}"])
            if result.returncode or not result.stdout.strip():
                raise RuntimeError("Cannot verify the Docker daemon for this task")
            if result.stdout.strip() != self.expected_daemon_id:
                raise RuntimeError("Task belongs to a different Docker daemon")

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
            self._verify_daemon()
            cmd = (
                [
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
            result = self._run(cmd, timeout=30)
            if result.returncode != 0:
                return SpawnResult(external_id=container_name, ok=False, error=result.stderr)
            container_id = result.stdout.strip()
            logger.info("local_docker_spawner: started container %s (%s)", container_name, container_id[:12])
            return SpawnResult(external_id=container_name)
        except Exception as exc:
            return SpawnResult(external_id=container_name, ok=False, error=str(exc))

    def status(self, external_id: str) -> TaskStatus:
        self._verify_daemon()
        result = self._run(
            ["inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", external_id],
            timeout=10,
        )
        if result.returncode != 0:
            if self._is_missing(result, external_id):
                return TaskStatus(failed=True, error_message="container not found")
            raise RuntimeError("Cannot inspect the Docker container")
        try:
            state, code = result.stdout.strip().split()
            exit_code = int(code)
            if state not in {"created", "restarting", "running", "removing", "paused", "exited", "dead"}:
                raise ValueError
        except ValueError:
            raise RuntimeError("Docker returned an invalid container status") from None
        return TaskStatus(
            running=state == "running",
            succeeded=state == "exited" and exit_code == 0,
            failed=state == "exited" and exit_code != 0,
            exit_code=exit_code,
        )

    def stop(self, external_id: str, *, expected_task_guid: str | None = None) -> None:
        self._verify_daemon()
        delete_id = external_id
        if expected_task_guid is not None:
            inspected = self._run(
                [
                    "inspect",
                    "--format",
                    '{{.Id}} {{ index .Config.Labels "astrolift.dev/task-id" }}',
                    external_id,
                ],
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
            result = self._run(
                ["rm", "-f", delete_id],
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
        self._verify_daemon()
        result = self._run(
            ["inspect", "--format", "{{.Id}}", external_id],
            timeout=5,
        )
        if result.returncode == 0:
            return False
        if self._is_missing(result, external_id):
            return True
        raise RuntimeError("Cannot confirm Docker container deletion")
