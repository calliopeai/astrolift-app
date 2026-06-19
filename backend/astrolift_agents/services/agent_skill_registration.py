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

import hashlib
import json
import logging

from django.utils import timezone
from django.utils.text import slugify

from astrolift_agents.models import AgentSkillRef, Brief, BriefSkillRef, Skill
from astrolift_agents.services.skill_resolver import (
    SkillResolutionError,
    resolve_skill,
)
from astrolift_manifest.brief import load_brief
from astrolift_manifest.parser import ManifestError
from astrolift_manifest.types import LoadedSkill, RawManifest, SkillRef

log = logging.getLogger(__name__)


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
    ordered_skills: list[Skill],
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
        "skills": [[s.slug, s.skill_version] for s in ordered_skills],
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
    manifest_path: str,
    readme_text: str,
    referenced_paths: tuple[str, ...],
    ordered_skills: list[Skill],
) -> Brief:
    """Create (or reuse) the content-addressed Brief for this agent.

    Reuses an existing Brief with the same ``content_hash`` (an unchanged
    re-scan), else creates a READY Brief. The brief's context records the
    README + in-folder reference paths so the read path (#936) can surface
    them; secrets/manifest-override resolution is a dispatch concern (#930) and
    intentionally not done here.
    """
    content_hash = _brief_content_hash(
        organization=organization,
        manifest_path=manifest_path,
        readme_text=readme_text,
        referenced_paths=referenced_paths,
        ordered_skills=ordered_skills,
    )

    brief = Brief.objects.filter(content_hash=content_hash).first()
    if brief is None:
        brief = Brief.objects.create(
            organization=organization,
            registered_app=registered_app,
            content_hash=content_hash,
            status=Brief.Status.READY,
            context={
                "source": "agent_manifest",
                "manifest_path": manifest_path,
                "brief_readme": readme_text,
                "brief_referenced_paths": list(referenced_paths),
            },
            assembled_at=timezone.now(),
        )
    _link_brief_skill_refs(brief=brief, ordered_skills=ordered_skills)
    return brief


def resolve_and_store_agent_skills(
    *,
    workload,
    manifest: RawManifest,
    manifest_path: str,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
) -> list[str]:
    """Resolve + persist the manifest's brief + skills for ``workload``.

    Idempotent: re-resolves, upserts Skills, reconciles AgentSkillRefs (adding
    new, repositioning, removing dropped), and assembles/links the Brief +
    sets ``Workload.brief``. Returns a list of human-readable notes for any
    **non-fatal** resolution failures (a missing local skill, a name not in the
    catalogue, an unavailable catalogue) — an empty list means everything
    resolved cleanly. Never raises on a resolution failure; the caller records
    the notes and the agent stays registered.

    ``catalogue_tree`` is the already-fetched built-in catalogue map (threaded
    in once per registration pass). ``None`` means the catalogue fetch failed
    upstream — every *named* skill then records a note and is skipped, while
    *local* skills still resolve.
    """
    notes: list[str] = []
    organization = workload.registered_app.organization

    resolved: list[Skill] = []
    for ref in manifest.skills:
        skill = _resolve_one_skill(
            ref=ref,
            organization=organization,
            agent_repo_tree=agent_repo_tree,
            catalogue_tree=catalogue_tree,
            notes=notes,
        )
        if skill is not None:
            resolved.append(skill)

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
            agent_repo_tree=agent_repo_tree,
            ordered_skills=resolved,
            notes=notes,
        )

    return notes


def _resolve_one_skill(
    *,
    ref: SkillRef,
    organization,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
    notes: list[str],
) -> Skill | None:
    """Resolve + upsert one skill ref; append a note + return None on failure."""
    if not ref.is_local and catalogue_tree is None:
        # Catalogue unavailable upstream — every named skill is skipped with a
        # note (the catalogue-fetch error itself was already logged once).
        notes.append(f"skill {ref.name!r}: built-in catalogue unavailable; skill not attached")
        return None
    try:
        loaded = resolve_skill(
            ref,
            agent_repo_tree=agent_repo_tree,
            catalogue_tree=catalogue_tree or {},
        )
    except (SkillResolutionError, ManifestError) as exc:
        # SkillResolutionError + the loader's SkillError (a ManifestError) both
        # carry a pathed, operator-readable message.
        notes.append(f"skill {ref.name!r}: {exc}")
        log.warning("agent skill %r failed to resolve for org %s: %s", ref.name, organization.id, exc)
        return None
    return _upsert_skill(organization=organization, loaded=loaded)


def _resolve_brief(
    *,
    workload,
    organization,
    registered_app,
    brief_path: str,
    manifest_path: str,
    agent_repo_tree: dict[str, str],
    ordered_skills: list[Skill],
    notes: list[str],
) -> None:
    """Load + assemble the brief, set ``Workload.brief``; note + skip on failure."""
    try:
        loaded_brief = load_brief(agent_repo_tree, readme_path=brief_path)
    except (SkillResolutionError, ManifestError) as exc:
        # load_brief raises BriefError (a ManifestError) when the README is
        # missing/non-text — non-fatal, recorded.
        notes.append(f"brief {brief_path!r}: {exc}")
        log.warning("agent brief %r failed to load for org %s: %s", brief_path, organization.id, exc)
        return

    brief = _assemble_brief(
        organization=organization,
        registered_app=registered_app,
        manifest_path=manifest_path,
        readme_text=loaded_brief.readme_text,
        referenced_paths=loaded_brief.referenced_paths,
        ordered_skills=ordered_skills,
    )
    if workload.brief_id != brief.id:
        workload.brief = brief
        workload.save(update_fields=["brief", "updated_at", "version"])


__all__ = ["resolve_and_store_agent_skills"]
