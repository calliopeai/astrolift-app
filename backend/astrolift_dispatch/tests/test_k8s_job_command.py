"""Tests for command/args passthrough in the K8s Job spawner.

``_render_agent_job`` honours the primary container's ``command`` /
``args`` (both ``JSONField(default=list)``). The contract:

  empty command/args  -> keys omitted so the image ENTRYPOINT/CMD runs
  non-empty command   -> set on the Job container's ``command`` (ENTRYPOINT)
  non-empty args       -> set on the Job container's ``args`` (CMD)

The function resolves the primary container via
``workload.containers.filter(is_primary=True).first()``, so the fake
workload exposes a ``containers`` manager with that shape.
"""

from __future__ import annotations

from astrolift_dispatch.spawners.k8s_job import _render_agent_job


class _FakeQS:
    def __init__(self, container):
        self._container = container

    def filter(self, **_kwargs):
        return self

    def first(self):
        return self._container


class _FakeContainer:
    def __init__(self, *, image_ref="img:1", port=0, command=None, args=None):
        self.image_ref = image_ref
        self.port = port
        self.command = command if command is not None else []
        self.args = args if args is not None else []


class _FakeWorkload:
    def __init__(self, container):
        self.containers = _FakeQS(container)


class _FakeTask:
    def __init__(self, *, guid="t-1", vnc_enabled=False, environment_spec=None):
        self.guid = guid
        self.vnc_enabled = vnc_enabled
        self.environment_spec = environment_spec


def _container_of(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def _render(container) -> dict:
    return _container_of(
        _render_agent_job(
            job_name="agent-task-t1",
            workload=_FakeWorkload(container),
            namespace="astrolift-agents-acme",
            task=_FakeTask(),
        )
    )


def test_empty_command_and_args_omits_keys():
    """An unconfigured container leaves the image ENTRYPOINT/CMD intact —
    setting the keys to [] would override the image, so they must be
    absent entirely."""
    rendered = _render(_FakeContainer(command=[], args=[]))
    assert "command" not in rendered
    assert "args" not in rendered


def test_command_set_when_non_empty():
    cmd = ["/bin/sh", "-c", "echo hi; exit 0"]
    rendered = _render(_FakeContainer(command=cmd))
    assert rendered["command"] == cmd
    # args still omitted (empty) so the image CMD isn't blanked.
    assert "args" not in rendered


def test_args_set_when_non_empty():
    rendered = _render(_FakeContainer(args=["--flag", "value"]))
    assert rendered["args"] == ["--flag", "value"]
    assert "command" not in rendered


def test_command_and_args_both_set():
    rendered = _render(
        _FakeContainer(command=["python", "main.py"], args=["--serve"])
    )
    assert rendered["command"] == ["python", "main.py"]
    assert rendered["args"] == ["--serve"]


def test_command_args_are_copied_not_aliased():
    """The rendered manifest must not alias the model's list objects —
    a later mutation of the container row shouldn't retroactively edit a
    rendered manifest (and vice-versa)."""
    container = _FakeContainer(command=["a"], args=["b"])
    rendered = _render(container)
    rendered["command"].append("MUTATED")
    rendered["args"].append("MUTATED")
    assert container.command == ["a"]
    assert container.args == ["b"]


def test_agent_pod_labeled_with_app_equal_task_guid():
    """#1013: the pod template carries astrolift.dev/app=<task guid> so the
    platform log surface (list_pods selects astrolift.dev/app) can discover an
    agent task's pod — without it, agentTaskLogs returned []."""
    job = _render_agent_job(
        job_name="agent-task-t1",
        workload=_FakeWorkload(_FakeContainer(image_ref="busybox:latest")),
        namespace="astrolift-agents-acme",
        task=_FakeTask(guid="abc-123"),
    )
    pod_labels = job["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["astrolift.dev/app"] == "abc-123"
    assert pod_labels["astrolift.dev/task-id"] == "abc-123"
