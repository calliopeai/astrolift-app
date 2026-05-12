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

import asyncio
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


def test_default_backend_is_stub() -> None:
    assert isinstance(get_exec_backend(), _StubExecBackend)


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

    import core.schema.ws_views as ws_views_mod

    monkeypatch.setattr(
        ws_views_mod,
        "_resolve_user_from_sessionid",
        _ok_user,
    )
    monkeypatch.setattr(
        ws_views_mod,
        "_resolve_tenant_for_user",
        _ok_tenant,
    )
    monkeypatch.setattr(mod, "_check_app_in_tenant", _ok_app)

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
