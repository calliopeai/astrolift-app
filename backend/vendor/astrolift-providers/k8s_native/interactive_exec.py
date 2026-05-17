"""Interactive ``kubectl exec -it`` for the k8s_native ClusterDriver (#423).

Powers the in-browser console terminal. The kubernetes-client SDK
exposes ``stream(...)`` against ``CoreV1Api.connect_get_namespaced_pod_exec``
which returns a ``WSClient`` — a thin wrapper over a websocket
multiplexed across stdin / stdout / stderr / resize channels. We
bridge that blocking-by-default WSClient into an async session so
the WS dispatcher's ``await``-based control loop can drive it.

Why a separate file from ``observability.py``: pod listing + log
streaming + exec are three independent kubernetes-client surfaces
with very different lifetime semantics. Keeping them apart means
a slow exec session can't pin the import path of the log + pod
backends.

Backends are pluggable through the same constructor-kwarg pattern
as the log + pod backends: tests inject a recording fake without
hitting the live kubernetes client.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Protocol

from _sdk.cluster import (
    ClusterAuth,
    InteractiveExecSession,
)
from k8s_native.observability import ClusterAuthError, build_api_client


class InteractiveExecBackend(Protocol):
    """Opens an ``InteractiveExecSession`` against a target pod.

    Implementations MUST surface a working session even on backends
    without TTY support (resize / wait_exit may no-op, but read /
    write contracts must hold) so the WS dispatcher's protocol stays
    the same across backends.
    """

    def open(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str,
        command: list[str],
        tty: bool = True,
    ) -> InteractiveExecSession: ...


class LiveInteractiveExecBackend:
    """Default backend — bridges the kubernetes-client ``WSClient`` into
    an :class:`InteractiveExecSession`.

    Stops on ``CancelledError`` and on remote command exit; ``close()``
    closes the websocket and is idempotent.
    """

    def open(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str,
        command: list[str],
        tty: bool = True,
    ) -> InteractiveExecSession:
        try:
            from kubernetes import client as k8s_client
            from kubernetes.stream import stream
        except ImportError as exc:  # pragma: no cover — packaging guard
            raise ClusterAuthError(
                "kubernetes python client is not installed; install astrolift-providers[k8s]",
            ) from exc

        api_client = build_api_client(auth)
        core_v1 = k8s_client.CoreV1Api(api_client)

        # ``_preload_content=False`` returns the underlying WSClient
        # instead of draining it eagerly. ``stream(...)`` wraps the
        # connect_get_namespaced_pod_exec call with websocket-aware
        # tunneling so stdin / stdout / stderr / resize are
        # multiplexed on the right channels.
        ws = stream(
            core_v1.connect_get_namespaced_pod_exec,
            name=pod_name,
            namespace=namespace,
            container=container,
            command=command or ["sh"],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=bool(tty),
            _preload_content=False,
        )
        return _LiveSession(ws)


class _LiveSession(InteractiveExecSession):
    """Async wrapper around the kubernetes-client ``WSClient``.

    The wrapped client is synchronous; we push each blocking
    ``read_*`` / ``write_*`` onto the default executor so the event
    loop stays responsive. ``close()`` is safe to call from any task
    and idempotent."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._loop = asyncio.get_running_loop()
        self._closed = False

    async def write_stdin(self, data: str) -> None:
        if self._closed:
            return
        await self._loop.run_in_executor(
            None,
            lambda: self._ws.write_stdin(data),
        )

    async def read_stdout(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        # WSClient.read_stdout(timeout=...) blocks up to timeout for
        # bytes; passing 0 polls without blocking. The dispatcher
        # loop calls this in a tight cycle so a short timeout keeps
        # the event loop responsive without burning CPU.
        return await self._loop.run_in_executor(
            None,
            lambda: self._ws.read_stdout(timeout=0.05),
        )

    async def read_stderr(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        return await self._loop.run_in_executor(
            None,
            lambda: self._ws.read_stderr(timeout=0.05),
        )

    async def resize(self, rows: int, cols: int) -> None:
        if self._closed:
            return
        # Channel 4 (RESIZE) on the kubelet exec protocol carries a
        # JSON ``{"Width": cols, "Height": rows}`` blob. WSClient's
        # ``write_channel`` is the low-level escape hatch.
        import json

        try:
            await self._loop.run_in_executor(
                None,
                lambda: self._ws.write_channel(
                    4,
                    json.dumps({"Width": int(cols), "Height": int(rows)}),
                ),
            )
        except Exception:
            # Old kubernetes-client builds don't expose write_channel
            # or the cluster doesn't support resize — best-effort.
            return

    async def wait_exit(self) -> int:
        # Poll the WSClient's open state on a short tick. When the
        # remote shell exits the websocket closes from the cluster
        # side and ``is_open()`` flips to False. The kubernetes-client
        # exposes the exit code via ``returncode`` after close.
        while True:
            if self._closed:
                return 0
            is_open = await self._loop.run_in_executor(
                None,
                self._ws.is_open,
            )
            if not is_open:
                code = getattr(self._ws, "returncode", 0)
                return int(code or 0)
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._loop.run_in_executor(None, self._ws.close)


class StubInteractiveExecBackend:
    """Backend used when the kubernetes SDK isn't importable. Returns
    a session that exits immediately with code 2 and emits a hint to
    install the right extras. Keeps the WS handshake clean so the UI
    renders the right empty-state without the connection erroring."""

    def open(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str,
        command: list[str],
        tty: bool = True,
    ) -> InteractiveExecSession:
        return _StubSession()


class _StubSession(InteractiveExecSession):
    def __init__(self) -> None:
        self._hint = (
            "interactive exec backend not wired in this build — install "
            "astrolift-providers[k8s] and register the LiveInteractiveExecBackend "
            "via the K8sNativeClusterDriver constructor"
        )
        self._sent = False
        self._closed = False

    async def write_stdin(self, data: str) -> None:
        return None

    async def read_stdout(self) -> str:
        return ""

    async def read_stderr(self) -> str:
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return self._hint + "\n"

    async def resize(self, rows: int, cols: int) -> None:
        return None

    async def wait_exit(self) -> int:
        return 2

    async def close(self) -> None:
        self._closed = True


def default_interactive_exec_backend() -> InteractiveExecBackend:
    """Return the live backend when the kubernetes SDK is importable,
    otherwise the stub. Mirrors the log + pod backend defaults."""
    try:
        import kubernetes  # noqa: F401
    except ImportError:
        return StubInteractiveExecBackend()
    return LiveInteractiveExecBackend()
