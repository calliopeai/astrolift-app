"""
Raw TOML → ``RawManifest`` parser.

Uses ``tomllib`` (stdlib, Python 3.11+). Errors are raised as
:class:`ManifestError` with the offending key path so the UI can
surface a useful message ("workloads[0].kind must be one of …").
"""

from __future__ import annotations

import tomllib
from typing import Any

from astrolift_manifest.types import (
    ContainerManifest,
    ManagedServiceManifest,
    RawManifest,
    WorkloadManifest,
)


class ManifestError(ValueError):
    """Raised when a manifest fails parsing or validation."""

    def __init__(self, message: str, *, path: str = ""):
        prefix = f"{path}: " if path else ""
        super().__init__(prefix + message)
        self.path = path


_VALID_WORKLOAD_KINDS = {"deployment", "statefulset", "job", "cronjob"}
_VALID_HEALTHCHECK = {"none", "http", "tcp", "exec"}


def parse_raw(toml_text: str) -> RawManifest:
    try:
        data: dict[str, Any] = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML: {exc}") from exc

    name = _require_str(data, "name", "name")
    workloads = tuple(
        _parse_workload(item, f"workloads[{i}]") for i, item in enumerate(data.get("workloads", []))
    )
    managed = tuple(
        _parse_managed_service(item, f"managed_services[{i}]")
        for i, item in enumerate(data.get("managed_services", []))
    )

    if workloads:
        public_count = sum(1 for w in workloads if w.is_public)
        if public_count > 1:
            # Allowed by spec, but only with the multi-workload hostname
            # template. Validation here is tolerant — caller decides.
            pass

    return RawManifest(name=name, workloads=workloads, managed_services=managed, raw=data)


def _parse_workload(d: dict[str, Any], path: str) -> WorkloadManifest:
    kind = _require_str(d, "kind", f"{path}.kind")
    if kind not in _VALID_WORKLOAD_KINDS:
        raise ManifestError(
            f"kind must be one of {sorted(_VALID_WORKLOAD_KINDS)}, got {kind!r}",
            path=f"{path}.kind",
        )
    name = _require_str(d, "name", f"{path}.name")
    schedule = d.get("schedule") if kind == "cronjob" else None
    if kind == "cronjob" and not schedule:
        raise ManifestError("cronjob workload requires a 'schedule'", path=f"{path}.schedule")

    containers = tuple(
        _parse_container(item, f"{path}.containers[{i}]") for i, item in enumerate(d.get("containers", []))
    )

    return WorkloadManifest(
        name=name,
        kind=kind,
        is_public=bool(d.get("is_public", False)),
        schedule=schedule,
        replicas=int(d.get("replicas", 1)),
        cpu_request=d.get("cpu_request"),
        cpu_limit=d.get("cpu_limit"),
        memory_request=d.get("memory_request"),
        memory_limit=d.get("memory_limit"),
        hpa_min=d.get("hpa_min"),
        hpa_max=d.get("hpa_max"),
        hpa_target_cpu_pct=int(d.get("hpa_target_cpu_pct", 80)),
        storage_class=d.get("storage_class"),
        storage_size=d.get("storage_size"),
        containers=containers,
    )


def _parse_container(d: dict[str, Any], path: str) -> ContainerManifest:
    name = _require_str(d, "name", f"{path}.name")
    healthcheck = d.get("healthcheck", {}) or {}
    hk = str(healthcheck.get("kind", "none"))
    if hk not in _VALID_HEALTHCHECK:
        raise ManifestError(
            f"healthcheck.kind must be one of {sorted(_VALID_HEALTHCHECK)}, got {hk!r}",
            path=f"{path}.healthcheck.kind",
        )

    env_pairs = tuple((str(k), str(v)) for k, v in (d.get("env", {}) or {}).items())

    return ContainerManifest(
        name=name,
        is_primary=bool(d.get("is_primary", False)),
        image_ref=d.get("image_ref"),
        dockerfile_path=str(d.get("dockerfile_path", "Dockerfile")),
        build_context=str(d.get("build_context", ".")),
        port=int(d.get("port", 0)),
        command=tuple(map(str, d.get("command", []) or ())),
        args=tuple(map(str, d.get("args", []) or ())),
        env=env_pairs,
        healthcheck_kind=hk,
        healthcheck_value=str(healthcheck.get("value", "")),
        healthcheck_port=healthcheck.get("port"),
    )


def _parse_managed_service(d: dict[str, Any], path: str) -> ManagedServiceManifest:
    return ManagedServiceManifest(
        kind=_require_str(d, "kind", f"{path}.kind"),
        name=str(d.get("name", "")),
        variant=d.get("variant"),
        config=dict(d.get("config", {}) or {}),
    )


def _require_str(d: dict[str, Any], key: str, path: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"required string {key!r} is missing or empty", path=path)
    return value
