"""A manifest declaring ``[workspace]`` ships a payload (#1847).

The runner reads ``[workspace]`` from ``astrolift.toml`` inside the payload.
Before this, only ``[package]`` produced a payload, so a workspace-only
manifest's repos and dependencies were silently never set up.
"""

from __future__ import annotations

import pytest

from astrolift_agents.services.agent_package import manifest_needs_payload
from astrolift_manifest.discover import DiscoveredAgentManifest
from astrolift_registry.services.manifest_sync import _needs_package_archive

_BASE = (
    'name = "triage"\n'
    "[[workloads]]\n"
    'name = "triage"\n'
    'kind = "agent"\n'
    "[[workloads.containers]]\n"
    'name = "triage"\n'
    "is_primary = true\n"
    'image_ref = "acme/agent:1"\n'
)
_WORKSPACE = '\n[[workspace.repos]]\nurl = "https://github.com/acme/tools"\nref = "v1"\npath = "tools"\n'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, False),
        ({"package": {}}, True),
        ({"workspace": {"repos": []}}, True),
        ({"environment": {}}, False),
    ],
)
def test_manifest_needs_payload(raw, expected):
    assert manifest_needs_payload(raw) is expected


def _discovered(text):
    return DiscoveredAgentManifest(
        manifest_path="agents/triage/astrolift.toml",
        name="triage",
        slug="triage",
        workload_kind="agent",
        raw_text=text,
    )


def test_a_workspace_only_manifest_needs_the_archive():
    assert _needs_package_archive([_discovered(_BASE + _WORKSPACE)]) is True


def test_a_plain_manifest_still_needs_no_archive():
    assert _needs_package_archive([_discovered(_BASE)]) is False
