"""The agent-box keep-alive contract (#128).

A long-lived pod that exists to be exec'd into. The parts worth pinning are the
ones that decide whether an agent survives a dropped connection and whether a
forgotten box burns a node forever.

Where tmux is available these run the script for real. Asserting on the text of
a shell script proves it was written, not that it works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from _sdk.agent_session import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    NEVER,
    POLL_SECONDS,
    SESSION_NAME,
    TERMINAL_REGISTRY,
    TERMINAL_REGISTRY_ENV,
    WORKSPACE_ENV_FILE,
    WORKSPACE_SETUP_COMMAND,
    SessionSpec,
    attach_argv,
    container_spec,
    keepalive_script,
)

HAS_TMUX = shutil.which("tmux") is not None
tmux_required = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")


# ---- the spec ----------------------------------------------------------------


def test_an_image_is_required():
    with pytest.raises(ValueError, match="needs an image"):
        SessionSpec(image="")


def test_a_negative_timeout_is_refused_and_points_at_the_sentinel():
    """`-1` meaning "never" is the kind of convention that gets misread."""
    with pytest.raises(ValueError, match="NEVER"):
        SessionSpec(image="agent:1", idle_timeout_seconds=-1)


def test_an_unsafe_session_name_is_refused():
    """The name is interpolated into the keep-alive script."""
    with pytest.raises(ValueError, match="not a safe tmux target"):
        SessionSpec(image="agent:1", session_name="a; rm -rf /")


def test_the_default_timeout_is_finite():
    """A forgotten agent-box burning a node is the failure this exists to
    prevent, so the default cannot be "never"."""
    assert SessionSpec(image="agent:1").idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS
    assert DEFAULT_IDLE_TIMEOUT_SECONDS > 0


# ---- the container ------------------------------------------------------------


def test_the_container_asks_for_a_terminal():
    """tmux refuses to attach without one, and the session then looks broken
    rather than absent."""
    container = container_spec(SessionSpec(image="agent:1"))

    assert container["stdin"] is True
    assert container["tty"] is True


def test_the_container_declares_no_ports_and_no_probes():
    """It serves nothing. A readiness probe would assert something untrue and
    an HTTP liveness check would restart a healthy pod with no listener."""
    container = container_spec(SessionSpec(image="agent:1"))

    assert "ports" not in container
    assert "readinessProbe" not in container
    assert "livenessProbe" not in container


def test_env_reaches_the_container_sorted():
    """The env-spec secret packet is how ANTHROPIC_API_KEY arrives. Sorted so a
    manifest diff reflects a real change rather than dict ordering."""
    container = container_spec(SessionSpec(image="agent:1", env={"B": "2", "A": "1"}))

    assert container["env"] == [
        {"name": "A", "value": "1"},
        {"name": TERMINAL_REGISTRY_ENV, "value": TERMINAL_REGISTRY},
        {"name": "B", "value": "2"},
    ]


def test_operator_env_cannot_redirect_terminal_accounting():
    container = container_spec(SessionSpec(image="agent:1", env={TERMINAL_REGISTRY_ENV: "/elsewhere"}))
    assert container["env"] == [{"name": TERMINAL_REGISTRY_ENV, "value": TERMINAL_REGISTRY}]


def test_attach_reuses_the_session_rather_than_racing_it():
    """`new-session -A` attaches if it exists and creates if not, so a client
    faster than the keep-alive loop's first line attaches instead of erroring."""
    assert attach_argv() == ["tmux", "new-session", "-A", "-s", SESSION_NAME]


# ---- the script, actually run --------------------------------------------------


def _run_script(spec: SessionSpec, socket: str) -> subprocess.Popen:
    script = keepalive_script(spec).replace("tmux ", f"tmux -L {socket} ")
    return subprocess.Popen(
        ["/bin/sh", "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True, check=False)


@pytest.fixture(autouse=True)
def terminal_registry(monkeypatch):
    # Unix socket paths are bounded; pytest's nested temp path can exceed it.
    with tempfile.TemporaryDirectory(prefix="astrobox-", dir="/tmp") as directory:
        registry = Path(directory) / "terminals"
        monkeypatch.setenv(TERMINAL_REGISTRY_ENV, str(registry))
        yield registry


@pytest.fixture
def socket_name(request):
    name = f"astrobox-{abs(hash(request.node.name)) % 100000}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, check=False)


@tmux_required
def test_the_script_starts_a_session_and_stays_up(socket_name):
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER), socket_name)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if _tmux(socket_name, "has-session", "-t", SESSION_NAME).returncode == 0:
                break
            time.sleep(0.2)
        else:
            pytest.fail("keep-alive never created the session")

        time.sleep(1)
        assert proc.poll() is None, "PID 1 exited while the session was alive"
    finally:
        proc.kill()


@tmux_required
def test_killing_the_session_ends_the_pod(socket_name):
    """The loop condition. Exiting the shell inside tmux must end the container
    rather than leave it billing a node until something else notices."""
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER), socket_name)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if _tmux(socket_name, "has-session", "-t", SESSION_NAME).returncode == 0:
                break
            time.sleep(0.2)

        _tmux(socket_name, "kill-session", "-t", SESSION_NAME)

        assert proc.wait(timeout=20) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


@tmux_required
def test_an_idle_box_reaps_itself(socket_name):
    """Scale-to-zero without a controller: the box notices it is unused."""
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=1), socket_name)
    try:
        assert proc.wait(timeout=30) is not None
        assert _tmux(socket_name, "has-session", "-t", SESSION_NAME).returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()


@tmux_required
def test_never_means_never(socket_name):
    """An operator running a long detached job opts out explicitly."""
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER), socket_name)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=8)
    finally:
        proc.kill()


@tmux_required
def test_a_busy_detached_session_is_not_reaped(socket_name):
    """The failure tmux was introduced to prevent. Reaping on "nobody attached"
    alone would kill an agent working while its operator is at lunch."""
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=3), socket_name)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if _tmux(socket_name, "has-session", "-t", SESSION_NAME).returncode == 0:
                break
            time.sleep(0.2)

        # Nothing is attached, but the pane keeps producing activity.
        for _ in range(8):
            _tmux(socket_name, "send-keys", "-t", SESSION_NAME, "echo working", "Enter")
            time.sleep(1)
            if proc.poll() is not None:
                pytest.fail("a detached but busy session was reaped")

        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.kill()


def _wait_for_session(socket):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _tmux(socket, "has-session", "-t", SESSION_NAME).returncode == 0:
            return
        time.sleep(0.1)
    pytest.fail("keep-alive never created the session")


def _register_private_terminal(socket, registry, command="sleep 300"):
    private = socket + "-private"
    result = _tmux(private, "new-session", "-d", "-s", "transferred", command)
    assert result.returncode == 0, result.stderr
    _tmux(private, "set-option", "-g", "remain-on-exit", "on")
    target = _tmux(private, "display-message", "-p", "#{socket_path}").stdout.strip()
    (registry / "private.sock").symlink_to(target)
    return private


@tmux_required
def test_registered_output_survives_default_shell_exit_then_reaps_when_idle(socket_name, terminal_registry):
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=3), socket_name)
    private = socket_name + "-private"
    try:
        _wait_for_session(socket_name)
        _register_private_terminal(socket_name, terminal_registry, "sh -c 'while :; do echo working; sleep 1; done'")
        _tmux(socket_name, "kill-session", "-t", SESSION_NAME)
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=8)
        # Stop producing output without ending the pane; idleness must still reap.
        _tmux(private, "respawn-pane", "-k", "-t", "=transferred:", "sleep 300")
        assert proc.wait(timeout=15) == 0
        assert _tmux(private, "has-session").returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
        _tmux(private, "kill-server")


@tmux_required
def test_attached_registered_terminal_survives_without_output(socket_name, terminal_registry):
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=3), socket_name)
    private = socket_name + "-private"
    client = None
    try:
        _wait_for_session(socket_name)
        _register_private_terminal(socket_name, terminal_registry)
        client = subprocess.Popen(
            ["tmux", "-L", private, "-C", "attach-session", "-t", "=transferred:"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, "TERM": "xterm"},
        )
        deadline = time.monotonic() + 5
        while not _tmux(private, "list-clients").stdout.strip():
            assert client.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.1)
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=8)
        client.terminate()
        client.wait(timeout=5)
        assert proc.wait(timeout=15) == 0
    finally:
        if client and client.poll() is None:
            client.kill()
            client.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
        _tmux(private, "kill-server")


@tmux_required
def test_dead_panes_and_stale_registrations_do_not_retain_box(socket_name, terminal_registry):
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER), socket_name)
    private = socket_name + "-private"
    try:
        _wait_for_session(socket_name)
        _register_private_terminal(socket_name, terminal_registry)
        (terminal_registry / "stale.sock").symlink_to("/tmp/astrobox-missing-socket")
        _tmux(private, "respawn-pane", "-k", "-t", "=transferred:", "true")
        deadline = time.monotonic() + 5
        while _tmux(private, "list-panes", "-F", "#{pane_dead}").stdout.strip() != "1":
            assert time.monotonic() < deadline
            time.sleep(0.1)
        _tmux(socket_name, "kill-session", "-t", SESSION_NAME)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        _tmux(private, "kill-server")


# ---- workspace setup before the session (#1877) -------------------------------


def test_a_bare_box_runs_no_setup():
    """Off by default, and off means the keep-alive it always was."""
    spec = SessionSpec(image="agent:1")

    assert spec.workspace_setup is False
    assert WORKSPACE_SETUP_COMMAND not in keepalive_script(spec)
    assert keepalive_script(spec).startswith('set -eu\nTERMINAL_REGISTRY="')


def test_setup_runs_and_its_env_is_sourced_before_the_session_starts():
    script = keepalive_script(SessionSpec(image="agent:1", workspace_setup=True))

    assert script.splitlines()[:3] == ["set -eu", WORKSPACE_SETUP_COMMAND, f'. "{WORKSPACE_ENV_FILE}"']
    assert script.index(WORKSPACE_ENV_FILE) < script.index("tmux new-session")


def test_a_box_that_sets_up_is_ready_only_once_its_session_exists():
    """Ready makes a box attachable. Setup can take minutes, and an attach
    during it would create the session and make the keep-alive's own
    new-session fail."""
    container = container_spec(SessionSpec(image="agent:1", workspace_setup=True))

    assert container["readinessProbe"] == {
        "exec": {"command": ["tmux", "has-session", "-t", f"={SESSION_NAME}"]},
        "periodSeconds": POLL_SECONDS,
    }
    assert "livenessProbe" not in container
    assert "ports" not in container


def _fake_setup(tmp_path, monkeypatch, body: str) -> None:
    """Put a stand-in for the runner's setup first on PATH, with a scratch HOME."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    setup = bin_dir / WORKSPACE_SETUP_COMMAND
    setup.write_text("#!/bin/sh\n" + body)
    setup.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _probe(socket: str) -> int:
    probe = container_spec(SessionSpec(image="x", workspace_setup=True))["readinessProbe"]["exec"]["command"]
    return _tmux(socket, *probe[1:]).returncode


@tmux_required
def test_the_session_inherits_what_the_setup_exported(socket_name, tmp_path, monkeypatch):
    _fake_setup(
        tmp_path,
        monkeypatch,
        'sleep 1\nmkdir -p "$HOME/.astrolift"\n'
        'echo "export VIRTUAL_ENV=/workspace/.venv" > "$HOME/.astrolift/workspace.env"\n',
    )
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER, workspace_setup=True), socket_name)
    try:
        # Not ready while the setup runs: no session to attach to yet.
        assert _probe(socket_name) != 0
        _wait_for_session(socket_name)
        assert _probe(socket_name) == 0
        shown = _tmux(socket_name, "show-environment", "-g", "VIRTUAL_ENV").stdout.strip()
        assert shown == "VIRTUAL_ENV=/workspace/.venv"
    finally:
        proc.kill()


@tmux_required
def test_a_failed_setup_ends_the_box_before_any_session(socket_name, tmp_path, monkeypatch):
    _fake_setup(tmp_path, monkeypatch, 'echo "[workspace] workspace.repos[0]: could not clone" >&2\nexit 1\n')
    proc = _run_script(SessionSpec(image="x", idle_timeout_seconds=NEVER, workspace_setup=True), socket_name)
    try:
        assert proc.wait(timeout=10) == 1
        assert _tmux(socket_name, "has-session", "-t", SESSION_NAME).returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
