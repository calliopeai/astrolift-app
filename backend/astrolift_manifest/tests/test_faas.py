"""Tests for the ``kind = "faas"`` manifest topology (#987).

Covers the manifest layer only: parsing the ``faas_*`` fields, the
package-type validation rules (image-mode forbids handler/runtime, zip-mode
requires handler+runtime+output), rejecting containers on a faas workload, and
round-tripping through normalize/serialize (the manifest hash reacting to a
``faas_*`` edit and the no-defaults pass-through for a container-less
workload), plus the manifest_sync body-change detection.

Lambda is the only v1 variant, but the manifest kind stays cloud-neutral
(``faas``) — these tests pin only the provider-neutral manifest surface.
"""

from __future__ import annotations

import dataclasses

import pytest

from astrolift_manifest.normalize import manifest_hash, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.types import RawManifest, WorkloadManifest


def test_parse_faas_image_mode_defaults():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
is_public = true
faas_public = true
"""
    raw = parse_raw(toml)
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.kind == "faas"
    assert w.containers == ()
    # Image is the default package type; runtime/handler stay empty.
    assert w.faas_package_type == "image"
    assert w.faas_runtime == ""
    assert w.faas_handler == ""
    assert w.faas_memory_mb == 512
    assert w.faas_timeout_seconds == 30
    assert w.faas_architecture == "arm64"
    assert w.faas_public is True


def test_parse_faas_zip_mode():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
faas_package_type = "zip"
faas_runtime = "python3.12"
faas_handler = "app.handler"
faas_build_command = "pip install -r requirements.txt -t ."
faas_output_dir = "build"
faas_memory_mb = 1024
faas_timeout_seconds = 60
faas_architecture = "x86_64"
"""
    w = parse_raw(toml).workloads[0]
    assert w.faas_package_type == "zip"
    assert w.faas_runtime == "python3.12"
    assert w.faas_handler == "app.handler"
    assert w.faas_build_command == "pip install -r requirements.txt -t ."
    assert w.faas_output_dir == "build"
    assert w.faas_memory_mb == 1024
    assert w.faas_timeout_seconds == 60
    assert w.faas_architecture == "x86_64"


def test_faas_rejects_containers():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "no containers" in str(exc.value)
    assert exc.value.path == "workloads[0].containers"


def test_faas_image_mode_forbids_handler():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
faas_handler = "app.handler"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "faas_handler" in str(exc.value)


def test_faas_image_mode_forbids_runtime():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
faas_runtime = "python3.12"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    # The runtime/handler forbid block is keyed on the handler path.
    assert "faas_handler" in str(exc.value)


@pytest.mark.parametrize(
    "missing_key, expected_path",
    [
        ("faas_handler", "workloads[0].faas_handler"),
        ("faas_runtime", "workloads[0].faas_runtime"),
        ("faas_output_dir", "workloads[0].faas_output_dir"),
    ],
)
def test_faas_zip_mode_requires_handler_runtime_output(missing_key, expected_path):
    fields = {
        "faas_handler": 'faas_handler = "app.handler"',
        "faas_runtime": 'faas_runtime = "python3.12"',
        "faas_output_dir": 'faas_output_dir = "build"',
    }
    del fields[missing_key]
    body = "\n".join(fields.values())
    toml = f"""
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
faas_package_type = "zip"
{body}
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == expected_path


def test_faas_rejects_unknown_package_type():
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "faas"
faas_package_type = "container"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "faas_package_type" in str(exc.value)


def test_unknown_kind_still_rejected():
    # Regression guard: adding ``faas`` to the valid set must not let a bare
    # ``lambda`` (or any other unknown) kind through.
    toml = """
name = "fn"

[[workloads]]
name = "api"
kind = "lambda"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "kind must be one of" in str(exc.value)


def test_faas_normalize_passthrough_no_defaults():
    raw = RawManifest(
        name="fn",
        workloads=(WorkloadManifest(name="api", kind="faas"),),
    )
    n = normalize(raw)
    w = n.workloads[0]
    # No cpu/mem/healthcheck defaults, no fabricated primary container.
    assert w.cpu_request is None
    assert w.cpu_limit is None
    assert w.memory_request is None
    assert w.containers == ()
    assert n.defaults_applied == ()


def test_faas_serialized_roundtrip_and_hash_reacts():
    base = WorkloadManifest(
        name="api",
        kind="faas",
        faas_package_type="image",
        faas_memory_mb=512,
        faas_public=False,
    )
    n = normalize(RawManifest(name="fn", workloads=(base,)))
    wdict = n.serialized["workloads"][0]
    assert wdict["faas_package_type"] == "image"
    assert wdict["faas_memory_mb"] == 512
    assert wdict["faas_public"] is False

    h_base = manifest_hash(n.serialized)
    # Each faas_* field must move the hash (real change, not no-op deploy).
    for field, value in (
        ("faas_memory_mb", 1024),
        ("faas_timeout_seconds", 60),
        ("faas_architecture", "x86_64"),
        ("faas_public", True),
    ):
        edited = dataclasses.replace(base, **{field: value})
        n2 = normalize(RawManifest(name="fn", workloads=(edited,)))
        assert manifest_hash(n2.serialized) != h_base, field


def test_manifest_sync_flags_faas_field_edit_as_changed():
    # manifest_sync._workload_body_changed must treat a faas_* edit as a body
    # change so an SCM-driven faas-config edit triggers a resync. Guards
    # against a field being dropped from the change-detection tuple.
    from astrolift_registry.services.manifest_sync import _workload_body_changed

    base = WorkloadManifest(
        name="api",
        kind="faas",
        faas_package_type="zip",
        faas_runtime="python3.12",
        faas_handler="app.handler",
        faas_memory_mb=512,
        faas_timeout_seconds=30,
        faas_architecture="arm64",
        faas_public=True,
        faas_build_command="make build",
        faas_output_dir="dist",
    )
    assert _workload_body_changed(base, base) is False
    for field, value in (
        ("faas_package_type", "image"),
        ("faas_runtime", "python3.13"),
        ("faas_handler", "main.handler"),
        ("faas_memory_mb", 1024),
        ("faas_timeout_seconds", 60),
        ("faas_architecture", "x86_64"),
        ("faas_public", False),
        ("faas_build_command", "make release"),
        ("faas_output_dir", "build"),
    ):
        edited = dataclasses.replace(base, **{field: value})
        assert _workload_body_changed(base, edited) is True, field


def test_existing_deployment_kind_carries_faas_defaults_harmlessly():
    # Regression guard: a normal deployment still gets resource defaults and an
    # is_primary container promotion, and carries the faas_* defaults harmlessly.
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
    assert w.faas_package_type == "image"
    assert w.faas_memory_mb == 512
    n = normalize(raw)
    nw = n.workloads[0]
    assert nw.cpu_request == "100m"
    assert nw.containers[0].is_primary is True
