"""Import Skill + ToolDef records from a GitHub ``astrolift.toml`` (#42).

``import_skills_from_repo`` fetches an agent-config repository as a
GitHub zipball, parses every ``[skills.*]`` and ``[tools.*]`` table in
its root ``astrolift.toml``, and upserts org-scoped
:class:`~astrolift_agents.models.skill.Skill` and
:class:`~astrolift_agents.models.skill.ToolDef` rows for the calling
org.

This is distinct from ``brief_assembler.assemble_agent_brief``: that
service snapshots a single agent's manifest into an immutable Brief,
whereas this one materializes the *reusable catalog* (multiple skills +
their tool defs) so the operator can browse and re-use them across many
Briefs. Both share the same zipball-fetch + root-manifest-selection
boundary, reused here from ``brief_assembler``.

Idempotency
-----------
Re-importing the same repo updates the existing rows in place (matched
on ``(organization, slug)``) rather than creating duplicates. A Skill's
``content_hash`` + ``skill_version`` are bumped only when its content
actually changes, so an unchanged re-import is a no-op for the version
counter.
"""

from __future__ import annotations

import hashlib
import io
import logging
import tomllib
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from django.db import transaction

from astrolift_agents.models import Skill, ToolDef
from astrolift_agents.services.brief_assembler import _fetch_zipball, select_manifest_member

log = logging.getLogger(__name__)

# Adapter tokens we accept from the manifest, mapped to the model enum.
_VALID_ADAPTERS = frozenset(a.value for a in ToolDef.Adapter)
_DEFAULT_ADAPTER = ToolDef.Adapter.PYTHON_FN.value


class InvalidRepoURLError(ValueError):
    """Raised when ``repo_url`` is not a parseable github.com repo URL."""


@dataclass
class ImportResult:
    imported_skills: list[str] = field(default_factory=list)
    imported_tools: list[str] = field(default_factory=list)
    source_ref: str = ""


def parse_repo_url(repo_url: str) -> str:
    """Validate ``repo_url`` is a github.com URL and return ``owner/repo``.

    Accepts ``https://github.com/owner/repo`` (with or without a
    trailing ``.git`` or extra path segments) and the scheme-less
    ``github.com/owner/repo`` shorthand. Raises
    :class:`InvalidRepoURLError` otherwise — we deliberately do not
    accept arbitrary git hosts here because the fetch path is the GitHub
    zipball API.
    """
    raw = (repo_url or "").strip()
    if not raw:
        raise InvalidRepoURLError("repo_url is required")

    # urlparse needs a scheme to populate netloc; add one for the
    # scheme-less shorthand so "github.com/o/r" parses the same way.
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)

    host = (parsed.netloc or "").lower()
    # Strip a possible "www." and any ":port".
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[len("www.") :]
    if host != "github.com":
        raise InvalidRepoURLError(f"only github.com URLs are supported, got host {host!r}")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise InvalidRepoURLError("github.com URL must include owner/repo")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        raise InvalidRepoURLError("github.com URL must include owner/repo")
    return f"{owner}/{repo}"


def _load_root_manifest(zip_bytes: bytes, manifest_path: str = "") -> dict[str, Any]:
    """Parse the ``astrolift.toml`` library manifest out of a GitHub zipball.

    Without ``manifest_path`` the repo-root manifest is used. With it, the
    manifest at that repo-relative path is used (so a monorepo can keep its
    skills library somewhere other than the root). Returns ``{}`` when nothing
    matches so the caller reports zero imports rather than crashing.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        toml_path = select_manifest_member(zf.namelist(), manifest_path)
        if toml_path is None:
            return {}
        with zf.open(toml_path) as fh:
            return tomllib.load(fh)


def _as_str_list(value: Any) -> list[str]:
    """Coerce a manifest value into a list of strings, dropping junk."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def _skill_content(cfg: dict[str, Any]) -> str:
    """Resolve a skill table's instruction body.

    ``content`` is the canonical key; ``system_prompt`` is accepted as an
    alias so a manifest written for ``brief_assembler`` (which reads
    ``system_prompt``) imports cleanly into the catalog.
    """
    content = cfg.get("content")
    if isinstance(content, str) and content:
        return content
    system_prompt = cfg.get("system_prompt")
    if isinstance(system_prompt, str):
        return system_prompt
    return ""


def _upsert_skill(
    *, organization, slug: str, cfg: dict[str, Any], source_ref: str = "", created_by_id: int | None = None
) -> Skill:
    """Create or update an org-scoped Skill from one ``[skills.<slug>]`` table.

    ``source_ref`` is the ``owner/repo@branch`` pointer the import read, for
    the catalog's Imported view; ``created_by_id`` is the importer, set on a
    new row only so a re-import by someone else keeps the original author.
    """
    content = _skill_content(cfg)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    skill = Skill.objects.filter(organization=organization, slug=slug).first()
    if skill is None:
        skill = Skill(
            organization=organization,
            slug=slug,
            is_global=False,
            created_by_id=created_by_id,
        )
        skill.skill_version = 1
    else:
        # Bump the version only when the content actually changed so an
        # unchanged re-import doesn't churn the counter.
        if skill.content_hash != content_hash:
            skill.skill_version = (skill.skill_version or 0) + 1

    skill.name = str(cfg.get("name") or slug)
    skill.description = str(cfg.get("description") or "")
    skill.content = content
    skill.content_hash = content_hash
    skill.dependencies = _as_str_list(cfg.get("dependencies"))
    skill.agent_type = str(cfg.get("agent_type") or "")
    skill.scaffolding_tags = _as_str_list(cfg.get("scaffolding_tags"))
    skill.is_active = bool(cfg.get("is_active", True))
    skill.source_kind = Skill.SourceKind.REPO_IMPORT
    skill.source_ref = source_ref[:512]
    skill.save()
    return skill


def _resolve_adapter(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in _VALID_ADAPTERS else _DEFAULT_ADAPTER


def _upsert_tooldef(
    *, skill: Skill, slug: str, cfg: dict[str, Any], created_by_id: int | None = None
) -> ToolDef:
    """Create or update a ToolDef attached to ``skill`` from one ``[tools.<slug>]`` table."""
    tool = ToolDef.objects.filter(skill=skill, slug=slug).first()
    if tool is None:
        tool = ToolDef(skill=skill, slug=slug, created_by_id=created_by_id)

    tool.name = str(cfg.get("name") or slug)
    tool.description = str(cfg.get("description") or "")
    tool.adapter = _resolve_adapter(cfg.get("adapter"))
    tool.handler_ref = str(cfg.get("handler_ref") or "")
    tool.input_schema = cfg.get("input_schema") if isinstance(cfg.get("input_schema"), dict) else {}
    tool.output_schema = cfg.get("output_schema") if isinstance(cfg.get("output_schema"), dict) else {}
    tool.implementation_config = (
        cfg.get("implementation_config") if isinstance(cfg.get("implementation_config"), dict) else {}
    )
    tool.commands = _as_str_list(cfg.get("commands"))
    tool.required_packages = (
        cfg.get("required_packages") if isinstance(cfg.get("required_packages"), list) else []
    )
    tool.capability_group = str(cfg.get("capability_group") or "")
    tool.agent_type_bindings = _as_str_list(cfg.get("agent_type_bindings"))
    tool.is_builtin = bool(cfg.get("is_builtin", False))
    tool.save()
    return tool


def import_skills_from_repo(
    *,
    organization,
    repo_url: str,
    branch: str = "main",
    manifest_path: str = "",
    created_by_id: int | None = None,
) -> ImportResult:
    """Import every ``[skills.*]`` + ``[tools.*]`` table from ``repo_url``.

    Args:
        organization: the ``astrolift_identity.Organization`` the imported
            Skills/ToolDefs belong to. All rows are org-scoped (never
            global — global skills are seeded by the platform, not imported
            from a tenant's repo).
        repo_url: a ``https://github.com/owner/repo`` URL.
        branch: the branch/ref to fetch. Defaults to ``main``.

    Returns:
        An :class:`ImportResult` naming the created/updated skill + tool
        slugs and the canonical ``owner/repo@branch`` source ref.

    Raises:
        InvalidRepoURLError: ``repo_url`` is not a github.com repo URL.
        requests.HTTPError: GitHub returned a non-2xx response.
    """
    owner_repo = parse_repo_url(repo_url)
    source_ref = f"{owner_repo}@{branch}"
    if manifest_path:
        source_ref = f"{source_ref}:{manifest_path}"

    zip_bytes = _fetch_zipball(owner_repo, branch)
    manifest = _load_root_manifest(zip_bytes, manifest_path)

    result = ImportResult(source_ref=source_ref)
    skills_table = manifest.get("skills") or {}
    tools_table = manifest.get("tools") or {}

    # The whole import is one transaction: a malformed tool table must
    # not leave half-imported skills behind.
    with transaction.atomic():
        skills_by_slug: dict[str, Skill] = {}
        for skill_slug, skill_cfg in skills_table.items():
            if not isinstance(skill_cfg, dict):
                continue
            skill = _upsert_skill(
                organization=organization,
                slug=skill_slug,
                cfg=skill_cfg,
                source_ref=source_ref,
                created_by_id=created_by_id,
            )
            skills_by_slug[skill_slug] = skill
            result.imported_skills.append(skill.slug)

        for tool_slug, tool_cfg in tools_table.items():
            if not isinstance(tool_cfg, dict):
                continue
            target = _target_skill_for_tool(tool_cfg, skills_by_slug)
            if target is None:
                # A tool that references no importable skill is skipped
                # rather than failing the whole import — the manifest may
                # legitimately reference a skill defined elsewhere.
                log.warning(
                    "skipping tool %r from %s: no resolvable skill (skill=%r, available=%s)",
                    tool_slug,
                    source_ref,
                    tool_cfg.get("skill"),
                    sorted(skills_by_slug),
                )
                continue
            tool = _upsert_tooldef(skill=target, slug=tool_slug, cfg=tool_cfg, created_by_id=created_by_id)
            result.imported_tools.append(tool.slug)

    log.info(
        "imported %d skills + %d tools for org %s from %s",
        len(result.imported_skills),
        len(result.imported_tools),
        organization.id,
        source_ref,
    )
    return result


def _target_skill_for_tool(
    tool_cfg: dict[str, Any],
    skills_by_slug: dict[str, Skill],
) -> Skill | None:
    """Pick the Skill a ``[tools.<slug>]`` table attaches to.

    A tool names its owner skill via ``skill = "<skill-slug>"``. When the
    manifest defines exactly one skill the binding is optional — the tool
    attaches to that sole skill. Otherwise an unmatched/missing reference
    yields ``None`` so the caller can skip it.
    """
    ref = tool_cfg.get("skill")
    if isinstance(ref, str) and ref in skills_by_slug:
        return skills_by_slug[ref]
    if ref is None and len(skills_by_slug) == 1:
        return next(iter(skills_by_slug.values()))
    return None
