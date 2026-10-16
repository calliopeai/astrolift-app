"""The build context a new registration starts with (astrolift-cli #159).

An app registered from a manifest in a sub-directory builds from that
directory: ``src/abbott-cgm/astrolift.toml`` builds ``src/abbott-cgm``, with
the app-level ``Dockerfile`` resolved inside it. A manifest that names its own
``build_context`` or ``dockerfile_path`` (on a container, a job or a task)
keeps the repository root as the app-level context, so its paths apply the
way they always have: resolved from the manifest's directory by
``build_image._resolve_build_paths``.

Only registration consults this, and only when the request names no build
context. An app already registered keeps the context it stored.
"""

from __future__ import annotations

import posixpath

from astrolift_manifest.path_safety import resolve_repo_relative
from astrolift_manifest.types import DEFAULT_BUILD_CONTEXT, DEFAULT_DOCKERFILE_PATH


def manifest_directory(manifest_path: str) -> str:
    """The manifest's repo-relative directory, ``"."`` at the repository root.

    A path that is absolute or climbs above the repository names no directory
    a build could use, so it yields ``"."`` too.
    """
    directory = posixpath.dirname((manifest_path or "").strip())
    if not directory:
        return DEFAULT_BUILD_CONTEXT
    return resolve_repo_relative(DEFAULT_BUILD_CONTEXT, directory) or DEFAULT_BUILD_CONTEXT


def sets_build_paths(dockerfile_path: str, build_context: str) -> bool:
    """True when a container names a Dockerfile or build context of its own."""
    dockerfile = dockerfile_path or DEFAULT_DOCKERFILE_PATH
    context = build_context or DEFAULT_BUILD_CONTEXT
    return dockerfile != DEFAULT_DOCKERFILE_PATH or context != DEFAULT_BUILD_CONTEXT


def detected_build_context(manifest_path: str, *, manifest_sets_build_paths: bool) -> str:
    """The app-level build context for a new registration of ``manifest_path``."""
    if manifest_sets_build_paths:
        return DEFAULT_BUILD_CONTEXT
    return manifest_directory(manifest_path)
