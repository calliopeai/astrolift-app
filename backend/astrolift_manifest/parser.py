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
    """Raised when a manifest fails parsing or validation.

    ``path`` is the dotted key path that triggered the error
    (e.g. ``workloads[0].kind``). ``line`` and ``column`` are the
    1-based source positions when known — set automatically for
    TOML syntax errors; left ``None`` for semantic errors that
    didn't go through a position-aware path.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        line: int | None = None,
        column: int | None = None,
    ):
        prefix = f"{path}: " if path else ""
        super().__init__(prefix + message)
        self.path = path
        self.line = line
        self.column = column


_VALID_WORKLOAD_KINDS = {"deployment", "statefulset", "job", "cronjob"}
_VALID_HEALTHCHECK = {"none", "http", "tcp", "exec"}
_VALID_CONCURRENCY_POLICY = {"forbid", "queue", "replace"}


_TOML_POS_RE = __import__("re").compile(r"line\s+(\d+),\s+column\s+(\d+)")


def parse_raw(toml_text: str) -> RawManifest:
    try:
        data: dict[str, Any] = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        # tomllib in 3.11+ doesn't expose structured position attrs;
        # the message embeds "(at line N, column M)" so we regex it
        # out. Still surfaces the original message in str(exc).
        line, col = _line_col_from_message(str(exc))
        raise ManifestError(f"invalid TOML: {exc}", line=line, column=col) from exc

    name = _require_str(data, "name", "name")
    workloads = tuple(
        _parse_workload(item, f"workloads[{i}]") for i, item in enumerate(data.get("workloads", []))
    )

    # ``[[jobs]]`` is a shorthand for a single-container cronjob.
    # Desugar into the same WorkloadManifest shape so downstream
    # rendering / validation only ever sees one workload format.
    desugared_jobs = tuple(_desugar_job(item, f"jobs[{i}]") for i, item in enumerate(data.get("jobs", [])))

    # Reject collisions between [[workloads]] and [[jobs]] sharing a
    # name — the resulting Workload rows would conflict on the unique
    # constraint, and silently dropping one is a footgun.
    workload_names = {w.name for w in workloads}
    for j in desugared_jobs:
        if j.name in workload_names:
            raise ManifestError(
                f"job name {j.name!r} collides with an existing workload",
                path="jobs",
            )
    workloads = workloads + desugared_jobs

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
    concurrency_policy = str(d.get("concurrency_policy", "forbid")).lower()
    if concurrency_policy not in _VALID_CONCURRENCY_POLICY:
        raise ManifestError(
            "concurrency_policy must be one of "
            f"{sorted(_VALID_CONCURRENCY_POLICY)}, got {concurrency_policy!r}",
            path=f"{path}.concurrency_policy",
        )

    containers = tuple(
        _parse_container(item, f"{path}.containers[{i}]") for i, item in enumerate(d.get("containers", []))
    )

    return WorkloadManifest(
        name=name,
        kind=kind,
        is_public=bool(d.get("is_public", False)),
        schedule=schedule,
        concurrency_policy=concurrency_policy,
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


def _desugar_job(d: dict[str, Any], path: str) -> WorkloadManifest:
    """Lift a ``[[jobs]]`` shorthand into a full WorkloadManifest.

    The job becomes a single-container cronjob workload. The parser
    rejects jobs without a schedule (same rule as bare cronjob
    workloads) to avoid silent never-runs.
    """
    name = _require_str(d, "name", f"{path}.name")
    schedule = d.get("schedule")
    if not schedule:
        raise ManifestError("[[jobs]] entry requires a 'schedule'", path=f"{path}.schedule")
    concurrency_policy = str(d.get("concurrency_policy", "forbid")).lower()
    if concurrency_policy not in _VALID_CONCURRENCY_POLICY:
        raise ManifestError(
            "concurrency_policy must be one of "
            f"{sorted(_VALID_CONCURRENCY_POLICY)}, got {concurrency_policy!r}",
            path=f"{path}.concurrency_policy",
        )

    env_pairs = tuple((str(k), str(v)) for k, v in (d.get("env", {}) or {}).items())

    container = ContainerManifest(
        name=name,
        is_primary=True,
        image_ref=d.get("image_ref"),
        dockerfile_path=str(d.get("dockerfile_path", d.get("dockerfile", "Dockerfile"))),
        build_context=str(d.get("build_context", ".")),
        port=0,  # jobs don't expose ports
        command=tuple(map(str, d.get("command", []) or ())),
        args=tuple(map(str, d.get("args", []) or ())),
        env=env_pairs,
        # jobs don't carry liveness/readiness probes — k8s tracks
        # success/failure via the Job controller's exit code.
        healthcheck_kind="none",
        healthcheck_value="",
        healthcheck_port=None,
    )

    return WorkloadManifest(
        name=name,
        kind="cronjob",
        is_public=False,
        schedule=schedule,
        concurrency_policy=concurrency_policy,
        replicas=1,
        cpu_request=d.get("cpu_request"),
        cpu_limit=d.get("cpu_limit"),
        memory_request=d.get("memory_request"),
        memory_limit=d.get("memory_limit"),
        # HPA + storage don't apply to one-shot jobs.
        hpa_min=None,
        hpa_max=None,
        hpa_target_cpu_pct=80,
        storage_class=None,
        storage_size=None,
        containers=(container,),
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


def _line_col_from_message(message: str) -> tuple[int | None, int | None]:
    m = _TOML_POS_RE.search(message)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _line_col_from_pos(text: str, pos: int | None) -> tuple[int | None, int | None]:
    """Convert a 0-based byte offset into 1-based (line, column).

    Returns (None, None) when the offset is unknown or out of range.
    Tomllib's ``pos`` is a character index, not byte offset, but the
    distinction only matters for non-ASCII keys — TOML keys are
    typically ASCII so the simple approach is fine.
    """
    if pos is None or pos < 0:
        return None, None
    line_start = text.rfind("\n", 0, pos) + 1
    line = text.count("\n", 0, pos) + 1
    column = pos - line_start + 1
    return line, column


def locate_in_source(toml_text: str, path: str) -> tuple[int | None, int | None]:
    """Best-effort lookup of (line, column) for a dotted key path.

    Used by callers that catch a semantic ``ManifestError`` and want
    to enrich it with source position. The implementation is a simple
    last-segment scan — exact for unique leaf names in small
    manifests, ambiguous for repeated keys (e.g. ``name`` appearing
    on every container). When the path can't be located we return
    ``(None, None)`` and callers display the dotted path on its own.
    """
    if not path:
        return None, None
    leaf = path.rsplit(".", 1)[-1]
    # Strip array indices like "containers[0]".
    if "[" in leaf:
        leaf = leaf.split("[", 1)[0]
    if not leaf:
        return None, None
    needle = f"{leaf}"
    # Match `<leaf> =` so we don't catch the leaf inside string values.
    import re

    pattern = re.compile(rf"^\s*{re.escape(needle)}\s*=", re.MULTILINE)
    m = pattern.search(toml_text)
    if not m:
        return None, None
    return _line_col_from_pos(toml_text, m.start())
