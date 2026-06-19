"""
Brief-folder loader (spec 38, §Formats).

A *brief folder* is a directory fronted by ``README.md`` that may link to
sibling files (``specs/``, ``scripts/``, ``utils/``, …) for context. The
README is the entry point; the files it references are loaded as additional
context for the agent at dispatch.

This module is pure over the same ``{path: contents}`` repo-tree map the
manifest discovery and skill loader consume, so Phase 3 registration can feed
it the fetched repo tree directly. It records the README text + the set of
referenced sibling paths — it does not recurse into those files or build a
context graph (that's a dispatch-time concern, #930).

Reference detection (kept deliberately simple + documented):

* Markdown inline links — ``[text](target)`` and bare autolinks
  ``<target>`` — are scanned out of the README.
* Each target is resolved relative to the brief folder.
* A target is kept only when it (a) stays *within* the brief folder (no
  ``../`` escape, no absolute path), (b) is not an external URL
  (``http://`` / ``https://`` / ``mailto:`` / a protocol-relative ``//``),
  (c) is not a pure ``#anchor``, and (d) actually exists in the repo-tree
  map. Unknown / escaping / external links are silently dropped — the README
  still loads, only its resolvable in-folder context is surfaced.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from typing import Any

from astrolift_manifest.parser import ManifestError
from astrolift_manifest.types import LoadedBrief

README_FILENAME = "README.md"

# ``[label](target)`` — capture the target. Tolerates an optional ``"title"``
# after the target inside the parens (standard Markdown). Non-greedy label so
# adjacent links don't merge.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
# ``<target>`` autolink — only when it looks like a path, not an email/URL
# (those are filtered by _is_external downstream anyway).
_AUTOLINK_RE = re.compile(r"<([^>\s]+)>")

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "//", "ftp://")


class BriefError(ManifestError):
    """Raised when a brief folder fails to load.

    Subclasses :class:`ManifestError` so callers that catch ``ManifestError``
    for manifest parsing catch brief-loading errors too, and ``path`` carries
    the offending location (the brief folder / README path).
    """


def load_brief(files: Mapping[str, Any], *, readme_path: str) -> LoadedBrief:
    """Parse the brief folder fronted by ``readme_path`` out of a repo tree.

    ``files`` is a ``{repo_relative_path: contents}`` map (the discovery
    shape). ``readme_path`` is the repo-relative path to the brief's entry
    README — the ``path`` of a :class:`~astrolift_manifest.types.BriefRef`,
    e.g. ``"brief/README.md"``. The brief *folder* is that file's parent
    directory; only references that resolve inside it are surfaced.

    Returns a :class:`~astrolift_manifest.types.LoadedBrief`. Raises
    :class:`BriefError` when the README is missing or isn't text.
    """
    raw = files.get(readme_path)
    if raw is None:
        raise BriefError(
            f"brief README {readme_path!r} not found in the repo tree",
            path=readme_path,
        )
    if not isinstance(raw, str):
        raise BriefError(
            f"brief README contents must be text, got {type(raw).__name__}",
            path=readme_path,
        )

    folder = posixpath.dirname(readme_path)
    referenced = _resolve_references(raw, folder=folder, readme_path=readme_path, files=files)
    return LoadedBrief(readme_text=raw, referenced_paths=referenced)


def _resolve_references(
    readme_text: str,
    *,
    folder: str,
    readme_path: str,
    files: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return the sorted, de-duplicated set of in-folder sibling paths the
    README links to that exist in ``files`` (excluding the README itself)."""
    targets: list[str] = []
    for m in _MD_LINK_RE.finditer(readme_text):
        targets.append(m.group(1))
    for m in _AUTOLINK_RE.finditer(readme_text):
        targets.append(m.group(1))

    kept: set[str] = set()
    folder_prefix = f"{folder}/" if folder else ""
    for target in targets:
        # Strip a trailing ``#anchor`` / ``?query`` — they don't change the
        # file a link points at.
        clean = target.split("#", 1)[0].split("?", 1)[0].strip()
        if not clean or _is_external(clean):
            continue
        # Resolve relative to the brief folder. ``normpath`` collapses ``./``
        # and ``../``; an escaping link normalizes to a path outside the
        # folder prefix and is dropped below.
        resolved = posixpath.normpath(posixpath.join(folder, clean)) if folder else posixpath.normpath(clean)
        if resolved == readme_path:
            continue
        # Must stay within the brief folder. With no folder (README at repo
        # root) any non-escaping relative path qualifies.
        if folder_prefix and not resolved.startswith(folder_prefix):
            continue
        if resolved not in files:
            continue
        kept.add(resolved)
    return tuple(sorted(kept))


def _is_external(target: str) -> bool:
    """True for links that point outside the repo tree (URLs, mail, etc.)."""
    lowered = target.lower()
    return any(lowered.startswith(p) for p in _EXTERNAL_PREFIXES)


__all__ = ["README_FILENAME", "BriefError", "load_brief"]
