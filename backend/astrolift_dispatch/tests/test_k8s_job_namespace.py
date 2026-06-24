"""The K8s Job spawner must ensure the per-org agent namespace exists before
creating the Job (#983 agents-grid).

Unlike the app deploy path there's no separate provision_namespace step, so a
first-ever agent dispatch would 404 on the Job POST. ``spawn()`` calls the
cluster driver's idempotent ``ensure_namespace`` first; this pins that order.
"""

from __future__ import annotations

import pytest

from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner


class _ApplyResult:
    ok = True

    def summary(self):  # pragma: no cover - not reached when ok
        return "ok"


class _RecordingDriver:
    def __init__(self):
        self.calls: list[str] = []

    def ensure_namespace(self, cluster, name, labels, annotations):
        self.calls.append(f"ensure_namespace:{name}")
        return None

    def apply_manifests(self, cluster, namespace, manifests):
        self.calls.append(f"apply_manifests:{namespace}")
        return _ApplyResult()


class _Ctx:
    slug = "astrolift-eks"


class _PrimaryQS:
    def __init__(self, container):
        self._c = container

    def filter(self, **_):
        return self

    def first(self):
        return self._c


class _Container:
    image_ref = "docker.io/library/busybox:latest"
    port = 0
    command = []
    args = []


class _Workload:
    containers = _PrimaryQS(_Container())


class _Task:
    guid = "abc12345-0000-0000-0000-000000000000"
    vnc_enabled = False
    environment_spec = None
    agent_definition = _Workload()


def test_spawn_ensures_namespace_before_applying_job(monkeypatch):
    driver = _RecordingDriver()
    # spawn() does `from core.cluster_management import ...` at call time, so
    # patch the source module, not the spawner module.
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    # The brief/snapshot injectors are import-time-resolved inside spawn(); stub
    # them to identity so the test isolates the namespace-then-apply ordering.
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector

    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")
    result = spawner.spawn(_Task())

    assert result.ok, result.error
    # ensure_namespace must run, and must precede apply_manifests.
    assert "ensure_namespace:astrolift-agents-steadymd" in driver.calls
    assert "apply_manifests:astrolift-agents-steadymd" in driver.calls
    assert driver.calls.index("ensure_namespace:astrolift-agents-steadymd") < driver.calls.index(
        "apply_manifests:astrolift-agents-steadymd"
    )
