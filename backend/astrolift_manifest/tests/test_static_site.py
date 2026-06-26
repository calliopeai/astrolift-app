"""Tests for the ``kind = "static_site"`` manifest topology (#1010).

Covers the manifest layer only: parsing the four ``static_*`` fields,
rejecting containers on a static workload, the build-command/output-dir
pairing rule, and round-tripping through normalize/serialize (including the
manifest hash reacting to a ``static_*`` edit and the no-defaults pass-through
for a container-less workload).
"""

from __future__ import annotations

import dataclasses

import pytest

from astrolift_manifest.normalize import manifest_hash, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.types import RawManifest, WorkloadManifest


def test_parse_static_site_platform_build():
    toml = """
name = "docs"

[[workloads]]
name = "site"
kind = "static_site"
is_public = true
static_build_command = "npm ci && npm run build"
static_output_dir = "dist"
static_spa = true
static_index = "index.html"
"""
    raw = parse_raw(toml)
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.kind == "static_site"
    assert w.is_public is True
    assert w.schedule is None
    assert w.containers == ()
    assert w.static_build_command == "npm ci && npm run build"
    assert w.static_output_dir == "dist"
    assert w.static_spa is True
    assert w.static_index == "index.html"


def test_parse_static_site_ci_pushed_defaults():
    # CI-pushed: no build command, no output dir, defaults for the rest.
    toml = """
name = "docs"

[[workloads]]
name = "site"
kind = "static_site"
"""
    w = parse_raw(toml).workloads[0]
    assert w.kind == "static_site"
    assert w.static_build_command == ""
    assert w.static_output_dir == ""
    assert w.static_spa is False
    assert w.static_index == "index.html"


def test_static_site_rejects_containers():
    toml = """
name = "docs"

[[workloads]]
name = "site"
kind = "static_site"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "no containers" in str(exc.value)
    assert exc.value.path == "workloads[0].containers"


def test_static_site_build_command_requires_output_dir():
    toml = """
name = "docs"

[[workloads]]
name = "site"
kind = "static_site"
static_build_command = "npm run build"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "static_output_dir" in str(exc.value)


def test_static_site_normalize_passthrough_no_defaults():
    raw = RawManifest(
        name="docs",
        workloads=(WorkloadManifest(name="site", kind="static_site"),),
    )
    n = normalize(raw)
    w = n.workloads[0]
    # No cpu/mem/healthcheck defaults, no fabricated primary container.
    assert w.cpu_request is None
    assert w.cpu_limit is None
    assert w.memory_request is None
    assert w.containers == ()
    assert n.defaults_applied == ()


def test_static_site_serialized_roundtrip_and_hash_reacts():
    base = WorkloadManifest(
        name="site",
        kind="static_site",
        static_build_command="npm run build",
        static_output_dir="dist",
        static_spa=False,
        static_index="index.html",
    )
    n = normalize(RawManifest(name="docs", workloads=(base,)))
    wdict = n.serialized["workloads"][0]
    assert wdict["static_build_command"] == "npm run build"
    assert wdict["static_output_dir"] == "dist"
    assert wdict["static_spa"] is False
    assert wdict["static_index"] == "index.html"

    h_base = manifest_hash(n.serialized)
    # Flipping the SPA flag must change the hash (real change, not no-op).
    spa = dataclasses.replace(base, static_spa=True)
    n2 = normalize(RawManifest(name="docs", workloads=(spa,)))
    assert manifest_hash(n2.serialized) != h_base


def test_existing_deployment_kind_unaffected():
    # Regression guard: a normal deployment still gets resource defaults and
    # an is_primary container promotion, and carries the static_* defaults
    # harmlessly.
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  port = 8080
"""
    raw = parse_raw(toml)
    w = raw.workloads[0]
    assert w.static_build_command == ""
    assert w.static_index == "index.html"
    n = normalize(raw)
    nw = n.workloads[0]
    assert nw.cpu_request == "100m"
    assert nw.containers[0].is_primary is True


def test_manifest_sync_flags_static_field_edit_as_changed():
    # manifest_sync._workload_body_changed must treat a static_* edit as a body
    # change so an SCM-driven static-config edit triggers a resync. Guards
    # against a field being dropped from the change-detection tuple.
    from astrolift_registry.services.manifest_sync import _workload_body_changed

    base = WorkloadManifest(
        name="site",
        kind="static_site",
        is_public=True,
        static_build_command="npm run build",
        static_output_dir="dist",
        static_spa=False,
        static_index="index.html",
    )
    assert _workload_body_changed(base, base) is False
    for field, value in (
        ("static_build_command", "npm run build2"),
        ("static_output_dir", "build"),
        ("static_spa", True),
        ("static_index", "main.html"),
    ):
        edited = dataclasses.replace(base, **{field: value})
        assert _workload_body_changed(base, edited) is True, field


def test_parse_whitespace_only_build_command_is_ci_pushed():
    # The mode-select rule keys on a STRIPPED build command (whitespace-only ==
    # CI-pushed). The parser must strip too, or the operator gets a manifest
    # that parses as platform-build while the runtime selector silently no-ops.
    toml = """
name = "docs"

[[workloads]]
name = "site"
kind = "static_site"
static_build_command = "   "
"""
    w = parse_raw(toml).workloads[0]
    # Stored stripped -> empty -> CI-pushed at both parser and runtime sites.
    assert w.static_build_command == ""
