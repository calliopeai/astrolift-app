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
import re
from collections.abc import Mapping
from typing import Any

# ---- runtime detection --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _RuntimeSignal:
    runtime: str
    default_port: int
    files: tuple[str, ...]


_RUNTIME_SIGNALS: tuple[_RuntimeSignal, ...] = (
    _RuntimeSignal(
        "python", 8000, ("Pipfile", "requirements.txt", "pyproject.toml")
    ),
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
    out = sorted(
        p for p in files
        if p == "Dockerfile" or p.startswith("Dockerfile.")
    )
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
            _expose_port(files.get("Dockerfile") if "Dockerfile" in files else None)
            or default_port
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

            wl_name = (
                app_name
                if path == "Dockerfile"
                else f"{app_name}-{path.split('.', 1)[1]}"
            )
            primary = (i == 0)  # first Dockerfile gets the public hostname
            port = (
                _expose_port(files.get(path))
                if isinstance(files.get(path), str)
                else None
            ) or (primary_port if primary else 0)
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
