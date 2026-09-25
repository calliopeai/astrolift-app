"""Repo-relative path validation for build fields (#1756 follow-up).

``dockerfile_path`` / ``build_context`` (app-level, set at ``astro app
register`` / ``updateApp``, or container-level, set in
``[[workloads.containers]]``) both end up as ``--dockerfile`` /
``--context-sub-path`` arguments to the in-cluster kaniko build
(``providers/k8s_native/build_kaniko.py``). Kaniko takes both verbatim, so
an unchecked value reaches straight past the intended repo checkout --
an absolute path, or a ``..`` that climbs above the repo root.

Two shapes need two checks:

* An app-level field is already meant to be relative to the repo root
  directly (there is no further base to combine it with) --
  :func:`validate_repo_relative_field` rejects it outright at
  register/update time.
* A container-level field is a relative offset from the manifest's own
  directory (``app.build_context`` -- ``"apps/web"`` for a monorepo
  service) -- :func:`resolve_repo_relative` joins and normalizes it,
  rejecting only when the *resolved* result would escape the repo root.
  ``"../.."`` from a manifest two directories deep resolving to the repo
  root is the intended case (#1756); ``"../../../etc"`` from the same
  manifest is not.
"""

from __future__ import annotations

import posixpath


def is_absolute_path(value: str) -> bool:
    """True for a leading ``/`` -- never valid for a value meant to be
    relative to a source checkout, whatever the base."""
    return value.startswith("/")


def resolve_repo_relative(base: str, value: str) -> str | None:
    """Join ``value`` onto ``base`` (both repo-root-relative, POSIX-style)
    and normalize.

    Returns ``None`` when ``value`` is itself absolute, or when the
    normalized result would escape the repo root (``".."`` or a path
    starting with ``"../"``) -- the caller decides whether that is a hard
    reject or a silent fall-back to the app-level value.
    """
    if is_absolute_path(value):
        return None
    joined = posixpath.normpath(posixpath.join(base or ".", value))
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return joined


def validate_repo_relative_field(value: str, *, field: str) -> str | None:
    """Validate an app-level field that is already relative to the repo
    root (register/update time; there is no separate base to resolve
    against). Returns a human-readable error message, or ``None`` when
    ``value`` is safe. An empty value is always safe -- callers apply
    their own "unset" default separately."""
    if not value:
        return None
    if is_absolute_path(value):
        return f"{field} must be a repo-relative path, got an absolute path {value!r}"
    normalized = posixpath.normpath(value)
    if normalized == ".." or normalized.startswith("../"):
        return f"{field} must not escape the repository root, got {value!r}"
    return None
