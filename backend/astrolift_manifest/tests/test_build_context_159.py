"""The build context a new registration starts with (astrolift-cli #159).

A manifest in a sub-directory builds from that directory, unless the manifest
names its own build_context / dockerfile_path, which then apply as declared.
Pure: no database.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.build_context import (
    detected_build_context,
    manifest_directory,
    sets_build_paths,
)
from astrolift_manifest.discover import scan_app_manifests


@pytest.mark.parametrize(
    ("manifest_path", "expected"),
    [
        ("src/abbott-cgm/astrolift.toml", "src/abbott-cgm"),
        ("apps/web/astrolift.toml", "apps/web"),
        ("./services/api/astrolift.toml", "services/api"),
        ("services//api/astrolift.toml", "services/api"),
        ("astrolift.toml", "."),
        ("", "."),
        # Nothing a build could use: the repository root instead.
        ("../outside/astrolift.toml", "."),
        ("src/../../outside/astrolift.toml", "."),
        ("/etc/astrolift.toml", "."),
    ],
)
def test_manifest_directory(manifest_path, expected):
    assert manifest_directory(manifest_path) == expected


@pytest.mark.parametrize(
    ("dockerfile_path", "build_context", "expected"),
    [
        ("Dockerfile", ".", False),
        ("", "", False),
        ("Dockerfile.web", ".", True),
        ("Dockerfile", "../..", True),
        ("docker/Dockerfile", "sub", True),
    ],
)
def test_sets_build_paths(dockerfile_path, build_context, expected):
    assert sets_build_paths(dockerfile_path, build_context) is expected


def test_detected_build_context_defers_to_a_manifest_with_its_own_paths():
    path = "src/abbott-cgm/astrolift.toml"
    assert detected_build_context(path, manifest_sets_build_paths=False) == "src/abbott-cgm"
    assert detected_build_context(path, manifest_sets_build_paths=True) == "."


def _app_toml(name: str, container_extra: str = "") -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        'kind = "deployment"\n'
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        "port = 8080\n" + container_extra
    )


def test_scan_keeps_the_root_context_for_a_manifest_with_its_own_build_paths():
    found = {
        d.manifest_path: d.build_context
        for d in scan_app_manifests(
            {
                "apps/web/astrolift.toml": _app_toml("web"),
                "apps/api/astrolift.toml": _app_toml(
                    "api", 'build_context = "../.."\ndockerfile_path = "Dockerfile.api"\n'
                ),
                "apps/docs/astrolift.toml": _app_toml("docs", 'build_context = "."\n'),
            }
        )
    }
    assert found == {
        "apps/api/astrolift.toml": ".",
        # "." and "Dockerfile" are the defaults: nothing of its own.
        "apps/docs/astrolift.toml": "apps/docs",
        "apps/web/astrolift.toml": "apps/web",
    }


def test_scan_reads_build_paths_a_job_names():
    job = (
        'name = "cron"\n[[jobs]]\nname = "nightly"\nschedule = "0 3 * * *"\ndockerfile = "Dockerfile.jobs"\n'
    )
    [found] = scan_app_manifests({"apps/cron/astrolift.toml": job})
    assert found.build_context == "."
