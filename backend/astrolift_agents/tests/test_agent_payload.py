from __future__ import annotations

import io
import stat
import zipfile

import pytest

from astrolift_agents.services.agent_payload import (
    PACKAGE_MARKER,
    AgentPayloadError,
    build_agent_payload,
)


def _source(files: dict[str, bytes], *, modes: dict[str, int] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, body in files.items():
            info = zipfile.ZipInfo(f"repo-sha/{path}")
            info.external_attr = ((stat.S_IFREG | (modes or {}).get(path, 0o644)) & 0xFFFF) << 16
            archive.writestr(info, body)
    return output.getvalue()


def _files(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_chroots_filters_and_preserves_binary_bytes():
    source = _source(
        {
            "agents/emr/astrolift.toml": b'name = "emr"\n',
            "agents/emr/scripts/run.sh": b"#!/bin/sh\n",
            "agents/emr/bin/helper": b"\x00\xff\x10binary",
            "agents/emr/private/debug.tmp": b"nope",
            "agents/other/secret.txt": b"must not leak",
            "shared/clients.json": b"{}",
        }
    )
    payload, metadata = build_agent_payload(
        source,
        {
            "root": "agents/emr",
            "manifest_path": "agents/emr/astrolift.toml",
            "include": ["astrolift.toml", "scripts/**", "bin/**"],
            "exclude": ["**/*.tmp"],
            "executables": ["scripts/**", "bin/**"],
            "shared": [{"source": "shared/clients.json", "mount": "config/clients.json"}],
        },
    )

    files = _files(payload)
    assert set(files) == {
        PACKAGE_MARKER,
        "astrolift.toml",
        "scripts/run.sh",
        "bin/helper",
        "config/clients.json",
    }
    assert files["bin/helper"] == b"\x00\xff\x10binary"
    assert metadata["runtime_manifest_path"] == "astrolift.toml"
    assert metadata["file_count"] == 4
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert (archive.getinfo("scripts/run.sh").external_attr >> 16) & 0o111
        assert (archive.getinfo("bin/helper").external_attr >> 16) & 0o111


def test_shared_mount_collision_is_rejected():
    source = _source({"agent/config.json": b"one", "shared/config.json": b"two"})
    with pytest.raises(AgentPayloadError, match="collide"):
        build_agent_payload(
            source,
            {
                "root": "agent",
                "include": ["**"],
                "shared": [{"source": "shared/config.json", "mount": "config.json"}],
            },
        )


def test_symlink_source_is_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("repo-sha/agent/link")
        info.external_attr = ((stat.S_IFLNK | 0o777) & 0xFFFF) << 16
        archive.writestr(info, "../../secret")
    with pytest.raises(AgentPayloadError, match="symlinks"):
        build_agent_payload(output.getvalue(), {"root": "agent"})


def test_manifest_outside_chroot_is_not_exposed_as_runtime_config():
    source = _source(
        {
            "agents/emr/astrolift.toml": b'name = "emr"\n',
            "agents/emr/runtime/run.py": b"print('ok')\n",
        }
    )
    payload, metadata = build_agent_payload(
        source,
        {
            "root": "agents/emr/runtime",
            "manifest_path": "agents/emr/astrolift.toml",
            "include": ["**"],
        },
    )
    assert metadata["runtime_manifest_path"] == ""
    assert "astrolift.toml" not in _files(payload)
    assert _files(payload)["run.py"] == b"print('ok')\n"


def test_empty_source_slice_is_rejected_before_registration():
    source = _source({"agents/emr/astrolift.toml": b'name = "emr"\n'})

    with pytest.raises(AgentPayloadError, match="selected no files"):
        build_agent_payload(
            source,
            {
                "root": "agents/emr",
                "include": ["scripts/**"],
            },
        )
