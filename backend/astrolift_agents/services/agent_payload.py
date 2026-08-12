"""Build the immutable, chrooted source payload for an Agent Package.

The SCM archive is untrusted input.  This module validates it before copying
only the package root and explicitly-declared shared mounts into a new ZIP.
The output never contains the rest of a monorepo, preserves binary bytes and
file modes, and carries a root marker so the in-container extractor does not
mistake a single top-level package directory for an SCM wrapper directory.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any

MAX_SOURCE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FILES = 10_000
MAX_SOURCE_EXPANDED_BYTES = 512 * 1024 * 1024
PACKAGE_MARKER = ".astrolift-package.json"


class AgentPayloadError(ValueError):
    """The source archive or requested package slice is unsafe/invalid."""


def _safe_path(value: str, *, field: str, allow_dot: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise AgentPayloadError(f"{field} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise AgentPayloadError(f"{field} may not be absolute or contain '..': {value!r}")
    if path.as_posix() == "." and not allow_dot:
        raise AgentPayloadError(f"{field} may not be '.'")
    return path


def _matches(path: str, patterns: list[str]) -> bool:
    """Match gitignore-like glob lists, including root files under ``**/``."""
    for pattern in patterns:
        if pattern in {".", "**", "**/*"}:
            return True
        if fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
            return True
    return False


def _repo_members(source_zip: bytes) -> dict[str, tuple[zipfile.ZipInfo, bytes]]:
    if len(source_zip) > MAX_SOURCE_ARCHIVE_BYTES:
        raise AgentPayloadError(f"source ZIP exceeds {MAX_SOURCE_ARCHIVE_BYTES} bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(source_zip))
    except zipfile.BadZipFile as exc:
        raise AgentPayloadError("source payload is not a valid ZIP archive") from exc

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_SOURCE_FILES:
            raise AgentPayloadError(f"source ZIP contains more than {MAX_SOURCE_FILES} files")
        if sum(info.file_size for info in infos) > MAX_SOURCE_EXPANDED_BYTES:
            raise AgentPayloadError(f"source ZIP expands beyond {MAX_SOURCE_EXPANDED_BYTES} bytes")

        paths = [_safe_path(info.filename, field="archive member") for info in infos]
        first = {path.parts[0] for path in paths if path.parts}
        strip_root = len(first) == 1 and all(len(path.parts) > 1 for path in paths)
        out: dict[str, tuple[zipfile.ZipInfo, bytes]] = {}
        for info, source_path in zip(infos, paths, strict=True):
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise AgentPayloadError(f"source ZIP symlinks are not allowed: {info.filename!r}")
            rel_path = PurePosixPath(*source_path.parts[1:]) if strip_root else source_path
            rel = _safe_path(rel_path.as_posix(), field="archive member").as_posix()
            if rel in out:
                raise AgentPayloadError(f"duplicate source ZIP member {rel!r}")
            try:
                out[rel] = (info, archive.read(info))
            except (RuntimeError, zipfile.BadZipFile) as exc:
                raise AgentPayloadError(f"could not read source ZIP member {rel!r}") from exc
        return out


def build_agent_payload(source_zip: bytes, source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Return ``(filtered_zip, metadata)`` for one canonical source slice."""
    if not isinstance(source, dict):
        raise AgentPayloadError("agent package source must be an object")
    members = _repo_members(source_zip)
    root = _safe_path(str(source.get("root") or "."), field="source.root", allow_dot=True)
    root_text = "" if root.as_posix() == "." else root.as_posix().rstrip("/")
    include = [str(value) for value in (source.get("include") or ["**"])]
    exclude = [str(value) for value in (source.get("exclude") or [])]
    executables = [str(value) for value in (source.get("executables") or [])]
    for field, patterns in (
        ("source.include", include),
        ("source.exclude", exclude),
        ("source.executables", executables),
    ):
        for pattern in patterns:
            _safe_path(pattern, field=field, allow_dot=True)

    selected: dict[str, tuple[zipfile.ZipInfo, bytes, bool]] = {}

    def add(source_path: str, destination: str, *, explicit_executable: bool = False) -> None:
        destination_path = _safe_path(destination, field="payload destination")
        destination_text = destination_path.as_posix()
        if destination_text == PACKAGE_MARKER:
            raise AgentPayloadError(f"package source collides with reserved {PACKAGE_MARKER!r}")
        if destination_text in selected:
            raise AgentPayloadError(f"package sources collide at {destination_text!r}")
        info, data = members[source_path]
        selected[destination_text] = (info, data, explicit_executable)

    root_prefix = f"{root_text}/" if root_text else ""
    for path in sorted(members):
        if root_text and path != root_text and not path.startswith(root_prefix):
            continue
        relative = path[len(root_prefix) :] if root_prefix else path
        if not relative:
            continue
        if not _matches(relative, include) or _matches(relative, exclude):
            continue
        add(path, relative, explicit_executable=_matches(relative, executables))

    raw_shared = source.get("shared") or []
    if not isinstance(raw_shared, list):
        raise AgentPayloadError("source.shared must be an array")
    for index, mount in enumerate(raw_shared):
        if not isinstance(mount, dict):
            raise AgentPayloadError(f"source.shared[{index}] must be an object")
        shared_source = _safe_path(str(mount.get("source") or ""), field=f"source.shared[{index}].source")
        shared_mount = _safe_path(str(mount.get("mount") or ""), field=f"source.shared[{index}].mount")
        source_text = shared_source.as_posix().rstrip("/")
        matches = [
            path for path in sorted(members) if path == source_text or path.startswith(f"{source_text}/")
        ]
        if not matches:
            raise AgentPayloadError(f"shared source {source_text!r} does not exist in the source archive")
        for path in matches:
            suffix = path[len(source_text) :].lstrip("/")
            destination = shared_mount / suffix if suffix else shared_mount
            add(
                path,
                destination.as_posix(),
                explicit_executable=_matches(destination.as_posix(), executables),
            )

    if not selected:
        raise AgentPayloadError("agent package source slice selected no files")

    manifest_path = str(source.get("manifest_path") or "")
    runtime_manifest_path = ""
    if manifest_path:
        manifest = _safe_path(manifest_path, field="source.manifest_path").as_posix()
        if manifest == root_text:
            runtime_manifest_path = PurePosixPath(manifest).name
        elif not root_text or manifest.startswith(root_prefix):
            runtime_manifest_path = manifest[len(root_prefix) :] if root_prefix else manifest

    metadata = {
        "schema": "astrolift.agent.payload/v1",
        "source": source,
        "runtime_manifest_path": runtime_manifest_path,
        "file_count": len(selected),
    }
    marker = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        marker_info = zipfile.ZipInfo(PACKAGE_MARKER)
        marker_info.external_attr = (0o100644 & 0xFFFF) << 16
        archive.writestr(marker_info, marker)
        for destination, (source_info, data, explicit_executable) in selected.items():
            target = zipfile.ZipInfo(destination, date_time=source_info.date_time)
            source_mode = (source_info.external_attr >> 16) & 0o777
            mode = source_mode or 0o644
            if explicit_executable:
                mode |= 0o111
            target.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
            target.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(target, data)
    payload = output.getvalue()
    if len(payload) > MAX_SOURCE_ARCHIVE_BYTES:
        raise AgentPayloadError(f"agent package ZIP exceeds {MAX_SOURCE_ARCHIVE_BYTES} bytes")
    metadata["sha256"] = hashlib.sha256(payload).hexdigest()
    return payload, metadata


__all__ = ["AgentPayloadError", "PACKAGE_MARKER", "build_agent_payload"]
