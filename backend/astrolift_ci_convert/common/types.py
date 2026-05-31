"""Shared intermediate types for the CI converter pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepDef:
    name: str
    uses: str | None = None  # built-in action ref (Astrolift action id)
    run: str | None = None  # shell command
    env: dict = field(default_factory=dict)
    with_params: dict = field(default_factory=dict)
    # Unsupported constructs — emitted as TODO comments in TOML output.
    todos: list[str] = field(default_factory=list)


@dataclass
class ServiceDef:
    image: str
    name: str = ""
    env: dict = field(default_factory=dict)


@dataclass
class JobDef:
    job_id: str
    name: str
    runs_on: str = "astrolift/default"
    container: str = ""
    needs: list[str] = field(default_factory=list)
    steps: list[StepDef] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    services: list[ServiceDef] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    # Unsupported constructs.
    todos: list[str] = field(default_factory=list)


@dataclass
class ScheduleDef:
    cron: str


@dataclass
class PipelineDef:
    name: str
    jobs: list[JobDef] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    on_push_branches: list[str] = field(default_factory=list)
    on_push_tags: list[str] = field(default_factory=list)
    on_pull_request_branches: list[str] = field(default_factory=list)
    on_schedule: list[ScheduleDef] = field(default_factory=list)
    on_workflow_dispatch: bool = False
    # Unsupported constructs.
    todos: list[str] = field(default_factory=list)
