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
    # Static-site (#1010) and faas (#987) have no container/pod — applying
    # cpu/mem/healthcheck defaults or fabricating an is_primary container would
    # pollute the serialized hash for a container-less workload. Pass them
    # through untouched, except:
    if w.kind == "static_site":
        return w
    if w.kind == "faas":
        # faas_public is the public switch for a faas workload: it drives the
        # Function-URL + CloudFront (cdn) + the public hostname/CNAME. The
        # hostname / alias / DNS machinery all key on is_public, so a workload
        # that declares only faas_public must be is_public too — otherwise the
        # cdn is created but gets no alias and no CNAME (#1035, live: faasprobe
        # set faas_public only and its custom host never resolved). Imply it
        # here so the operator declares one switch, not two.
        if w.faas_public and not w.is_public:
            applied.append(f"workload.{w.name}.is_public")
            return dataclasses.replace(w, is_public=True)
        return w

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
    # Only default an http healthcheck onto a container that actually serves
    # a port. A port-less worker (e.g. a background consumer / queue poller)
    # has nothing to probe over http — defaulting one fabricates a port and
    # CrashLoops the pod on a probe that can never succeed (#1033).
    if healthcheck_kind == "none" and c.port > 0:
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
        "metrics_enabled": w.metrics_enabled,
        "metrics_port": w.metrics_port,
        "metrics_path": w.metrics_path,
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
        # Agent dispatch tuning (#795) participates in the manifest hash
        # so a change to e.g. ``max_retries`` is detected as a real change
        # rather than collapsing to a silent no-op deploy. Only meaningful
        # on ``kind == "agent"`` workloads; defaults elsewhere.
        "max_retries": w.max_retries,
        "tool_timeout_seconds": w.tool_timeout_seconds,
        "result_ttl_hours": w.result_ttl_hours,
        # Agent run family (#1027) participates in the manifest hash so a
        # task<->service flip (which changes whether a Deployment renders at
        # all) is detected as a real change, not a silent no-op deploy. Only
        # meaningful on ``kind == "agent"`` workloads; defaults elsewhere.
        "run_family": w.run_family,
        # Temporal worker config (#796) participates in the manifest hash
        # so a change to e.g. ``task_queue`` is detected as a real change
        # rather than collapsing to a silent no-op deploy. Only set on
        # ``kind == "workflow"`` workloads; defaults elsewhere.
        "workflow_type": w.workflow_type,
        "task_queue": w.task_queue,
        "temporal_namespace": w.temporal_namespace,
        "max_concurrent_activities": w.max_concurrent_activities,
        "max_concurrent_workflows": w.max_concurrent_workflows,
        # Static-site config (#1010) participates in the manifest hash so a
        # change to e.g. ``static_build_command`` is detected as a real change
        # rather than collapsing to a silent no-op deploy. Only meaningful on
        # ``kind == "static_site"`` workloads; defaults elsewhere.
        "static_build_command": w.static_build_command,
        "static_output_dir": w.static_output_dir,
        "static_spa": w.static_spa,
        "static_index": w.static_index,
        # FaaS config (#987) participates in the manifest hash so a change to
        # e.g. ``faas_memory_mb`` or ``faas_public`` is detected as a real
        # change rather than collapsing to a silent no-op deploy. Only
        # meaningful on ``kind == "faas"`` workloads; defaults elsewhere.
        "faas_package_type": w.faas_package_type,
        "faas_runtime": w.faas_runtime,
        "faas_handler": w.faas_handler,
        "faas_memory_mb": w.faas_memory_mb,
        "faas_timeout_seconds": w.faas_timeout_seconds,
        "faas_architecture": w.faas_architecture,
        "faas_public": w.faas_public,
        "faas_build_command": w.faas_build_command,
        "faas_output_dir": w.faas_output_dir,
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
        "owner_scope": m.owner_scope,
        "environment": m.environment,
        "bind_workloads": list(m.bind_workloads),
        "size": m.size,
        "isolation": m.isolation,
        "auth_mode": m.auth_mode,
        "extensions": dict(m.extensions),
        "networking": dict(m.networking),
        "retention": dict(m.retention),
        "backup": dict(m.backup),
        "restore": dict(m.restore),
        "deletion_policy": m.deletion_policy,
        "confirm_delete": m.confirm_delete,
    }
