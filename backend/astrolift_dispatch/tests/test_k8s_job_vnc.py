"""Tests for VNC image-variant selection in the K8s Job spawner (#877).

``_render_agent_job`` and ``_vnc_image`` are pure (no cluster, no DB), so
these exercise them directly with lightweight stand-ins. The contract:

  vnc disabled -> base image untouched, only the app port (if any) exposed
  vnc enabled  -> -vnc image variant + containerPort 5900 (raw RFB) exposed
"""

from __future__ import annotations

import pytest

from astrolift_dispatch.spawners.k8s_job import (
    VNC_PORT,
    _render_agent_job,
    _vnc_image,
)


class _FakeQS:
    def __init__(self, container):
        self._container = container

    def filter(self, **_kwargs):
        return self

    def first(self):
        return self._container


class _FakeContainer:
    def __init__(self, image_ref: str, port: int):
        self.image_ref = image_ref
        self.port = port


class _FakeWorkload:
    def __init__(self, image_ref: str, port: int):
        self.container_set = _FakeQS(_FakeContainer(image_ref, port))


class _FakeSpec:
    def __init__(self, image_tag: str):
        self.image_tag = image_tag


class _FakeTask:
    def __init__(self, *, guid: str, vnc_enabled: bool, environment_spec=None):
        self.guid = guid
        self.vnc_enabled = vnc_enabled
        self.environment_spec = environment_spec


def _container_of(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


# ---- _vnc_image ------------------------------------------------------


@pytest.mark.parametrize(
    "base,expected",
    [
        (
            "ghcr.io/calliopeai/astrolift-agent-claude:1.2",
            "ghcr.io/calliopeai/astrolift-agent-claude-vnc:1.2",
        ),
        (
            "ghcr.io/calliopeai/astrolift-agent-codex:sha-abc",
            "ghcr.io/calliopeai/astrolift-agent-codex-vnc:sha-abc",
        ),
        # No tag -> still appends -vnc to the repo.
        (
            "ghcr.io/calliopeai/astrolift-agent-claude",
            "ghcr.io/calliopeai/astrolift-agent-claude-vnc",
        ),
        # Registry host:port must not be mistaken for a tag separator.
        (
            "registry.local:5000/agent:v3",
            "registry.local:5000/agent-vnc:v3",
        ),
        # Already a -vnc image is returned unchanged (idempotent).
        (
            "ghcr.io/calliopeai/astrolift-agent-claude-vnc:1.2",
            "ghcr.io/calliopeai/astrolift-agent-claude-vnc:1.2",
        ),
        # Digest-pinned ref: the digest's internal ":" must survive — the
        # "-vnc" is appended to the repo, NOT inside the @sha256:... digest.
        (
            "ghcr.io/calliopeai/agent@sha256:abcd1234ef567890",
            "ghcr.io/calliopeai/agent-vnc@sha256:abcd1234ef567890",
        ),
        # Digest-pinned ref that is already -vnc is returned unchanged.
        (
            "ghcr.io/calliopeai/agent-vnc@sha256:abcd1234ef567890",
            "ghcr.io/calliopeai/agent-vnc@sha256:abcd1234ef567890",
        ),
    ],
)
def test_vnc_image_variant(base, expected):
    assert _vnc_image(base) == expected


# ---- _render_agent_job ----------------------------------------------


def test_render_vnc_disabled_leaves_image_and_app_port():
    workload = _FakeWorkload("ghcr.io/calliopeai/astrolift-agent-claude:1.2", 8080)
    task = _FakeTask(guid="t-1", vnc_enabled=False)

    manifest = _render_agent_job(
        job_name="agent-task-t1",
        workload=workload,
        namespace="astrolift-agents-acme",
        task=task,
    )
    container = _container_of(manifest)

    assert container["image"] == "ghcr.io/calliopeai/astrolift-agent-claude:1.2"
    assert container["ports"] == [{"containerPort": 8080}]


def test_render_vnc_disabled_no_app_port_omits_ports():
    workload = _FakeWorkload("ghcr.io/calliopeai/astrolift-agent-claude:1.2", 0)
    task = _FakeTask(guid="t-1", vnc_enabled=False)

    manifest = _render_agent_job(
        job_name="agent-task-t1",
        workload=workload,
        namespace="ns",
        task=task,
    )
    assert "ports" not in _container_of(manifest)


def test_render_vnc_enabled_uses_spec_image_variant_and_raw_rfb_port():
    # Spec image_tag is the authoritative ECR/GHCR URI; the spawner must
    # consult it and swap to the -vnc variant when vnc is enabled.
    workload = _FakeWorkload("ghcr.io/old/ignored:0", 8080)
    spec = _FakeSpec("ghcr.io/calliopeai/astrolift-agent-claude:1.2")
    task = _FakeTask(guid="t-2", vnc_enabled=True, environment_spec=spec)

    manifest = _render_agent_job(
        job_name="agent-task-t2",
        workload=workload,
        namespace="ns",
        task=task,
    )
    container = _container_of(manifest)

    assert container["image"] == "ghcr.io/calliopeai/astrolift-agent-claude-vnc:1.2"
    ports = [p["containerPort"] for p in container["ports"]]
    # The pod serves raw RFB on 5900 (x11vnc) — no noVNC/websockify in the
    # pod, so the exposed VNC port must be 5900, not the old noVNC 6080.
    assert VNC_PORT == 5900
    assert 5900 in ports
    # The original app port is preserved alongside the VNC port.
    assert 8080 in ports
    # VNC port is not duplicated.
    assert ports.count(VNC_PORT) == 1


def test_render_vnc_enabled_falls_back_to_workload_image_when_spec_has_no_tag():
    # vnc_enabled with no usable spec image_tag -> derive -vnc from the
    # workload's primary container image.
    workload = _FakeWorkload("ghcr.io/calliopeai/astrolift-agent-codex:9", 0)
    task = _FakeTask(guid="t-3", vnc_enabled=True, environment_spec=_FakeSpec(""))

    manifest = _render_agent_job(
        job_name="agent-task-t3",
        workload=workload,
        namespace="ns",
        task=task,
    )
    container = _container_of(manifest)

    assert container["image"] == "ghcr.io/calliopeai/astrolift-agent-codex-vnc:9"
    assert container["ports"] == [{"containerPort": VNC_PORT}]
