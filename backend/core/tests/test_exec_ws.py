"""Tests for the container-exec WebSocket protocol (#280).

The auth + ASGI dispatch live behind starlette/cookies/sessions —
those parts are exercised in functional tests that hit the live
runserver. These unit tests cover the protocol-level pieces:

  - path parser
  - frame dispatch (open / stdin / resize / close / unknown)
  - backend swap
  - the default stub's behavior
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.schema.exec_ws import (
    ExecBackend,
    ExecSession,
    _parse_target_id,
    _StubExecBackend,
    get_exec_backend,
    set_exec_backend,
)


def test_parse_path_pair() -> None:
    assert _parse_target_id("/app/exec/acme/web") == ("acme", "web")


def test_parse_path_with_extra_segments_takes_first_two() -> None:
    """Trailing path segments (a deployment-id pinned to the URL,
    say) don't break the parser — it takes app + workload from
    the first two slots after /app/exec/."""
    assert _parse_target_id("/app/exec/acme/web/abc-123") == (
        "acme",
        "web",
    )


def test_parse_path_too_short_returns_none() -> None:
    assert _parse_target_id("/app/exec") is None
    assert _parse_target_id("/app/exec/onlyapp") is None
    assert _parse_target_id("/other/path") is None


def test_default_backend_is_either_stub_or_production_wired() -> None:
    """Module-level default is the stub; ``core.apps.CoreConfig.ready``
    swaps in :class:`K8sExecBackend` when Django boots. Either is a
    valid runtime state — the test asserts the swap is at least one of
    the two expected types instead of pinning to the stub. The real
    proof that swap works lives in :func:`test_set_exec_backend_swaps`.
    """
    from core.cluster_exec import K8sExecBackend

    backend = get_exec_backend()
    assert isinstance(backend, (_StubExecBackend, K8sExecBackend))


def test_set_exec_backend_swaps() -> None:
    class _Other(ExecBackend):
        async def open(self, **kw: Any) -> ExecSession:
            return ExecSession()  # type: ignore[abstract]

    prior = get_exec_backend()
    try:
        set_exec_backend(_Other())
        assert isinstance(get_exec_backend(), _Other)
    finally:
        set_exec_backend(prior)


@pytest.mark.asyncio
async def test_stub_backend_emits_not_wired_message_then_exits() -> None:
    backend = _StubExecBackend()
    captured: dict[str, list] = {
        "stdout": [],
        "stderr": [],
        "exit": [],
        "error": [],
    }

    async def stdout(d: str) -> None:
        captured["stdout"].append(d)

    async def stderr(d: str) -> None:
        captured["stderr"].append(d)

    async def exit_code(c: int) -> None:
        captured["exit"].append(c)

    async def err(m: str) -> None:
        captured["error"].append(m)

    session = await backend.open(
        app_slug="acme",
        workload_slug="web",
        container="main",
        command=["sh"],
        send_stdout=stdout,
        send_stderr=stderr,
        send_exit=exit_code,
        send_error=err,
    )

    assert captured["stderr"]
    assert "not wired" in captured["stderr"][0].lower()
    assert captured["exit"] == [2]
    # Stub session accepts close + stdin no-op
    await session.stdin("ignored")
    await session.close()


# ---- Frame-level dispatch test (in-process, no real WS) -----------


class _RecordingBackend(ExecBackend):
    """Records open/stdin/close calls so we can assert the
    dispatcher routed frames correctly."""

    def __init__(self) -> None:
        self.opens: list[dict] = []
        self.stdin: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.closes = 0
        self.stdin_closes = 0

    async def open(self, **kw: Any) -> ExecSession:
        self.opens.append(
            {
                "app_slug": kw["app_slug"],
                "workload_slug": kw["workload_slug"],
                "container": kw["container"],
                "command": kw["command"],
            }
        )
        backend = self

        class _Sess(ExecSession):
            async def stdin(self, data: str) -> None:
                backend.stdin.append(data)

            async def resize(self, rows: int, cols: int) -> None:
                backend.resizes.append((rows, cols))

            async def close(self) -> None:
                backend.closes += 1

            async def close_stdin(self) -> None:
                backend.stdin_closes += 1

        return _Sess()


@pytest.mark.asyncio
async def test_dispatcher_routes_frames_to_backend(
    monkeypatch,
) -> None:
    """Drive the ASGI handler with a scripted receive/send pair
    that bypasses cookie auth (we patch the module's auth
    helpers to grant access)."""
    from core.schema import exec_ws as mod

    backend = _RecordingBackend()
    set_exec_backend(backend)

    # Stub auth + tenant resolution so the test doesn't need a
    # real Django session.
    async def _ok_user(_session_key):
        class _U:
            is_authenticated = True

        return _U(), {}

    async def _ok_tenant(_user, _data):
        class _T:
            organization_id = 1

        return _T()

    async def _ok_app(*, app_slug, tenant_org_id):
        return True

    async def _ok_perm(*, tenant_org_id, actor_user_id):
        return True

    async def _noop_audit(**_kw):
        return None

    import core.schema.ws_auth as ws_auth_mod

    monkeypatch.setattr(
        ws_auth_mod,
        "_resolve_user_from_sessionid",
        _ok_user,
    )
    monkeypatch.setattr(
        ws_auth_mod,
        "_resolve_tenant_for_user",
        _ok_tenant,
    )
    monkeypatch.setattr(mod, "_check_app_in_tenant", _ok_app)
    monkeypatch.setattr(mod, "_check_exec_permission", _ok_perm)
    monkeypatch.setattr(mod, "_audit_exec_open", _noop_audit)

    incoming: list[dict] = [
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "type": "open",
                    "container": "main",
                    "command": ["sh", "-c", "echo hi"],
                }
            ),
        },
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "stdin", "data": "more"}),
        },
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "type": "resize",
                    "rows": 24,
                    "cols": 80,
                }
            ),
        },
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "stdin_eof"}),
        },
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "close"}),
        },
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [(b"cookie", b"sessionid=fake")],
    }

    await mod.exec_ws_application(scope, receive, send)

    assert backend.opens == [
        {
            "app_slug": "acme",
            "workload_slug": "web",
            "container": "main",
            "command": ["sh", "-c", "echo hi"],
        }
    ]
    assert backend.stdin == ["more"]
    assert backend.resizes == [(24, 80)]
    assert backend.stdin_closes == 1
    # Close runs from explicit 'close' frame + finally block.
    assert backend.closes >= 1
    assert sent[0] == {"type": "websocket.accept"}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}


@pytest.mark.asyncio
async def test_dispatcher_rejects_unauthed(monkeypatch) -> None:
    """No sessionid cookie → 4401 close."""
    from core.schema import exec_ws as mod

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [],
    }
    await mod.exec_ws_application(scope, receive, send)
    # Last frame is the close with 4401
    assert sent[-1]["type"] == "websocket.close"
    assert sent[-1]["code"] == 4401


@pytest.mark.asyncio
async def test_dispatcher_rejects_bad_path() -> None:
    """Path doesn't match /app/exec/<app>/<workload> → 4404."""
    from core.schema import exec_ws as mod

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/just-app",
        "headers": [],
    }
    await mod.exec_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4404}


# ---- Permission + audit + replay gates (#423) --------------------


def _patch_authed(monkeypatch, *, perm_ok: bool, audit_sink: list | None = None):
    """Common test scaffolding — patches the auth helpers so the
    dispatcher's WS handshake succeeds (or fails on the permission
    gate when ``perm_ok=False``) without standing up Django session
    + RBAC seeds."""
    import core.schema.ws_auth as ws_auth_mod
    from core.schema import exec_ws as mod

    async def _ok_user(_session_key):
        class _U:
            is_authenticated = True

        return _U(), {}

    async def _ok_tenant(_user, _data):
        class _T:
            organization_id = 1
            actor_user_id = 99

        return _T()

    async def _ok_app(*, app_slug, tenant_org_id):
        return True

    async def _perm(*, tenant_org_id, actor_user_id):
        return perm_ok

    async def _audit(**kw):
        if audit_sink is not None:
            audit_sink.append(kw)

    monkeypatch.setattr(ws_auth_mod, "_resolve_user_from_sessionid", _ok_user)
    monkeypatch.setattr(ws_auth_mod, "_resolve_tenant_for_user", _ok_tenant)
    monkeypatch.setattr(mod, "_check_app_in_tenant", _ok_app)
    monkeypatch.setattr(mod, "_check_exec_permission", _perm)
    monkeypatch.setattr(mod, "_audit_exec_open", _audit)


@pytest.mark.asyncio
async def test_dispatcher_rejects_when_permission_denied(monkeypatch) -> None:
    """App.exec_pod denied at the WS handshake → 4403 close before the
    backend ever sees the open frame."""
    from core.schema import exec_ws as mod

    _patch_authed(monkeypatch, perm_ok=False)

    backend = _RecordingBackend()
    set_exec_backend(backend)

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.exec_ws_application(scope, receive, send)
    assert sent[-1] == {"type": "websocket.close", "code": 4403}
    assert backend.opens == []


@pytest.mark.asyncio
async def test_dispatcher_audits_successful_open(monkeypatch) -> None:
    """A successful open frame emits exactly one audit row carrying the
    actor + app + container + command."""
    from core.schema import exec_ws as mod

    audited: list[dict] = []
    _patch_authed(monkeypatch, perm_ok=True, audit_sink=audited)

    backend = _RecordingBackend()
    set_exec_backend(backend)

    incoming: list[dict] = [
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "type": "open",
                    "container": "main",
                    "command": ["sh", "-c", "id"],
                }
            ),
        },
        {"type": "websocket.receive", "text": json.dumps({"type": "close"})},
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.exec_ws_application(scope, receive, send)

    assert len(audited) == 1
    row = audited[0]
    assert row["app_slug"] == "acme"
    assert row["workload_slug"] == "web"
    assert row["container"] == "main"
    assert row["command"] == ["sh", "-c", "id"]
    assert row["actor_user_id"] == 99


@pytest.mark.asyncio
async def test_dispatcher_emits_ready_frame_after_open(monkeypatch) -> None:
    """The dispatcher sends a {"type": "ready"} frame as soon as the
    backend's open returns — lets the FE clear its 'connecting' banner
    before the first stdout byte arrives."""
    from core.schema import exec_ws as mod

    _patch_authed(monkeypatch, perm_ok=True)
    set_exec_backend(_RecordingBackend())

    incoming: list[dict] = [
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "open", "container": "main", "command": ["sh"]}),
        },
        {"type": "websocket.receive", "text": json.dumps({"type": "close"})},
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.exec_ws_application(scope, receive, send)

    text_frames = [json.loads(m["text"]) for m in sent if m["type"] == "websocket.send"]
    assert {"type": "ready"} in text_frames


@pytest.mark.asyncio
async def test_dispatcher_replays_buffered_lines(monkeypatch) -> None:
    """A client {"type": "replay"} frame returns the server-side ring
    buffer in a single ``replay`` frame. The backend's send_stdout
    populates the buffer as it streams."""
    from core.schema import exec_ws as mod

    _patch_authed(monkeypatch, perm_ok=True)

    class _StreamingBackend(ExecBackend):
        async def open(self, **kw: Any) -> ExecSession:
            send_stdout = kw["send_stdout"]
            await send_stdout("line 1\n")
            await send_stdout("line 2\n")

            class _Sess(ExecSession):
                async def stdin(self, data: str) -> None:
                    return None

                async def close(self) -> None:
                    return None

            return _Sess()

    set_exec_backend(_StreamingBackend())

    incoming: list[dict] = [
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "open", "container": "main", "command": ["sh"]}),
        },
        {"type": "websocket.receive", "text": json.dumps({"type": "replay"})},
        {"type": "websocket.receive", "text": json.dumps({"type": "close"})},
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        if incoming:
            return incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/app/exec/acme/web",
        "headers": [(b"cookie", b"sessionid=fake")],
    }
    await mod.exec_ws_application(scope, receive, send)

    text_frames = [json.loads(m["text"]) for m in sent if m["type"] == "websocket.send"]
    replays = [f for f in text_frames if f.get("type") == "replay"]
    assert len(replays) == 1
    lines = replays[0]["lines"]
    # ring buffer holds every stdout chunk in order
    assert {"type": "stdout", "data": "line 1\n"} in lines
    assert {"type": "stdout", "data": "line 2\n"} in lines


def test_bearer_from_scope_extracts_token() -> None:
    """_bearer_from_scope pulls the plaintext token out of an ASGI scope's
    Authorization header (CLI exec path) and ignores non-bearer schemes."""
    from core.schema.ws_auth import _bearer_from_scope

    scope = {"headers": [(b"authorization", b"Bearer alft_secrettoken")]}
    assert _bearer_from_scope(scope) == "alft_secrettoken"
    # Case-insensitive scheme, whitespace trimmed.
    assert _bearer_from_scope({"headers": [(b"authorization", b"bearer  x ")]}) == "x"
    # No header / wrong scheme → empty.
    assert _bearer_from_scope({"headers": []}) == ""
    assert _bearer_from_scope({"headers": [(b"authorization", b"Basic abc")]}) == ""
