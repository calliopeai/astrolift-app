"""Substrate spawn backend, spike #1853.

The transport is faked; the calls it records are the kubectl-ate commands the
spike ran against a kind cluster.
"""

import base64
import gzip
import ipaddress
import json
import pathlib
from types import SimpleNamespace

import pytest

from astrolift_dispatch.spawners import substrate
from astrolift_dispatch.spawners.substrate import (
    SubstrateSpawner,
    public_ipv4_allowlist,
    render_actor_template,
)

IMAGE = "ghcr.io/calliopeai/astrolift-agent-base@sha256:" + "a" * 64
GUID = "019f2065-6789-7001-8000-000000000001"
CONFIG = {
    "context": "kind-kind",
    "storage_location": "gs://ate-snapshots/astrolift/",
    "sandbox_config": "gvisor-default",
    "worker_labels": {"workload": "astrolift-agent-box"},
    "cpu": "4",
    "memory": "8Gi",
    "allow_cidrs": "",
}


def _render(**overrides):
    kwargs = {
        "atespace": "astro-agents",
        "image": IMAGE,
        "env": {},
        "cpu": "4",
        "memory": "8Gi",
        "storage_location": CONFIG["storage_location"],
        "sandbox_config": "gvisor-default",
        "worker_labels": {"workload": "astrolift-agent-box"},
    }
    kwargs.update(overrides)
    return render_actor_template(**kwargs)


class FakeAte:
    """Records kubectl-ate calls; ``objects`` holds what 'exists'."""

    def __init__(self, objects=None, states=None):
        self.calls = []
        self.objects = set(objects or ())
        self.states = states or {}

    def __call__(self, *args, stdin=None):
        self.calls.append((args, stdin))
        verb, kind = args[0], args[1]
        if verb == "get":
            key = (kind, args[2])
            if key not in self.objects:
                return None
            return {"status": {"state": self.states.get(args[2], "ACTOR_STATE_SUSPENDED")}}
        if verb == "create":
            name = json.loads(stdin)["metadata"]["name"] if kind == "actor-template" else args[2]
            self.objects.add((kind, name))
        return {}


def _task(**spec):
    return SimpleNamespace(
        guid=GUID,
        agent_definition=SimpleNamespace(slug="claude"),
        environment_spec=SimpleNamespace(pk=None, env_vars={"MODE": "box"}, secret_refs=[], **spec),
    )


@pytest.fixture
def image(monkeypatch):
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.k8s_job._resolve_base_image", lambda workload, spec: IMAGE
    )


def test_template_is_digest_pinned_sized_and_within_substrate_bounds():
    template = _render()
    container = template["containers"][0]
    assert all(len(arg) <= substrate.ARGV_MAX_CHARS for arg in container["command"])
    assert len(container["env"]) <= substrate.ENV_MAX_ITEMS
    assert template["resources"]["limits"] == [
        {"name": "cpu", "quantity": "4"},
        {"name": "memory", "quantity": "8Gi"},
    ]
    assert template["volumes"] == [{"name": "workspace", "durableDir": {}}]
    assert container["wakeupProbe"] == {"httpGet": {"path": "/readyz", "port": 80}}
    with pytest.raises(ValueError, match="digest-pinned"):
        _render(image="ghcr.io/calliopeai/astrolift-agent-base:latest")


def test_template_name_is_content_addressed():
    assert _render()["metadata"]["name"] == _render()["metadata"]["name"]
    assert _render()["metadata"]["name"] != _render(env={"MODE": "other"})["metadata"]["name"]


def test_box_never_reaps_itself_under_substrate():
    """Snapshot-carried tmux activity plus a wall clock that jumps on restore
    made a 60 s box reap itself within one poll of resuming; idle is the
    spawner's suspend decision instead."""
    keepalive = gzip.decompress(base64.b64decode(_render()["containers"][0]["command"][5])).decode()
    assert "IDLE_TIMEOUT=0" in keepalive
    assert "tmux new-session -d -s astrolift -c /workspace" in keepalive


def test_egress_allowlist_is_the_fence_complement():
    allowed = [ipaddress.ip_network(cidr) for cidr in public_ipv4_allowlist()]

    def reachable(ip):
        return any(ipaddress.ip_address(ip) in net for net in allowed)

    for internal in ("10.96.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "127.0.0.1"):
        assert not reachable(internal), internal
    for public in ("140.82.114.4", "8.8.8.8", "1.1.1.1", "34.36.57.103"):
        assert reachable(public), public


def test_spawn_creates_atespace_template_actor_and_egress_policy(image):
    ate = FakeAte()
    result = SubstrateSpawner(namespace="agents", transport=ate, config=dict(CONFIG)).spawn(_task())

    assert result.ok, result.error
    assert result.external_id == "astro-agents/agent-task-019f2065678970018000000000000001"
    creates = [args[:2] for args, _ in ate.calls if args[0] == "create"]
    assert creates == [
        ("create", "atespace"),
        ("create", "actor-template"),
        ("create", "actor"),
        ("create", "egress-policy"),
    ]
    template = json.loads(
        next(stdin for args, stdin in ate.calls if args[:2] == ("create", "actor-template"))
    )
    assert {"name": "MODE", "value": "box"} in template["containers"][0]["env"]
    # No resume on spawn: the first attach resumes the box.
    assert not any(args[0] == "resume" for args, _ in ate.calls)


def test_spawn_is_idempotent(image):
    ate = FakeAte()
    spawner = SubstrateSpawner(namespace="agents", transport=ate, config=dict(CONFIG))
    first = spawner.spawn(_task())
    ate.calls.clear()
    again = spawner.spawn(_task())
    assert again.external_id == first.external_id
    assert not [args for args, _ in ate.calls if args[0] == "create"]


def test_spawn_refuses_non_root_and_secrets(image):
    ate = FakeAte()
    spawner = SubstrateSpawner(namespace="agents", transport=ate, config=dict(CONFIG))
    non_root = spawner.spawn(_task(run_as_non_root=True))
    assert not non_root.ok and "non-root" in non_root.error
    task = _task()
    task.environment_spec.secret_refs = [{"env_var": "GITHUB_TOKEN", "uri": "secret://gh"}]
    secrets = spawner.spawn(task)
    assert not secrets.ok and "secrets" in secrets.error
    assert ate.calls == []


def test_status_keeps_suspended_boxes_alive():
    ate = FakeAte(
        objects={("actor", "a"), ("actor", "b"), ("actor", "c")},
        states={"a": "ACTOR_STATE_SUSPENDED", "b": "ACTOR_STATE_RUNNING", "c": "ACTOR_STATE_CRASHED"},
    )
    spawner = SubstrateSpawner(transport=ate, config=dict(CONFIG))
    assert spawner.status("astro-agents/a").running
    assert spawner.status("astro-agents/b").running
    assert spawner.status("astro-agents/c").failed
    assert spawner.status("astro-agents/gone").failed


def test_stop_refuses_another_tasks_actor():
    spawner = SubstrateSpawner(transport=FakeAte(), config=dict(CONFIG))
    with pytest.raises(RuntimeError, match="does not belong"):
        spawner.stop("astro-agents/agent-task-deadbeef", expected_task_guid=GUID)


def test_attach_goes_through_the_router_not_a_pod_exec():
    target = SubstrateSpawner.attach_target("astro-agents/agent-task-1")
    assert target["header"] == "ate-target-actor"
    assert target["value"] == "astro-agents/agent-task-1"


def test_registry_keeps_substrate_opt_in(settings):
    from astrolift_dispatch.spawners.registry import get_spawner

    settings.SUBSTRATE_SPAWNER_ENABLED = False
    with pytest.raises(ValueError, match="spike"):
        get_spawner("substrate", namespace="agents")
    settings.SUBSTRATE_SPAWNER_ENABLED = True
    assert isinstance(get_spawner("substrate", namespace="agents"), SubstrateSpawner)


def test_nothing_else_imports_the_substrate_backend():
    backend = pathlib.Path(__file__).resolve().parents[2]
    allowed = {
        backend / "astrolift_dispatch" / "spawners" / "registry.py",
        backend / "astrolift_dispatch" / "spawners" / "substrate.py",
        pathlib.Path(__file__).resolve(),
    }
    offenders = [
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if path.resolve() not in allowed
        and ".venv" not in path.parts
        and (
            "spawners.substrate" in (text := path.read_text(errors="ignore"))
            or "substrate_attach_shim" in text
        )
    ]
    assert offenders == []
