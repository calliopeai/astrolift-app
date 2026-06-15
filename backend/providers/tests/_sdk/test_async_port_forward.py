"""Tests for the async port-forward bridge (#877).

``AsyncK8sPortForward`` wraps a blocking ``PortForwardHandle`` so the
VNC relay can read/write the pod socket without blocking the event
loop. We don't open a real port-forward — a fake handle with a fake
blocking socket proves the executor bridge moves bytes both ways and
tears the handle down on close.
"""

from __future__ import annotations

import pytest

from _sdk.async_port_forward import AsyncK8sPortForward


class _FakeSock:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: list[bytes] = []

    def recv(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class _FakeHandle:
    def __init__(self, sock: _FakeSock) -> None:
        self._sock = sock
        self.closed = False

    def socket(self, port: int):
        return self._sock

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_open_recv_sendall_close() -> None:
    sock = _FakeSock([b"chunk-1", b"chunk-2"])
    handle = _FakeHandle(sock)

    fwd = await AsyncK8sPortForward.open(open_fn=lambda: handle, port=5900)

    # recv drains the scripted chunks, then EOFs.
    assert await fwd.recv() == b"chunk-1"
    assert await fwd.recv() == b"chunk-2"
    assert await fwd.recv() == b""

    # sendall forwards to the blocking socket.
    await fwd.sendall(b"to-pod")
    assert sock.sent == [b"to-pod"]

    # close tears the handle down and makes the surface inert.
    await fwd.close()
    assert handle.closed is True
    assert await fwd.recv() == b""
    await fwd.sendall(b"after-close")
    assert sock.sent == [b"to-pod"]


@pytest.mark.asyncio
async def test_open_runs_open_fn_once() -> None:
    sock = _FakeSock([])
    handle = _FakeHandle(sock)
    calls: list[int] = []

    def _open():
        calls.append(1)
        return handle

    await AsyncK8sPortForward.open(open_fn=_open, port=5900)
    assert calls == [1]
