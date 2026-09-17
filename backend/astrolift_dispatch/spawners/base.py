"""Base interface for container spawn backends (#49)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask


@dataclasses.dataclass
class SpawnResult:
    """Result of a container spawn operation."""

    external_id: str  # Pod/Job/Task name assigned by the backend
    ok: bool = True
    error: str = ""


@dataclasses.dataclass
class TaskStatus:
    """Current status of a spawned container."""

    running: bool = False
    succeeded: bool = False
    failed: bool = False
    exit_code: int | None = None
    error_message: str = ""


class ContainerSpawner:
    """Abstract base for container spawn backends.

    Each backend (K8s Job, ECS Task, local Docker) implements these methods.
    """

    def spawn(self, task: AgentTask) -> SpawnResult:
        """Spawn a container for the given AgentTask.

        Returns SpawnResult with the backend-assigned external_id.
        """
        raise NotImplementedError

    def reserve_input_wait(self, task: AgentTask, seconds: int) -> None:
        """Reserve a bounded human wait allowance before acknowledging a question."""
        raise NotImplementedError("Dispatch backend cannot reserve an input-wait deadline")

    def status(self, external_id: str) -> TaskStatus:
        """Return the current status of a spawned container."""
        raise NotImplementedError

    def stop(self, external_id: str, *, expected_task_guid: str | None = None) -> None:
        """Stop and clean up a running container."""
        raise NotImplementedError

    def confirm_stopped(self, external_id: str) -> bool:
        """Return true only after the task's resource and live dependents are absent."""
        raise NotImplementedError
