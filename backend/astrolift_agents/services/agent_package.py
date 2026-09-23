"""Canonical, source-format-independent Agent Package projection.

TOML, visual-flow importers, AGENTS.md adapters, and future package formats
all normalize into this JSON-shaped contract before dispatch.  Runtime code
must consume this projection rather than depending on the source adapter's
syntax.  The compatibility fields returned alongside it keep current runners
working while the package protocol rolls out.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

PACKAGE_SCHEMA = "astrolift.agent.package/v1"
DEFAULT_EXCLUDES = (
    ".git/**",
    "**/.DS_Store",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/node_modules/**",
)
MAX_ENVIRONMENT_ENTRIES = 1000
MAX_ENVIRONMENT_NAME_LENGTH = 253
MAX_SECRET_URI_LENGTH = 512
_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Runtime identity, callback credentials, immutable package delivery, and VNC
# upload controls are owned by the dispatcher. Allowing package/user values to
# shadow them can redirect results or make an agent execute a different brief.
RESERVED_AGENT_ENVIRONMENT_NAMES = frozenset(
    {
        "AGENT_CALLBACK_URL",
        "AGENT_DISPATCH_URL",
        "AGENT_PROMPT",
        "AGENT_SYSTEM",
        "AGENT_TASK_ID",
        # An agent-box's identity env (#128). A box's own variables are how
        # anything inside the pod knows which box it is; a spec that set them
        # would make the pod lie about itself.
        "ASTROLIFT_AGENT_BOX",
        "ASTROLIFT_AGENT_BOX_GUID",
        "ASTROLIFT_BRIEF_HASH",
        "ASTROLIFT_BRIEF_ID",
        "ASTROLIFT_CLUSTER_KEY",
        "ASTROLIFT_CONTROLLER_URL",
        "ASTROLIFT_MANIFEST_PATH",
        "ASTROLIFT_PAYLOAD_HASH",
        "ASTROLIFT_PAYLOAD_URL",
        "ASTROLIFT_SNAPSHOT_INTERVAL",
        "ASTROLIFT_SNAPSHOT_URL",
        "ASTROLIFT_TASK_ID",
        "ASTROLIFT_TMUX_SESSION",
        "ASTROLIFT_TRIGGER_PAYLOAD",
        "ASTROLIFT_WORKSPACE",
    }
)


class AgentPackageError(ValueError):
    """The normalized package or its source-slice boundary is invalid."""


def is_reserved_agent_environment_name(value: str) -> bool:
    return str(value or "").strip() in RESERVED_AGENT_ENVIRONMENT_NAMES


def _json_value(value: Any, *, field: str, depth: int = 0) -> Any:
    """Validate/copy extensible package metadata into a JSON-safe shape."""
    if depth > 12:
        raise AgentPackageError(f"{field} is nested too deeply")
    if isinstance(value, float) and not math.isfinite(value):
        raise AgentPackageError(f"{field} contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        if len(value) > 1000:
            raise AgentPackageError(f"{field} has too many items")
        return [_json_value(item, field=f"{field}[]", depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 1000 or any(not isinstance(key, str) for key in value):
            raise AgentPackageError(f"{field} must have at most 1000 string keys")
        return {
            key: _json_value(item, field=f"{field}.{key}", depth=depth + 1) for key, item in value.items()
        }
    raise AgentPackageError(f"{field} contains non-JSON value {type(value).__name__}")


def normalize_environment_values(value: Any, *, field: str = "environment.values") -> dict[str, Any]:
    """Validate non-secret environment values without silently stringifying structures."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AgentPackageError(f"{field} must be an object")
    if len(value) > MAX_ENVIRONMENT_ENTRIES:
        raise AgentPackageError(f"{field} has more than {MAX_ENVIRONMENT_ENTRIES} entries")
    normalized: dict[str, Any] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str):
            raise AgentPackageError(f"{field} keys must be strings")
        name = raw_name.strip()
        if len(name) > MAX_ENVIRONMENT_NAME_LENGTH or not _ENVIRONMENT_NAME_RE.fullmatch(name):
            raise AgentPackageError(f"{field} contains invalid environment variable name {raw_name!r}")
        if is_reserved_agent_environment_name(name):
            raise AgentPackageError(f"{field} may not override dispatcher-owned variable {name!r}")
        if name in normalized:
            raise AgentPackageError(f"{field} contains duplicate environment variable {name!r}")
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise AgentPackageError(f"{field}.{name} must be a finite scalar")
        if raw_value is not None and not isinstance(raw_value, (str, int, float, bool)):
            raise AgentPackageError(f"{field}.{name} must be a scalar or null")
        normalized[name] = raw_value
    return normalized


def normalize_secret_references(
    value: Any, *, field: str = "environment.secret_refs"
) -> list[dict[str, str]]:
    """Validate secret references and discard no fields, especially plaintext values."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentPackageError(f"{field} must be an array")
    if len(value) > MAX_ENVIRONMENT_ENTRIES:
        raise AgentPackageError(f"{field} has more than {MAX_ENVIRONMENT_ENTRIES} entries")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(raw, dict):
            raise AgentPackageError(f"{item_field} must be an object")
        unexpected = sorted(str(key) for key in set(raw) - {"env_var", "uri"})
        if unexpected:
            raise AgentPackageError(f"{item_field} contains unsupported fields: {', '.join(unexpected)}")
        env_var = raw.get("env_var")
        uri = raw.get("uri")
        if not isinstance(env_var, str) or not isinstance(uri, str):
            raise AgentPackageError(f"{item_field}.env_var and .uri must be strings")
        env_var = env_var.strip()
        uri = uri.strip()
        if len(env_var) > MAX_ENVIRONMENT_NAME_LENGTH or not _ENVIRONMENT_NAME_RE.fullmatch(env_var):
            raise AgentPackageError(f"{item_field}.env_var is not a valid environment variable name")
        if is_reserved_agent_environment_name(env_var):
            raise AgentPackageError(
                f"{item_field}.env_var may not override dispatcher-owned variable {env_var!r}"
            )
        if not uri or "\x00" in uri or len(uri) > MAX_SECRET_URI_LENGTH:
            raise AgentPackageError(
                f"{item_field}.uri must be non-empty and at most {MAX_SECRET_URI_LENGTH} characters"
            )
        if env_var in seen:
            raise AgentPackageError(f"{field} contains duplicate environment variable {env_var!r}")
        seen.add(env_var)
        normalized.append({"env_var": env_var, "uri": uri})
    return normalized


def manifest_needs_payload(raw: dict) -> bool:
    """Whether the runner must receive the source tree as a payload.

    ``[package]`` ships files to the agent; ``[workspace]`` is read by the
    runner from ``astrolift.toml`` inside that payload (#1847), so without a
    payload the declared repos and dependencies would silently never load.
    """
    return "package" in raw or "workspace" in raw


def project_manifest_environment(
    environment_section: Any,
    secrets_section: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Project source TOML environment/secret tables into the package IR.

    This is deliberately strict at the source boundary.  In particular, a
    secret declaration may contain only an external reference; silently
    ignoring an unexpected ``value`` field would leave plaintext in source
    while making registration appear successful.
    """
    environment = environment_section or {}
    if not isinstance(environment, dict):
        raise AgentPackageError("environment must be a TOML table/object")
    values = normalize_environment_values(
        {key: value for key, value in environment.items() if key not in {"tool_preset", "allow_install"}}
    )

    secrets = secrets_section or {}
    if not isinstance(secrets, dict):
        raise AgentPackageError("secrets must be a TOML table/object")
    if len(secrets) > MAX_ENVIRONMENT_ENTRIES:
        raise AgentPackageError(f"secrets has more than {MAX_ENVIRONMENT_ENTRIES} entries")

    refs: list[dict[str, str]] = []
    for env_var, raw_ref in secrets.items():
        if isinstance(raw_ref, str):
            uri = raw_ref
        elif isinstance(raw_ref, dict):
            unexpected = sorted(str(key) for key in set(raw_ref) - {"secret_name", "uri"})
            if unexpected:
                raise AgentPackageError(
                    f"secrets.{env_var} contains unsupported fields: {', '.join(unexpected)}"
                )
            names = [raw_ref.get(key) for key in ("secret_name", "uri") if raw_ref.get(key) is not None]
            if len(names) != 1 or not isinstance(names[0], str):
                raise AgentPackageError(
                    f"secrets.{env_var} must contain exactly one string secret_name or uri"
                )
            uri = names[0]
        else:
            raise AgentPackageError(f"secrets.{env_var} must be a secret-name string or reference table")
        refs.append({"env_var": env_var, "uri": uri})
    return values, normalize_secret_references(refs)


def _normalize_imports(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise AgentPackageError("imports must be an array of tables/objects")
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        item = _json_value(raw, field=f"imports[{index}]")
        fmt = str(item.get("format") or "").strip().lower()
        if not fmt:
            raise AgentPackageError(f"imports[{index}].format is required")
        item["format"] = fmt
        if item.get("path") is not None:
            item["path"] = _safe_rel(item["path"], field=f"imports[{index}].path")
        mode = str(item.get("mode") or "agent").strip().lower()
        if mode not in {"agent", "workflow", "federation", "opaque"}:
            raise AgentPackageError(f"imports[{index}].mode must be agent, workflow, federation, or opaque")
        item["mode"] = mode
        out.append(item)
    return out


def _normalize_federation(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AgentPackageError("federation must be a table/object")
    result = _json_value(value, field="federation")
    for field in ("manifest_path", "anchor_manifest"):
        if result.get(field):
            result[field] = _safe_rel(result[field], field=f"federation.{field}")
    return result


def _safe_rel(value: str, *, field: str, allow_dot: bool = False) -> str:
    raw = str(value or "").strip()
    if allow_dot and raw == ".":
        return "."
    if not raw or "\x00" in raw or "\\" in raw:
        raise AgentPackageError(f"{field} must be a non-empty POSIX path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise AgentPackageError(f"{field} may not be absolute or contain '..': {raw!r}")
    normalized = path.as_posix().removeprefix("./")
    if not normalized or normalized == ".":
        if allow_dot:
            return "."
        raise AgentPackageError(f"{field} resolves to an empty path")
    return normalized


def _string_patterns(value: Any, *, field: str, default: Iterable[str] = ()) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AgentPackageError(f"{field} must be a string array")
    return [_safe_rel(item, field=field, allow_dot=True) for item in value]


def source_slice_from_manifest(*, manifest_path: str, package_config: Any) -> dict[str, Any]:
    """Normalize the manifest's virtual source chroot.

    ``root`` is relative to the manifest directory and cannot escape it.
    Explicit shared mounts name repo-root sources and safe in-package mount
    points; they are the only files allowed to cross the slice boundary.
    """
    manifest = _safe_rel(manifest_path, field="manifest_path")
    manifest_dir = PurePosixPath(manifest).parent
    if str(manifest_dir) == ".":
        manifest_dir = PurePosixPath("")

    config = package_config or {}
    if not isinstance(config, dict):
        raise AgentPackageError("package must be a TOML table/object")
    relative_root = _safe_rel(config.get("root", "."), field="package.root", allow_dot=True)
    root = manifest_dir if relative_root == "." else manifest_dir / relative_root
    normalized_root = root.as_posix() or "."

    include = _string_patterns(config.get("include"), field="package.include", default=("**",))
    exclude = _string_patterns(config.get("exclude"), field="package.exclude", default=DEFAULT_EXCLUDES)
    executables = _string_patterns(config.get("executables"), field="package.executables")

    raw_shared = config.get("shared", [])
    if not isinstance(raw_shared, list):
        raise AgentPackageError("package.shared must be an array of tables")
    shared: list[dict[str, str]] = []
    seen_mounts: set[str] = set()
    for index, item in enumerate(raw_shared):
        if not isinstance(item, dict):
            raise AgentPackageError(f"package.shared[{index}] must be a table/object")
        source = _safe_rel(item.get("source", ""), field=f"package.shared[{index}].source")
        mount = _safe_rel(item.get("mount", ""), field=f"package.shared[{index}].mount")
        if mount in seen_mounts:
            raise AgentPackageError(f"duplicate package.shared mount {mount!r}")
        seen_mounts.add(mount)
        shared.append({"source": source, "mount": mount})

    return {
        "root": normalized_root,
        "manifest_path": manifest,
        "include": include,
        "exclude": exclude,
        "executables": executables,
        "shared": shared,
        "path_mode": "chroot",
    }


def compose_system_prompt(
    *,
    brief: str,
    skills: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """Compose one stable prompt, including the runnable command contract.

    Harness runtimes receive this prompt directly rather than the thread-mode
    structured tool packet.  Folding command-backed tools into the prompt keeps
    ``tool_refs`` and legacy ``tools = [...]`` declarations actionable in both
    modes instead of leaving the resolved tools only in Brief metadata.
    """
    sections: list[str] = []
    brief_text = (brief or "").strip()
    if brief_text:
        sections.append(f"# Agent brief\n\n{brief_text}")
    for skill in skills:
        content = str(skill.get("instructions") or "").strip()
        if not content:
            continue
        name = str(skill.get("name") or skill.get("slug") or "skill")
        sections.append(f"# Skill: {name}\n\n{content}")
    tool_lines: list[str] = []
    for tool in tools or []:
        slug = str(tool.get("slug") or "").strip()
        commands = [
            str(command).strip()
            for command in (tool.get("commands") or [])
            if isinstance(command, str) and command.strip()
        ]
        if not slug or not commands:
            continue
        description = re.sub(r"\s+", " ", str(tool.get("description") or "").strip())
        command_text = ", ".join(f"`{command}`" for command in commands)
        detail = f" — {description}" if description else ""
        tool_lines.append(f"- `{slug}`{detail}. Commands: {command_text}.")
    if tool_lines:
        sections.append(
            "# Available command tools\n\n"
            "The following commands are provided by the selected agent runtime. "
            "Use only the commands needed for this task.\n\n" + "\n".join(tool_lines)
        )
    return "\n\n---\n\n".join(sections)


def build_agent_package(
    *,
    agent_name: str,
    manifest_path: str,
    package_config: Any,
    brief_text: str,
    context_files: dict[str, str],
    skills: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    environment: dict[str, Any],
    secret_refs: list[dict[str, str]],
    runtime: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    imports: list[dict[str, Any]] | None = None,
    federation: dict[str, Any] | None = None,
    payload_required: bool | None = None,
) -> dict[str, Any]:
    """Build the canonical IR persisted into ``Brief.manifest_snapshot``."""
    source = source_slice_from_manifest(manifest_path=manifest_path, package_config=package_config)
    system_prompt = compose_system_prompt(brief=brief_text, skills=skills, tools=tools)
    package = {
        "schema": PACKAGE_SCHEMA,
        "agent": {"name": agent_name},
        "source": source,
        "prompt": {
            "system": system_prompt,
            "brief": brief_text,
            "context_files": dict(sorted(context_files.items())),
        },
        "skills": skills,
        "tools": tools,
        "runtime": runtime or {},
        "environment": {
            "values": normalize_environment_values(environment),
            "secret_refs": normalize_secret_references(secret_refs),
        },
        "execution": execution or {},
        "delivery": {
            # Declaring [package] is the opt-in boundary for runtime files.
            # Brief/skill prose is already frozen above; source bytes are
            # required only when an author explicitly asks for a slice.
            "payload_required": (
                bool(package_config) if payload_required is None else bool(payload_required)
            ),
        },
        "imports": _normalize_imports(imports),
        "federation": _normalize_federation(federation),
    }
    return validate_agent_package(package)


def compatibility_snapshot(package: dict[str, Any]) -> dict[str, Any]:
    """Project the package onto fields understood by current pod runners."""
    prompt = package.get("prompt") or {}
    environment = package.get("environment") or {}
    tools = package.get("tools") or []
    return {
        "agent_package": package,
        "agent_package_schema": package.get("schema", PACKAGE_SCHEMA),
        "system_prompt": prompt.get("system", ""),
        "tools": [tool.get("slug", "") for tool in tools if tool.get("slug")],
        "env_vars": environment.get("values", {}),
        "source_slice": package.get("source", {}),
        "manifest_path": (package.get("source") or {}).get("manifest_path", "astrolift.toml"),
        "requires_payload": bool((package.get("delivery") or {}).get("payload_required")),
    }


def validate_agent_package(value: Any) -> dict[str, Any]:
    """Validate a canonical package supplied by an importer/API caller.

    Source-authored TOML reaches :func:`build_agent_package`; imported native
    package JSON reaches this function. Both enforce the same chroot and
    JSON-only metadata boundary before a snapshot can become dispatchable.
    """
    if not isinstance(value, dict):
        raise AgentPackageError("agent package must be an object")
    package = _json_value(value, field="package")
    if package.get("schema") != PACKAGE_SCHEMA:
        raise AgentPackageError(f"package.schema must be {PACKAGE_SCHEMA!r}")

    agent = package.get("agent")
    if not isinstance(agent, dict) or not isinstance(agent.get("name"), str) or not agent["name"].strip():
        raise AgentPackageError("package.agent.name is required")
    agent["name"] = agent["name"].strip()
    source = package.get("source")
    if not isinstance(source, dict):
        raise AgentPackageError("package.source must be an object")
    if source.get("path_mode") != "chroot":
        raise AgentPackageError("package.source.path_mode must be 'chroot'")
    source["root"] = _safe_rel(source.get("root", "."), field="package.source.root", allow_dot=True)
    source["manifest_path"] = _safe_rel(source.get("manifest_path", ""), field="package.source.manifest_path")
    source["include"] = _string_patterns(
        source.get("include"), field="package.source.include", default=("**",)
    )
    source["exclude"] = _string_patterns(source.get("exclude"), field="package.source.exclude")
    source["executables"] = _string_patterns(source.get("executables"), field="package.source.executables")
    shared = source.get("shared") or []
    if not isinstance(shared, list) or any(not isinstance(item, dict) for item in shared):
        raise AgentPackageError("package.source.shared must be an array of objects")
    mounts: set[str] = set()
    for index, item in enumerate(shared):
        item["source"] = _safe_rel(item.get("source", ""), field=f"package.source.shared[{index}].source")
        item["mount"] = _safe_rel(item.get("mount", ""), field=f"package.source.shared[{index}].mount")
        if item["mount"] in mounts:
            raise AgentPackageError(f"duplicate package.source.shared mount {item['mount']!r}")
        mounts.add(item["mount"])

    prompt = package.get("prompt") or {}
    if not isinstance(prompt, dict):
        raise AgentPackageError("package.prompt must be an object")
    for field in ("system", "brief"):
        if prompt.get(field) is not None and not isinstance(prompt[field], str):
            raise AgentPackageError(f"package.prompt.{field} must be a string")
    context_files = prompt.get("context_files") or {}
    if not isinstance(context_files, dict) or any(
        not isinstance(path, str) or not isinstance(body, str) for path, body in context_files.items()
    ):
        raise AgentPackageError("package.prompt.context_files must map paths to strings")
    for path in context_files:
        _safe_rel(path, field="package.prompt.context_files path")

    for field in ("skills", "tools"):
        rows = package.get(field) or []
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise AgentPackageError(f"package.{field} must be an array of objects")
        package[field] = rows
    for field in ("runtime", "environment", "execution", "delivery"):
        row = package.get(field) or {}
        if not isinstance(row, dict):
            raise AgentPackageError(f"package.{field} must be an object")
        package[field] = row
    runtime_image = package["runtime"].get("image")
    if runtime_image is not None and not isinstance(runtime_image, str):
        raise AgentPackageError("package.runtime.image must be a string")
    timeout = package["execution"].get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            raise AgentPackageError("package.execution.timeout_seconds must be an integer")
        if timeout < 1 or timeout > 604800:
            raise AgentPackageError("package.execution.timeout_seconds must be between 1 and 604800")
    package["environment"]["values"] = normalize_environment_values(
        package["environment"].get("values"), field="package.environment.values"
    )
    package["environment"]["secret_refs"] = normalize_secret_references(
        package["environment"].get("secret_refs"), field="package.environment.secret_refs"
    )
    if not isinstance(package["delivery"].get("payload_required", False), bool):
        raise AgentPackageError("package.delivery.payload_required must be a boolean")

    package["imports"] = _normalize_imports(package.get("imports"))
    package["federation"] = _normalize_federation(package.get("federation"))
    return package


__all__ = [
    "AgentPackageError",
    "PACKAGE_SCHEMA",
    "build_agent_package",
    "compatibility_snapshot",
    "compose_system_prompt",
    "is_reserved_agent_environment_name",
    "normalize_environment_values",
    "project_manifest_environment",
    "normalize_secret_references",
    "source_slice_from_manifest",
    "validate_agent_package",
]
