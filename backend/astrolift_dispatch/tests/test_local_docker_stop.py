from types import SimpleNamespace

import pytest

from astrolift_dispatch.spawners.local_docker import LocalDockerSpawner


def test_local_stop_surfaces_failed_container_deletion(monkeypatch):
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.local_docker.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="permission denied"),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        LocalDockerSpawner().stop("agent-task-123")


def test_local_stop_accepts_confirmed_container_deletion(monkeypatch):
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.local_docker.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    LocalDockerSpawner().stop("agent-task-123")


def test_parallel_tasks_keep_distinct_deterministic_container_names(monkeypatch):
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="container-id\n", stderr="")

    monkeypatch.setattr("astrolift_dispatch.spawners.local_docker.subprocess.run", run)
    monkeypatch.setattr(brief_injector, "brief_env_vars", lambda task: [])
    monkeypatch.setattr(snapshot_injector, "snapshot_env_vars", lambda task: [])
    first = SimpleNamespace(guid="019f2065-6789-7001-8000-000000000001", agent_definition=None)
    second = SimpleNamespace(guid="019f2065-6789-7002-8000-000000000002", agent_definition=None)
    spawner = LocalDockerSpawner()
    a, b, retry = spawner.spawn(first), spawner.spawn(second), spawner.spawn(first)
    assert a.ok and b.ok and retry.ok
    assert a.external_id != b.external_id
    assert retry.external_id == a.external_id
    assert all(len(result.external_id) <= 63 for result in (a, b, retry))
    assert [command[command.index("--name") + 1] for command in commands] == [
        a.external_id,
        b.external_id,
        a.external_id,
    ]


def test_owned_docker_stop_checks_label_and_deletes_exact_container_id(monkeypatch):
    commands = []
    container_id = "a" * 64

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=f"{container_id} task-guid", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("astrolift_dispatch.spawners.local_docker.subprocess.run", run)
    LocalDockerSpawner().stop("task-container", expected_task_guid="task-guid")
    assert commands[-1] == ["docker", "rm", "-f", container_id]


def test_owned_docker_stop_refuses_another_tasks_container(monkeypatch):
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.local_docker.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="container-id other-task", stderr=""),
    )
    with pytest.raises(RuntimeError, match="different agent task"):
        LocalDockerSpawner().stop("task-container", expected_task_guid="task-guid")


@pytest.mark.parametrize(
    "error,missing",
    [
        ("Error: No such object: task-container", True),
        ("Cannot connect to the Docker daemon", False),
    ],
)
def test_docker_confirmation_distinguishes_missing_from_unreachable(monkeypatch, error, missing):
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.local_docker.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=error),
    )
    if missing:
        assert LocalDockerSpawner().confirm_stopped("task-container")
    else:
        with pytest.raises(RuntimeError, match="Cannot confirm"):
            LocalDockerSpawner().confirm_stopped("task-container")
