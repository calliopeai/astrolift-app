"""Tests for the WS exec → cluster-driver bridge (#423).

The bridge ``K8sExecBackend`` is the production wiring for the WS
dispatcher's exec backend. It:

  1. Resolves the app + workload to a TenantCluster + namespace.
  2. Opens an :class:`InteractiveExecSession` against the SDK driver.
  3. Pumps stdout/stderr/exit through the WS dispatcher's send
     callbacks until the SDK session signals exit or the WS closes.

These tests cover the bridge mechanics with a recording driver +
session so we don't need a live kubernetes apiserver. The path that
talks to RegisteredApp / TenantCluster goes through
:func:`_resolve_target`; we monkey-patch it here because the model
graph + tenancy setup belongs to the lifecycle test suite.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.cluster_exec import (
    K8sExecBackend,
    reset_driver_exec_backend_for_tests,
    set_driver_exec_backend_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_driver():
    yield
    reset_driver_exec_backend_for_tests()


class _RecordingSession:
    """Stand-in for an SDK :class:`InteractiveExecSession`. Records
    stdin/resize/close and lets the test drive stdout/stderr +
    exit via injected queues."""

    def __init__(
        self,
        *,
        stdout_chunks: list[str] | None = None,
        stderr_chunks: list[str] | None = None,
        exit_code: int = 0,
    ) -> None:
        self.stdins: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.closes = 0
        self._stdout = list(stdout_chunks or [])
        self._stderr = list(stderr_chunks or [])
        self._exit_code = exit_code
        self._exit_event = asyncio.Event()

    async def write_stdin(self, data: str) -> None:
        self.stdins.append(data)

    async def read_stdout(self) -> str:
        if self._stdout:
            return self._stdout.pop(0)
        # Once exit fires, signal EOF on stdout so the dispatcher's
        # reader loop wraps up cleanly.
        if self._exit_event.is_set():
            raise StopAsyncIteration
        await asyncio.sleep(0.01)
        return ""

    async def read_stderr(self) -> str:
        if self._stderr:
            return self._stderr.pop(0)
        if self._exit_event.is_set():
            raise StopAsyncIteration
        await asyncio.sleep(0.01)
        return ""

    async def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    async def wait_exit(self) -> int:
        await self._exit_event.wait()
        return self._exit_code

    async def close(self) -> None:
        self.closes += 1
        self._exit_event.set()

    def signal_exit(self) -> None:
        self._exit_event.set()


class _RecordingDriver:
    """Stand-in for a cluster driver. Records open calls and returns
    the session passed at construction time."""

    def __init__(self, session: _RecordingSession) -> None:
        self.session = session
        self.opens: list[dict] = []

    def interactive_exec(
        self,
        *,
        auth: Any,
        namespace: str,
        pod_name: str,
        container: str,
        command: list[str],
        tty: bool = True,
    ) -> _RecordingSession:
        self.opens.append(
            {
                "namespace": namespace,
                "pod_name": pod_name,
                "container": container,
                "command": command,
                "tty": tty,
            }
        )
        return self.session


def _patch_target(monkeypatch, cluster: Any | None) -> None:
    """Swap _resolve_target to return a synthetic cluster + namespace
    pair (or None to simulate 'no runtime')."""
    from core import cluster_exec as mod

    async def _resolve(*, app_slug, workload_slug):
        if cluster is None:
            return None
        return {"cluster": cluster, "namespace": "acme-web"}

    # ``_auth_for`` is sync — the backend wraps it in ``sync_to_async``
    # (#1040, to keep sync ORM/config reads off the event loop), so the
    # stub must be a plain function or ``sync_to_async`` rejects it.
    def _auth_for(_cluster):
        return object()

    monkeypatch.setattr(mod, "_resolve_target", _resolve)
    monkeypatch.setattr(mod, "_auth_for", _auth_for)


@pytest.mark.asyncio
async def test_open_pipes_stdout_to_sender(monkeypatch) -> None:
    """End-to-end: driver pushes two chunks, dispatcher captures them
    via the send_stdout callback."""
    sess = _RecordingSession(stdout_chunks=["one\n", "two\n"], exit_code=0)
    driver = _RecordingDriver(sess)
    set_driver_exec_backend_for_tests(driver)
    _patch_target(monkeypatch, cluster=object())

    stdout: list[str] = []
    stderr: list[str] = []
    exit_codes: list[int] = []

    backend = K8sExecBackend()
    backend_session = await backend.open(
        app_slug="acme",
        workload_slug="web-7f9",
        container="main",
        command=["bash"],
        send_stdout=lambda d: stdout.append(d) or _aio_none(),
        send_stderr=lambda d: stderr.append(d) or _aio_none(),
        send_exit=lambda c: exit_codes.append(c) or _aio_none(),
        send_error=lambda m: _aio_none(),
    )

    # Give the reader tasks a few ticks to drain.
    for _ in range(50):
        if len(stdout) >= 2:
            break
        await asyncio.sleep(0.02)
    assert stdout == ["one\n", "two\n"]

    # Trigger exit + close — the watcher should send an exit frame.
    sess.signal_exit()
    for _ in range(50):
        if exit_codes:
            break
        await asyncio.sleep(0.02)
    assert exit_codes == [0]

    await backend_session.close()
    assert sess.closes >= 1
    assert driver.opens[0]["pod_name"] == "web-7f9"
    assert driver.opens[0]["container"] == "main"


@pytest.mark.asyncio
async def test_open_emits_no_runtime_when_no_cluster(monkeypatch) -> None:
    """If _resolve_target returns None (no cluster wired), the dispatcher
    sees a stderr hint + exit code 2 and the open path doesn't talk to
    any driver."""
    set_driver_exec_backend_for_tests(_RecordingDriver(_RecordingSession()))
    _patch_target(monkeypatch, cluster=None)

    stderr: list[str] = []
    exit_codes: list[int] = []

    backend = K8sExecBackend()
    await backend.open(
        app_slug="acme",
        workload_slug="web",
        container="main",
        command=["sh"],
        send_stdout=lambda d: _aio_none(),
        send_stderr=lambda d: stderr.append(d) or _aio_none(),
        send_exit=lambda c: exit_codes.append(c) or _aio_none(),
        send_error=lambda m: _aio_none(),
    )

    assert exit_codes == [2]
    assert any("no runtime cluster" in s for s in stderr)


@pytest.mark.asyncio
async def test_stdin_and_resize_proxy_to_session(monkeypatch) -> None:
    """write_stdin + resize on the dispatcher's session forward straight
    through to the underlying SDK session."""
    sess = _RecordingSession()
    set_driver_exec_backend_for_tests(_RecordingDriver(sess))
    _patch_target(monkeypatch, cluster=object())

    backend = K8sExecBackend()
    backend_session = await backend.open(
        app_slug="acme",
        workload_slug="web",
        container="main",
        command=["sh"],
        send_stdout=lambda d: _aio_none(),
        send_stderr=lambda d: _aio_none(),
        send_exit=lambda c: _aio_none(),
        send_error=lambda m: _aio_none(),
    )

    await backend_session.stdin("echo hi\n")
    await backend_session.resize(rows=40, cols=120)

    assert sess.stdins == ["echo hi\n"]
    assert sess.resizes == [(40, 120)]

    await backend_session.close()


def _aio_none():
    """Return a fresh coroutine that resolves to None — lets the
    test build single-line sender callbacks that satisfy ``await``."""

    async def _inner():
        return None

    return _inner()
