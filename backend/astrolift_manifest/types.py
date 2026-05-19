"""
Manifest types — the parsed and normalized shapes.

Kept separate from the parser so re-exports are cheap and other
modules can type-hint against ``NormalizedManifest`` without pulling
in the parser logic.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(slots=True, frozen=True)
class WorkloadManifest:
    name: str
    kind: str
    is_public: bool = False
    schedule: str | None = None
    # CronJob concurrency policy (#427). One of ``forbid`` / ``queue``
    # / ``replace``; only meaningful when ``kind == "cronjob"``. The
    # default ``forbid`` mirrors the manifest renderer's pre-#427
    # hard-coded value, so a manifest that omits the key keeps the
    # existing K8s behaviour.
    concurrency_policy: str = "forbid"
    replicas: int = 1
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    hpa_min: int | None = None
    hpa_max: int | None = None
    hpa_target_cpu_pct: int = 80
    storage_class: str | None = None
    storage_size: str | None = None
    containers: tuple[ContainerManifest, ...] = ()
    # Volume declarations from ``[[workloads.<name>.volumes]]`` (#739).
    # Each dict carries the raw parsed shape (name, kind, mount_path,
    # size, storage_class, access_mode) so the platform can persist them
    # and the workload-detail page can render volume cards without re-
    # parsing the TOML. Kept as dicts (not VolumeDecl) so this module
    # stays independent of security_volumes.py.
    volumes: tuple[dict, ...] = ()


@dataclasses.dataclass(slots=True, frozen=True)
class ContainerManifest:
    name: str
    is_primary: bool = False
    image_ref: str | None = None
    dockerfile_path: str = "Dockerfile"
    build_context: str = "."
    port: int = 0
    command: tuple[str, ...] = ()
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    healthcheck_kind: str = "none"
    healthcheck_value: str = ""
    healthcheck_port: int | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class ManagedServiceManifest:
    kind: str
    name: str = ""
    variant: str | None = None
    config: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True, frozen=True)
class RawManifest:
    name: str
    workloads: tuple[WorkloadManifest, ...] = ()
    managed_services: tuple[ManagedServiceManifest, ...] = ()
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True, frozen=True)
class NormalizedManifest:
    """Concrete, defaulted shape stored on RegisteredApp.

    Always has every workload + container fully resolved (no "use the
    org default" left over), with a stable key ordering so the SHA-256
    hash is deterministic.
    """

    name: str
    workloads: tuple[WorkloadManifest, ...]
    managed_services: tuple[ManagedServiceManifest, ...]
    defaults_applied: tuple[str, ...]  # which defaults filled in
    serialized: dict[str, Any]  # JSON-shaped for storage
