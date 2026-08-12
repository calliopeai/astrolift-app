"""
Auto-discover an ``astrolift.toml`` shape from repo signals (#119).

The function walks a `{path: contents}` map (the SCM provider hands
us the list of files at HEAD; we read a few selectively for inner
signals like ``EXPOSE`` lines). Output is a ``dict`` matching the
TOML manifest shape — caller can write it as TOML, render it as a
diff, or pass it through ``parse_raw`` to validate.

The inference is **always a draft**: the discoverer never claims to
have produced a runnable manifest, only a starting point.
``confidence`` per workload reflects how many strong signals
agreed; the UI surfaces it so reviewers can spot speculative bits.

Why a heuristic rather than parsing every config:
the goal is to scaffold something useful for 80% of repos in 90% of
cases, not to be a perfect parser of every build tool. Strong
signals (Dockerfile + EXPOSE) win; weak signals (presence of a
runtime ecosystem file) just hint.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import re
import tomllib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

# ---- runtime detection --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _RuntimeSignal:
    runtime: str
    default_port: int
    files: tuple[str, ...]


_RUNTIME_SIGNALS: tuple[_RuntimeSignal, ...] = (
    _RuntimeSignal("python", 8000, ("Pipfile", "requirements.txt", "pyproject.toml")),
    _RuntimeSignal("node", 3000, ("package.json",)),
    _RuntimeSignal("go", 8080, ("go.mod",)),
    _RuntimeSignal("rust", 8080, ("Cargo.toml",)),
    _RuntimeSignal("java", 8080, ("pom.xml", "build.gradle", "build.gradle.kts")),
)


def _detect_runtime(files: Mapping[str, Any]) -> tuple[str, int]:
    """Return ``(runtime, default_port)`` based on ecosystem files at
    the repo root. ``unknown`` when nothing matches; default port is
    8080 (a reasonable guess for HTTP services)."""
    for sig in _RUNTIME_SIGNALS:
        if any(f in files for f in sig.files):
            # Special-case: a Django app (manage.py at root) is
            # usually 8000 even if it's a Pipfile-driven Python repo.
            if sig.runtime == "python" and "manage.py" in files:
                return "python", 8000
            return sig.runtime, sig.default_port
    return "unknown", 8080


# ---- Dockerfile parsing -------------------------------------------------


_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(\d+)", re.MULTILINE)


def _expose_port(dockerfile_text: str | None) -> int | None:
    """First ``EXPOSE`` integer in a Dockerfile, or None."""
    if not dockerfile_text:
        return None
    m = _EXPOSE_RE.search(dockerfile_text)
    return int(m.group(1)) if m else None


def _list_dockerfiles(files: Mapping[str, Any]) -> list[str]:
    """Return Dockerfile paths in the repo, deterministic order.

    Recognises the canonical name + variants like ``Dockerfile.api``
    or ``Dockerfile.jobs``. Doesn't recurse into subdirectories — the
    multi-package monorepo case is its own follow-up.
    """
    out = sorted(p for p in files if p == "Dockerfile" or p.startswith("Dockerfile."))
    return out


# ---- env hint parsing --------------------------------------------------


_ENV_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)


def _env_hint_keys(env_example_text: str | None) -> list[str]:
    """Return keys from a ``.env.example`` (or ``.env.sample``).

    The platform doesn't carry the values across — those are the
    secret bits that should be re-set per environment. We surface
    just the keys so the UI can prompt for managed-service binding
    or per-env env var entry.
    """
    if not env_example_text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _ENV_KEY_RE.finditer(env_example_text):
        k = m.group(1)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ---- compose service hints ---------------------------------------------


_COMPOSE_SERVICE_HINTS: dict[str, str] = {
    # service-image-prefix → managed service kind
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "redis": "redis",
    "rabbitmq": "rabbitmq",
    "kafka": "kafka",
    "mongo": "mongodb",
    "elasticsearch": "opensearch",
    "opensearch": "opensearch",
    "memcached": "memcached",
}


def _compose_managed_services(compose_text: str | None) -> list[str]:
    """Cheap regex over docker-compose.yml looking for known service
    images. Returns a list of canonical managed-service kinds we
    could provision in their place. Skipped when the file is absent.

    YAML parsing would be more correct but pyyaml is a runtime dep
    we don't pull in just for this; the heuristic catches the common
    case (``image: postgres:16``) and the rare false-positive lands
    in the discovered draft as a hint, not a commitment.
    """
    if not compose_text:
        return []
    image_re = re.compile(r"image:\s*[\"']?([a-z0-9._/-]+)", re.IGNORECASE)
    found: list[str] = []
    seen: set[str] = set()
    for m in image_re.finditer(compose_text):
        ref = m.group(1).lower().rsplit("/", 1)[-1].split(":", 1)[0]
        for prefix, kind in _COMPOSE_SERVICE_HINTS.items():
            if ref.startswith(prefix) and kind not in seen:
                seen.add(kind)
                found.append(kind)
                break
    return found


# ---- main entry --------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveredManifest:
    """Output of ``infer_manifest_from_signals``.

    ``manifest`` is a TOML-shaped dict the caller can write/serialize.
    ``confidence`` is high/medium/low — high when at least one
    Dockerfile + an EXPOSE line + a runtime match agreed; low when
    only ecosystem files were found (a draft barely better than a
    blank).
    ``hints`` is a list of human-readable notes the UI surfaces
    next to the draft (env-var keys, managed-service kinds, runtime).
    """

    manifest: dict[str, Any]
    confidence: str
    hints: list[str]


def infer_manifest_from_signals(
    *,
    app_name: str,
    files: Mapping[str, Any],
) -> DiscoveredManifest:
    """Build a draft TOML manifest from a `{path: contents}` map.

    ``files`` may map a path to ``None`` for 'present, contents not
    fetched' or to ``str``/``bytes`` for content-aware inference. Big
    files we never read (lockfiles) should always be ``None`` so the
    caller doesn't haul them through.
    """
    runtime, default_port = _detect_runtime(files)
    dockerfiles = _list_dockerfiles(files)
    env_keys = _env_hint_keys(files.get(".env.example") or files.get(".env.sample"))
    compose_kinds = _compose_managed_services(files.get("docker-compose.yml"))

    workloads: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    confidence = "low"
    hints: list[str] = [f"detected runtime: {runtime}"]

    # No Dockerfile → fall back to a single inferred workload using
    # the runtime's default port. The user is expected to wire build
    # config separately.
    if not dockerfiles:
        workloads.append(
            {
                "name": app_name,
                "kind": "deployment",
                "is_public": True,
                "containers": [
                    {
                        "name": app_name,
                        "is_primary": True,
                        "port": default_port,
                    }
                ],
            }
        )
    else:
        primary_port = (
            _expose_port(files.get("Dockerfile") if "Dockerfile" in files else None) or default_port
        )
        if "Dockerfile" in dockerfiles or "Dockerfile.api" in dockerfiles:
            confidence = "high"

        for i, path in enumerate(dockerfiles):
            if path == "Dockerfile.jobs":
                jobs.append(
                    {
                        "name": "jobs",
                        "schedule": "0 0 * * *",  # nightly default; user edits
                        "command": [],  # caller fills in
                        "dockerfile_path": "Dockerfile.jobs",
                    }
                )
                hints.append(
                    "saw Dockerfile.jobs → drafted nightly [[jobs]] entry; "
                    "set the schedule + command before deploying"
                )
                continue

            wl_name = app_name if path == "Dockerfile" else f"{app_name}-{path.split('.', 1)[1]}"
            primary = i == 0  # first Dockerfile gets the public hostname
            port = (_expose_port(files.get(path)) if isinstance(files.get(path), str) else None) or (
                primary_port if primary else 0
            )
            workloads.append(
                {
                    "name": wl_name,
                    "kind": "deployment",
                    "is_public": primary,
                    "containers": [
                        {
                            "name": wl_name,
                            "is_primary": True,
                            "port": port,
                            "dockerfile_path": path,
                        }
                    ],
                }
            )

    if env_keys:
        hints.append(f"saw .env.example with {len(env_keys)} key(s): {', '.join(env_keys[:5])}")
    managed: list[dict[str, Any]] = []
    for kind in compose_kinds:
        managed.append({"kind": kind, "name": kind})
        hints.append(f"docker-compose.yml suggests {kind} — drafted as managed service")

    manifest: dict[str, Any] = {"name": app_name}
    if workloads:
        manifest["workloads"] = workloads
    if jobs:
        manifest["jobs"] = jobs
    if managed:
        manifest["managed_services"] = managed
    if env_keys:
        # Surface as a comment-friendly hint rather than auto-injecting
        # env values — values belong in secrets/managed-service
        # bindings, not the TOML shape.
        manifest["_discovered_env_keys"] = env_keys

    if not workloads and not jobs:
        confidence = "low"

    return DiscoveredManifest(
        manifest=manifest,
        confidence=confidence,
        hints=hints,
    )


# ---- agent-manifest scan (spec 33, PR-3) -------------------------------
#
# Distinct concern from ``infer_manifest_from_signals`` above (which drafts
# a manifest from Dockerfile / ecosystem heuristics). This walks a fetched
# repo tree looking for *committed* ``astrolift.toml`` files that declare an
# agent workload, in one of two layouts:
#
#   * monorepo — ``agents/<slug>/astrolift.toml`` (one manifest per agent,
#     each in its own directory directly under ``agents/``);
#   * single   — a root ``astrolift.toml``.
#
# Each candidate is parsed with the real manifest parser and kept only when
# it declares exactly an agent (its sole workload's ``kind == "agent"``).
# Non-agent manifests (a web app's root ``astrolift.toml``, a worker, …) are
# silently ignored so pointing the scanner at a mixed repo registers only
# the agents. Unparseable / malformed manifests are skipped too — discovery
# is best-effort and never raises on one bad file.

# Directory under which monorepo agent manifests live. A manifest must sit
# *directly* in a child directory of this prefix (``agents/<slug>/…``);
# deeper nesting (``agents/<slug>/sub/astrolift.toml``) is not an agent root
# and is ignored so a vendored fixture or example dir doesn't register.
_AGENTS_DIR = "agents"
_MANIFEST_BASENAME = "astrolift.toml"
_FEDERATION_BASENAME = "astrolift.agents.toml"
_FEDERATION_SCHEMA = "astrolift.agent.federation/v1"


class AgentFederationError(ValueError):
    """The repo's explicit agent-bundle boundary is malformed.

    A present-but-invalid bundle must fail closed. Falling back to the legacy
    broad ``agents/*`` scan would silently ignore exclusions and could
    register code the repo owner deliberately kept outside the bundle.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveredAgentManifest:
    """One agent manifest found by :func:`scan_agent_manifests`.

    ``manifest_path`` is the repo-relative path (the value that lands on
    ``RegisteredApp.manifest_path`` and participates in the
    ``(source_repo, manifest_path)`` uniqueness, so re-scans dedupe on it).
    ``name`` / ``slug`` / ``workload_kind`` come from parsing the manifest:
    ``name`` is the manifest's top-level name; ``slug`` is the agent
    workload's name (the value that becomes the ``Workload.slug``);
    ``workload_kind`` is always ``"agent"`` for a kept entry (carried
    explicitly so the preview type is self-describing).
    """

    manifest_path: str
    name: str
    slug: str
    workload_kind: str
    raw_text: str
    federation: dict[str, Any] = dataclasses.field(default_factory=dict)


def _safe_federation_path(value: Any, *, field: str, allow_glob: bool = False) -> str:
    raw = str(value or "").strip().removeprefix("./")
    if not raw or "\x00" in raw or "\\" in raw:
        raise AgentFederationError(f"{field} must be a non-empty POSIX path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise AgentFederationError(f"{field} may not be absolute or contain '..': {raw!r}")
    if not allow_glob and any(char in raw for char in "*?["):
        raise AgentFederationError(f"{field} may not contain glob characters: {raw!r}")
    return raw


def _string_globs(value: Any, *, field: str, default: tuple[str, ...] = ()) -> list[str]:
    if value is None:
        value = list(default)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AgentFederationError(f"{field} must be a string array")
    return [_safe_federation_path(item, field=field, allow_glob=True) for item in value]


def _federated_candidates(files: Mapping[str, Any]) -> dict[str, dict[str, Any]] | None:
    """Return selected manifest paths + member metadata for an explicit bundle.

    ``None`` means no federation file is present and legacy discovery should
    apply. An empty dict is a valid bundle that currently selects no agents.
    """
    raw_text = files.get(_FEDERATION_BASENAME)
    if raw_text is None:
        return None
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise AgentFederationError(f"{_FEDERATION_BASENAME} must contain TOML text")
    try:
        raw = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise AgentFederationError(f"invalid {_FEDERATION_BASENAME}: {exc}") from exc

    schema = str(raw.get("schema") or "").strip()
    if schema != _FEDERATION_SCHEMA:
        raise AgentFederationError(f"{_FEDERATION_BASENAME}.schema must be {_FEDERATION_SCHEMA!r}")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise AgentFederationError(f"{_FEDERATION_BASENAME}.name is required")
    include = _string_globs(
        raw.get("include"),
        field=f"{_FEDERATION_BASENAME}.include",
        default=("agents/*/astrolift.toml",),
    )
    exclude = _string_globs(raw.get("exclude"), field=f"{_FEDERATION_BASENAME}.exclude")
    auto_register_new = raw.get("auto_register_new", False)
    if not isinstance(auto_register_new, bool):
        raise AgentFederationError(f"{_FEDERATION_BASENAME}.auto_register_new must be a boolean")

    candidate_paths = sorted(
        path
        for path in files
        if auto_register_new
        and path != _FEDERATION_BASENAME
        and path.endswith(f"/{_MANIFEST_BASENAME}")
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in include)
        and not any(fnmatch.fnmatchcase(path, pattern) for pattern in exclude)
    )
    selected: dict[str, dict[str, Any]] = {path: {} for path in candidate_paths}

    members = raw.get("agents") or []
    if not isinstance(members, list):
        raise AgentFederationError(f"{_FEDERATION_BASENAME}.agents must be an array of tables")
    disabled_paths: set[str] = set()
    aliases: set[str] = set()
    for index, member in enumerate(members):
        field = f"{_FEDERATION_BASENAME}.agents[{index}]"
        if not isinstance(member, dict):
            raise AgentFederationError(f"{field} must be a table")
        path = _safe_federation_path(member.get("manifest"), field=f"{field}.manifest")
        if not path.endswith(_MANIFEST_BASENAME):
            raise AgentFederationError(f"{field}.manifest must point to an astrolift.toml")
        enabled = member.get("enabled", True)
        if not isinstance(enabled, bool):
            raise AgentFederationError(f"{field}.enabled must be a boolean")
        alias = str(member.get("alias") or PurePosixPath(path).parent.name).strip()
        if not alias or "/" in alias or "\\" in alias:
            raise AgentFederationError(f"{field}.alias must be one path-free name")
        if alias in aliases:
            raise AgentFederationError(f"duplicate federation agent alias {alias!r}")
        aliases.add(alias)
        if not enabled:
            disabled_paths.add(path)
            selected.pop(path, None)
            continue
        if path not in files:
            raise AgentFederationError(f"{field}.manifest {path!r} does not exist")
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in exclude):
            raise AgentFederationError(
                f"{field}.manifest {path!r} is excluded by {_FEDERATION_BASENAME}.exclude"
            )
        selected[path] = {"alias": alias}

    for path in disabled_paths:
        selected.pop(path, None)
    ordered_paths = sorted(selected)
    anchor = ordered_paths[0] if ordered_paths else ""
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    for path in ordered_paths:
        selected[path] = {
            "schema": _FEDERATION_SCHEMA,
            "name": name,
            "manifest_path": _FEDERATION_BASENAME,
            "definition_sha256": digest,
            "member": selected[path].get("alias") or PurePosixPath(path).parent.name,
            "anchor_manifest": anchor,
            "auto_register_new": auto_register_new,
            "include": include,
            "exclude": exclude,
        }
    return selected


def _candidate_manifest_paths(files: Mapping[str, Any]) -> list[str]:
    """Repo-relative paths that *could* be agent manifests, sorted.

    Returns the root ``astrolift.toml`` (when present) plus every
    ``agents/<slug>/astrolift.toml``. Deeper paths under ``agents/`` and
    ``astrolift.toml`` files nested elsewhere are excluded — only the two
    sanctioned layouts. Sorted for deterministic registration order so a
    re-scan creates rows in a stable sequence.
    """
    out: list[str] = []
    if _MANIFEST_BASENAME in files:
        out.append(_MANIFEST_BASENAME)
    prefix = f"{_AGENTS_DIR}/"
    for path in files:
        if not path.startswith(prefix) or not path.endswith(f"/{_MANIFEST_BASENAME}"):
            continue
        # Exactly ``agents/<slug>/astrolift.toml`` — three segments. A
        # deeper path (``agents/<slug>/nested/astrolift.toml``) has more
        # and is not an agent root.
        if len(path.split("/")) != 3:
            continue
        out.append(path)
    return sorted(set(out))


def _parse_agent_manifest(text: str | None) -> tuple[str, str, str] | None:
    """Parse one manifest body and return ``(name, slug, kind)`` when it is
    an agent manifest, else ``None``.

    A manifest qualifies as an agent when it has exactly one workload and
    that workload's ``kind == "agent"``. We deliberately require a *single*
    agent workload: an ``agents/<slug>/astrolift.toml`` describes one agent,
    and a root manifest with mixed workloads is an app (handled by the app
    registration path), not an agent. Parse / validation errors return
    ``None`` so the scan skips the file rather than failing the whole walk.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    # Local import keeps ``discover`` free of a hard parser dependency at
    # module import time (the heuristic half above has no such need).
    from astrolift_manifest.parser import ManifestError, parse_raw

    try:
        manifest = parse_raw(text)
    except ManifestError:
        return None
    agent_workloads = [w for w in manifest.workloads if w.kind == "agent"]
    if len(agent_workloads) != 1 or len(manifest.workloads) != 1:
        return None
    workload = agent_workloads[0]
    return manifest.name, workload.name, workload.kind


def scan_agent_manifests(files: Mapping[str, Any]) -> list[DiscoveredAgentManifest]:
    """Find every agent manifest in a fetched repo tree.

    ``files`` is a ``{repo_relative_path: contents}`` map — the same shape
    :func:`infer_manifest_from_signals` consumes and the SCM provider's
    zipball unpacker produces. Paths must map to ``str`` contents to be
    parsed; a ``None`` value (present-but-not-fetched) is skipped, so a
    caller that wants discovery must fetch the bodies of the manifest
    candidates.

    Returns the kept agent manifests in deterministic path order. A repo
    with no agent manifests returns ``[]`` (not an error) — the caller
    surfaces "no agents found" to the operator.
    """
    found: list[DiscoveredAgentManifest] = []
    federated = _federated_candidates(files)
    candidate_paths = sorted(federated) if federated is not None else _candidate_manifest_paths(files)
    for path in candidate_paths:
        parsed = _parse_agent_manifest(files.get(path) if isinstance(files.get(path), str) else None)
        if parsed is None:
            continue
        name, slug, kind = parsed
        found.append(
            DiscoveredAgentManifest(
                manifest_path=path,
                name=name,
                slug=slug,
                workload_kind=kind,
                raw_text=files[path],
                federation=(federated or {}).get(path, {}),
            )
        )
    return found


# ---- app-manifest scan (#979) -----------------------------------------
#
# Monorepo / multi-service *app* discovery — the app-side mirror of the agent
# scan above (and deliberately the same shape, so there is one discovery
# contract). Walks a fetched repo tree for *committed* ``astrolift.toml``
# files that declare a deployable app, in one of two layouts:
#
#   * monorepo — ``apps/<slug>/astrolift.toml`` (one manifest per service,
#     each in its own directory directly under ``apps/``);
#   * single   — a root ``astrolift.toml``.
#
# Each candidate is parsed with the real manifest parser and kept only when it
# declares at least one *non-agent* workload (a deployment / cronjob / worker /
# static_site / function / …). A pure single-agent manifest is owned by the
# agent-registration path (``scan_agent_manifests``) and is silently ignored
# here, so pointing both scanners at one mixed repo registers each manifest
# once, in the right place. A manifest with an agent workload *plus* a
# non-agent workload is an app (it has a deployable surface) and is kept.
# Unparseable / malformed manifests are skipped — discovery is best-effort and
# never raises on one bad file.
#
# ``build_context`` on each kept manifest is the manifest's own directory
# (``apps/<slug>`` for a monorepo service, ``"."`` for a root manifest) so each
# service builds from its own subdir — the build activity reads
# ``RegisteredApp.build_context`` as the context path within the source tree.
_APPS_DIR = "apps"


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveredAppManifest:
    """One app manifest found by :func:`scan_app_manifests`.

    ``manifest_path`` is the repo-relative path (the value that lands on
    ``RegisteredApp.manifest_path`` and participates in the
    ``(source_repo, manifest_path)`` uniqueness, so re-scans dedupe on it).
    ``build_context`` is the manifest's own directory — the subdir the service
    builds from (``"."`` for a root manifest) — so each service in a monorepo
    builds from its own folder. ``name`` is the manifest's top-level name;
    ``workload_count`` is how many workloads the manifest declares (a
    self-describing preview hint).
    """

    manifest_path: str
    name: str
    build_context: str
    workload_count: int
    raw_text: str


def _candidate_app_manifest_paths(files: Mapping[str, Any]) -> list[str]:
    """Repo-relative paths that *could* be app manifests, sorted.

    Returns the root ``astrolift.toml`` (when present) plus every
    ``apps/<slug>/astrolift.toml``. Deeper paths under ``apps/`` and
    ``astrolift.toml`` files nested elsewhere are excluded — only the two
    sanctioned layouts. Sorted for deterministic registration order so a
    re-scan creates rows in a stable sequence.
    """
    out: list[str] = []
    if _MANIFEST_BASENAME in files:
        out.append(_MANIFEST_BASENAME)
    prefix = f"{_APPS_DIR}/"
    for path in files:
        if not path.startswith(prefix) or not path.endswith(f"/{_MANIFEST_BASENAME}"):
            continue
        # Exactly ``apps/<slug>/astrolift.toml`` — three segments. A deeper
        # path (``apps/<slug>/nested/astrolift.toml``) has more and is not an
        # app root.
        if len(path.split("/")) != 3:
            continue
        out.append(path)
    return sorted(set(out))


def _parse_app_manifest(text: str | None) -> tuple[str, int] | None:
    """Parse one manifest body and return ``(name, workload_count)`` when it is
    an app manifest, else ``None``.

    A manifest qualifies as an app when it declares at least one *non-agent*
    workload. A pure single-agent manifest (its sole workload is
    ``kind == "agent"``) is owned by the agent-registration path and returns
    ``None`` so a mixed repo registers agents and apps from their respective
    scanners without overlap. Parse / validation errors return ``None`` so the
    scan skips the file rather than failing the whole walk.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    # Local import keeps ``discover`` free of a hard parser dependency at
    # module import time (mirrors ``_parse_agent_manifest`` above).
    from astrolift_manifest.parser import ManifestError, parse_raw

    try:
        manifest = parse_raw(text)
    except ManifestError:
        return None
    if not manifest.workloads:
        return None
    if not any(w.kind != "agent" for w in manifest.workloads):
        return None
    return manifest.name, len(manifest.workloads)


def scan_app_manifests(files: Mapping[str, Any]) -> list[DiscoveredAppManifest]:
    """Find every app manifest in a fetched repo tree.

    ``files`` is a ``{repo_relative_path: contents}`` map — the same shape
    :func:`scan_agent_manifests` consumes and the SCM provider's zipball
    unpacker produces. Paths must map to ``str`` contents to be parsed; a
    ``None`` value (present-but-not-fetched) is skipped, so a caller that wants
    discovery must fetch the bodies of the manifest candidates.

    Returns the kept app manifests in deterministic path order. A repo with no
    app manifests returns ``[]`` (not an error).
    """
    found: list[DiscoveredAppManifest] = []
    for path in _candidate_app_manifest_paths(files):
        parsed = _parse_app_manifest(files.get(path) if isinstance(files.get(path), str) else None)
        if parsed is None:
            continue
        name, workload_count = parsed
        build_context = path.rsplit("/", 1)[0] if "/" in path else "."
        found.append(
            DiscoveredAppManifest(
                manifest_path=path,
                name=name,
                build_context=build_context,
                workload_count=workload_count,
                raw_text=files[path],
            )
        )
    return found
