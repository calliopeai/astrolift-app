"""
SCM -> Brief assembly service (#41).

``assemble_agent_brief`` fetches an agent config repository as a zipball —
through the ``astrolift_scm`` provider abstraction when a SourceConnection is
supplied (so non-GitHub hosts like GitLab work), or via the legacy GitHub /
``GITHUB_PAT`` path otherwise — parses the ``astrolift.toml`` it contains, and
assembles an immutable
:class:`~astrolift_agents.models.brief.Brief` in ``READY`` status that an agent
fetches at boot time.

Content addressing
------------------
The ``Brief.content_hash`` column is globally ``unique`` (see the model). It is
therefore *not* the hash of the raw zipball bytes — two different orgs could
legitimately assemble a Brief from the same public config repo, and a raw-bytes
hash would either collide on the unique constraint or leak one org's Brief to
another. Instead the hash is taken over the *canonical JSON payload* the model
docstring describes: the org identity, the zipball digest, and the task context.
That makes the hash unique to the inputs that actually matter and lets dedup
short-circuit safely within a single org.

Blob storage
------------
The zipball is stored via the install's ``BlobStoreDriver``, resolved through
the same path the pipeline subsystem uses
(``astrolift_pipelines.artifact_store._get_blob_driver``). Storage is
best-effort: if no blob store is configured for the install the Brief is still
created with an empty ``storage_key`` so dispatch is not blocked in dev/CI.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import stat
import tomllib
import zipfile
from pathlib import PurePosixPath
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from astrolift_agents.models import Brief
from astrolift_agents.services.agent_payload import (
    MAX_SOURCE_ARCHIVE_BYTES,
    MAX_SOURCE_EXPANDED_BYTES,
    MAX_SOURCE_FILES,
)

log = logging.getLogger(__name__)

# GitHub zipball endpoint. ``allow_redirects`` is required: the API replies with
# a 302 to a short-lived codeload.github.com URL that streams the archive.
_GITHUB_API = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 60

# Keys consumed structurally from the manifest's [environment] table; everything
# else under [environment] is treated as a literal env var.
_ENVIRONMENT_RESERVED_KEYS = frozenset({"tool_preset", "allow_install"})
_MAX_MANIFEST_BYTES = 1 * 1024 * 1024
_MAX_PROMPT_BYTES = 1 * 1024 * 1024
_MAX_INCLUDE_DEPTH = 16


class ManifestNotFoundError(ValueError):
    """Raised when an explicit ``manifest_path`` has no matching astrolift.toml."""


class ManifestReferenceError(ValueError):
    """A manifest include/file reference is unsafe, missing, or malformed."""


class PayloadStorageError(RuntimeError):
    """A manifest references runtime files but its bundle could not be stored."""


def assemble_agent_brief(
    *,
    organization,
    config_repo: str,
    config_branch: str = "main",
    manifest_path: str = "",
    context: dict | None = None,
    ttl_seconds: int = 3600,
    source_connection=None,
) -> Brief:
    """Fetch ``config_repo``, parse its ``astrolift.toml``, and assemble a Brief.

    Args:
        organization: the ``astrolift_identity.Organization`` the Brief belongs
            to. The Brief is scoped to this org and the org identity is folded
            into the content hash.
        config_repo: ``"owner/repo"`` of the repository holding the agent's
            ``astrolift.toml``.
        config_branch: the branch (or tag/ref) to fetch. Defaults to ``main``.
        manifest_path: optional repo-relative path to the agent's manifest
            (e.g. ``agents/foo/astrolift.toml`` or the directory ``agents/foo``),
            so a single repo can hold many agents. Empty (default) selects the
            root-most ``astrolift.toml``. Folded into the content hash so each
            agent in a monorepo gets a distinct Brief.
        context: optional task context metadata (task id, actor, slugs). Stored
            verbatim on the Brief and folded into the content hash.
        ttl_seconds: how long the READY Brief stays valid before the agent
            bootstrap layer treats it as expired. ``0`` means no expiry.
        source_connection: optional ``astrolift_scm.SourceConnection`` whose
            stored credential and host fetch the zipball through the SCM
            provider abstraction — so a GitLab (or any supported host) config
            repo works, not just GitHub. When None the fetch falls back to the
            anonymous / ``GITHUB_PAT`` GitHub path for back-compat.

    Returns:
        A ``Brief`` in ``READY`` status. If an identical Brief (same org, same
        zipball digest, same context) already exists and is READY it is returned
        unchanged rather than re-assembled.

    Raises:
        requests.HTTPError: the GitHub fallback path returned a non-2xx response.
        astrolift_scm.providers.ProviderError: the SCM provider path failed
            (auth, network, or an unsupported host).
    """
    context = context or {}
    owner_repo = config_repo.strip("/")

    if source_connection is not None:
        from astrolift_scm.providers import fetch_zipball

        zip_bytes = fetch_zipball(
            source_connection,
            repo_full_name=owner_repo,
            ref=config_branch,
        )
    else:
        zip_bytes = _fetch_zipball(owner_repo, config_branch)
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()

    # Content-address over the canonical payload, not the raw zip bytes, so the
    # globally-unique content_hash is org-correct (see module docstring).
    content_hash = _content_hash(
        organization=organization,
        zip_digest=zip_digest,
        context=context,
        manifest_path=manifest_path,
    )

    existing = Brief.objects.filter(content_hash=content_hash).first()
    if existing is not None and existing.status == Brief.Status.READY:
        # content_hash folds in the org guid, so a match is the same org; the
        # equality check is a defensive belt-and-braces guard, not load-bearing.
        if existing.organization_id == organization.id:
            log.info(
                "reusing existing READY Brief %s for org %s (%s@%s)",
                existing.guid,
                organization.id,
                owner_repo,
                config_branch,
            )
            return existing

    manifest, secrets_refs = _parse_manifest(zip_bytes, manifest_path)

    storage_key = _store_bundle(organization=organization, content_hash=content_hash, zip_bytes=zip_bytes)
    if manifest.get("requires_payload") and not storage_key:
        raise PayloadStorageError(
            "agent manifest references files/scripts/binaries, but no payload blob store is available"
        )

    brief = Brief.objects.create(
        organization=organization,
        content_hash=content_hash,
        storage_key=storage_key,
        manifest_snapshot=manifest,
        secrets_refs=secrets_refs,
        context=context,
        status=Brief.Status.READY,
        ttl_seconds=ttl_seconds,
        assembled_at=timezone.now(),
    )
    log.info(
        "assembled Brief %s for org %s from %s@%s (storage_key=%r)",
        brief.guid,
        organization.id,
        owner_repo,
        config_branch,
        storage_key,
    )
    return brief


# -- internal helpers -------------------------------------------------------


def _fetch_zipball(owner_repo: str, branch: str) -> bytes:
    """Download the GitHub zipball for ``owner_repo`` at ``branch``.

    Raises ``requests.HTTPError`` on a non-2xx response.
    """
    zipball_url = f"{_GITHUB_API}/repos/{owner_repo}/zipball/{branch}"

    github_pat = getattr(settings, "GITHUB_PAT", "") or ""
    headers = {"Accept": "application/vnd.github+json"}
    if github_pat:
        headers["Authorization"] = f"Bearer {github_pat}"

    resp = requests.get(
        zipball_url,
        headers=headers,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        allow_redirects=True,
        stream=True,
    )
    resp.raise_for_status()
    from astrolift_scm.providers.archive_download import (
        ArchiveDownloadTooLarge,
        read_requests_response,
    )

    try:
        return read_requests_response(resp, limit=MAX_SOURCE_ARCHIVE_BYTES)
    except ArchiveDownloadTooLarge as exc:
        raise ManifestReferenceError(str(exc)) from exc


def _content_hash(*, organization, zip_digest: str, context: dict, manifest_path: str = "") -> str:
    """SHA-256 of the canonical JSON payload identifying this Brief.

    The payload binds the org identity, the zipball digest, and the task
    context. ``sort_keys`` makes the encoding deterministic so two assemblies
    with the same inputs hash identically (the basis for dedup).

    ``manifest_path`` is folded in only when set, so a monorepo's per-agent
    Briefs are distinct while root-manifest hashes stay identical to those
    assembled before manifest_path existed (no dedup churn / re-assembly).
    """
    payload = {
        "org": str(organization.guid),
        "zip_sha256": zip_digest,
        "context": context,
    }
    if manifest_path:
        payload["manifest_path"] = manifest_path
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def select_manifest_member(names: list[str], manifest_path: str = "") -> str | None:
    """Pick the ``astrolift.toml`` member to read from a zipball's namelist.

    GitHub zipballs nest everything under a single ``<owner>-<repo>-<sha>/``
    top-level directory. Without ``manifest_path`` the root-most manifest (the
    fewest path segments) is chosen — the historical behaviour. With
    ``manifest_path`` (a repo-relative path like ``agents/foo/astrolift.toml``
    or the directory ``agents/foo``), the member whose path *below the
    top-level dir* matches it exactly is chosen, enabling many agents per repo.
    Returns ``None`` when nothing matches.
    """
    candidates = [n for n in names if n.endswith("astrolift.toml")]
    if not candidates:
        return None
    if not manifest_path:
        # Fewest path separators == closest to the archive root.
        return min(candidates, key=lambda n: n.count("/"))

    target = manifest_path.strip().strip("/")
    if not target.endswith("astrolift.toml"):
        target = f"{target}/astrolift.toml" if target else "astrolift.toml"
    for name in candidates:
        # Strip the zipball's "<owner>-<repo>-<sha>/" top-level dir.
        rel = name.split("/", 1)[1] if "/" in name else name
        if rel == target:
            return name
    return None


def _parse_manifest(zip_bytes: bytes, manifest_path: str = "") -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse ``astrolift.toml`` from the zipball.

    Returns ``(manifest_snapshot, secrets_refs)``. When the archive contains no
    root ``astrolift.toml`` (and no ``manifest_path`` was requested) both are
    empty — the Brief is still assembled so the caller can decide how to treat a
    config-less repo. An explicit ``manifest_path`` that matches nothing raises
    ``ManifestNotFoundError`` so a misconfigured agent fails loudly instead of
    booting an empty Brief.
    """
    manifest: dict[str, Any] = {}
    secrets_refs: list[dict[str, str]] = []

    if len(zip_bytes) > MAX_SOURCE_ARCHIVE_BYTES:
        raise ManifestReferenceError(f"source ZIP exceeds {MAX_SOURCE_ARCHIVE_BYTES} bytes")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ManifestReferenceError("source payload is not a valid ZIP archive") from exc

    with zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > MAX_SOURCE_FILES:
            raise ManifestReferenceError(f"source ZIP contains more than {MAX_SOURCE_FILES} files")
        if sum(info.file_size for info in infos) > MAX_SOURCE_EXPANDED_BYTES:
            raise ManifestReferenceError(f"source ZIP expands beyond {MAX_SOURCE_EXPANDED_BYTES} bytes")
        for info in infos:
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise ManifestReferenceError(f"source ZIP symlinks are not allowed: {info.filename!r}")
        toml_path = select_manifest_member(zf.namelist(), manifest_path)
        if toml_path is None:
            if manifest_path:
                raise ManifestNotFoundError(f"no astrolift.toml at {manifest_path!r} in the config repo")
            return manifest, secrets_refs
        repo_members, selected_manifest = _repo_member_map(zf, toml_path)
        config = _load_manifest_tree(repo_members, selected_manifest)

        manifest_dir = str(PurePosixPath(selected_manifest).parent)
        if manifest_dir == ".":
            manifest_dir = ""

        # File-backed system prompts keep the TOML small while preserving the
        # same immutable Brief snapshot contract.  References are relative to
        # the selected astrolift.toml and cannot escape its directory.
        skills = config.get("skills") or {}
        if not isinstance(skills, dict):
            raise ManifestReferenceError("[skills] must be a TOML table")
        for skill_slug, raw_skill_cfg in skills.items():
            skill_cfg = raw_skill_cfg or {}
            if not isinstance(skill_cfg, dict):
                raise ManifestReferenceError(f"[skills.{skill_slug}] must be a TOML table")
            inline_prompt = skill_cfg.get("system_prompt", "")
            prompt_ref = skill_cfg.get("system_prompt_file", "")
            if inline_prompt and prompt_ref:
                raise ManifestReferenceError(
                    f"skills.{skill_slug} may set only one of system_prompt or system_prompt_file"
                )
            if prompt_ref:
                prompt_path = _referenced_repo_path(
                    str(prompt_ref), base_dir=manifest_dir, field=f"skills.{skill_slug}.system_prompt_file"
                )
                prompt_bytes = _read_referenced_member(
                    repo_members,
                    prompt_path,
                    field=f"skills.{skill_slug}.system_prompt_file",
                    max_bytes=_MAX_PROMPT_BYTES,
                )
                try:
                    skill_cfg["system_prompt"] = prompt_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ManifestReferenceError(f"system prompt file {prompt_path!r} is not UTF-8") from exc

        asset_paths, executable_paths = _collect_assets(config, repo_members, manifest_dir)

    # [skills.*] -> manifest. Keep the first skill's slug for compatibility,
    # but compose every skill prompt/tool list in declaration order.
    skills = config.get("skills") or {}
    if skills:
        skill_slug = next(iter(skills))
        skill_rows: list[dict[str, Any]] = []
        for slug, raw_cfg in skills.items():
            cfg = raw_cfg or {}
            prompt = cfg.get("system_prompt", "")
            raw_tools = cfg.get("tools", []) or []
            if not isinstance(prompt, str):
                raise ManifestReferenceError(f"skills.{slug}.system_prompt must be a string")
            if not isinstance(raw_tools, list) or any(not isinstance(tool, str) for tool in raw_tools):
                raise ManifestReferenceError(f"skills.{slug}.tools must be a string array")
            skill_rows.append(
                {
                    "slug": slug,
                    "system_prompt": prompt,
                    "tools": list(raw_tools),
                }
            )
        prompts = [str(row["system_prompt"]).strip() for row in skill_rows if row["system_prompt"]]
        composed_prompt = "\n\n---\n\n".join(prompts)
        if len(composed_prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            raise ManifestReferenceError(f"composed skill prompts exceed {_MAX_PROMPT_BYTES} bytes")
        tools: list[Any] = []
        for row in skill_rows:
            for tool in row["tools"]:
                if tool not in tools:
                    tools.append(tool)
        manifest["skill_slug"] = skill_slug
        manifest["skills"] = skill_rows
        manifest["system_prompt"] = composed_prompt
        manifest["tools"] = tools

    manifest["payload_sha256"] = hashlib.sha256(zip_bytes).hexdigest()
    manifest["manifest_path"] = selected_manifest
    manifest["asset_paths"] = asset_paths
    manifest["executable_paths"] = executable_paths
    manifest["requires_payload"] = bool(asset_paths)

    # [environment] -> env_vars plus the two structural keys.
    env_section = config.get("environment") or {}
    manifest["env_vars"] = {k: v for k, v in env_section.items() if k not in _ENVIRONMENT_RESERVED_KEYS}
    manifest["tool_preset"] = env_section.get("tool_preset", "")
    manifest["allow_install"] = env_section.get("allow_install", False)

    # [secrets] -> references only (never the values). Each entry maps an env
    # var name to the secret's external URI/name; the Dispatch Service resolves
    # the value at spawn time.
    for env_var, ref in (config.get("secrets") or {}).items():
        if isinstance(ref, dict):
            secrets_refs.append({"env_var": env_var, "uri": ref.get("secret_name", "")})
        elif isinstance(ref, str):
            # Shorthand form: SECRET_ENV = "secret-name".
            secrets_refs.append({"env_var": env_var, "uri": ref})

    return manifest, secrets_refs


def _repo_member_map(zf: zipfile.ZipFile, selected_member: str) -> tuple[dict[str, bytes], str]:
    """Return ``{repo-relative path: bytes}`` and the selected relative path.

    GitHub/GitLab archives normally wrap the repository in one generated top
    directory.  The map strips only the selected manifest's first component,
    making all subsequent include and asset validation independent of the SCM
    provider's archive prefix.
    """
    prefix = selected_member.split("/", 1)[0] if "/" in selected_member else ""
    members: dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        rel = name[len(prefix) + 1 :] if prefix and name.startswith(f"{prefix}/") else name
        path = _referenced_repo_path(rel, field="archive member")
        if path in members:
            raise ManifestReferenceError(f"duplicate archive member {path!r}")
        members[path] = zf.read(info)
    selected = selected_member[len(prefix) + 1 :] if prefix else selected_member
    return members, _referenced_repo_path(selected, field="manifest_path")


def _load_manifest_tree(
    members: dict[str, bytes],
    path: str,
    *,
    stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    if path in stack:
        chain = " -> ".join((*stack, path))
        raise ManifestReferenceError(f"manifest include cycle: {chain}")
    if len(stack) >= _MAX_INCLUDE_DEPTH:
        raise ManifestReferenceError(f"manifest include depth exceeds {_MAX_INCLUDE_DEPTH}")
    raw = _read_referenced_member(members, path, field="include", max_bytes=_MAX_MANIFEST_BYTES)
    try:
        current = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestReferenceError(f"invalid TOML in {path!r}: {exc}") from exc

    includes = current.pop("include", [])
    if isinstance(includes, str):
        includes = [includes]
    if not isinstance(includes, list) or any(not isinstance(item, str) for item in includes):
        raise ManifestReferenceError(f"top-level include in {path!r} must be a string array")

    merged: dict[str, Any] = {}
    base_dir = str(PurePosixPath(path).parent)
    if base_dir == ".":
        base_dir = ""
    for include in includes:
        include_path = _referenced_repo_path(include, base_dir=base_dir, field=f"include in {path}")
        merged = _deep_merge(merged, _load_manifest_tree(members, include_path, stack=(*stack, path)))
    return _deep_merge(merged, current)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge TOML tables; later includes/main values win."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _referenced_repo_path(value: str, *, base_dir: str = "", field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ManifestReferenceError(f"{field} must be a non-empty POSIX path")
    raw = PurePosixPath(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ManifestReferenceError(f"{field} path may not be absolute or contain '..': {value!r}")
    joined = PurePosixPath(base_dir) / raw if base_dir else raw
    normalized = joined.as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        raise ManifestReferenceError(f"{field} resolves to an empty path")
    return normalized


def _read_referenced_member(
    members: dict[str, bytes], path: str, *, field: str, max_bytes: int | None = None
) -> bytes:
    try:
        value = members[path]
    except KeyError as exc:
        raise ManifestReferenceError(f"{field} references missing file {path!r}") from exc
    if max_bytes is not None and len(value) > max_bytes:
        raise ManifestReferenceError(f"{field} file {path!r} exceeds {max_bytes} bytes")
    return value


def _collect_assets(
    config: dict[str, Any], members: dict[str, bytes], manifest_dir: str
) -> tuple[list[str], list[str]]:
    """Validate file/script/binary refs and return stable repo-relative lists."""
    sources: list[tuple[str, dict[str, Any]]] = []
    assets = config.get("assets") or {}
    if not isinstance(assets, dict):
        raise ManifestReferenceError("[assets] must be a TOML table")
    sources.append(("assets", assets))
    skills = config.get("skills") or {}
    if not isinstance(skills, dict):
        raise ManifestReferenceError("[skills] must be a TOML table")
    for skill_slug, raw_skill_cfg in skills.items():
        skill_cfg = raw_skill_cfg or {}
        if not isinstance(skill_cfg, dict):
            raise ManifestReferenceError(f"[skills.{skill_slug}] must be a TOML table")
        sources.append((f"skills.{skill_slug}", skill_cfg))

    all_paths: list[str] = []
    executables: list[str] = []
    for label, source in sources:
        for key in ("files", "scripts", "binaries"):
            refs = source.get(key, [])
            if isinstance(refs, str):
                refs = [refs]
            if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
                raise ManifestReferenceError(f"{label}.{key} must be a string array")
            for item in refs:
                path = _referenced_repo_path(item, base_dir=manifest_dir, field=f"{label}.{key}")
                _read_referenced_member(members, path, field=f"{label}.{key}")
                if path not in all_paths:
                    all_paths.append(path)
                if key in {"scripts", "binaries"} and path not in executables:
                    executables.append(path)
    return all_paths, executables


def _store_bundle(*, organization, content_hash: str, zip_bytes: bytes) -> str:
    """Upload the zipball to the install's blob store; return the storage key.

    Best-effort: if no blob store is configured for the install (dev/CI without
    ``PIPELINE_ARTIFACT_LOCAL_PATH``, no provider plugin), the upload is skipped
    and an empty key is returned so Brief assembly is never blocked on storage.
    """
    storage_key = f"{organization.slug}/payloads/{content_hash}/bundle.zip"
    try:
        from astrolift_pipelines.artifact_store import _get_blob_driver

        driver = _get_blob_driver(organization)
        driver.upload(storage_key, zip_bytes, content_type="application/zip")
    except Exception:
        log.warning(
            "blob store unavailable; Brief for org %s assembled without stored bundle (content_hash=%s)",
            organization.id,
            content_hash,
            exc_info=True,
        )
        return ""
    return storage_key
