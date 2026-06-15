"""Tests for the agent runtime catalog and its dispatcher resolution.

The catalog maps a runtime short-name to the public Docker Hub image
``docker.io/calliopeai/astrolift-agent-<name>``. These exercise:

  * name -> public image resolution (default + env-var registry override)
  * the spawner image precedence: explicit image_tag > runtime > workload
  * runtime composes with the -vnc watchable variant
  * an unknown runtime fails loudly

The catalog functions are pure; the ``_render_agent_job`` cases reuse the
same lightweight fakes the VNC spawner tests use (no DB, no cluster).
"""

from __future__ import annotations

import pytest

from astrolift_agents import runtime_catalog as rc
from astrolift_dispatch.spawners.k8s_job import _render_agent_job, _resolve_base_image

# ---- pure catalog resolution ----------------------------------------


def test_all_published_runtimes_are_catalogued():
    # The 12 runtimes published to Docker Hub (plus the shared base).
    expected = {
        "claude",
        "codex",
        "opencode",
        "mistral",
        "deepseek",
        "claude-code",
        "codex-cli",
        "aider",
        "goose",
        "openhands",
        "calliope-cli",
        "base",
    }
    assert set(rc.RUNTIME_NAMES) == expected


@pytest.mark.parametrize(
    "name,expected_repo",
    [
        ("claude", "docker.io/calliopeai/astrolift-agent-claude"),
        ("aider", "docker.io/calliopeai/astrolift-agent-aider"),
        ("calliope-cli", "docker.io/calliopeai/astrolift-agent-calliope-cli"),
        ("base", "docker.io/calliopeai/astrolift-agent-base"),
    ],
)
def test_runtime_repo_resolves_to_public_namespace(name, expected_repo):
    assert rc.runtime_repo(name) == expected_repo


def test_resolve_runtime_image_appends_default_tag():
    assert rc.resolve_runtime_image("claude") == "docker.io/calliopeai/astrolift-agent-claude:latest"


def test_resolve_runtime_image_honours_explicit_tag():
    assert rc.resolve_runtime_image("codex", tag="1.4") == "docker.io/calliopeai/astrolift-agent-codex:1.4"


def test_unknown_runtime_raises():
    assert not rc.is_known_runtime("not-a-real-runtime")
    with pytest.raises(KeyError):
        rc.runtime_repo("not-a-real-runtime")


def test_registry_namespace_env_override(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_AGENT_REGISTRY", "ghcr.io/mirror/")
    assert rc.registry_namespace() == "ghcr.io/mirror"
    assert rc.resolve_runtime_image("claude") == "ghcr.io/mirror/astrolift-agent-claude:latest"


def test_default_tag_env_override(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_AGENT_RUNTIME_TAG", "stable")
    assert rc.default_tag() == "stable"
    assert rc.resolve_runtime_image("goose") == "docker.io/calliopeai/astrolift-agent-goose:stable"


def test_catalog_entries_list_name_and_image():
    entries = rc.catalog_entries()
    assert len(entries) == len(rc.RUNTIME_NAMES)
    by_name = {e["name"]: e["image"] for e in entries}
    assert by_name["claude"] == "docker.io/calliopeai/astrolift-agent-claude:latest"
    # Every entry carries a fully-qualified, default-tagged base image.
    assert all(":" in e["image"] and e["image"].startswith("docker.io/") for e in entries)


# ---- spawner image precedence ---------------------------------------
#
# Lightweight fakes mirror test_k8s_job_vnc.py: a spec exposes image_tag +
# runtime; a workload exposes a primary container image.


class _FakeQS:
    def __init__(self, container):
        self._container = container

    def filter(self, **_kwargs):
        return self

    def first(self):
        return self._container


class _FakeContainer:
    def __init__(self, image_ref: str, port: int = 0):
        self.image_ref = image_ref
        self.port = port


class _FakeWorkload:
    def __init__(self, image_ref: str, port: int = 0):
        self.container_set = _FakeQS(_FakeContainer(image_ref, port))


class _FakeSpec:
    def __init__(self, *, image_tag: str = "", runtime: str = ""):
        self.image_tag = image_tag
        self.runtime = runtime


class _FakeTask:
    def __init__(self, *, guid: str, vnc_enabled: bool, environment_spec=None):
        self.guid = guid
        self.vnc_enabled = vnc_enabled
        self.environment_spec = environment_spec


def _container_of(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_resolve_base_image_uses_runtime_when_no_image_tag():
    workload = _FakeWorkload("ghcr.io/old/ignored:0")
    spec = _FakeSpec(runtime="claude")
    assert _resolve_base_image(workload, spec) == "docker.io/calliopeai/astrolift-agent-claude:latest"


def test_resolve_base_image_explicit_image_tag_wins_over_runtime():
    # A pinned private image must override the catalog runtime.
    workload = _FakeWorkload("ghcr.io/old/ignored:0")
    spec = _FakeSpec(image_tag="123456.dkr.ecr.us-west-2.amazonaws.com/agent:pin", runtime="claude")
    assert _resolve_base_image(workload, spec) == "123456.dkr.ecr.us-west-2.amazonaws.com/agent:pin"


def test_resolve_base_image_falls_back_to_workload_when_no_spec_image():
    workload = _FakeWorkload("ghcr.io/calliopeai/astrolift-agent-codex:9")
    spec = _FakeSpec()  # neither image_tag nor runtime
    assert _resolve_base_image(workload, spec) == "ghcr.io/calliopeai/astrolift-agent-codex:9"


def test_resolve_base_image_unknown_runtime_falls_back_to_workload():
    # An unknown runtime name shouldn't crash the spawn — fall through to the
    # workload image rather than minting a nonexistent catalog ref.
    workload = _FakeWorkload("ghcr.io/calliopeai/astrolift-agent-codex:9")
    spec = _FakeSpec(runtime="bogus-runtime")
    assert _resolve_base_image(workload, spec) == "ghcr.io/calliopeai/astrolift-agent-codex:9"


def test_render_job_runtime_composes_with_vnc_variant():
    # runtime selected + vnc enabled -> catalog image swapped to its -vnc
    # variant, raw RFB port exposed.
    workload = _FakeWorkload("ghcr.io/old/ignored:0", 8080)
    spec = _FakeSpec(runtime="claude")
    task = _FakeTask(guid="t-cat-1", vnc_enabled=True, environment_spec=spec)

    manifest = _render_agent_job(
        job_name="agent-task-cat1",
        workload=workload,
        namespace="ns",
        task=task,
    )
    container = _container_of(manifest)
    assert container["image"] == "docker.io/calliopeai/astrolift-agent-claude-vnc:latest"
    ports = [p["containerPort"] for p in container["ports"]]
    assert 5900 in ports


def test_render_job_runtime_no_vnc_uses_plain_catalog_image():
    workload = _FakeWorkload("ghcr.io/old/ignored:0", 0)
    spec = _FakeSpec(runtime="aider")
    task = _FakeTask(guid="t-cat-2", vnc_enabled=False, environment_spec=spec)

    manifest = _render_agent_job(
        job_name="agent-task-cat2",
        workload=workload,
        namespace="ns",
        task=task,
    )
    container = _container_of(manifest)
    assert container["image"] == "docker.io/calliopeai/astrolift-agent-aider:latest"
    # No app port, no vnc -> no ports block.
    assert "ports" not in container
