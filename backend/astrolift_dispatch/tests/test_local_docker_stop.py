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
