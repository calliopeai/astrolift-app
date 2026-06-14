"""WebSocket exec stream — `kubectl exec -it` over WS (#280, hardened in #423).

Endpoint: ``/app/exec/<app-slug>/<workload-slug>``

Frame protocol (JSON-line for control, raw bytes for stream):

    Client → Server:
      {"type": "open", "command": ["sh", "-c", "ls /tmp"],
       "container": "main"}            ← issue command
      {"type": "stdin", "data": "..."} ← write stdin (utf-8)
      {"type": "resize", "rows": 24, "cols": 80}
      {"type": "replay"}               ← request server-side buffer replay
      {"type": "close"}

    Server → Client:
      {"type": "stdout", "data": "..."}
      {"type": "stderr", "data": "..."}
      {"type": "exit", "code": 0}
      {"type": "error", "message": "..."}
      {"type": "ready"}                 ← session opened, ready for stdin
      {"type": "replay", "lines": [...]}← stdout/stderr ring buffer for reconnect

Auth re-uses the cookie-aware resolution from ws_views: the user must be
authenticated, scoped to a tenant that owns the referenced app, and hold
the ``app.exec_pod`` permission. Misses close with the right WS code:

  4401 — unauthenticated
  4403 — tenant mismatch OR ``app.exec_pod`` denied
  4404 — path is not a valid /app/exec/<app>/<workload>

Production wiring: the connection-time backend resolution looks up the
app's ``default_tenant_cluster`` (or per-environment cluster) and calls
``driver.exec_in_pod(...)`` via the ``astrolift-providers`` SDK. The
backend factory is swappable so tests can stand up a recording fake
without touching the kubernetes client.

Audit: every successful ``open`` frame emits an ``app.exec_pod.opened``
``Event`` carrying the actor, app, pod, container, and command. The
log/event row is the single source-of-truth for who got an interactive
shell where.
"""

from __future__ import annotations

import collections
import json
import logging
from typing import Any

from asgiref.sync import sync_to_async

# ws_views.py top-level imports starlette (via strawberry.asgi); import
# its helpers lazily so this module loads in environments that don't
# have starlette (e.g. management commands, schema export).

logger = logging.getLogger(__name__)


# Server-side ring-buffer size per session — replayed verbatim on a
# {"type": "replay"} request from the client after a transient
# disconnect. The cap is small enough that an idle session doesn't
# pin many KB per WS connection but large enough to cover a typical
# burst (e.g. ``kubectl exec ls -laR /``).
_REPLAY_BUFFER_LINES = 1000


# ---- Backend protocol ------------------------------------------------


class ExecBackend:
    """Pluggable backend for actual exec sessions.

    Production wiring lives in ``core.cluster_exec.K8sExecBackend`` and
    is registered from ``core/apps.py`` at app-ready time. Tests inject a
    recording fake.
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
    ) -> ExecSession:
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
    """No-op backend that emits a friendly 'not yet wired' message and
    exits with code 2 so the frontend renders the right state without
    blowing up the WS handshake. Used when no production backend is
    registered (e.g. management commands, schema export)."""

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


@sync_to_async
def _check_exec_permission(*, tenant_org_id, actor_user_id) -> bool:
    """Resolver-entry permission check — deny-by-default. Returns True
    iff the resolved tenant + user holds ``app.exec_pod``."""
    from core.permissions import (
        Permission,
        PermissionDenied,
        check_permission,
    )
    from core.tenancy import TenantContext, set_current_tenant

    if not tenant_org_id:
        return False
    set_current_tenant(
        TenantContext(
            organization_id=tenant_org_id,
            actor_user_id=actor_user_id,
        ),
    )
    try:
        check_permission(Permission.APP_EXEC_POD)
    except PermissionDenied:
        return False
    return True


@sync_to_async
def _audit_exec_open(
    *,
    app_slug: str,
    workload_slug: str,
    container: str,
    command: list[str],
    tenant_org_id,
    actor_user_id,
) -> None:
    """Append-only audit row for a successful exec open. Failures are
    swallowed — audit emission is best-effort and we don't want a
    writer hiccup to drop the operator's session."""
    from astrolift_registry.models import RegisteredApp
    from core.events import Event

    try:
        app_pk = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                organization_id=tenant_org_id,
                deleted_at__isnull=True,
            )
            .values_list("pk", flat=True)
            .first()
        )
        Event.emit(
            "app.exec_pod.opened",
            payload={
                "app_slug": app_slug,
                "workload_slug": workload_slug,
                "container": container,
                # Cap the audited command — operators occasionally
                # paste large heredocs and we don't want the audit row
                # to balloon.
                "command": [str(c)[:512] for c in (command or [])][:32],
            },
            resource_kind="app",
            resource_id=app_slug,
            actor_user_id=actor_user_id,
            organization_id=tenant_org_id,
            registered_app_id=app_pk,
        )
    except Exception:  # noqa: BLE001
        logger.exception("exec_ws: audit emission failed")


# ---- Path parser -----------------------------------------------------


def _parse_target_id(path: str) -> tuple[str, str] | None:
    """Path shape: ``/app/exec/<app_slug>/<workload_slug>``.

    A bare ``<id>`` path resolves at the deployment row, but the most
    common UX is the app + workload pair so the frontend doesn't need
    a deployment-id round-trip — return the pair.

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

    from core.schema.ws_auth import (
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
    actor_user_id = getattr(tenant, "actor_user_id", None) if tenant else None
    if not await _check_app_in_tenant(
        app_slug=app_slug,
        tenant_org_id=org_id,
    ):
        await send({"type": "websocket.close", "code": 4403})
        return

    # Deny-by-default permission gate. ``app.exec_pod`` is the
    # operator-shell-into-pod capability. RBAC seeds grant it to org
    # owners + cluster operators; everyone else is denied at the
    # handshake so the frontend never sees the terminal pane.
    if not await _check_exec_permission(
        tenant_org_id=org_id,
        actor_user_id=actor_user_id,
    ):
        await send({"type": "websocket.close", "code": 4403})
        return

    await send({"type": "websocket.accept"})

    # Server-side replay buffer — every stdout/stderr line the backend
    # produces gets a copy in this deque. On reconnect (or any client
    # that wants to catch up after a brief disconnect), a {"type":
    # "replay"} frame from the client returns the deque's contents.
    replay: collections.deque[dict[str, Any]] = collections.deque(
        maxlen=_REPLAY_BUFFER_LINES,
    )

    async def _send_json(payload: dict) -> None:
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(payload),
            }
        )

    async def _send_stdout(data: str) -> None:
        replay.append({"type": "stdout", "data": data})
        await _send_json({"type": "stdout", "data": data})

    async def _send_stderr(data: str) -> None:
        replay.append({"type": "stderr", "data": data})
        await _send_json({"type": "stderr", "data": data})

    async def _send_exit(code: int) -> None:
        replay.append({"type": "exit", "code": code})
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
                container = str(frame.get("container", "main"))
                command = list(frame.get("command", []) or [])
                session = await backend.open(
                    app_slug=app_slug,
                    workload_slug=workload_slug,
                    container=container,
                    command=command,
                    send_stdout=_send_stdout,
                    send_stderr=_send_stderr,
                    send_exit=_send_exit,
                    send_error=_send_error,
                )
                await _audit_exec_open(
                    app_slug=app_slug,
                    workload_slug=workload_slug,
                    container=container,
                    command=command,
                    tenant_org_id=org_id,
                    actor_user_id=actor_user_id,
                )
                # Signal the frontend that the backend handshake
                # succeeded and stdin will now be accepted. Lets the
                # client clear any "connecting…" banner before the
                # first stdout byte lands.
                await _send_json({"type": "ready"})
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
            elif kind == "replay":
                # Drain the ring buffer back to the client in order.
                # Wrapped in its own frame type so the client can
                # tell catch-up output apart from new live output.
                await _send_json(
                    {
                        "type": "replay",
                        "lines": list(replay),
                    }
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
