"""
Raw TOML → ``RawManifest`` parser.

Uses ``tomllib`` (stdlib, Python 3.11+). Errors are raised as
:class:`ManifestError` with the offending key path so the UI can
surface a useful message ("workloads[0].kind must be one of …").
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from astrolift_manifest.security_volumes import parse_volume
from astrolift_manifest.types import (
    BriefRef,
    ContainerManifest,
    EdgeIdentityConfig,
    ManagedServiceManifest,
    RawManifest,
    SkillRef,
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


_VALID_WORKLOAD_KINDS = {
    "deployment",
    "statefulset",
    "job",
    "cronjob",
    "task",
    "agent",
    "workflow",
    "function",
    "static_site",
    "faas",
}
_VALID_HEALTHCHECK = {"none", "http", "tcp", "exec"}
_VALID_CONCURRENCY_POLICY = {"forbid", "queue", "replace"}

# Mirrors ``astrolift_registry.models.Workload.RunMode`` (#1680). Kept as a
# literal rather than imported so this parser stays free of Django models --
# ``test_run_mode_matches_the_model`` asserts the two agree.
_VALID_RUN_MODES = {"once", "loop", "schedule", "trigger", "persistent"}

# Every key ``_parse_workload`` reads. Unknown keys are refused rather than
# dropped (#1680): the sibling ``_parse_managed_service`` has always been
# strict, and the asymmetry is what let a manifest declare ``run_mode`` --
# a field that did not exist -- and parse clean, so the operator learned
# nothing until the workload did not behave. A manifest is a contract.
_WORKLOAD_KEYS = frozenset(
    {
        "concurrency",
        "concurrency_policy",
        "containers",
        "cpu_limit",
        "cpu_request",
        "faas_architecture",
        "faas_build_command",
        "faas_handler",
        "faas_memory_mb",
        "faas_output_dir",
        "faas_package_type",
        "faas_public",
        "faas_runtime",
        "faas_timeout_seconds",
        "fs_group",
        "hpa_max",
        "hpa_min",
        "hpa_target_cpu_pct",
        "is_public",
        "kind",
        "max_concurrent_activities",
        "max_concurrent_workflows",
        "max_retries",
        "max_scale",
        "memory_limit",
        "memory_request",
        "metrics",
        "min_scale",
        "name",
        "replicas",
        "result_ttl_hours",
        "run_cron_expression",
        "run_family",
        "run_mode",
        "schedule",
        "security",
        "static_build_command",
        "static_index",
        "static_output_dir",
        "static_spa",
        "storage_class",
        "storage_size",
        "task_queue",
        "temporal_namespace",
        "timeout_seconds",
        "tool_timeout_seconds",
        "volumes",
        "workflow_type",
    }
)
_VALID_AGENT_RUN_FAMILY = {"task", "service"}


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

    # Reject duplicate names within [[workloads]]. Two workloads sharing a
    # name would render onto one Deployment/Service and the set-based dedup
    # below would silently drop one — a footgun. Flag the offending row so
    # the UI can point at it (#1033).
    seen_workload_names: set[str] = set()
    for i, w in enumerate(workloads):
        if w.name in seen_workload_names:
            raise ManifestError(
                f"duplicate workload name {w.name!r}",
                path=f"workloads[{i}].name",
            )
        seen_workload_names.add(w.name)

    # ``[[jobs]]`` is a shorthand for a single-container cronjob.
    # Desugar into the same WorkloadManifest shape so downstream
    # rendering / validation only ever sees one workload format.
    desugared_jobs = tuple(_desugar_job(item, f"jobs[{i}]") for i, item in enumerate(data.get("jobs", [])))

    # ``[[tasks]]`` is a shorthand for a single-container one-shot Job
    # (no schedule). Desugar into the same WorkloadManifest shape — with
    # ``kind == "task"`` — so downstream rendering / validation only ever
    # sees one workload format.
    desugared_tasks = tuple(
        _desugar_task(item, f"tasks[{i}]") for i, item in enumerate(data.get("tasks", []))
    )

    # Reject collisions between [[workloads]], [[jobs]], and [[tasks]]
    # sharing a name — the resulting Workload rows would conflict on the
    # unique constraint, and silently dropping one is a footgun.
    workload_names = {w.name for w in workloads}
    for j in desugared_jobs:
        if j.name in workload_names:
            raise ManifestError(
                f"job name {j.name!r} collides with an existing workload",
                path="jobs",
            )
        workload_names.add(j.name)
    for t in desugared_tasks:
        if t.name in workload_names:
            raise ManifestError(
                f"task name {t.name!r} collides with an existing workload",
                path="tasks",
            )
        workload_names.add(t.name)
    workloads = workloads + desugared_jobs + desugared_tasks

    managed = tuple(
        _parse_managed_service(item, f"managed_services[{i}]")
        for i, item in enumerate(data.get("managed_services", []))
    )
    service_keys: set[tuple[str, str, str, str]] = set()
    for i, service in enumerate(managed):
        missing = set(service.bind_workloads) - workload_names - {"*"}
        if missing:
            raise ManifestError(
                f"bind_workloads names unknown workload(s): {', '.join(sorted(missing))}",
                path=f"managed_services[{i}].bind_workloads",
            )
        key = (
            service.owner_scope,
            service.environment,
            service.kind,
            service.name or service.kind,
        )
        if key in service_keys:
            raise ManifestError(
                f"duplicate managed service {key[-1]!r} in environment {service.environment!r}",
                path=f"managed_services[{i}]",
            )
        service_keys.add(key)

    # Agent brief + skills (spec 38). Both keys are optional and only
    # meaningful for agent manifests; a manifest that omits them parses to
    # ``brief=None`` / ``skills=()`` and round-trips unchanged.
    brief = _parse_brief(data.get("brief"), "brief")
    skills = _parse_skills(data.get("skills", []), "skills")
    edge = _parse_edge(data.get("edge"), "edge")

    if workloads:
        public_count = sum(1 for w in workloads if w.is_public)
        if public_count > 1:
            # Allowed by spec, but only with the multi-workload hostname
            # template. Validation here is tolerant — caller decides.
            pass

    return RawManifest(
        name=name,
        workloads=workloads,
        managed_services=managed,
        brief=brief,
        skills=skills,
        edge=edge,
        raw=data,
    )


# The gate identities an app may ask to have forwarded, and the
# oauth2-proxy response header each one arrives on. Restricted to what the
# gate actually sets (``OIDCAuthConfig.response_headers``): a mapping for a
# header the gate never sends would render config that silently forwards
# nothing (#1733).
_EDGE_IDENTITIES = {
    "user": "X-Auth-Request-User",
    "email": "X-Auth-Request-Email",
    "access_token": "X-Auth-Request-Access-Token",
}

# RFC 7230 token: what a header name may contain. Anything else would be
# injected verbatim into the ingress controller's config.
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")

_EDGE_KEYS = frozenset({"gateway_secret_header", "identity_headers"})


def _valid_header_name(value: str, path: str) -> str:
    text = str(value).strip()
    if not text or not _HEADER_NAME_RE.match(text):
        raise ManifestError(
            f"{text!r} is not a valid HTTP header name",
            path=path,
        )
    return text


def _parse_edge(value: Any, path: str) -> EdgeIdentityConfig | None:
    """Parse the optional top-level ``[edge]`` block (#1733).

    Absent → ``None``, which renders exactly what the platform rendered
    before this existed.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManifestError("edge must be a table", path=path)
    unknown = sorted(set(value) - _EDGE_KEYS)
    if unknown:
        raise ManifestError(
            f"unknown edge key(s): {', '.join(unknown)}; valid keys are " f"{', '.join(sorted(_EDGE_KEYS))}",
            path=path,
        )

    secret_header = ""
    if value.get("gateway_secret_header") is not None:
        secret_header = _valid_header_name(
            value["gateway_secret_header"],
            f"{path}.gateway_secret_header",
        )

    raw_headers = value.get("identity_headers") or {}
    if not isinstance(raw_headers, dict):
        raise ManifestError(
            "edge.identity_headers must be a table of identity = header-name",
            path=f"{path}.identity_headers",
        )
    pairs: list[tuple[str, str]] = []
    for identity in sorted(raw_headers):
        if identity not in _EDGE_IDENTITIES:
            raise ManifestError(
                f"unknown edge identity {identity!r}; the gate forwards "
                f"{', '.join(sorted(_EDGE_IDENTITIES))}",
                path=f"{path}.identity_headers",
            )
        pairs.append(
            (
                identity,
                _valid_header_name(
                    raw_headers[identity],
                    f"{path}.identity_headers.{identity}",
                ),
            )
        )
    if not secret_header and not pairs:
        raise ManifestError(
            "edge declares nothing; set gateway_secret_header, identity_headers, or drop the block",
            path=path,
        )
    return EdgeIdentityConfig(
        gateway_secret_header=secret_header,
        identity_headers=tuple(pairs),
    )


def _parse_brief(value: Any, path: str) -> BriefRef | None:
    """Parse the optional top-level ``brief`` key (spec 38).

    ``brief`` is a single string path to a brief folder's entry README.
    Absent → ``None``. Present-but-not-a-non-empty-string → ``ManifestError``
    (a brief that can't be located is a footgun, same posture as the rest
    of the parser).
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError("brief must be a non-empty string path to a brief README", path=path)
    return BriefRef(path=value)


def _parse_skills(raw_list: Any, path: str) -> tuple[SkillRef, ...]:
    """Parse the optional top-level ``skills`` list (spec 38 + spec 39).

    Each entry is one of spec 39's three reference forms:

    * a single-key inline table ``{ name = "relative/path" }`` → a **local**
      skill (an agentskills.io folder in this agent's own repo); or
    * a bare string **with no ``/``** (``"pr-review"`` / ``"pr-review@1.2.0"``)
      → a **catalogue** skill (resolved from the built-in catalogue repo); or
    * a bare string **containing a ``/``** (``"<alias>/<skill-path>@<ref>"``,
      e.g. ``"acme/dev-skills/pr-review@v2"``) → an **org-repo** skill
      (resolved from one of the org's registered skill repos — spec 39 §2).

    The ``@ref`` pin (a tag/branch/sha) is optional on catalogue + org-repo
    refs and is split off into :attr:`SkillRef.ref`; :attr:`SkillRef.name`
    always holds the bare skill name (so catalogue/org-repo folder lookup
    keys on a clean name, not the pinned string). The spec's heterogeneous
    array (dicts + strings intermixed) parses cleanly in ``tomllib``.

    Malformed entries raise :class:`ManifestError` with an indexed path
    (``skills[1]``) so the UI can point at the offending row — a skill that
    can't be resolved would silently drop the agent's tooling otherwise.
    Validated: a stray/empty ``@`` pin, an empty alias or skill path on an
    org-repo ref, an empty local path, and duplicate skill names.
    """
    # Inline agent-brief skills — the ``[skills.<slug>]`` table form (each value
    # a ``{system_prompt, tools}`` body) used by agent briefs. These are the
    # brief's OWN skill definitions, assembled at dispatch by the brief
    # assembler; they are NOT top-level manifest skill *references* (the spec-38
    # list form below). The workload manifest carries no skill refs in that
    # case, so treat it as empty and let the brief assembler own the tables —
    # otherwise a valid agent brief fails to parse and never registers.
    if isinstance(raw_list, dict):
        return ()

    if not isinstance(raw_list, list):
        raise ManifestError("skills must be a list", path=path)

    out: list[SkillRef] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(raw_list):
        entry_path = f"{path}[{i}]"
        if isinstance(entry, str):
            ref = _parse_skill_string(entry, entry_path)
        elif isinstance(entry, dict):
            ref = _parse_local_skill_table(entry, entry_path)
        else:
            raise ManifestError(
                "skill entry must be a string (catalogue / org-repo) or a "
                f"single-key table (local), got {type(entry).__name__}",
                path=entry_path,
            )
        if ref.name in seen_names:
            raise ManifestError(f"duplicate skill name {ref.name!r}", path=entry_path)
        seen_names.add(ref.name)
        out.append(ref)
    return tuple(out)


def _split_ref_pin(value: str, entry_path: str) -> tuple[str, str]:
    """Split a ``"<body>@<ref>"`` skill string into ``(body, ref)``.

    ``ref`` is ``""`` when there is no ``@``. Rejects a trailing/empty pin
    (``"foo@"``) and more than one ``@`` (``"a@b@c"``) — both are operator
    paste-errors that would otherwise resolve to a nonsense ref silently.
    Local ``./path`` refs never reach here (a leading ``./`` is detected
    first), so an ``@`` in a path is not mis-split.
    """
    if "@" not in value:
        return value, ""
    parts = value.split("@")
    if len(parts) != 2:
        raise ManifestError(
            f"skill ref {value!r} has more than one '@' — expected at most one pin",
            path=entry_path,
        )
    body, ref = parts[0], parts[1]
    if not ref.strip():
        raise ManifestError(
            f"skill ref {value!r} has an empty '@' pin",
            path=entry_path,
        )
    return body, ref.strip()


def _parse_skill_string(entry: str, entry_path: str) -> SkillRef:
    """Parse a bare-string skill entry into a local / catalogue / org-repo ref.

    Discrimination (spec 39 §Reference grammar):
      * a leading ``./`` or ``../`` → **local** (a path in the agent's repo);
      * else a string containing a ``/`` → **org-repo**
        (``<alias>/<skill-path>``); the first segment is the repo alias, the
        remainder is the skill folder path within that repo;
      * else (no ``/``) → **catalogue**.
    """
    raw = entry.strip()
    if not raw:
        raise ManifestError("skill entry must be a non-empty string", path=entry_path)

    # Local "./path" form (spec 39: a path in the agent's own repo). Detected
    # before pin-splitting so an '@' inside a path isn't treated as a pin.
    if raw.startswith(("./", "../")):
        name = raw.rstrip("/").rsplit("/", 1)[-1]
        if not name:
            raise ManifestError(f"local skill path {raw!r} has no folder name", path=entry_path)
        return SkillRef(name=name, path=raw, kind="local")

    body, ref = _split_ref_pin(raw, entry_path)
    body = body.strip()
    if not body:
        raise ManifestError(f"skill ref {entry!r} has no name before the '@' pin", path=entry_path)

    if "/" not in body:
        # Catalogue: a bare name, optional @pin.
        return SkillRef(name=body, path=None, kind="catalogue", ref=ref)

    # Org-repo: "<alias>/<skill-subpath>". Alias is the first segment; the
    # rest is the skill folder path within the registered repo.
    alias, _, subpath = body.partition("/")
    alias = alias.strip()
    subpath = subpath.strip().strip("/")
    if not alias:
        raise ManifestError(
            f"org-repo skill ref {entry!r} has an empty repo alias",
            path=entry_path,
        )
    if not subpath:
        raise ManifestError(
            f"org-repo skill ref {entry!r} has an empty skill path after the alias",
            path=entry_path,
        )
    # Skill name = the last path segment (the agentskills.io folder name).
    name = subpath.rsplit("/", 1)[-1]
    return SkillRef(
        name=name,
        path=None,
        kind="org_repo",
        repo_alias=alias,
        skill_subpath=subpath,
        ref=ref,
    )


def _parse_local_skill_table(entry: dict, entry_path: str) -> SkillRef:
    """Parse a ``{ name = "relative/path" }`` single-key table → a local ref."""
    if len(entry) != 1:
        raise ManifestError(
            "local skill entry must be a single-key table "
            '{ name = "relative/path" }, got '
            f"{len(entry)} keys",
            path=entry_path,
        )
    ((name, raw_path),) = entry.items()
    name = str(name).strip()
    if not name:
        raise ManifestError("local skill entry name must be non-empty", path=entry_path)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ManifestError(
            f"local skill {name!r} must map to a non-empty path string",
            path=entry_path,
        )
    return SkillRef(name=name, path=raw_path, kind="local")


def _parse_workload(d: dict[str, Any], path: str) -> WorkloadManifest:
    unknown = sorted(set(d) - _WORKLOAD_KEYS)
    if unknown:
        raise ManifestError(
            f"unknown workload field(s): {', '.join(unknown)}",
            path=path,
        )
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

    # Prometheus scrape opt-in (#1226): `[workloads.<name>.metrics]` with
    # `port`, and optionally `path`. Absent means not scraped, which is
    # what every existing manifest means.
    metrics_enabled = False
    metrics_port: int | None = None
    metrics_path = "/metrics"
    raw_metrics = d.get("metrics")
    if raw_metrics is not None:
        if not isinstance(raw_metrics, dict):
            raise ManifestError(
                "metrics must be a table, e.g. metrics = { port = 9090 }",
                path=f"{path}.metrics",
            )
        # `enabled` defaults true: writing the table at all is the opt-in.
        # `enabled = false` is how you keep the config while turning it off.
        metrics_enabled = bool(raw_metrics.get("enabled", True))
        raw_port = raw_metrics.get("port")
        if metrics_enabled and raw_port is None:
            raise ManifestError(
                "metrics requires a 'port' — a scrape target with no port would "
                "have to be guessed, and guessing scrapes whatever is listening",
                path=f"{path}.metrics.port",
            )
        if raw_port is not None:
            try:
                metrics_port = int(raw_port)
            except (TypeError, ValueError):
                raise ManifestError(
                    f"metrics.port must be an integer, got {raw_port!r}",
                    path=f"{path}.metrics.port",
                ) from None
            if not (1 <= metrics_port <= 65535):
                raise ManifestError(
                    f"metrics.port must be between 1 and 65535, got {metrics_port}",
                    path=f"{path}.metrics.port",
                )
        metrics_path = str(raw_metrics.get("path", "/metrics"))
        if not metrics_path.startswith("/"):
            raise ManifestError(
                f"metrics.path must start with '/', got {metrics_path!r}",
                path=f"{path}.metrics.path",
            )

    # Workflow-worker fields (#796). ``workflow_type`` + ``task_queue``
    # are required when ``kind == "workflow"`` — a worker that doesn't
    # know which workflow type to register or which task queue to poll
    # is a silent no-op in the cluster, so reject it at parse time.
    if kind == "workflow":
        _require_str(d, "workflow_type", f"{path}.workflow_type")
        _require_str(d, "task_queue", f"{path}.task_queue")

    # Agent run family (#1027). Defaults to ``task`` (one-shot Job dispatch,
    # no standing K8s resource); only ``task`` / ``service`` are valid for an
    # agent. A bad value would otherwise silently render an always-on
    # Deployment and CrashLoop a task agent.
    run_family = str(d.get("run_family", "task")).lower()
    if kind == "agent" and run_family not in _VALID_AGENT_RUN_FAMILY:
        raise ManifestError(
            f"agent run_family must be one of {sorted(_VALID_AGENT_RUN_FAMILY)}, got {run_family!r}",
            path=f"{path}.run_family",
        )

    # Agent trigger mode (#1680), bound to kind the way `schedule` is bound
    # to cronjob: declaring it on a workload that can never dispatch is a
    # mistake worth naming, not a value to carry.
    run_mode = str(d.get("run_mode", "") or "").strip().lower()
    run_cron_expression = str(d.get("run_cron_expression", "") or "").strip()
    if run_mode or run_cron_expression:
        if kind != "agent":
            raise ManifestError(
                "run_mode / run_cron_expression apply to agent workloads only, " f"got kind={kind!r}",
                path=f"{path}.run_mode",
            )
        if run_mode and run_mode not in _VALID_RUN_MODES:
            raise ManifestError(
                f"run_mode must be one of {sorted(_VALID_RUN_MODES)}, got {run_mode!r}",
                path=f"{path}.run_mode",
            )
    if run_mode == "schedule" and not run_cron_expression:
        raise ManifestError(
            "run_mode = \"schedule\" requires a 'run_cron_expression'",
            path=f"{path}.run_cron_expression",
        )
    if run_cron_expression and run_mode not in ("", "schedule"):
        raise ManifestError(
            f'run_cron_expression is only read for run_mode = "schedule", got {run_mode!r}',
            path=f"{path}.run_cron_expression",
        )
    if run_cron_expression:
        # The platform's own validator, so a manifest cannot declare a
        # schedule the scheduler will later refuse. Pure-python and
        # model-free, so importing it keeps this parser Django-free.
        from astrolift_registry.cron import CronValidationError, validate_cron_expression

        try:
            run_cron_expression = validate_cron_expression(run_cron_expression)
        except CronValidationError as exc:
            raise ManifestError(
                f"run_cron_expression is not a valid cron expression: {exc}",
                path=f"{path}.run_cron_expression",
            ) from exc

    # Static-site (#1010). A static_site serves built assets from object
    # storage + a CDN — it has no container/pod, so declaring containers is
    # a mistake (they would be silently ignored). ``static_output_dir`` is
    # only required when the platform builds in-cluster
    # (``static_build_command`` set); a CI-pushed app may sync at the bucket
    # root and leave both empty.
    if kind == "static_site":
        if d.get("containers"):
            raise ManifestError(
                "static_site workload must declare no containers",
                path=f"{path}.containers",
            )
        # Mode select is keyed on a STRIPPED command (a whitespace-only value
        # is CI-pushed, same as the runtime selector) -- strip here too so the
        # output_dir requirement and the stored value agree at both sites.
        if str(d.get("static_build_command", "")).strip() and not str(d.get("static_output_dir", "")):
            raise ManifestError(
                "static_build_command requires static_output_dir",
                path=f"{path}.static_output_dir",
            )

    # Provider-managed FaaS (#987). The cloud runs the function — there is no
    # container/pod, so declaring containers is a mistake (silently ignored).
    # ``faas_package_type`` selects ``image`` (PackageType=Image from the
    # per-app ECR repo, the default) or ``zip`` (a built artifact). Image mode
    # bundles its own runtime + entrypoint, so ``faas_handler``/``faas_runtime``
    # are zip-only and rejected on an image function; zip mode requires the
    # runtime + handler + the built ``faas_output_dir`` to package.
    if kind == "faas":
        if d.get("containers"):
            raise ManifestError(
                "faas workload must declare no containers",
                path=f"{path}.containers",
            )
        package_type = str(d.get("faas_package_type", "image")).strip().lower()
        if package_type not in ("image", "zip"):
            raise ManifestError(
                f"faas_package_type must be one of ['image', 'zip'], got {package_type!r}",
                path=f"{path}.faas_package_type",
            )
        if package_type == "image":
            if str(d.get("faas_handler", "")).strip() or str(d.get("faas_runtime", "")).strip():
                raise ManifestError(
                    "image-package faas workload must not set faas_handler/faas_runtime "
                    "(the container image bundles its own runtime and entrypoint)",
                    path=f"{path}.faas_handler",
                )
        else:
            if not str(d.get("faas_handler", "")).strip():
                raise ManifestError(
                    "zip-package faas workload requires faas_handler",
                    path=f"{path}.faas_handler",
                )
            if not str(d.get("faas_runtime", "")).strip():
                raise ManifestError(
                    "zip-package faas workload requires faas_runtime",
                    path=f"{path}.faas_runtime",
                )
            if not str(d.get("faas_output_dir", "")).strip():
                raise ManifestError(
                    "zip-package faas workload requires faas_output_dir",
                    path=f"{path}.faas_output_dir",
                )

    containers = tuple(
        _parse_container(item, f"{path}.containers[{i}]") for i, item in enumerate(d.get("containers", []))
    )

    volumes = _parse_volumes(d.get("volumes", []), path)
    fs_group = _parse_fs_group(d, path)

    return WorkloadManifest(
        name=name,
        kind=kind,
        is_public=bool(d.get("is_public", False)),
        schedule=schedule,
        concurrency_policy=concurrency_policy,
        metrics_enabled=metrics_enabled,
        metrics_port=metrics_port,
        metrics_path=metrics_path,
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
        fs_group=fs_group,
        containers=containers,
        volumes=volumes,
        # Agent dispatch tuning (#795). Only meaningful when
        # ``kind == "agent"``; other kinds carry the defaults and ignore
        # them. Read unconditionally so a manifest that sets them on a
        # non-agent workload round-trips without error.
        max_retries=int(d.get("max_retries", 5)),
        tool_timeout_seconds=int(d.get("tool_timeout_seconds", 300)),
        result_ttl_hours=int(d.get("result_ttl_hours", 72)),
        run_family=run_family,
        run_mode=run_mode,
        run_cron_expression=run_cron_expression,
        # Temporal worker config (#796). Required pair validated above for
        # ``kind == "workflow"``; other kinds carry the empty/default
        # values and ignore them. Read unconditionally so a manifest that
        # sets them on a non-workflow workload round-trips without error.
        workflow_type=str(d.get("workflow_type", "")),
        task_queue=str(d.get("task_queue", "")),
        temporal_namespace=str(d.get("temporal_namespace", "default")),
        max_concurrent_activities=int(d.get("max_concurrent_activities", 20)),
        max_concurrent_workflows=int(d.get("max_concurrent_workflows", 10)),
        # ``kind == "function"`` Knative autoscaling parameters.
        min_scale=int(d.get("min_scale", 0)),
        max_scale=int(d.get("max_scale", 10)),
        function_concurrency=int(d.get("concurrency", 1)),
        function_timeout_seconds=int(d.get("timeout_seconds", 300)),
        # ``kind == "static_site"`` (#1010). Read unconditionally — harmless
        # defaults on other kinds, same posture as the agent/function fields.
        static_build_command=str(d.get("static_build_command", "")).strip(),
        static_output_dir=str(d.get("static_output_dir", "")),
        static_spa=bool(d.get("static_spa", False)),
        static_index=str(d.get("static_index", "index.html")),
        # ``kind == "faas"`` (#987). Read unconditionally — harmless defaults on
        # other kinds, same posture as the static_site/function fields.
        faas_package_type=str(d.get("faas_package_type", "image")).strip().lower(),
        faas_runtime=str(d.get("faas_runtime", "")).strip(),
        faas_handler=str(d.get("faas_handler", "")).strip(),
        faas_memory_mb=int(d.get("faas_memory_mb", 512)),
        faas_timeout_seconds=int(d.get("faas_timeout_seconds", 30)),
        faas_architecture=str(d.get("faas_architecture", "arm64")),
        faas_public=bool(d.get("faas_public", False)),
        faas_build_command=str(d.get("faas_build_command", "")).strip(),
        faas_output_dir=str(d.get("faas_output_dir", "")),
    )


# Every key ``_parse_container`` reads. Strict for the same reason
# ``_WORKLOAD_KEYS`` is (#1680) -- a container block that silently drops
# what it does not recognise teaches the operator nothing until the pod
# runs without it.
_CONTAINER_KEYS = frozenset(
    {
        "args",
        "build_context",
        "command",
        "concurrency_policy",
        "cpu_limit",
        "cpu_request",
        "dockerfile",
        "dockerfile_path",
        "env",
        "healthcheck",
        "image_ref",
        "is_primary",
        "memory_limit",
        "memory_request",
        "name",
        "port",
        "schedule",
    }
)


def _parse_container(d: dict[str, Any], path: str) -> ContainerManifest:
    unknown = sorted(set(d) - _CONTAINER_KEYS)
    if unknown:
        raise ManifestError(
            f"unknown container field(s): {', '.join(unknown)}",
            path=path,
        )

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


def _desugar_task(d: dict[str, Any], path: str) -> WorkloadManifest:
    """Lift a ``[[tasks]]`` shorthand into a full WorkloadManifest.

    A task is a single-container one-shot Job — it runs to completion
    once and is not rescheduled. Unlike ``[[jobs]]`` (which desugars to
    a cronjob and requires a ``schedule``), a task has no schedule and
    no concurrency policy: ``schedule`` stays ``None`` and the renderer
    emits a bare ``batch/v1 Job``.
    """
    name = _require_str(d, "name", f"{path}.name")

    env_pairs = tuple((str(k), str(v)) for k, v in (d.get("env", {}) or {}).items())

    container = ContainerManifest(
        name=name,
        is_primary=True,
        image_ref=d.get("image_ref"),
        dockerfile_path=str(d.get("dockerfile_path", d.get("dockerfile", "Dockerfile"))),
        build_context=str(d.get("build_context", ".")),
        port=0,  # tasks don't expose ports
        command=tuple(map(str, d.get("command", []) or ())),
        args=tuple(map(str, d.get("args", []) or ())),
        env=env_pairs,
        # tasks don't carry liveness/readiness probes — k8s tracks
        # success/failure via the Job controller's exit code.
        healthcheck_kind="none",
        healthcheck_value="",
        healthcheck_port=None,
    )

    return WorkloadManifest(
        name=name,
        kind="task",
        is_public=False,
        # One-shot: no schedule, no concurrency policy.
        schedule=None,
        concurrency_policy="forbid",
        replicas=1,
        cpu_request=d.get("cpu_request"),
        cpu_limit=d.get("cpu_limit"),
        memory_request=d.get("memory_request"),
        memory_limit=d.get("memory_limit"),
        # HPA + storage don't apply to one-shot tasks.
        hpa_min=None,
        hpa_max=None,
        hpa_target_cpu_pct=80,
        storage_class=None,
        storage_size=None,
        containers=(container,),
    )


def _parse_fs_group(d: dict, path: str) -> int | None:
    """Explicit ``fs_group`` on the workload, else ``run_as_user`` from its
    ``[workloads.<name>.security]`` block.

    Only the one key is read from ``security`` -- the rest of that block
    (``run_as_non_root``, capabilities, ...) is parsed by
    ``security_volumes.parse_security_context`` but not yet rendered, and
    reading it here would imply a coverage this renderer does not have.

    Returning ``None`` means "no explicit intent"; the renderer applies the
    platform default, and only for workloads that mount a pvc.
    """
    raw = d.get("fs_group")
    if raw is None:
        sec = d.get("security")
        if isinstance(sec, dict):
            raw = sec.get("run_as_user")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ManifestError(f"fs_group must be an int (GID), got {raw!r}", path=f"{path}.fs_group")
    if raw < 0:
        raise ManifestError("fs_group must be non-negative", path=f"{path}.fs_group")
    return raw


def _parse_volumes(raw_list: list, workload_path: str) -> tuple[dict, ...]:
    """Parse ``[[workloads.<name>.volumes]]`` entries, validate each via
    ``parse_volume()``, and return as plain dicts so ``WorkloadManifest``
    stays independent of the ``security_volumes`` types."""
    from astrolift_drivers.storage_tiers import StorageError

    out: list[dict] = []
    for i, raw in enumerate(raw_list):
        path = f"{workload_path}.volumes[{i}]"
        try:
            vd = parse_volume(raw)
        except StorageError as exc:
            raise ManifestError(str(exc), path=path) from exc
        out.append(
            {
                "name": vd.name,
                "kind": str(vd.kind),
                "mount_path": vd.mount_path,
                "size": vd.size,
                "storage_class": vd.storage_class,
                "access_mode": vd.access_mode,
                "performance_tier": str(vd.performance_tier),
                "durability": str(vd.durability),
                "source_name": vd.source_name,
                "size_limit": vd.size_limit,
            }
        )
    return tuple(out)


def _parse_managed_service(d: dict[str, Any], path: str) -> ManagedServiceManifest:
    allowed = {
        "kind",
        "name",
        "variant",
        "config",
        "owner_scope",
        "scope",
        "environment",
        "bind_workloads",
        "size",
        "tier",
        "isolation",
        "auth_mode",
        "extensions",
        "networking",
        "retention",
        "backup",
        "restore",
        "deletion_policy",
        "confirm_delete",
        # Compatibility with early demo manifests. These are provider config,
        # not lifecycle controls, and are preserved rather than ignored.
        "version",
        "inject_as",
    }
    unknown = sorted(set(d) - allowed)
    if unknown:
        raise ManifestError(
            f"unknown managed-service field(s): {', '.join(unknown)}; provider-specific options belong in extensions",
            path=path,
        )

    scope = str(d.get("owner_scope", d.get("scope", "app"))).lower()
    if "owner_scope" in d and "scope" in d and d["owner_scope"] != d["scope"]:
        raise ManifestError("owner_scope and scope disagree", path=f"{path}.owner_scope")
    if scope not in {"app", "project"}:
        raise ManifestError("owner_scope must be 'app' or 'project'", path=f"{path}.owner_scope")

    environment = str(d.get("environment", "production") or "production")
    raw_bind = d.get("bind_workloads", ["*"])
    if isinstance(raw_bind, str):
        raw_bind = [raw_bind]
    if (
        not isinstance(raw_bind, list)
        or not raw_bind
        or any(not isinstance(v, str) or not v for v in raw_bind)
    ):
        raise ManifestError("bind_workloads must be a non-empty string array", path=f"{path}.bind_workloads")
    bind_workloads = tuple(dict.fromkeys(raw_bind))
    if "*" in bind_workloads and len(bind_workloads) > 1:
        raise ManifestError(
            "'*' cannot be combined with named workload selectors", path=f"{path}.bind_workloads"
        )

    if "size" in d and "tier" in d and d["size"] != d["tier"]:
        raise ManifestError("size and tier disagree", path=f"{path}.size")
    size = str(d.get("size", d.get("tier", "small"))).lower()
    if size not in {"small", "medium", "large", "xlarge", "custom"}:
        raise ManifestError(
            "size must be small, medium, large, xlarge, or custom; provider SKUs belong in extensions",
            path=f"{path}.size",
        )
    isolation = str(d.get("isolation", "")).lower()
    if isolation not in {"", "shared", "dedicated"}:
        raise ManifestError("isolation must be shared or dedicated", path=f"{path}.isolation")
    auth_mode = str(d.get("auth_mode", "")).lower()
    if auth_mode not in {"", "password", "iam", "mtls"}:
        raise ManifestError("auth_mode must be password, iam, or mtls", path=f"{path}.auth_mode")

    groups: dict[str, dict[str, Any]] = {}
    for key in ("config", "networking", "retention", "backup", "restore"):
        value = d.get(key, {}) or {}
        if not isinstance(value, dict):
            raise ManifestError(f"{key} must be a table", path=f"{path}.{key}")
        groups[key] = dict(value)
        _reject_inline_secrets(groups[key], f"{path}.{key}")
    raw_extensions = d.get("extensions", {}) or {}
    if isinstance(raw_extensions, list):
        if any(not isinstance(value, str) or not value for value in raw_extensions):
            raise ManifestError(
                "extensions must contain non-empty strings",
                path=f"{path}.extensions",
            )
        groups["extensions"] = {"desired_extensions": list(dict.fromkeys(raw_extensions))}
    elif isinstance(raw_extensions, dict):
        groups["extensions"] = dict(raw_extensions)
        _reject_inline_secrets(groups["extensions"], f"{path}.extensions")
    else:
        raise ManifestError(
            "extensions must be a string array or provider-extension table",
            path=f"{path}.extensions",
        )
    for legacy_key in ("version", "inject_as"):
        if legacy_key in d:
            groups["config"][legacy_key] = d[legacy_key]

    restore = groups["restore"]
    if restore and (not restore.get("snapshot_id") or not restore.get("source_handle")):
        raise ManifestError(
            "restore requires snapshot_id and source_handle",
            path=f"{path}.restore",
        )
    deletion_policy = str(d.get("deletion_policy", "retain")).lower()
    if deletion_policy not in {"retain", "delete"}:
        raise ManifestError("deletion_policy must be retain or delete", path=f"{path}.deletion_policy")
    confirm_delete = d.get("confirm_delete", False)
    if not isinstance(confirm_delete, bool):
        raise ManifestError("confirm_delete must be a boolean", path=f"{path}.confirm_delete")
    if deletion_policy == "delete" and not confirm_delete:
        raise ManifestError(
            "deletion_policy='delete' requires confirm_delete=true",
            path=f"{path}.confirm_delete",
        )

    variant = d.get("variant")
    if variant is not None and (not isinstance(variant, str) or not variant):
        raise ManifestError("variant must be a non-empty string", path=f"{path}.variant")

    return ManagedServiceManifest(
        kind=_require_str(d, "kind", f"{path}.kind"),
        name=str(d.get("name", "")),
        variant=variant,
        config=groups["config"],
        owner_scope=scope,
        environment=environment,
        bind_workloads=bind_workloads,
        size=size,
        isolation=isolation,
        auth_mode=auth_mode,
        extensions=groups["extensions"],
        networking=groups["networking"],
        retention=groups["retention"],
        backup=groups["backup"],
        restore=restore,
        deletion_policy=deletion_policy,
        confirm_delete=confirm_delete,
    )


def _reject_inline_secrets(value: dict[str, Any], path: str) -> None:
    """Manifest config may carry secret references, never secret material."""
    for key, item in value.items():
        normalized = str(key).lower()
        is_reference = normalized.endswith(("_ref", "_name", "_id", "_path"))
        if not is_reference and normalized in {
            "password",
            "secret",
            "token",
            "credential",
            "connection_string",
            "api_key",
            "access_key",
            "private_key",
        }:
            raise ManifestError(
                f"inline secret field {key!r} is forbidden; use Workload Identity or a Key Vault reference",
                path=f"{path}.{key}",
            )
        if isinstance(item, dict):
            _reject_inline_secrets(item, f"{path}.{key}")


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
