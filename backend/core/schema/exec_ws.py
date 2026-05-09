"""WebSocket exec stream — `kubectl exec -it` over WS (#280).

Endpoint: ``/app/exec/<deployment-or-workload-id>``

Frame protocol (JSON-line for control, raw bytes for stream):

    Client → Server:
      {"type": "open", "command": ["sh", "-c", "ls /tmp"],
       "container": "main"}            ← issue command
      {"type": "stdin", "data": "..."} ← write stdin (utf-8)
      {"type": "resize", "rows": 24, "cols": 80}
      {"type": "close"}

    Server → Client:
      {"type": "stdout", "data": "..."}
      {"type": "stderr", "data": "..."}
      {"type": "exit", "code": 0}
      {"type": "error", "message": "..."}

Auth re-uses the cookie-aware resolution from ws_views: the user
must be authenticated + scoped to a tenant that owns the
referenced deployment/workload.

Production wiring: the connection-time backend resolution looks up
the deployment's TenantCluster, retrieves its ClusterDriver via the
provider plugin registry (per spec 23-26), and calls
``driver.exec_in_pod(...)`` with stdin/stdout/stderr piped through
the WS frames.

This file ships the protocol + auth glue + a pluggable backend
factory. The default backend ``_StubExecBackend`` returns a
single 'exec backend not yet wired' message and exits with code
2 so the frontend's command-runner page can connect + display the
right message without exploding. Replace the factory at deploy
time with the real exec backend once the cluster registry can
hand back a driver instance per cluster.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from asgiref.sync import sync_to_async

# ws_views.py top-level imports starlette (via strawberry.asgi);
# import its helpers lazily so this module loads in environments
# that don't have starlette.

logger = logging.getLogger(__name__)


# ---- Backend protocol ------------------------------------------------


class ExecBackend:
    """Pluggable backend for actual exec sessions.

    Production wiring lives in astrolift_workflows or a sibling and
    looks like:

        class K8sExecBackend(ExecBackend):
            async def open(self, *, app_slug, workload_slug, container,
                           command, send_stdout, send_stderr, send_exit,
                           send_error):
                cluster = resolve_cluster_for_app(app_slug)
                driver = plugin_registry.cluster_driver_for(cluster)
                # Bridge driver.exec_in_pod's blocking iter into async
                # send_stdout / send_stderr; relay exit code on close.

    Tests inject a fake backend; the default is the stub below.
    """

    async def open(
        self,
        *,
        app_slug: str,
        workload_slug: str,
        container: str,
        command: list[str],
        send_stdout: Any,
        send_stderr: Any,
        send_exit: Any,
        send_error: Any,
    ) -> "ExecSession":
        raise NotImplementedError


class ExecSession:
    """Handle held by the dispatcher to push stdin + close."""

    async def stdin(self, data: str) -> None:
        raise NotImplementedError

    async def resize(self, rows: int, cols: int) -> None:
        # Best-effort; backends without TTY support no-op.
        return None

    async def close(self) -> None:
        raise NotImplementedError


# Default stub --------------------------------------------------------


class _StubExecSession(ExecSession):
    def __init__(self) -> None:
        self._closed = False

    async def stdin(self, data: str) -> None:
        return None

    async def close(self) -> None:
        self._closed = True


class _StubExecBackend(ExecBackend):
    """No-op backend that emits a friendly 'not yet wired' message
    and exits with code 2 so the frontend renders the right state
    without blowing up the WS handshake."""

    async def open(
        self,
        *,
        app_slug: str,
        workload_slug: str,
        container: str,
        command: list[str],
        send_stdout: Any,
        send_stderr: Any,
        send_exit: Any,
        send_error: Any,
    ) -> ExecSession:
        await send_stderr(
            "exec backend not wired in this build — connect a real "
            "ClusterDriver via core.schema.exec_ws.set_exec_backend "
            "at deploy time\n",
        )
        await send_exit(2)
        return _StubExecSession()


_BACKEND: ExecBackend = _StubExecBackend()


def set_exec_backend(backend: ExecBackend) -> None:
    """Swap the exec backend (production wiring + tests)."""
    global _BACKEND
    _BACKEND = backend


def get_exec_backend() -> ExecBackend:
    return _BACKEND


# ---- Auth + tenant resolution ---------------------------------------


@sync_to_async
def _check_app_in_tenant(*, app_slug: str, tenant_org_id) -> bool:
    from astrolift_registry.models import RegisteredApp

    if not tenant_org_id:
        return False
    return RegisteredApp.objects.filter(
        slug=app_slug,
        organization_id=tenant_org_id,
        deleted_at__isnull=True,
    ).exists()


# ---- Path parser -----------------------------------------------------


def _parse_target_id(path: str) -> tuple[str, str] | None:
    """Path shape: ``/app/exec/<app_slug>/<workload_slug>``.

    A bare ``<id>`` path resolves at the deployment row, but the
    most common UX is the app + workload pair so the frontend
    doesn't need a deployment-id round-trip — return the pair.

    Returns None on malformed paths."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 4 and parts[0] == "app" and parts[1] == "exec":
        return parts[2], parts[3]
    return None


# ---- ASGI app --------------------------------------------------------


async def exec_ws_application(scope: dict, receive, send) -> None:
    """ASGI WebSocket handler for /app/exec/<app>/<workload>."""
    if scope["type"] != "websocket":
        await send({"type": "websocket.close", "code": 4400})
        return

    target = _parse_target_id(scope.get("path", ""))
    if target is None:
        await send({"type": "websocket.close", "code": 4404})
        return
    app_slug, workload_slug = target

    from core.schema.ws_views import (
        _parse_cookies,
        _resolve_tenant_for_user,
        _resolve_user_from_sessionid,
    )

    cookies = _parse_cookies(scope)
    session_key = cookies.get("sessionid", "")
    user, session_data = await _resolve_user_from_sessionid(session_key)
    if not getattr(user, "is_authenticated", False):
        await send({"type": "websocket.close", "code": 4401})
        return

    tenant = await _resolve_tenant_for_user(user, session_data)
    org_id = getattr(tenant, "organization_id", None) if tenant else None
    if not await _check_app_in_tenant(
        app_slug=app_slug, tenant_org_id=org_id,
    ):
        await send({"type": "websocket.close", "code": 4403})
        return

    await send({"type": "websocket.accept"})

    async def _send_json(payload: dict) -> None:
        await send({
            "type": "websocket.send",
            "text": json.dumps(payload),
        })

    async def _send_stdout(data: str) -> None:
        await _send_json({"type": "stdout", "data": data})

    async def _send_stderr(data: str) -> None:
        await _send_json({"type": "stderr", "data": data})

    async def _send_exit(code: int) -> None:
        await _send_json({"type": "exit", "code": code})

    async def _send_error(message: str) -> None:
        await _send_json({"type": "error", "message": message})

    backend = get_exec_backend()
    session: ExecSession | None = None

    try:
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break
            if message["type"] != "websocket.receive":
                continue
            text = message.get("text") or ""
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                await _send_error("frame is not valid JSON")
                continue
            kind = frame.get("type")
            if kind == "open":
                if session is not None:
                    await _send_error("session already open")
                    continue
                session = await backend.open(
                    app_slug=app_slug,
                    workload_slug=workload_slug,
                    container=str(frame.get("container", "main")),
                    command=list(frame.get("command", []) or []),
                    send_stdout=_send_stdout,
                    send_stderr=_send_stderr,
                    send_exit=_send_exit,
                    send_error=_send_error,
                )
            elif kind == "stdin":
                if session is None:
                    await _send_error(
                        "no session open — send 'open' first",
                    )
                    continue
                await session.stdin(str(frame.get("data", "")))
            elif kind == "resize":
                if session is not None:
                    await session.resize(
                        rows=int(frame.get("rows", 0)),
                        cols=int(frame.get("cols", 0)),
                    )
            elif kind == "close":
                if session is not None:
                    await session.close()
                break
            else:
                await _send_error(
                    f"unknown frame type: {kind!r}",
                )
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass
        await send({"type": "websocket.close", "code": 1000})
