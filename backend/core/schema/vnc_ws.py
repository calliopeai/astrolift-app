"""WebSocket VNC relay — RFB-over-WS into a running agent task (#877).

Endpoint: ``/app/vnc/<task-guid>``

This relay IS the websockify: the agent pod serves raw RFB on :5900
(x11vnc) and ships no noVNC/websockify of its own; the browser runs the
noVNC client and speaks RFB over this websocket. Unlike the exec relay
(:mod:`core.schema.exec_ws`) there is no JSON control protocol — RFB is
a raw binary stream — so this handler just pumps bytes in both directions
between the browser WebSocket and the agent pod's raw RFB port (5900)
reached via a kubernetes port-forward.

Auth mirrors the exec relay and reuses the shared cookie/session/tenant
resolution from :mod:`core.schema.ws_auth`. The caller must be:

  * authenticated (sessionid cookie resolves to an active user),
  * scoped to the tenant org that owns the referenced AgentTask,
  * holding the ``agent_task.watch`` permission, and
  * watching a task that is RUNNING and VNC-capable
    (the frozen ``AgentTask.vnc_enabled`` column).

Close codes:

  4400 — scope is not a websocket
  4401 — unauthenticated
  4403 — tenant mismatch OR ``agent_task.watch`` denied
  4404 — path is not a valid ``/app/vnc/<task-guid>``
  4410 — task is not RUNNING / not VNC-capable (no live framebuffer)

The actual port-forward is opened through a pluggable
:class:`VncBackend`. The production backend lives in
``core.cluster_vnc.K8sVncBackend`` and is registered from
``core.apps.CoreConfig.ready``; the default stub closes immediately so
management commands, schema export, and tests need no cluster.

Audit: every accepted session emits an ``agent_task.vnc.opened`` Event,
best-effort, mirroring ``exec_ws._audit_exec_open``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

# Container port the agent's raw RFB server (x11vnc) listens on (see the
# k8s_job spawner, which adds containerPort 5900 when vnc is enabled).
VNC_PORT = 5900


# ---- Backend protocol ------------------------------------------------


class VncSession:
    """Open port-forward to a pod's raw RFB port.

    ``recv`` returns the next chunk of bytes from the pod (``b""`` on
    EOF); ``sendall`` writes client bytes to the pod; ``close`` tears
    the forward down.
    """

    async def recv(self) -> bytes:
        raise NotImplementedError

    async def sendall(self, data: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class VncBackend:
    """Pluggable backend that opens a :class:`VncSession` for a task."""

    async def open(self, *, task_guid: str) -> VncSession:
        raise NotImplementedError


# Default stub --------------------------------------------------------


class _StubVncSession(VncSession):
    """Returns EOF immediately so the relay loop ends cleanly."""

    async def recv(self) -> bytes:
        return b""

    async def sendall(self, data: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


class _StubVncBackend(VncBackend):
    """No-op backend used when no production backend is registered
    (management commands, schema export, tests). Logs and returns a
    session that EOFs immediately, so the WS closes without a cluster."""

    async def open(self, *, task_guid: str) -> VncSession:
        logger.info(
            "vnc backend not wired in this build — connect a real "
            "port-forward backend via core.schema.vnc_ws.set_vnc_backend "
            "at deploy time (task=%s)",
            task_guid,
        )
        return _StubVncSession()


_BACKEND: VncBackend = _StubVncBackend()


def set_vnc_backend(backend: VncBackend) -> None:
    """Swap the VNC backend (production wiring + tests)."""
    global _BACKEND
    _BACKEND = backend


def get_vnc_backend() -> VncBackend:
    return _BACKEND


# ---- Auth + tenant + task resolution --------------------------------


@sync_to_async
def _resolve_vnc_task(*, task_guid: str, tenant_org_id) -> dict | None:
    """Resolve a VNC target for ``task_guid`` within the tenant org.

    Deny-by-default. Returns ``None`` when the task is missing, owned
    by another org, not RUNNING, or not VNC-capable — the handler maps
    that to the appropriate close code. On success returns the pod name
    needed to port-forward.
    """
    from astrolift_agents.models import AgentTask

    if not tenant_org_id:
        return None

    task = (
        AgentTask.objects.filter(
            guid=task_guid,
            organization_id=tenant_org_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if task is None:
        return None
    if task.status != AgentTask.Status.RUNNING:
        return None
    # Gate on the FROZEN task.vnc_enabled column — the same flag the
    # GraphQL read surface exposes and that drives vnc_url publication.
    # Reading the live spec here would let a spec edit/delete after spawn
    # refuse a session that a published vnc_url already promised. The
    # cluster/namespace/pod for the port-forward are re-resolved
    # downstream in core.cluster_vnc, independently of the spec.
    if not task.vnc_enabled:
        return None
    if not task.pod_name:
        return None
    return {"pod_name": task.pod_name}


@sync_to_async
def _check_vnc_permission(*, tenant_org_id, actor_user_id) -> bool:
    """Resolver-entry permission check — deny-by-default. Returns True
    iff the resolved tenant + user holds ``agent_task.watch``."""
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
        check_permission(Permission.AGENT_TASK_WATCH)
    except PermissionDenied:
        return False
    return True


@sync_to_async
def _audit_vnc_open(*, task_guid: str, pod_name: str, tenant_org_id, actor_user_id) -> None:
    """Append-only audit row for a successful VNC open. Best-effort —
    a writer hiccup must not drop the operator's session."""
    from core.events import Event

    try:
        Event.emit(
            "agent_task.vnc.opened",
            payload={"task_guid": task_guid, "pod_name": pod_name},
            resource_kind="agent_task",
            resource_id=task_guid,
            actor_user_id=actor_user_id,
            organization_id=tenant_org_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("vnc_ws: audit emission failed")


# ---- Path parser -----------------------------------------------------


def _parse_task_guid(path: str) -> str | None:
    """Path shape: ``/app/vnc/<task-guid>``. Returns the guid or None."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "vnc":
        return parts[2]
    return None


# ---- ASGI app --------------------------------------------------------


async def vnc_ws_application(scope: dict, receive, send) -> None:
    """ASGI WebSocket handler for /app/vnc/<task-guid>."""
    if scope["type"] != "websocket":
        await send({"type": "websocket.close", "code": 4400})
        return

    task_guid = _parse_task_guid(scope.get("path", ""))
    if task_guid is None:
        await send({"type": "websocket.close", "code": 4404})
        return

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

    # Deny-by-default permission gate before any task lookup.
    if not await _check_vnc_permission(
        tenant_org_id=org_id,
        actor_user_id=actor_user_id,
    ):
        await send({"type": "websocket.close", "code": 4403})
        return

    resolved = await _resolve_vnc_task(task_guid=task_guid, tenant_org_id=org_id)
    if resolved is None:
        # Task missing / cross-tenant / not RUNNING / not VNC-capable.
        # 4410 mirrors the old stub's HTTP 410 "no live framebuffer".
        await send({"type": "websocket.close", "code": 4410})
        return
    pod_name = resolved["pod_name"]

    await send({"type": "websocket.accept"})

    backend = get_vnc_backend()
    session: VncSession | None = None
    tasks: list[asyncio.Task] = []

    try:
        session = await backend.open(task_guid=task_guid)
        await _audit_vnc_open(
            task_guid=task_guid,
            pod_name=pod_name,
            tenant_org_id=org_id,
            actor_user_id=actor_user_id,
        )

        # Both directions run as concurrent pumps; the relay lives until
        # whichever side closes first. RFB is a raw byte stream, so neither
        # pump interprets frame contents — they just move bytes.
        async def _pump_pod_to_client() -> None:
            while True:
                chunk = await session.recv()
                if not chunk:
                    # Pod socket EOF — the framebuffer is gone.
                    return
                await send({"type": "websocket.send", "bytes": chunk})

        async def _pump_client_to_pod() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message["type"] != "websocket.receive":
                    continue
                data = message.get("bytes")
                if data:
                    await session.sendall(data)

        tasks = [
            asyncio.ensure_future(_pump_pod_to_client()),
            asyncio.ensure_future(_pump_client_to_pod()),
        ]
        # First side to finish (client disconnect or pod EOF) ends the
        # session; the finally block cancels the other pump.
        done, _pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            # Surface an unexpected pump failure into the log; a clean
            # return (disconnect/EOF) raises nothing.
            exc = task.exception()
            if exc is not None:
                logger.exception("vnc_ws: relay pump failed", exc_info=exc)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()
        await send({"type": "websocket.close", "code": 1000})
