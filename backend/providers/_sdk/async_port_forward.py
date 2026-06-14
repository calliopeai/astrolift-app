"""Async bridge over the blocking kubernetes port-forward (#877).

The kubernetes SDK port-forward (:meth:`KubernetesDynamicClient.
port_forward` -> :class:`PortForwardHandle`) is fully blocking: opening
the websocket blocks, and the per-port socket returned by
``handle.socket(port)`` is a blocking socket-like object. The VNC WS
relay needs an asyncio-friendly read/write surface so it can pump
frames between a browser WebSocket and the pod's noVNC port without
stalling the event loop.

:class:`AsyncK8sPortForward` is the analogue of
``core.cluster_exec._BackendSession``: the blocking ``open`` runs in
the default executor, and ``recv`` / ``sendall`` wrap the blocking
socket calls in ``run_in_executor`` so the coroutine yields while the
kubelet relay does its work. There is no JSON control protocol here —
noVNC speaks RFB over a raw byte stream, so the relay just moves bytes.

The class is cloud-neutral: it takes a synchronous ``open_fn`` callable
(returning a ``PortForwardHandle``) so the caller decides which driver
to forward through. Tests inject a fake handle without a cluster.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Per-read chunk size off the pod socket. noVNC framebuffer updates are
# bursty; 64 KiB keeps the executor round-trip count low without pinning
# much memory per session.
_RECV_CHUNK = 65536


class AsyncK8sPortForward:
    """Asyncio wrapper around a blocking k8s ``PortForwardHandle``.

    Construct with :meth:`open`, which runs the blocking port-forward
    handshake in the executor. ``recv`` / ``sendall`` bridge the
    blocking per-port socket; ``close`` tears the handle down (also in
    the executor, since ``handle.close()`` is synchronous).
    """

    def __init__(self, *, handle: Any, port: int) -> None:
        self._handle = handle
        self._port = port
        self._sock = handle.socket(port)
        self._closed = False
        self._loop = asyncio.get_running_loop()

    @classmethod
    async def open(
        cls,
        *,
        open_fn: Callable[[], Any],
        port: int,
    ) -> AsyncK8sPortForward:
        """Open a port-forward by running ``open_fn`` in the executor.

        ``open_fn`` must return a ``PortForwardHandle``-shaped object
        exposing ``socket(port)`` and ``close()``. Mirrors the
        ``loop.run_in_executor`` open path in ``K8sExecBackend.open``.
        """
        loop = asyncio.get_running_loop()
        handle = await loop.run_in_executor(None, open_fn)
        return cls(handle=handle, port=port)

    async def recv(self, n: int = _RECV_CHUNK) -> bytes:
        """Read up to ``n`` bytes from the pod socket.

        Returns ``b""`` on a clean EOF (peer closed) so the caller's
        pump loop can break. Blocking ``recv`` runs in the executor.
        """
        if self._closed:
            return b""
        return await self._loop.run_in_executor(
            None,
            lambda: self._sock.recv(n),
        )

    async def sendall(self, data: bytes) -> None:
        """Write all of ``data`` to the pod socket (blocking, executored)."""
        if self._closed or not data:
            return
        await self._loop.run_in_executor(
            None,
            lambda: self._sock.sendall(data),
        )

    async def close(self) -> None:
        """Tear down the port-forward websocket (best-effort)."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._loop.run_in_executor(None, self._handle.close)
        except Exception:
            logger.exception("async_port_forward: close failed")
