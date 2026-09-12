"""A dispatch with no resolvable image is refused, not spawned (#1698).

``_resolve_base_image`` used to fall back to ``gcr.io/distroless/base``
when nothing else resolved -- an image with no interpreter. The Job was
created, the pod died in seconds with

    exec: "python": executable file not found in $PATH

and the task was marked failed with nothing pointing at the missing
image. ``astro agent logs`` returned nothing, because the container
never started, so the CLI offered no route to the reason either. All
four agents on one install were in that state.

A placeholder base image is never a useful default for a workload whose
command the operator supplies: the two can only agree by accident.
"""

from __future__ import annotations

from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _resolve_base_image


class _FakeQS:
    def __init__(self, container):
        self._container = container

    def filter(self, **_kwargs):
        return self

    def first(self):
        return self._container


class _FakeContainer:
    def __init__(self, image_ref=""):
        self.image_ref = image_ref
        self.port = 0
        self.command = []
        self.args = []


class _FakeWorkload:
    def __init__(self, container, slug="triage"):
        self.containers = _FakeQS(container)
        self.slug = slug


class _FakeSpec:
    def __init__(self, image_tag="", runtime=""):
        self.image_tag = image_tag
        self.runtime = runtime


class _FakeTask:
    def __init__(self, workload, spec=None):
        self.guid = "t-1698"
        self.agent_definition = workload
        self.environment_spec = spec
        self.vnc_enabled = False


# ---- the resolver is honest about failing -----------------------------


def test_nothing_resolvable_returns_empty_not_a_placeholder():
    assert _resolve_base_image(_FakeWorkload(_FakeContainer("")), None) == ""


def test_a_container_image_still_resolves():
    assert _resolve_base_image(_FakeWorkload(_FakeContainer("acme/agent:1")), None) == "acme/agent:1"


def test_a_pinned_spec_image_wins():
    workload = _FakeWorkload(_FakeContainer("acme/agent:1"))
    assert _resolve_base_image(workload, _FakeSpec(image_tag="ecr/agent:2")) == "ecr/agent:2"


def test_a_workload_with_no_container_resolves_nothing():
    assert _resolve_base_image(_FakeWorkload(None), None) == ""


# ---- the spawn refuses before creating anything -----------------------


def test_spawn_refuses_and_names_the_agent_and_the_fix():
    spawner = K8sJobSpawner(cluster=object(), namespace="agents")
    task = _FakeTask(_FakeWorkload(_FakeContainer(""), slug="triage"))

    result = spawner.spawn(task)

    assert result.ok is False
    assert result.external_id == ""
    assert "triage" in result.error
    # The message has to carry the way out, not just the fact.
    assert "--image-tag" in result.error


def test_the_refusal_creates_no_cluster_objects():
    """Refusing after the driver is resolved would leave a Job or a
    per-task Secret behind; this returns before any of that."""

    spawner = K8sJobSpawner(cluster=object(), namespace="agents")
    task = _FakeTask(_FakeWorkload(_FakeContainer("")))

    assert spawner.spawn(task).ok is False
    # Nothing was registered for cleanup, because nothing was applied.
    assert spawner._spawn_cleanup_refs == {}
