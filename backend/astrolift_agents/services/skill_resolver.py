"""
Skill resolver — turn a manifest ``SkillRef`` into a ``LoadedSkill`` (spec 38
Phase 3 + spec 39c).

A ``skills = [ … ]`` entry in an agent manifest resolves against one of two
sources (the v1 subset of spec 39's three-source model):

* **local** (``kind == "local"``) — ``{ name = "relative/path" }`` / a ``./``
  string → a folder in the agent's own repo (agentskills.io layout). Read from
  the repo tree already fetched for registration. Highest specificity;
  versioned with the agent.
* **catalogue** (``kind == "catalogue"``) — a bare string with no ``/``
  (``"pr-review"``) → resolved from the **built-in catalogue**
  (``calliopeai/astrolift-skills``), the baseline repo shipped with every
  deployment. Found at ``skills/<name>/`` in the catalogue tree.
* **org-repo** (``kind == "org_repo"``) — ``"<alias>/<skill-path>@<ref>"``
  (spec 39d) → resolved from one of the org's registered
  :class:`~astrolift_agents.models.org_skill_repo.OrgSkillRepo` rows.

:func:`resolve_skill` itself handles the local + catalogue forms (both resolve
from trees already threaded in). The org-repo form needs a per-org DB lookup +
its own repo fetch, so it is resolved by the registration flow
(:mod:`astrolift_agents.services.agent_skill_registration`) which looks the
alias up in the caller's org, fetches that repo's tree (anonymously when
public, via the linked ``SourceConnection`` when private) with
:func:`fetch_org_repo_tree`, and calls :func:`resolve_org_repo_skill` with the
fetched tree — keeping :func:`resolve_skill` free of DB / network concerns.

Catalogue fetch + caching
--------------------------
:func:`load_catalogue_tree` fetches the catalogue repo **once** as an
unauthenticated public zipball (no ``SourceConnection`` required — the
catalogue is a public repo every install can read) and returns the
``{path: text}`` map. The caller (registration) fetches it once per pass and
threads the same map into every :func:`resolve_skill` call, so the catalogue
is never re-fetched per skill. A network / HTTP failure is surfaced as a
:class:`CatalogueFetchError` (a subclass of :class:`SkillResolutionError`) so
the caller can record it and keep registering the agent (resolution is
non-fatal — see :mod:`astrolift_registry.services.manifest_sync`).
"""

from __future__ import annotations

import logging

from django.conf import settings

from astrolift_manifest.parser import ManifestError
from astrolift_manifest.skills import load_skill
from astrolift_manifest.types import LoadedSkill, SkillRef

log = logging.getLogger(__name__)

# Catalogue defaults (spec 39 §The built-in catalogue). Overridable via
# settings so a deployment can pin a snapshot / point at a fork without a code
# change. ``getattr`` with a default keeps the resolver working even when the
# settings module hasn't declared them (e.g. an older config).
_DEFAULT_CATALOGUE_REPO = "calliopeai/astrolift-skills"
_DEFAULT_CATALOGUE_REF = "main"

# Catalogue skills live under ``skills/<name>/`` (spec 39 §The built-in
# catalogue layout). A named ref resolves to that folder.
_CATALOGUE_SKILLS_DIR = "skills"


class SkillResolutionError(ManifestError):
    """A skill reference could not be resolved to a loadable skill folder.

    Subclasses :class:`ManifestError` so callers already catching
    ``ManifestError`` for manifest/skill/brief parsing catch resolution
    failures too. ``path`` carries the offending reference (the skill name or
    the local path) for a useful operator message.
    """


class CatalogueFetchError(SkillResolutionError):
    """The built-in catalogue repo could not be fetched.

    Distinct subclass so registration can tell "this one named skill isn't in
    the catalogue" (every named ref still records its own
    :class:`SkillResolutionError`) apart from "the catalogue itself is
    unreachable" — both are non-fatal to agent registration, but the latter
    affects every named skill at once and is logged once per pass.
    """


def catalogue_repo() -> str:
    """The catalogue ``owner/repo`` to fetch (settings-overridable)."""
    return getattr(settings, "ASTROLIFT_SKILLS_CATALOGUE_REPO", "") or _DEFAULT_CATALOGUE_REPO


def catalogue_ref() -> str:
    """The catalogue ref/branch/tag to fetch (settings-overridable)."""
    return getattr(settings, "ASTROLIFT_SKILLS_CATALOGUE_REF", "") or _DEFAULT_CATALOGUE_REF


def load_catalogue_tree() -> dict[str, str]:
    """Fetch the built-in catalogue repo once and return its ``{path: text}`` map.

    Fetches ``ASTROLIFT_SKILLS_CATALOGUE_REPO`` @ ``ASTROLIFT_SKILLS_CATALOGUE_REF``
    (defaults ``calliopeai/astrolift-skills`` @ ``main``) as an unauthenticated
    public zipball — no ``SourceConnection`` needed. Intended to be called
    **once per registration pass**; the result is threaded into every
    :func:`resolve_skill` call so the catalogue is not re-fetched per skill.

    Raises :class:`CatalogueFetchError` on any fetch failure (HTTP non-2xx,
    network error, malformed archive). The caller treats this as non-fatal:
    named-skill resolution is skipped + recorded, the agent still registers.
    """
    from astrolift_scm.providers.repo_tree import fetch_public_repo_tree

    repo = catalogue_repo()
    ref = catalogue_ref()
    try:
        tree = fetch_public_repo_tree(repo_full_name=repo, ref=ref)
    except Exception as exc:  # noqa: BLE001 — any fetch failure is one error class
        log.warning(
            "skills catalogue fetch failed (%s@%s): %s",
            repo,
            ref,
            exc,
            exc_info=True,
        )
        raise CatalogueFetchError(
            f"could not fetch the skills catalogue {repo}@{ref}: {exc}",
            path=repo,
        ) from exc
    log.info("fetched skills catalogue %s@%s (%d files)", repo, ref, len(tree))
    return tree


def resolve_skill(
    ref: SkillRef,
    *,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str],
) -> LoadedSkill:
    """Resolve one local / catalogue :class:`SkillRef` to a :class:`LoadedSkill`.

    * **local** (``kind == "local"`` / ``ref.path`` set) — :func:`load_skill`
      from ``agent_repo_tree`` at ``ref.path`` (the agentskills.io folder in
      the agent's own repo, repo-relative against the fetched tree).
    * **catalogue** (``kind == "catalogue"``, a bare name) — locate
      ``skills/<ref.name>/`` in ``catalogue_tree`` and :func:`load_skill`.

    The **org-repo** form (``kind == "org_repo"``) is NOT resolved here — it
    needs a per-org DB lookup + its own repo fetch, so the registration flow
    resolves it via :func:`resolve_org_repo_skill`. Passing an org-repo ref
    here raises :class:`SkillResolutionError` (a caller bug, surfaced loudly
    rather than silently mis-resolved against the catalogue).

    Raises :class:`SkillResolutionError` when the reference can't be resolved
    (a local path with no ``SKILL.md``, a name absent from the catalogue, or a
    malformed skill folder). ``SkillError`` raised by :func:`load_skill` is a
    ``ManifestError`` subclass and propagates as-is — both surface a clear,
    pathed message the caller records.
    """
    if ref.kind == "org_repo":
        raise SkillResolutionError(
            f"org-repo skill {ref.repo_alias}/{ref.skill_subpath!r} must be resolved "
            "via resolve_org_repo_skill (registration flow), not resolve_skill",
            path=ref.name,
        )

    if ref.is_local:
        if not ref.path:
            # Defensive: the parser guarantees a local ref carries a path, but
            # never trust the invariant silently — a path-less local ref can't
            # be located.
            raise SkillResolutionError(
                f"local skill {ref.name!r} has no path to resolve",
                path=ref.name,
            )
        # load_skill raises SkillError (a ManifestError) with a pathed message
        # when SKILL.md is missing/invalid — let it propagate.
        return load_skill(agent_repo_tree, folder=ref.path)

    # Catalogue → built-in catalogue, at ``skills/<name>/``.
    folder = f"{_CATALOGUE_SKILLS_DIR}/{ref.name}"
    skill_md_path = f"{folder}/SKILL.md"
    if skill_md_path not in catalogue_tree:
        raise SkillResolutionError(
            f"catalogue skill {ref.name!r} not found in the built-in catalogue "
            f"({catalogue_repo()}@{catalogue_ref()}) at {folder}/",
            path=ref.name,
        )
    return load_skill(catalogue_tree, folder=folder)


def resolve_org_repo_skill(ref: SkillRef, *, repo_tree: dict[str, str]) -> LoadedSkill:
    """Resolve an org-repo :class:`SkillRef` against a fetched org-repo tree.

    ``repo_tree`` is the ``{path: text}`` map of the registered org skill repo
    (fetched by the registration flow via :func:`fetch_org_repo_tree`). The
    skill folder is located by ``ref.skill_subpath`` (the part of the ref after
    the alias):

    * preferred: ``skills/<subpath>/SKILL.md`` (the agentskills.io repo layout,
      same ``skills/`` convention as the built-in catalogue); else
    * fallback: ``<subpath>/SKILL.md`` (the subpath already points at the
      folder, e.g. a repo whose skills aren't nested under ``skills/``).

    Raises :class:`SkillResolutionError` when neither location has a
    ``SKILL.md``. ``load_skill`` (a ``ManifestError``) propagates for a folder
    whose ``SKILL.md`` is present-but-malformed — both record a clear note.
    """
    if ref.kind != "org_repo":
        raise SkillResolutionError(
            f"resolve_org_repo_skill called with a non-org-repo ref (kind={ref.kind!r})",
            path=ref.name,
        )
    subpath = ref.skill_subpath.strip("/")
    nested = f"{_CATALOGUE_SKILLS_DIR}/{subpath}"
    for folder in (nested, subpath):
        if f"{folder}/SKILL.md" in repo_tree:
            return load_skill(repo_tree, folder=folder)
    raise SkillResolutionError(
        f"org-repo skill {subpath!r} not found in repo (looked at "
        f"{nested}/ and {subpath}/)",
        path=ref.name,
    )


def fetch_org_repo_tree(repo, *, ref: str) -> dict[str, str]:
    """Fetch a registered :class:`OrgSkillRepo`'s file tree at ``ref``.

    Routes by whether the repo is private:

    * **public** (``repo.source_connection`` is None) — anonymous fetch via
      :func:`astrolift_scm.providers.repo_tree.fetch_public_repo_tree` (the
      same no-auth path the built-in catalogue uses).
    * **private** (``repo.source_connection`` set) — authenticated fetch via
      :func:`astrolift_scm.providers.repo_tree.fetch_repo_tree` through that
      connection's credential.

    Returns the ``{repo_relative_path: text}`` map. Raises whatever the
    underlying fetch raises (``requests`` errors for public, ``ProviderError``
    for private) — the registration flow catches these and records a non-fatal
    note so a skill-repo outage never aborts agent registration.
    """
    from astrolift_scm.providers.repo_tree import (
        fetch_public_repo_tree,
        fetch_repo_tree,
    )

    conn = repo.source_connection
    if conn is None:
        return fetch_public_repo_tree(repo_full_name=repo.repo_full_name, ref=ref)
    return fetch_repo_tree(conn, repo_full_name=repo.repo_full_name, ref=ref)


__all__ = [
    "CatalogueFetchError",
    "SkillResolutionError",
    "catalogue_ref",
    "catalogue_repo",
    "fetch_org_repo_tree",
    "load_catalogue_tree",
    "resolve_org_repo_skill",
    "resolve_skill",
]
