import json
import subprocess
import traceback
from types import SimpleNamespace

import pytest

from astrolift_dispatch.spawners import docker_connection as docker
from astrolift_dispatch.spawners.local_docker import LocalDockerSpawner
from astrolift_dispatch.spawners.registry import get_spawner


@pytest.fixture
def connection():
    return {
        "version": 1,
        "mode": "host",
        "config_dir": "/original/docker",
        "endpoint": "unix:///original/docker.sock",
        "tls": False,
        "tls_verify": False,
        "cert_path": "/original/certs",
    }


@pytest.fixture
def clean_environment(monkeypatch):
    for key in (*docker._CONNECTION_ENV, "DOCKER_CONFIG"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("tls,tls_verify", [(False, False), (True, False), (True, True)])
def test_host_connection_ignores_new_defaults_and_preserves_tls(monkeypatch, connection, tls, tls_verify):
    connection.update(tls=tls, tls_verify=tls_verify)
    for key in (*docker._CONNECTION_ENV, "DOCKER_CONFIG"):
        monkeypatch.setenv(key, "replacement")
    calls = []
    monkeypatch.setattr(docker.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    docker.run_docker(connection, ["info", "--format", "{{.ID}}"])
    command, kwargs = calls[0]
    assert command[:5] == ["docker", "--config", "/original/docker", "--host", "unix:///original/docker.sock"]
    if tls:
        assert command[5:7] == ["--tls", f"--tlsverify={str(tls_verify).lower()}"]
    else:
        assert not any(arg.startswith("--tls") for arg in command)
    assert kwargs["env"]["DOCKER_CERT_PATH"] == "/original/certs"
    assert not any(key in kwargs["env"] for key in docker._CONNECTION_ENV if key != "DOCKER_CERT_PATH")


@pytest.mark.parametrize("verify", ["", "1", "0"])
def test_snapshot_default_stores_locations_and_tls_without_credentials(
    monkeypatch, clean_environment, verify
):
    monkeypatch.setenv("DOCKER_HOST", "tcp://original:2376")
    monkeypatch.setenv("DOCKER_CONFIG", "/original/docker")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", verify)
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "credential-material")

    def run(command, **kwargs):
        if command[-2:] == ["context", "show"]:
            return SimpleNamespace(returncode=0, stdout="default\n")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Host": "tcp://original:2376"}))

    monkeypatch.setattr(docker.subprocess, "run", run)
    saved = docker.snapshot_docker_connection()
    assert saved == {
        "version": 1,
        "mode": "host",
        "config_dir": "/original/docker",
        "endpoint": "tcp://original:2376",
        "cert_path": "/original/docker",
        "tls": bool(verify),
        "tls_verify": bool(verify),
    }
    assert "credential-material" not in json.dumps(saved)


@pytest.mark.parametrize("changed", [False, True])
def test_named_context_is_pinned_and_refuses_replaced_endpoint(monkeypatch, clean_environment, changed):
    monkeypatch.setenv("DOCKER_CONFIG", "/original/docker")
    current = "original"
    endpoint = "tcp://original:2376"
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[-2:] == ["context", "show"]:
            return SimpleNamespace(returncode=0, stdout=current)
        if "inspect" in command:
            assert command[-1] == "original"
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"Host": endpoint, "SkipTLSVerify": False})
            )
        return SimpleNamespace(returncode=0, stdout="daemon")

    monkeypatch.setattr(docker.subprocess, "run", run)
    saved = docker.snapshot_docker_connection()
    assert saved["mode"] == "context" and saved["context"] == "original"
    current = "replacement"
    monkeypatch.setenv("DOCKER_CONTEXT", current)
    monkeypatch.setenv("DOCKER_HOST", "tcp://replacement:2375")
    if changed:
        endpoint = "tcp://replacement:2376"
        with pytest.raises(RuntimeError, match="endpoint or TLS policy changed"):
            docker.run_docker(saved, ["info"])
        assert "info" not in commands[-1]
    else:
        assert docker.run_docker(saved, ["info"]).stdout == "daemon"
        assert commands[-1] == ["docker", "--config", "/original/docker", "--context", "original", "info"]


@pytest.mark.parametrize(
    "patch",
    [
        {"endpoint": "ssh://user:secret@original"},
        {"endpoint": "tcp://user@original:2375"},
        {"endpoint": "unix:///socket?token=secret"},
        {"endpoint": "http://original"},
        {"config_dir": "relative"},
        {"cert_path": "relative"},
        {"tls": "false"},
        {"version": True},
        {"tls_verify": True},
        {"credential": "secret"},
    ],
)
def test_invalid_references_are_rejected_without_command_or_credential_echo(monkeypatch, connection, patch):
    connection.update(patch)
    monkeypatch.setattr(docker.subprocess, "run", lambda *a, **kw: pytest.fail("invalid connection executed"))
    with pytest.raises(RuntimeError) as error:
        docker.run_docker(connection, ["rm", "-f", "container"])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("pinned", [False, True])
def test_spawn_timeout_never_reports_token_in_error_or_traceback(monkeypatch, connection, pinned):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(docker.subprocess, "run", run)
    spawner = LocalDockerSpawner(connection=connection if pinned else None)
    arguments = ["run", "-e", "TASK_TOKEN=private-token"]
    with pytest.raises(RuntimeError) as error:
        spawner._run(arguments, timeout=30)
    assert "private-token" not in "".join(traceback.format_exception(error.type, error.value, error.tb))


@pytest.mark.parametrize("operation", ["status", "stop", "confirm_stopped"])
def test_task_spawner_checks_saved_daemon_before_contacting_container(monkeypatch, connection, operation):
    calls = []

    def run(saved, args, **kwargs):
        assert saved == connection
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="replacement-daemon")

    monkeypatch.setattr(docker, "run_docker", run)
    task = SimpleNamespace(
        dispatch_target={"docker_connection": connection, "docker_daemon_id": "original-daemon"}
    )
    spawner = get_spawner("local_docker", task=task)
    with pytest.raises(RuntimeError, match="different Docker daemon"):
        getattr(spawner, operation)("task-container")
    assert calls == [["info", "--format", "{{.ID}}"]]


@pytest.mark.parametrize(
    "output,returncode",
    [
        ("Cannot connect to the Docker daemon", 1),
        ("permission denied", 1),
        ("", 0),
        ("unknown 0", 0),
        ("exited not-a-number", 0),
        ("exited", 0),
    ],
)
def test_unavailable_or_invalid_status_is_transient(monkeypatch, output, returncode):
    monkeypatch.setattr(
        docker.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=returncode,
            stdout=output if not returncode else "",
            stderr=output if returncode else "",
        ),
    )
    with pytest.raises(RuntimeError):
        LocalDockerSpawner().status("task-container")


@pytest.mark.parametrize(
    "state,failed,succeeded",
    [("running 0", False, False), ("exited 1", True, False), ("exited 0", False, True)],
)
def test_observed_container_state_can_settle_task(monkeypatch, state, failed, succeeded):
    monkeypatch.setattr(
        docker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=state)
    )
    status = LocalDockerSpawner().status("task-container")
    assert status.failed is failed and status.succeeded is succeeded
