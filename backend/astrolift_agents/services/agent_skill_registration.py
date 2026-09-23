"""
Resolve + persist an agent manifest's brief + skills at registration
(spec 38 Phase 3 + spec 39c).

``resolve_and_store_agent_skills`` is called from
:func:`astrolift_registry.services.manifest_sync._register_one_agent` after the
agent ``Workload`` is created/updated. Given the parsed manifest's
``brief`` / ``skills`` pointers, the agent's already-fetched repo tree, and the
built-in catalogue tree, it:

1. **Resolves** each :class:`~astrolift_manifest.types.SkillRef` to a
   :class:`~astrolift_manifest.types.LoadedSkill` (local from the agent repo,
   named from the catalogue) via :mod:`astrolift_agents.services.skill_resolver`.
2. **Upserts a** :class:`~astrolift_agents.models.skill.Skill` per resolved
   skill in the agent's **org** (matched on ``(organization, slug)``), bumping
   ``skill_version`` only when its content actually changed (mirrors
   ``skill_importer._upsert_skill``).
3. **Reconciles** :class:`~astrolift_agents.models.skill.AgentSkillRef` rows so
   the workload's skill set + ``position`` ordering matches the manifest —
   creating new refs, updating positions, and **removing** refs for skills no
   longer in the manifest (idempotent re-scan).
4. **Assembles/upserts a** :class:`~astrolift_agents.models.brief.Brief` from
   the brief folder (when ``brief`` is set) — content-addressed over the org +
   README + referenced paths + resolved skill set, links the resolved skills
   via :class:`~astrolift_agents.models.skill.BriefSkillRef`, and sets
   ``Workload.brief``.

Non-fatal resolution (deliberate)
---------------------------------
Skill/brief resolution failures — a missing local path, a name absent from the
catalogue, a catalogue fetch outage — **must not abort agent registration**.
The agent Workload still registers; each failure is collected as a
human-readable note (returned to the caller as the per-agent ``skill_notes``
list on the ``RegisteredAgent`` result, and logged as a warning) so it
surfaces without taking down the whole ``register_agent_repo`` pass. Rationale:
a transient catalogue outage or one fat-fingered skill path in a monorepo of
many agents should degrade that agent's tooling, not block every agent's
onboarding. The operator sees the note (the GraphQL ``registerAgentRepo``
payload exposes ``skillNotes``) and re-scans once the repo/catalogue is fixed.

Why not reuse ``brief_assembler.assemble_agent_brief``
------------------------------------------------------
That service content-addresses over a **zipball digest** and re-fetches the
whole config repo over the network — it's the dispatch-time "snapshot this
agent's whole repo" path. Here the repo tree is already in hand (fetched once
for the whole registration pass) and the Brief is assembled from the brief
*folder's* README + the resolved skill set per spec 38 §Formats, so we build
the ``Brief`` directly (content-addressed over those inputs) rather than paying
a second network fetch and hashing the wrong thing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from pathlib import PurePosixPath

from django.utils import timezone
from django.utils.text import slugify

from astrolift_agents.models import AgentSkillRef, Brief, BriefSkillRef, Skill
from astrolift_agents.services.agent_package import AgentPackageError
from astrolift_agents.services.skill_resolver import (
    SkillResolutionError,
    resolve_skill,
)
from astrolift_manifest.brief import load_brief
from astrolift_manifest.parser import ManifestError
from astrolift_manifest.types import LoadedSkill, RawManifest, SkillRef

log = logging.getLogger(__name__)


def _tool_package(tool) -> dict:
    return {
        "slug": tool.slug,
        "name": tool.name,
        "description": tool.description,
        "adapter": tool.adapter,
        "handler_ref": tool.handler_ref,
        "implementation_config": tool.implementation_config or {},
        "commands": list(tool.commands or []),
        "required_packages": list(tool.required_packages or []),
        "input_schema": tool.input_schema or {},
        "output_schema": tool.output_schema or {},
    }


def _resolve_explicit_tools(*, organization, manifest: RawManifest, notes: list[str]) -> list:
    """Resolve ``tool_refs`` against the org/global registry by stable slug."""
    from django.db.models import Q

    from astrolift_agents.models import ToolDef

    raw_refs = manifest.raw.get("tool_refs") or []
    if isinstance(raw_refs, str):
        raw_refs = [raw_refs]
    if not isinstance(raw_refs, list) or any(
        not isinstance(value, str) or not value.strip() for value in raw_refs
    ):
        notes.append("tool_refs must be a string array; no explicit tools attached")
        return []

    resolved: list = []
    seen: set[str] = set()
    for raw_slug in raw_refs:
        slug = raw_slug.strip()
        if slug in seen:
            continue
        seen.add(slug)
        candidates = list(
            ToolDef.objects.select_related("skill")
            .filter(
                slug=slug,
                deleted_at__isnull=True,
                skill__deleted_at__isnull=True,
                skill__is_active=True,
            )
            .filter(Q(skill__organization=organization) | Q(skill__organization__isnull=True))
            .order_by("pk")
        )
        tool = next((item for item in candidates if item.skill.organization_id == organization.id), None)
        tool = tool or next((item for item in candidates if item.skill.organization_id is None), None)
        if tool is None:
            notes.append(f"tool {slug!r}: not found in the org/global registry; tool not attached")
            continue
        resolved.append(tool)
    return resolved


def _skill_slug(name: str) -> str:
    """Slug for a Skill from its agentskills.io name.

    ``Skill`` extends ``core.models.base.BaseCoreModel`` (not the
    auto-slugifying ``NamedBaseCoreModel``), so the slug must be set
    explicitly. Falls back to a hash-derived token only if slugify produces an
    empty string (a name of all-punctuation), so the ``(organization, slug)``
    unique key always has a non-empty slug.
    """
    slug = slugify(name)[:128]
    if slug:
        return slug
    return f"skill-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}"


def _upsert_skill(*, organization, loaded: LoadedSkill) -> Skill:
    """Create or update an org-scoped Skill from a resolved ``LoadedSkill``.

    Matched on ``(organization, slug)``; ``skill_version`` bumps only when the
    instruction body (``content``) actually changes, so an unchanged re-scan
    is a no-op for the version counter. Mirrors
    ``skill_importer._upsert_skill`` but reads a ``LoadedSkill`` (a SKILL.md)
    rather than a TOML table.
    """
    slug = _skill_slug(loaded.name)
    content = loaded.instructions
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    skill = Skill.objects.filter(organization=organization, slug=slug).first()
    if skill is None:
        skill = Skill(organization=organization, slug=slug, is_global=False)
        skill.skill_version = 1
    elif skill.content_hash != content_hash:
        skill.skill_version = (skill.skill_version or 0) + 1

    skill.name = loaded.name
    skill.description = loaded.description
    skill.content = content
    skill.content_hash = content_hash
    skill.is_active = True
    skill.save()
    return skill


def _reconcile_agent_skill_refs(*, workload, ordered_skills: list[Skill]) -> None:
    """Make the workload's AgentSkillRef set == ``ordered_skills`` (by position).

    Creates refs for newly-resolved skills, updates ``position`` to the
    manifest order, and soft-deletes refs for skills no longer referenced so a
    re-scan that dropped a skill reconciles cleanly (idempotent — no
    duplicates). ``AgentSkillRef.skill`` is ``on_delete=PROTECT``; we never
    touch the ``Skill`` rows here, only the join.
    """
    wanted_ids = {s.id for s in ordered_skills}

    existing = {
        ref.skill_id: ref for ref in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    }

    for position, skill in enumerate(ordered_skills):
        ref = existing.get(skill.id)
        if ref is None:
            AgentSkillRef.objects.create(workload=workload, skill=skill, position=position)
        elif ref.position != position:
            ref.position = position
            ref.save(update_fields=["position", "updated_at", "version"])

    # Drop refs for skills no longer in the manifest (soft delete — never
    # hard-delete onboarding data).
    for skill_id, ref in existing.items():
        if skill_id not in wanted_ids:
            ref.soft_delete()


def _brief_content_hash(
    *,
    organization,
    manifest_path: str,
    readme_text: str,
    referenced_paths: tuple[str, ...],
    referenced_files: dict[str, str],
    ordered_skills: list[Skill],
    manifest_raw: dict,
    federation: dict | None = None,
    payload_sha256: str = "",
) -> str:
    """SHA-256 of the canonical payload identifying this agent Brief.

    Binds the org identity (so the globally-``unique`` ``content_hash`` is
    org-correct, same posture as ``brief_assembler._content_hash``), the brief
    folder's README + the in-folder references it surfaced, the manifest path
    (distinct Briefs per agent in a monorepo), and the resolved skill set as
    ``(slug, version)`` pairs. Folding skill versions in means: an unchanged
    re-scan reuses the same Brief, while a changed skill (version bumped) or a
    changed README produces a new immutable Brief — the spec's content-
    addressed, never-mutated Brief semantics.
    """
    payload = {
        "org": str(organization.guid),
        "manifest_path": manifest_path,
        "readme": readme_text,
        "referenced_paths": list(referenced_paths),
        "referenced_files": dict(sorted(referenced_files.items())),
        "skills": [[s.slug, s.skill_version] for s in ordered_skills],
        # Environment, secret bindings, source-slice policy, execution policy,
        # and imports are package inputs too. A change to any of them must
        # produce a new immutable Brief even if prose is unchanged.
        "manifest": manifest_raw,
        "federation": federation or {},
        "payload_sha256": payload_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _link_brief_skill_refs(*, brief: Brief, ordered_skills: list[Skill]) -> None:
    """Idempotently link the resolved skills onto ``brief`` via BriefSkillRef.

    Snapshots each skill's current ``skill_version`` (the Brief is immutable;
    the snapshot keeps it stable even if the Skill is bumped later). Idempotent
    on ``(brief, skill)`` so re-linking an existing/reused Brief doesn't
    duplicate refs; refreshes the version snapshot when the skill changed.
    """
    existing = {ref.skill_id: ref for ref in BriefSkillRef.objects.filter(brief=brief)}
    for skill in ordered_skills:
        ref = existing.get(skill.id)
        if ref is None:
            BriefSkillRef.objects.create(brief=brief, skill=skill, skill_version=skill.skill_version)
        elif ref.skill_version != skill.skill_version:
            ref.skill_version = skill.skill_version
            ref.save(update_fields=["skill_version", "updated_at", "version"])


def _assemble_brief(
    *,
    organization,
    registered_app,
    workload,
    manifest_path: str,
    readme_text: str,
    referenced_paths: tuple[str, ...],
    referenced_files: dict[str, str],
    ordered_skills: list[Skill],
    manifest: RawManifest,
    source_archive: bytes | None = None,
    explicit_tools: list | None = None,
    federation: dict | None = None,
    notes: list[str] | None = None,
) -> Brief:
    """Create (or reuse) the content-addressed Brief for this agent.

    Reuses an existing Brief with the same ``content_hash`` (an unchanged
    re-scan), else creates a READY Brief. The brief's context records the
    README + in-folder reference paths so the read path (#936) can surface
    them; secrets/manifest-override resolution is a dispatch concern (#930) and
    intentionally not done here.
    """
    skill_packages: list[dict] = []
    tool_packages: list[dict] = []
    seen_tools: set[str] = set()
    for skill in ordered_skills:
        skill_packages.append(
            {
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
                "version": skill.skill_version,
                "content_hash": skill.content_hash,
                "instructions": skill.content,
            }
        )
        for tool in skill.tool_defs.filter(deleted_at__isnull=True).order_by("slug"):
            if tool.slug not in seen_tools:
                tool_packages.append(_tool_package(tool))
                seen_tools.add(tool.slug)
    for tool in explicit_tools or []:
        if tool.slug not in seen_tools:
            tool_packages.append(_tool_package(tool))
            seen_tools.add(tool.slug)

    from astrolift_agents.services.agent_package import (
        build_agent_package,
        compatibility_snapshot,
        manifest_needs_payload,
        project_manifest_environment,
    )

    environment, secret_refs = project_manifest_environment(
        manifest.raw.get("environment"),
        manifest.raw.get("secrets"),
    )

    primary = workload.containers.filter(is_primary=True).first()
    package = build_agent_package(
        agent_name=manifest.name,
        manifest_path=manifest_path,
        package_config=manifest.raw.get("package") or {},
        brief_text=readme_text,
        context_files=referenced_files,
        skills=skill_packages,
        tools=tool_packages,
        environment=environment,
        secret_refs=secret_refs,
        runtime={"image": getattr(primary, "image_ref", "") or ""},
        execution={
            "run_family": getattr(workload, "run_family", "task"),
            "timeout_seconds": getattr(workload, "tool_timeout_seconds", 300),
        },
        imports=manifest.raw.get("imports") or [],
        federation=federation or manifest.raw.get("federation") or {},
        payload_required=manifest_needs_payload(manifest.raw),
    )

    payload_bytes: bytes | None = None
    payload_metadata: dict = {}
    if (package.get("delivery") or {}).get("payload_required") and source_archive is not None:
        from astrolift_agents.services.agent_payload import build_agent_payload

        payload_bytes, payload_metadata = build_agent_payload(source_archive, package["source"])

    payload_sha256 = str(payload_metadata.get("sha256") or "")
    content_hash = _brief_content_hash(
        organization=organization,
        manifest_path=manifest_path,
        readme_text=readme_text,
        referenced_paths=referenced_paths,
        referenced_files=referenced_files,
        ordered_skills=ordered_skills,
        manifest_raw=manifest.raw,
        federation=federation,
        payload_sha256=payload_sha256,
    )
    snapshot = compatibility_snapshot(package)
    if payload_sha256:
        snapshot.update(
            {
                "payload_sha256": payload_sha256,
                "payload_file_count": int(payload_metadata.get("file_count") or 0),
                "manifest_path": str(payload_metadata.get("runtime_manifest_path") or ""),
            }
        )
    brief = Brief.objects.filter(content_hash=content_hash).first()
    storage_key = (brief.storage_key if brief is not None else "") or ""
    if payload_bytes is not None and not storage_key:
        from astrolift_agents.services.brief_assembler import _store_bundle

        storage_key = _store_bundle(
            organization=organization,
            content_hash=content_hash,
            zip_bytes=payload_bytes,
        )

    snapshot["payload_storage_ready"] = bool(storage_key) if payload_bytes is not None else True
    if payload_bytes is not None and not storage_key and notes is not None:
        notes.append(
            "agent package was assembled but its immutable payload could not be stored; "
            "dispatch is blocked until blob storage is configured and the repo is re-synced"
        )
    if brief is None:
        brief = Brief.objects.create(
            organization=organization,
            registered_app=registered_app,
            content_hash=content_hash,
            storage_key=storage_key,
            status=Brief.Status.READY,
            manifest_snapshot=snapshot,
            secrets_refs=secret_refs,
            # Definition-level package snapshots remain valid until a source
            # re-scan creates a new content-addressed Brief.
            ttl_seconds=0,
            context={
                "source": "agent_manifest",
                "manifest_path": manifest_path,
                "brief_readme": readme_text,
                "brief_referenced_paths": list(referenced_paths),
            },
            assembled_at=timezone.now(),
        )
    elif storage_key and not brief.storage_key:
        # Repair a content-identical Brief that was first assembled while the
        # blob store was unavailable. The immutable bytes/hash are unchanged;
        # only the delivery pointer and readiness diagnostic are recovered.
        brief.storage_key = storage_key
        brief.manifest_snapshot = snapshot
        brief.save(update_fields=["storage_key", "manifest_snapshot", "updated_at", "version"])
    _link_brief_skill_refs(brief=brief, ordered_skills=ordered_skills)
    return brief


def resolve_and_store_agent_skills(
    *,
    workload,
    manifest: RawManifest,
    manifest_path: str,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
    org_repo_tree_cache: dict[str, dict[str, str] | None] | None = None,
    source_archive: bytes | None = None,
    federation: dict | None = None,
) -> list[str]:
    """Resolve + persist the manifest's brief + skills for ``workload``.

    Idempotent: re-resolves, upserts Skills, reconciles AgentSkillRefs (adding
    new, repositioning, removing dropped), and assembles/links the Brief +
    sets ``Workload.brief``. Returns a list of human-readable notes for any
    **non-fatal** resolution failures (a missing local skill, a name not in the
    catalogue, an unavailable catalogue, an unknown org-repo alias, an org-repo
    fetch failure) — an empty list means everything resolved cleanly. Never
    raises on a resolution failure; the caller records the notes and the agent
    stays registered.

    ``catalogue_tree`` is the already-fetched built-in catalogue map (threaded
    in once per registration pass). ``None`` means the catalogue fetch failed
    upstream — every *catalogue* skill then records a note and is skipped,
    while *local* + *org-repo* skills still resolve.

    ``org_repo_tree_cache`` is a per-pass cache of fetched org-repo trees keyed
    by ``"<alias>@<ref>"`` so a repo referenced by many agents (or many skills)
    is fetched once. A cached value of ``None`` records a prior fetch failure
    (don't re-try the same alias@ref this pass). Defaults to a fresh dict when
    omitted (a single-agent caller).
    """
    notes: list[str] = []
    organization = workload.registered_app.organization
    if org_repo_tree_cache is None:
        org_repo_tree_cache = {}

    resolved: list[Skill] = []
    for ref in manifest.skills:
        skill = _resolve_one_skill(
            ref=ref,
            manifest=manifest,
            manifest_path=manifest_path,
            organization=organization,
            agent_repo_tree=agent_repo_tree,
            catalogue_tree=catalogue_tree,
            org_repo_tree_cache=org_repo_tree_cache,
            notes=notes,
        )
        if skill is not None:
            resolved.append(skill)
    explicit_tools = _resolve_explicit_tools(
        organization=organization,
        manifest=manifest,
        notes=notes,
    )

    # Reconcile the workload's skill set to exactly what resolved this pass
    # (drops refs for skills that no longer resolve / were removed).
    _reconcile_agent_skill_refs(workload=workload, ordered_skills=resolved)

    # Brief (optional). A brief that fails to load is non-fatal too; the agent
    # keeps its skills and registration.
    if manifest.brief is not None:
        _resolve_brief(
            workload=workload,
            organization=organization,
            registered_app=workload.registered_app,
            brief_path=manifest.brief.path,
            manifest_path=manifest_path,
            manifest=manifest,
            agent_repo_tree=agent_repo_tree,
            ordered_skills=resolved,
            notes=notes,
            source_archive=source_archive,
            explicit_tools=explicit_tools,
            federation=federation,
        )
    else:
        # A package may be driven entirely by skills or by a self-contained
        # image. It still needs a content-addressed runtime packet even when
        # the author did not add a separate brief README.
        brief = _assemble_brief(
            workload=workload,
            organization=organization,
            registered_app=workload.registered_app,
            manifest_path=manifest_path,
            readme_text="",
            referenced_paths=(),
            referenced_files={},
            ordered_skills=resolved,
            manifest=manifest,
            source_archive=source_archive,
            explicit_tools=explicit_tools,
            federation=federation,
            notes=notes,
        )
        if workload.brief_id != brief.id:
            workload.brief = brief
            workload.save(update_fields=["brief", "updated_at", "version"])

    return notes


def _resolve_one_skill(
    *,
    ref: SkillRef,
    manifest: RawManifest,
    manifest_path: str,
    organization,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
    org_repo_tree_cache: dict[str, dict[str, str] | None],
    notes: list[str],
) -> Skill | None:
    """Resolve + upsert one skill ref; append a note + return None on failure."""
    # Org-repo (spec 39d) resolves through a per-org registered repo, not the
    # catalogue / agent repo — handled separately (DB lookup + own fetch).
    if ref.kind == "org_repo":
        loaded = _resolve_org_repo_skill(
            ref=ref,
            organization=organization,
            org_repo_tree_cache=org_repo_tree_cache,
            notes=notes,
        )
        return _upsert_skill(organization=organization, loaded=loaded) if loaded is not None else None

    if ref.kind == "catalogue" and catalogue_tree is None:
        # Catalogue unavailable upstream — every catalogue skill is skipped
        # with a note (the catalogue-fetch error itself was already logged
        # once).
        notes.append(f"skill {ref.name!r}: built-in catalogue unavailable; skill not attached")
        return None
    if ref.kind == "local" and ref.path:
        try:
            resolved_path = _resolve_local_repo_path(
                ref.path,
                manifest_path=manifest_path,
                agent_repo_tree=agent_repo_tree,
                marker="SKILL.md",
                strict="package" in manifest.raw,
                package_config=manifest.raw.get("package"),
            )
        except ManifestError as exc:
            if "package" in manifest.raw:
                raise AgentPackageError(f"skill {ref.name!r}: {exc}") from exc
            notes.append(f"skill {ref.name!r}: {exc}")
            return None
        ref = dataclasses.replace(ref, path=resolved_path)

    try:
        loaded = resolve_skill(
            ref,
            agent_repo_tree=agent_repo_tree,
            catalogue_tree=catalogue_tree or {},
        )
    except (SkillResolutionError, ManifestError) as exc:
        # SkillResolutionError + the loader's SkillError (a ManifestError) both
        # carry a pathed, operator-readable message.
        if ref.kind == "local" and "package" in manifest.raw:
            raise AgentPackageError(f"skill {ref.name!r}: {exc}") from exc
        notes.append(f"skill {ref.name!r}: {exc}")
        log.warning("agent skill %r failed to resolve for org %s: %s", ref.name, organization.id, exc)
        return None
    return _upsert_skill(organization=organization, loaded=loaded)


def _resolve_org_repo_skill(
    *,
    ref: SkillRef,
    organization,
    org_repo_tree_cache: dict[str, dict[str, str] | None],
    notes: list[str],
) -> LoadedSkill | None:
    """Resolve one org-repo skill ref against the org's registered repos.

    Looks the ref's ``repo_alias`` up among the org's active
    :class:`~astrolift_agents.models.org_skill_repo.OrgSkillRepo` rows, fetches
    that repo's tree at the effective ref (``ref.ref`` else the repo's
    ``default_ref``) — caching the fetched tree per ``"<alias>@<ref>"`` so a
    repo referenced by many skills/agents is fetched once — and locates the
    skill folder via :func:`resolve_org_repo_skill`. Every failure (unknown
    alias, fetch error, skill-not-in-repo) is **non-fatal**: a note is
    appended and ``None`` returned so the agent still registers.
    """
    from astrolift_agents.models import OrgSkillRepo
    from astrolift_agents.services.skill_resolver import (
        fetch_org_repo_tree,
        resolve_org_repo_skill,
    )

    repo = OrgSkillRepo.objects.filter(
        organization=organization,
        alias=ref.repo_alias,
        is_active=True,
        deleted_at__isnull=True,
    ).first()
    if repo is None:
        notes.append(
            f"skill {ref.repo_alias}/{ref.skill_subpath!r}: no skill repo registered "
            f"for alias {ref.repo_alias!r} in this org; skill not attached"
        )
        return None

    eff_ref = (ref.ref or repo.default_ref or "main").strip()
    cache_key = f"{repo.alias}@{eff_ref}"
    if cache_key in org_repo_tree_cache:
        tree = org_repo_tree_cache[cache_key]
        if tree is None:
            # A prior fetch of this alias@ref already failed this pass — record
            # a per-skill note without re-fetching.
            notes.append(
                f"skill {ref.repo_alias}/{ref.skill_subpath!r}: skill repo "
                f"{repo.repo_full_name}@{eff_ref} could not be fetched; skill not attached"
            )
            return None
    else:
        try:
            tree = fetch_org_repo_tree(repo, ref=eff_ref)
        except Exception as exc:  # noqa: BLE001 — any fetch failure is one non-fatal note
            org_repo_tree_cache[cache_key] = None
            notes.append(
                f"skill {ref.repo_alias}/{ref.skill_subpath!r}: skill repo "
                f"{repo.repo_full_name}@{eff_ref} fetch failed: {exc or exc.__class__.__name__}; "
                "skill not attached"
            )
            log.warning(
                "org skill repo %r (alias %r) fetch failed for org %s: %s",
                repo.repo_full_name,
                repo.alias,
                organization.id,
                exc,
            )
            return None
        org_repo_tree_cache[cache_key] = tree

    try:
        return resolve_org_repo_skill(ref, repo_tree=tree)
    except (SkillResolutionError, ManifestError) as exc:
        notes.append(f"skill {ref.repo_alias}/{ref.skill_subpath!r}: {exc}")
        log.warning(
            "org-repo skill %r/%r failed to resolve for org %s: %s",
            ref.repo_alias,
            ref.skill_subpath,
            organization.id,
            exc,
        )
        return None


def _resolve_brief(
    *,
    workload,
    organization,
    registered_app,
    brief_path: str,
    manifest_path: str,
    manifest: RawManifest,
    agent_repo_tree: dict[str, str],
    ordered_skills: list[Skill],
    notes: list[str],
    source_archive: bytes | None = None,
    explicit_tools: list | None = None,
    federation: dict | None = None,
) -> None:
    """Load + assemble the brief, set ``Workload.brief``; note + skip on failure."""
    try:
        resolved_brief_path = _resolve_local_repo_path(
            brief_path,
            manifest_path=manifest_path,
            agent_repo_tree=agent_repo_tree,
            strict="package" in manifest.raw,
            package_config=manifest.raw.get("package"),
        )
        loaded_brief = load_brief(agent_repo_tree, readme_path=resolved_brief_path)
    except (SkillResolutionError, ManifestError) as exc:
        # load_brief raises BriefError (a ManifestError) when the README is
        # missing/non-text — non-fatal, recorded.
        if "package" in manifest.raw:
            raise AgentPackageError(f"brief {brief_path!r}: {exc}") from exc
        notes.append(f"brief {brief_path!r}: {exc}")
        log.warning("agent brief %r failed to load for org %s: %s", brief_path, organization.id, exc)
        return

    brief = _assemble_brief(
        workload=workload,
        organization=organization,
        registered_app=registered_app,
        manifest_path=manifest_path,
        readme_text=loaded_brief.readme_text,
        referenced_paths=loaded_brief.referenced_paths,
        referenced_files={
            path: agent_repo_tree[path]
            for path in loaded_brief.referenced_paths
            if isinstance(agent_repo_tree.get(path), str)
        },
        ordered_skills=ordered_skills,
        manifest=manifest,
        source_archive=source_archive,
        explicit_tools=explicit_tools,
        federation=federation,
        notes=notes,
    )
    if workload.brief_id != brief.id:
        workload.brief = brief
        workload.save(update_fields=["brief", "updated_at", "version"])


def _resolve_local_repo_path(
    value: str,
    *,
    manifest_path: str,
    agent_repo_tree: dict[str, str],
    marker: str = "",
    strict: bool,
    package_config: object = None,
) -> str:
    """Resolve a package-local path against the manifest's virtual root.

    New manifests that declare ``[package]`` are strict/chrooted: local paths
    are relative to the manifest directory and cannot fall back to repo-root
    lookups. Legacy manifests retain the old repo-relative fallback.
    """
    raw = str(value or "").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "\\" in raw:
        raise ManifestError(f"unsafe package-local path {value!r}", path=value)
    manifest_dir = PurePosixPath(manifest_path).parent
    if str(manifest_dir) == ".":
        manifest_dir = PurePosixPath("")
    package = package_config or {}
    relative_root = str(package.get("root") or ".") if isinstance(package, dict) else "."
    root_path = PurePosixPath(relative_root)
    if root_path.is_absolute() or ".." in root_path.parts or "\\" in relative_root:
        raise ManifestError(f"unsafe package.root {relative_root!r}", path=relative_root)
    package_root = manifest_dir if relative_root == "." else manifest_dir / root_path
    relative = path.as_posix().removeprefix("./")
    candidate = (package_root / relative).as_posix()

    def _exists(resolved: str) -> bool:
        check = f"{resolved.rstrip('/')}/{marker}" if marker else resolved
        return check in agent_repo_tree

    if _exists(candidate):
        return candidate
    if not strict and _exists(relative):
        return relative
    raise ManifestError(
        f"package-local path {value!r} not found under {(package_root.as_posix() or '.')!r}",
        path=value,
    )


__all__ = ["resolve_and_store_agent_skills"]
