"""
Skill resolver — turn a manifest ``SkillRef`` into a ``LoadedSkill`` (spec 38
Phase 3 + spec 39c).

A ``skills = [ … ]`` entry in an agent manifest resolves against one of two
sources (the v1 subset of spec 39's three-source model):

* **local** — ``{ name = "relative/path" }`` → a folder in the agent's own
  repo (agentskills.io layout). Read from the repo tree already fetched for
  registration. Highest specificity; versioned with the agent.
* **named** — a bare string (``"pr-review"``) → resolved from the **built-in
  catalogue** (``calliopeai/astrolift-skills``), the baseline repo shipped
  with every deployment. Found at ``skills/<name>/`` in the catalogue tree.

The third source from spec 39 — per-org skill repos referenced
``owner/repo/skill@ref`` — is **39d** and is *not parsed yet*: the manifest
parser produces a bare-name :class:`SkillRef` with ``is_local=False`` for any
non-table entry, so an ``owner/repo/...`` string would arrive here as a single
``name`` and resolve against the catalogue (and miss). That's the documented
v1 boundary; a name absent from the catalogue raises a clear
:class:`SkillResolutionError`.

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
    """Resolve one :class:`SkillRef` to a :class:`LoadedSkill`.

    * **local** (``ref.is_local`` / ``ref.path`` set) — :func:`load_skill`
      from ``agent_repo_tree`` at ``ref.path`` (the agentskills.io folder in
      the agent's own repo, repo-relative against the fetched tree).
    * **named** (``not ref.is_local``, a bare name) — locate
      ``skills/<ref.name>/`` in ``catalogue_tree`` and :func:`load_skill`.

    Raises :class:`SkillResolutionError` when the reference can't be resolved
    (a local path with no ``SKILL.md``, a name absent from the catalogue, or a
    malformed skill folder). ``SkillError`` raised by :func:`load_skill` is a
    ``ManifestError`` subclass and propagates as-is — both surface a clear,
    pathed message the caller records.
    """
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

    # Named → built-in catalogue. The org-repo ``owner/repo/skill@ref`` form
    # (spec 39 source #2) is 39d and not parsed yet; a named ref resolves to
    # the catalogue only.
    folder = f"{_CATALOGUE_SKILLS_DIR}/{ref.name}"
    skill_md_path = f"{folder}/SKILL.md"
    if skill_md_path not in catalogue_tree:
        raise SkillResolutionError(
            f"named skill {ref.name!r} not found in the built-in catalogue "
            f"({catalogue_repo()}@{catalogue_ref()}) at {folder}/",
            path=ref.name,
        )
    return load_skill(catalogue_tree, folder=folder)


__all__ = [
    "CatalogueFetchError",
    "SkillResolutionError",
    "catalogue_ref",
    "catalogue_repo",
    "load_catalogue_tree",
    "resolve_skill",
]
