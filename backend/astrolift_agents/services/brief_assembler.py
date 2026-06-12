"""
GitHub -> Brief assembly service (#41).

``assemble_agent_brief`` fetches an agent config repository from GitHub as a
zipball, parses the ``astrolift.toml`` it contains, and assembles an immutable
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
import tomllib
import zipfile
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from astrolift_agents.models import Brief

log = logging.getLogger(__name__)

# GitHub zipball endpoint. ``allow_redirects`` is required: the API replies with
# a 302 to a short-lived codeload.github.com URL that streams the archive.
_GITHUB_API = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 60

# Keys consumed structurally from the manifest's [environment] table; everything
# else under [environment] is treated as a literal env var.
_ENVIRONMENT_RESERVED_KEYS = frozenset({"tool_preset", "allow_install"})


def assemble_agent_brief(
    *,
    organization,
    config_repo: str,
    config_branch: str = "main",
    context: dict | None = None,
    ttl_seconds: int = 3600,
) -> Brief:
    """Fetch ``config_repo``, parse its ``astrolift.toml``, and assemble a Brief.

    Args:
        organization: the ``astrolift_identity.Organization`` the Brief belongs
            to. The Brief is scoped to this org and the org identity is folded
            into the content hash.
        config_repo: ``"owner/repo"`` of the GitHub repository holding the
            agent's ``astrolift.toml``.
        config_branch: the branch (or tag/ref) to fetch. Defaults to ``main``.
        context: optional task context metadata (task id, actor, slugs). Stored
            verbatim on the Brief and folded into the content hash.
        ttl_seconds: how long the READY Brief stays valid before the agent
            bootstrap layer treats it as expired. ``0`` means no expiry.

    Returns:
        A ``Brief`` in ``READY`` status. If an identical Brief (same org, same
        zipball digest, same context) already exists and is READY it is returned
        unchanged rather than re-assembled.

    Raises:
        requests.HTTPError: GitHub returned a non-2xx response (e.g. the repo or
            branch does not exist, or the configured PAT lacks access).
    """
    context = context or {}
    owner_repo = config_repo.strip("/")

    zip_bytes = _fetch_zipball(owner_repo, config_branch)
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()

    # Content-address over the canonical payload, not the raw zip bytes, so the
    # globally-unique content_hash is org-correct (see module docstring).
    content_hash = _content_hash(organization=organization, zip_digest=zip_digest, context=context)

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

    manifest, secrets_refs = _parse_manifest(zip_bytes)

    storage_key = _store_bundle(organization=organization, content_hash=content_hash, zip_bytes=zip_bytes)

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
    )
    resp.raise_for_status()
    return resp.content


def _content_hash(*, organization, zip_digest: str, context: dict) -> str:
    """SHA-256 of the canonical JSON payload identifying this Brief.

    The payload binds the org identity, the zipball digest, and the task
    context. ``sort_keys`` makes the encoding deterministic so two assemblies
    with the same inputs hash identically (the basis for dedup).
    """
    payload = {
        "org": str(organization.guid),
        "zip_sha256": zip_digest,
        "context": context,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_manifest(zip_bytes: bytes) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse ``astrolift.toml`` from the zipball.

    Returns ``(manifest_snapshot, secrets_refs)``. When the archive contains no
    ``astrolift.toml`` both are empty — the Brief is still assembled so the
    caller can decide how to treat a config-less repo.

    GitHub zipballs nest everything under a single
    ``<owner>-<repo>-<sha>/`` top-level directory, so the root manifest is the
    matching path with the fewest segments.
    """
    manifest: dict[str, Any] = {}
    secrets_refs: list[dict[str, str]] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        candidates = [n for n in zf.namelist() if n.endswith("astrolift.toml")]
        if not candidates:
            return manifest, secrets_refs

        # Fewest path separators == closest to the archive root.
        toml_path = min(candidates, key=lambda n: n.count("/"))
        with zf.open(toml_path) as fh:
            config = tomllib.load(fh)

    # [skills.*] -> manifest. The first skill table is the agent's primary
    # skill; its slug is the table key (e.g. [skills.reviewer]).
    skills = config.get("skills") or {}
    if skills:
        skill_slug = next(iter(skills))
        skill_cfg = skills[skill_slug] or {}
        manifest["skill_slug"] = skill_slug
        manifest["system_prompt"] = skill_cfg.get("system_prompt", "")
        manifest["tools"] = skill_cfg.get("tools", [])

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
