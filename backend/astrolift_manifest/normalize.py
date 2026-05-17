"""
Normalizer — apply org / team / project defaults onto a ``RawManifest``.

The output is what the platform stores. We also produce the
serializable JSON shape (dict[str, Any]) so the SHA-256 hash on the
``RegisteredApp`` row is deterministic across processes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from astrolift_manifest.types import (
    ContainerManifest,
    ManagedServiceManifest,
    NormalizedManifest,
    RawManifest,
    WorkloadManifest,
)


@dataclasses.dataclass(slots=True, frozen=True)
class NormalizationDefaults:
    cpu_request: str = "100m"
    cpu_limit: str = "500m"
    memory_request: str = "128Mi"
    memory_limit: str = "512Mi"
    storage_class: str | None = None
    storage_size: str | None = None
    healthcheck_kind: str = "http"
    healthcheck_value: str = "/health"
    healthcheck_port: int | None = None


def normalize(
    raw: RawManifest,
    *,
    defaults: NormalizationDefaults | None = None,
) -> NormalizedManifest:
    defaults = defaults or NormalizationDefaults()
    applied: list[str] = []
    workloads = tuple(_normalize_workload(w, defaults, applied) for w in raw.workloads)
    serialized = {
        "name": raw.name,
        "workloads": [_workload_dict(w) for w in workloads],
        "managed_services": [_msvc_dict(m) for m in raw.managed_services],
    }
    return NormalizedManifest(
        name=raw.name,
        workloads=workloads,
        managed_services=raw.managed_services,
        defaults_applied=tuple(applied),
        serialized=serialized,
    )


def manifest_hash(serialized: dict[str, Any]) -> str:
    payload = json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---- helpers ----------------------------------------------------------


def _normalize_workload(
    w: WorkloadManifest,
    defaults: NormalizationDefaults,
    applied: list[str],
) -> WorkloadManifest:
    cpu_request = w.cpu_request or _apply(applied, f"workload.{w.name}.cpu_request", defaults.cpu_request)
    cpu_limit = w.cpu_limit or _apply(applied, f"workload.{w.name}.cpu_limit", defaults.cpu_limit)
    memory_request = w.memory_request or _apply(
        applied, f"workload.{w.name}.memory_request", defaults.memory_request
    )
    memory_limit = w.memory_limit or _apply(applied, f"workload.{w.name}.memory_limit", defaults.memory_limit)

    containers = tuple(_normalize_container(c, defaults, applied, w.name) for c in w.containers)
    if not any(c.is_primary for c in containers) and containers:
        first = containers[0]
        applied.append(f"workload.{w.name}.containers[{first.name}].is_primary")
        containers = (dataclasses.replace(first, is_primary=True),) + containers[1:]

    return dataclasses.replace(
        w,
        cpu_request=cpu_request,
        cpu_limit=cpu_limit,
        memory_request=memory_request,
        memory_limit=memory_limit,
        containers=containers,
    )


def _normalize_container(
    c: ContainerManifest,
    defaults: NormalizationDefaults,
    applied: list[str],
    workload_name: str,
) -> ContainerManifest:
    healthcheck_kind = c.healthcheck_kind
    healthcheck_value = c.healthcheck_value
    healthcheck_port = c.healthcheck_port
    if healthcheck_kind == "none":
        healthcheck_kind = _apply(
            applied,
            f"workload.{workload_name}.containers[{c.name}].healthcheck.kind",
            defaults.healthcheck_kind,
        )
        healthcheck_value = healthcheck_value or defaults.healthcheck_value
        healthcheck_port = healthcheck_port or defaults.healthcheck_port

    return dataclasses.replace(
        c,
        healthcheck_kind=healthcheck_kind,
        healthcheck_value=healthcheck_value,
        healthcheck_port=healthcheck_port,
    )


def _apply(applied: list[str], key: str, value: str) -> str:
    applied.append(key)
    return value


def _workload_dict(w: WorkloadManifest) -> dict[str, Any]:
    return {
        "name": w.name,
        "kind": w.kind,
        "is_public": w.is_public,
        "schedule": w.schedule,
        "concurrency_policy": w.concurrency_policy,
        "replicas": w.replicas,
        "cpu_request": w.cpu_request,
        "cpu_limit": w.cpu_limit,
        "memory_request": w.memory_request,
        "memory_limit": w.memory_limit,
        "hpa_min": w.hpa_min,
        "hpa_max": w.hpa_max,
        "hpa_target_cpu_pct": w.hpa_target_cpu_pct,
        "storage_class": w.storage_class,
        "storage_size": w.storage_size,
        "containers": [_container_dict(c) for c in w.containers],
    }


def _container_dict(c: ContainerManifest) -> dict[str, Any]:
    return {
        "name": c.name,
        "is_primary": c.is_primary,
        "image_ref": c.image_ref,
        "dockerfile_path": c.dockerfile_path,
        "build_context": c.build_context,
        "port": c.port,
        "command": list(c.command),
        "args": list(c.args),
        "env": dict(c.env),
        "healthcheck": {
            "kind": c.healthcheck_kind,
            "value": c.healthcheck_value,
            "port": c.healthcheck_port,
        },
    }


def _msvc_dict(m: ManagedServiceManifest) -> dict[str, Any]:
    return {
        "kind": m.kind,
        "name": m.name,
        "variant": m.variant,
        "config": dict(m.config),
    }
