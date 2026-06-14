"""Tests for the noVNC WebSocket relay (#877).

Mirrors ``test_exec_ws.py``: the auth + ASGI dispatch live behind
cookies/sessions, so these unit tests cover the protocol-level pieces by
patching the auth + task-resolution helpers:

  - path parser
  - auth rejection (no/invalid session)
  - permission denial
  - non-running / non-vnc-capable task rejection
  - happy-path bidirectional byte relay against a fake backend
  - backend swap + the default stub's EOF behavior
"""

from __future__ import annotations

import asyncio

import pytest

from core.schema.vnc_ws import (
    VncBackend,
    VncSession,
    _parse_task_guid,
    _StubVncBackend,
    get_vnc_backend,
    set_vnc_backend,
)

# ---- path parser ----------------------------------------------------


def test_parse_task_guid() -> None:
    assert _parse_task_guid("/app/vnc/abc-123") == "abc-123"


def test_parse_task_guid_with_trailing_segments_takes_guid() -> None:
    assert _parse_task_guid("/app/vnc/abc-123/extra") == "abc-123"


def test_parse_task_guid_too_short_returns_none() -> None:
    assert _parse_task_guid("/app/vnc") is None
    assert _parse_task_guid("/app/exec/acme/web") is None
    assert _parse_task_guid("/other/path") is None


# ---- backend swap + stub --------------------------------------------


def test_default_backend_is_either_stub_or_production_wired() -> None:
    from core.cluster_vnc import K8sVncBackend

    backend = get_vnc_backend()
    assert isinstance(backend, (_StubVncBackend, K8sVncBackend))


def test_set_vnc_backend_swaps() -> None:
    class _Other(VncBackend):
        async def open(self, *, task_guid: str) -> VncSession:
            return VncSession()  # type: ignore[abstract]

    prior = get_vnc_backend()
    try:
        set_vnc_backend(_Other())
        assert isinstance(get_vnc_backend(), _Other)
    finally:
        set_vnc_backend(prior)


@pytest.mark.asyncio
async def test_stub_backend_eofs_immediately() -> None:
    backend = _StubVncBackend()
    session = await backend.open(task_guid="abc-123")
    assert await session.recv() == b""
    await session.sendall(b"ignored")
    await session.close()


# ---- shared scaffolding ---------------------------------------------


class _FakeVncSession(VncSession):
    """Records bytes written to the pod and replays a scripted set of
    pod->client chunks, then EOFs."""

    def __init__(self, pod_chunks: list[bytes]) -> None:
        self._pod_chunks = list(pod_chunks)
        self.sent_to_pod: list[bytes] = []
        self.closed = False

    async def recv(self) -> bytes:
        if self._pod_chunks:
            return self._pod_chunks.pop(0)
        # Block until cancelled so the relay's receive loop drives the
        # teardown — emulates an idle-but-open framebuffer.
        await asyncio.sleep(3600)
        return b""

    async def sendall(self, data: bytes) -> None:
        self.sent_to_pod.append(data)

    async def close(self) -> None:
        self.closed = True


class _FakeVncBackend(VncBackend):
    def __init__(self, session: _FakeVncSession) -> None:
        self._session = session
        self.opened_for: list[str] = []

    async def open(self, *, task_guid: str) -> VncSession:
        self.opened_for.append(task_guid)
        return self._session


def _patch_auth(
    monkeypatch,
    *,
    authed: bool = True,
    org_id: int | None = 1,
    perm_ok: bool = True,
    task_ok: bool = True,
    pod_name: str = "agent-task-deadbeef",
    audit_sink: list | None = None,
) -> None:
    """Patch the auth + task-resolution helpers so the relay handshake
    reaches (or fails before) the accept, without Django sessions / a
    cluster."""
    import core.schema.ws_auth as ws_auth_mod
    from core.schema import vnc_ws as mod

    async def _user(_session_key):
        class _U:
            is_authenticated = authed

        return _U(), {}

    async def _tenant(_user, _data):
        if org_id is None:
            return None

        class _T:
            organization_id = org_id
            actor_user_id = 99

        return _T()

    async def _perm(*, tenant_org_id, actor_user_id):
        return perm_ok

    async def _task(*, task_guid, tenant_org_id):
        if not task_ok:
            return None
        return {"pod_name": pod_name}

    async def _audit(**kw):
        if audit_sink is not None:
            audit_sink.append(kw)

    monkeypatch.setattr(ws_auth_mod, "_resolve_user_from_sessionid", _user)
    monkeypatch.setattr(ws_auth_mod, "_resolve_tenant_for_user", _tenant)
    monkeypatch.setattr(mod, "_check_vnc_permission", _perm)
    monkeypatch.setattr(mod, "_resolve_vnc_task", _task)
    monkeypatch.setattr(mod, "_audit_vnc_open", _audit)


def _drive(scope, incoming):
    """Build a receive()/send() pair over a scripted incoming list."""
    queue = list(incoming)
    sent: list[dict] = []

    async def receive() -> dict:
        if queue:
            return queue.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    return receive, send, sent


# ---- rejection paths ------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_bad_path() -> None:
    from core.schema import vnc_ws as mod

    receive, send, sent = _drive(None, [])
    scope = {"type": "websocket", "path": "/app/vnc", "headers": []}
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4404}


@pytest.mark.asyncio
async def test_rejects_unauthed(monkeypatch) -> None:
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, authed=False)
    receive, send, sent = _drive(None, [])
    scope = {"type": "websocket", "path": "/app/vnc/abc-123", "headers": []}
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4401}


@pytest.mark.asyncio
async def test_rejects_permission_denied(monkeypatch) -> None:
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, perm_ok=False)
    backend = _FakeVncBackend(_FakeVncSession([]))
    set_vnc_backend(backend)
    receive, send, sent = _drive(None, [])
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4403}
    # Never accepted, never opened a port-forward.
    assert backend.opened_for == []
    assert {"type": "websocket.accept"} not in sent


@pytest.mark.asyncio
async def test_rejects_non_running_or_non_vnc_task(monkeypatch) -> None:
    """When the task is missing / not RUNNING / not vnc_enabled the
    resolver returns None and the relay closes 4410 before accept."""
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch, task_ok=False)
    backend = _FakeVncBackend(_FakeVncSession([]))
    set_vnc_backend(backend)
    receive, send, sent = _drive(None, [])
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4410}
    assert backend.opened_for == []
    assert {"type": "websocket.accept"} not in sent


# ---- happy path -----------------------------------------------------


@pytest.mark.asyncio
async def test_relays_frames_both_directions(monkeypatch) -> None:
    """Client bytes reach the pod via sendall; pod bytes reach the
    client as websocket.send bytes frames. The session is opened once,
    audited once, and closed on teardown."""
    from core.schema import vnc_ws as mod

    audited: list[dict] = []
    _patch_auth(monkeypatch, audit_sink=audited)

    session = _FakeVncSession(pod_chunks=[b"RFB 003.008\n", b"framebuffer"])
    backend = _FakeVncBackend(session)
    set_vnc_backend(backend)

    incoming = [
        {"type": "websocket.receive", "bytes": b"client->pod-1"},
        {"type": "websocket.receive", "bytes": b"client->pod-2"},
        {"type": "websocket.disconnect"},
    ]
    receive, send, sent = _drive(None, incoming)
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }

    await mod.vnc_ws_application(scope, receive, send)

    # Accepted, then closed 1000.
    assert sent[0] == {"type": "websocket.accept"}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}

    # Opened exactly once for the right task + audited once.
    assert backend.opened_for == ["abc-123"]
    assert len(audited) == 1
    assert audited[0]["task_guid"] == "abc-123"
    assert audited[0]["actor_user_id"] == 99

    # Client -> pod: both byte frames forwarded in order.
    assert session.sent_to_pod == [b"client->pod-1", b"client->pod-2"]

    # Pod -> client: both scripted chunks delivered as binary sends.
    pod_to_client = [m["bytes"] for m in sent if m.get("type") == "websocket.send"]
    assert b"RFB 003.008\n" in pod_to_client
    assert b"framebuffer" in pod_to_client

    # Session torn down.
    assert session.closed is True


@pytest.mark.asyncio
async def test_pod_eof_closes_relay(monkeypatch) -> None:
    """When the pod side EOFs (recv returns b"") with no further client
    frames, the relay tears down cleanly and closes 1000."""
    from core.schema import vnc_ws as mod

    _patch_auth(monkeypatch)
    # Single chunk then immediate EOF (empty list -> the fake would
    # sleep, so give it a chunk and rely on the disconnect to end).
    session = _FakeVncSession(pod_chunks=[b"hello"])
    set_vnc_backend(_FakeVncBackend(session))

    incoming = [{"type": "websocket.disconnect"}]
    receive, send, sent = _drive(None, incoming)
    scope = {
        "type": "websocket",
        "path": "/app/vnc/abc-123",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.vnc_ws_application(scope, receive, send)

    assert sent[0] == {"type": "websocket.accept"}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}
    assert session.closed is True
