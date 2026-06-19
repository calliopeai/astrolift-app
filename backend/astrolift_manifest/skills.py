"""
agentskills.io skill-folder loader (spec 38, §Formats).

A *skill folder* is a directory in the **[agentskills.io](https://agentskills.io)**
/ Anthropic Agent Skills format: a required ``SKILL.md`` (YAML frontmatter
with ``name`` + ``description`` required, followed by the markdown
instructions body) plus optional ``scripts/`` (executable code),
``references/`` (docs), and ``assets/`` (templates/resources).

This module is pure over the same ``{path: contents}`` repo-tree map the
manifest discovery consumes (see :mod:`astrolift_manifest.discover` and the
SCM zipball unpacker), so Phase 3 registration can feed it the fetched repo
tree directly. It records pointers + parsed prose only — it does not fetch,
execute, or persist anything.

Frontmatter schema: the agentskills.io / Agent Skills standard *confirms*
``name`` + ``description`` as the required pair (spec 38 §Formats). Any
optional keys beyond those (e.g. ``license``, ``version``, ``allowed-tools``)
are not enumerated here — they pass through verbatim on
:attr:`~astrolift_manifest.types.LoadedSkill.frontmatter` so Phase 2 can map
them onto the ``Skill`` record without this loader guessing the full schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from astrolift_manifest.parser import ManifestError
from astrolift_manifest.types import LoadedSkill

# Required SKILL.md basename and the three optional bundle subdirectories.
SKILL_FILENAME = "SKILL.md"
_BUNDLE_DIRS = ("scripts", "references", "assets")

# A document opening with a ``---`` line begins YAML frontmatter, closed by
# the next ``---`` (or ``...``) line on its own. Mirrors the Jekyll / Agent
# Skills convention. We hand-split rather than pull a frontmatter lib in.
_FENCE = "---"


class SkillError(ManifestError):
    """Raised when a skill folder fails to load.

    Subclasses :class:`ManifestError` so callers that already catch
    ``ManifestError`` for manifest parsing catch skill-loading errors too,
    and the ``path`` carries the offending location (the skill folder or the
    ``SKILL.md`` frontmatter key).
    """


def load_skill(files: Mapping[str, Any], *, folder: str) -> LoadedSkill:
    """Parse the skill folder rooted at ``folder`` out of a repo-tree map.

    ``files`` is a ``{repo_relative_path: contents}`` map (the discovery
    shape). ``folder`` is the repo-relative skill directory, e.g.
    ``"skills/reviewer"`` (the ``path`` of a local
    :class:`~astrolift_manifest.types.SkillRef`). A trailing slash is
    tolerated.

    Returns a :class:`~astrolift_manifest.types.LoadedSkill`. Raises
    :class:`SkillError` when ``SKILL.md`` is missing, isn't text, or lacks
    the required ``name`` / ``description`` frontmatter.
    """
    prefix = folder.rstrip("/")
    skill_md_path = f"{prefix}/{SKILL_FILENAME}"
    raw = files.get(skill_md_path)
    if raw is None:
        raise SkillError(
            f"skill folder {prefix!r} is missing the required {SKILL_FILENAME}",
            path=prefix,
        )
    if not isinstance(raw, str):
        raise SkillError(
            f"{SKILL_FILENAME} contents must be text, got {type(raw).__name__}",
            path=skill_md_path,
        )

    frontmatter, body = _split_frontmatter(raw, path=skill_md_path)

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SkillError(
            "SKILL.md frontmatter must set a non-empty 'name'",
            path=f"{skill_md_path}#name",
        )
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillError(
            "SKILL.md frontmatter must set a non-empty 'description'",
            path=f"{skill_md_path}#description",
        )

    scripts = _list_bundle(files, prefix, "scripts")
    references = _list_bundle(files, prefix, "references")
    assets = _list_bundle(files, prefix, "assets")

    return LoadedSkill(
        name=name.strip(),
        description=description.strip(),
        instructions=body.strip(),
        scripts=scripts,
        references=references,
        assets=assets,
        frontmatter=frontmatter,
    )


def _split_frontmatter(text: str, *, path: str) -> tuple[dict[str, Any], str]:
    """Split a ``---``-fenced YAML frontmatter block from the markdown body.

    Returns ``(frontmatter_dict, body)``. A document with no opening fence is
    an error — the Agent Skills format requires frontmatter (it carries the
    required name/description). A malformed / non-mapping YAML block is an
    error too.
    """
    lines = text.splitlines()
    # The opening fence must be the first non-blank line. A leading BOM or
    # blank lines before it are tolerated.
    idx = 0
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx >= len(lines) or lines[idx].strip().lstrip("﻿") != _FENCE:
        raise SkillError(
            "SKILL.md must begin with a '---' YAML frontmatter block",
            path=path,
        )

    closing = None
    for j in range(idx + 1, len(lines)):
        if lines[j].strip() in (_FENCE, "..."):
            closing = j
            break
    if closing is None:
        raise SkillError(
            "SKILL.md frontmatter block is not closed with a '---' line",
            path=path,
        )

    fm_text = "\n".join(lines[idx + 1 : closing])
    body = "\n".join(lines[closing + 1 :])

    try:
        loaded = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"SKILL.md frontmatter is not valid YAML: {exc}", path=path) from exc
    if not isinstance(loaded, dict):
        raise SkillError(
            "SKILL.md frontmatter must be a YAML mapping (key: value)",
            path=path,
        )
    return loaded, body


def _list_bundle(files: Mapping[str, Any], prefix: str, subdir: str) -> tuple[str, ...]:
    """Repo-relative paths of files under ``<prefix>/<subdir>/``, sorted.

    Returns every file *at any depth* under the subdirectory (a script may
    sit in ``scripts/lib/helper.py``). The ``SKILL.md`` itself is never under
    a bundle dir so it can't leak in. Empty tuple when the subdir is absent.
    """
    sub_prefix = f"{prefix}/{subdir}/"
    out = sorted(p for p, contents in files.items() if p.startswith(sub_prefix) and not p.endswith("/"))
    return tuple(out)


__all__ = ["SKILL_FILENAME", "SkillError", "load_skill"]
