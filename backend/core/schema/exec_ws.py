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

Two kinds of thing answer to the first path segment. Usually it is a
``RegisteredApp`` slug. It may also be an ``AgentBox`` slug (#129): a box
is a warm pod that exists to be attached to, and this relay is the only
way in that does not hand the operator direct cluster access. The relay
resolves the slug to whichever kind owns it *before* it decides which
grant to demand — a box is authorized as a box, never by widening the
app path's tenancy check to admit one.

Auth re-uses the cookie-aware resolution from ws_views: the user must be
authenticated, scoped to a tenant that owns the target, and hold the
grant that target's kind requires at the target's own scope —
``app.exec_pod`` on the app, ``agent_box.attach`` on the box's project,
team, agent app or org (#1866) — under a bearer token's scope ceiling.
Misses close with the right WS code:

  4401 — unauthenticated
  4403 — the caller lacks the grant for this target's kind
  4404 — nothing to exec into: the path is not a valid
         /app/exec/<target>/<workload>, or no app and no box in the
         caller's tenant answers to <target>

4403 and 4404 are deliberately distinct (#129). They used to be the same
code, so a slug the relay could not resolve — a box, before this module
knew what one was — reached the operator as "you lack app.exec_pod",
which sent them to an administrator for a grant they already held. A
target belonging to *another* tenant closes 4404 alongside one that
never existed, because confirming existence across a tenant boundary is
itself a leak.

Every one of these closes happens **before** ``websocket.accept``, which
is what makes the ASGI server reject the handshake at HTTP level with a
403. The CLI keys its error message off that status rather than off the
WS code, so the pre-accept ordering is load-bearing: moving any of these
closes after accept would silently change what ``astro exec`` prints.

Production wiring: the connection-time backend resolution looks up the
app's ``default_tenant_cluster`` (or per-environment cluster), or for a
box the org's agent cluster plus the namespace frozen on the row, and
calls ``driver.exec_in_pod(...)`` via the ``astrolift-providers`` SDK.
The backend factory is swappable so tests can stand up a recording fake
without touching the kubernetes client.

Audit: every successful ``open`` frame emits an ``Event`` carrying the
actor, target, pod, container, and command — ``app.exec_pod.opened`` for
an app, ``agent_box.attached`` for a box. The log/event row is the
single source-of-truth for who got an interactive shell where.
"""

from __future__ import annotations

import collections
import json
import logging
from typing import Any

from asgiref.sync import sync_to_async

from core.permissions import Permission, route_auth

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
        tty: bool = True,
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
        tty: bool = True,
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


# What the first path segment turned out to name. The relay needs this
# before it can pick a grant, an audit event type, or a cluster.
TARGET_APP = "app"
TARGET_BOX = "box"


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
def _check_box_in_tenant(*, box_slug: str, tenant_org_id) -> bool:
    """Does a live agent box in *this* tenant answer to ``box_slug``?

    Filtered on the organization explicitly: ``@tenant_scoped`` asserts a
    tenant, it does not filter, so a by-slug fetch that trusts the slug
    alone would hand one org a session inside another org's pod.
    """
    from astrolift_agents.models import AgentBox

    if not tenant_org_id:
        return False
    return AgentBox.objects.filter(
        slug=box_slug,
        organization_id=tenant_org_id,
        deleted_at__isnull=True,
    ).exists()


async def _resolve_exec_target(*, target_slug: str, tenant_org_id) -> str | None:
    """Which kind of thing ``target_slug`` names inside the caller's
    tenant, or None when nothing does.

    Apps win a slug collision: they are the surface every existing client
    addresses, and a box slug is platform-derived (``box-<agent>-u<id>``)
    so it cannot be chosen to shadow one.
    """
    if await _check_app_in_tenant(app_slug=target_slug, tenant_org_id=tenant_org_id):
        return TARGET_APP
    if await _check_box_in_tenant(box_slug=target_slug, tenant_org_id=tenant_org_id):
        return TARGET_BOX
    return None


def _holds(permission, *, scope_for, tenant_org_id, actor_user_id, api_token=None) -> bool:
    """Deny-by-default check of one permission at the target's scope.

    ``scope_for`` names the target once the tenant is installed, since a
    scope resolves inside the caller's org. A bearer's token row is pinned
    so its scopes cap the check, as HTTP middleware pins it (#1866).

    Also installs the tenant context the backend's cluster resolution
    reads back out of the contextvar once the handshake is through.
    """
    from astrolift_identity.api_tokens import set_current_api_token
    from core.permissions import PermissionDenied, check_permission
    from core.tenancy import TenantContext, set_current_tenant

    if not tenant_org_id:
        return False
    set_current_tenant(
        TenantContext(
            organization_id=tenant_org_id,
            actor_user_id=actor_user_id,
        ),
    )
    if api_token is not None:
        set_current_api_token(api_token)
    try:
        check_permission(permission, scope=scope_for())
    except PermissionDenied:
        return False
    return True


@sync_to_async
def _check_exec_permission(*, app_slug: str, tenant_org_id, actor_user_id, api_token=None) -> bool:
    """Resolver-entry permission check — deny-by-default. Returns True
    iff the resolved tenant + user holds ``app.exec_pod`` on this app."""
    from astrolift_lifecycle.scopes import live_app_scope
    from core.permissions import Permission

    return _holds(
        Permission.APP_EXEC_POD,
        scope_for=lambda: live_app_scope("app_slug")({"app_slug": app_slug}),
        tenant_org_id=tenant_org_id,
        actor_user_id=actor_user_id,
        api_token=api_token,
    )


@sync_to_async
def _check_box_attach_permission(*, box_slug: str, tenant_org_id, actor_user_id, api_token=None) -> bool:
    """Deny-by-default gate on ``agent_box.attach`` — the box-shaped
    counterpart to ``app.exec_pod``. See the permission's own comment
    for why attaching an agent is not authorized by an app grant.

    Checked at the box's own scope, and the box must be among the rows the
    grant reaches, so a team token cannot reach past its team (#1866)."""
    from astrolift_agents.scopes import agent_box_scope
    from astrolift_agents.visibility import agent_boxes
    from core.permissions import Permission

    if not _holds(
        Permission.AGENT_BOX_ATTACH,
        scope_for=lambda: agent_box_scope("slug", Permission.AGENT_BOX_ATTACH)({"slug": box_slug}),
        tenant_org_id=tenant_org_id,
        actor_user_id=actor_user_id,
        api_token=api_token,
    ):
        return False
    return (
        agent_boxes(tenant_org_id, Permission.AGENT_BOX_ATTACH)
        .filter(slug=box_slug, organization_id=tenant_org_id)
        .exists()
    )


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


@sync_to_async
def _record_box_attach(
    *,
    box_slug: str,
    pod_name: str,
    container: str,
    command: list[str],
    tenant_org_id,
    actor_user_id,
) -> None:
    """Audit row + ``last_attached_at`` stamp for a box session (#129).

    Both are best-effort for the same reason the app audit is: a writer
    hiccup must not cost the operator the session they just opened.
    Dropping the stamp is safe because it is advisory telemetry for the
    operator surface — "when did someone last ask for a way in". It is
    *not* what reaps the box. Real idleness is measured inside the pod,
    where tmux can see an attached client the instant it detaches; a
    control-plane timestamp cannot, and a missed write here would reap
    somebody sitting right there.
    """
    from django.utils import timezone

    from astrolift_agents.models import AgentBox
    from core.events import Event

    try:
        box = AgentBox.objects.filter(
            slug=box_slug,
            organization_id=tenant_org_id,
            deleted_at__isnull=True,
        ).first()
        Event.emit(
            "agent_box.attached",
            payload={
                "box_slug": box_slug,
                "pod_name": pod_name,
                "container": container,
                "command": [str(c)[:512] for c in (command or [])][:32],
            },
            resource_kind="agent_box",
            resource_id=box_slug,
            actor_user_id=actor_user_id,
            organization_id=tenant_org_id,
        )
        if box is not None:
            box.last_attached_at = timezone.now()
            box.save(update_fields=["last_attached_at", "updated_at", "version"])
    except Exception:  # noqa: BLE001
        logger.exception("exec_ws: box attach record failed")


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


@route_auth(
    credential="session cookie or alft_ API bearer (its scopes cap the check)",
    permissions=(Permission.APP_EXEC_POD, Permission.AGENT_BOX_ATTACH),
    scope="app.exec_pod at the named app; agent_box.attach at the named box's project, team, agent app or org",
)
async def exec_ws_application(scope: dict, receive, send) -> None:
    """ASGI WebSocket handler for /app/exec/<app>/<workload>."""
    if scope["type"] != "websocket":
        await send({"type": "websocket.close", "code": 4400})
        return

    parsed = _parse_target_id(scope.get("path", ""))
    if parsed is None:
        await send({"type": "websocket.close", "code": 4404})
        return
    app_slug, workload_slug = parsed

    from core.schema.ws_auth import (
        _bearer_from_scope,
        _header_from_scope,
        _parse_cookies,
        _resolve_bearer_identity,
        _resolve_tenant_for_user,
        _resolve_user_from_sessionid,
    )

    # CLI (`astro exec`) presents an ``alft_`` API token on the handshake;
    # the browser presents a sessionid cookie. Try the bearer first, fall
    # back to the cookie so both surfaces share one relay.
    bearer = _bearer_from_scope(scope)
    tenant = None
    api_token = None
    if bearer:
        user, tenant, api_token = await sync_to_async(_resolve_bearer_identity)(
            bearer, _header_from_scope(scope, "x-astrolift-organization")
        )
        if getattr(user, "is_authenticated", False) and tenant is None:
            await send({"type": "websocket.close", "code": 4403})
            return
    else:
        cookies = _parse_cookies(scope)
        session_key = cookies.get("sessionid", "")
        user, session_data = await _resolve_user_from_sessionid(session_key)
        if getattr(user, "is_authenticated", False):
            tenant = await _resolve_tenant_for_user(user, session_data)

    if not getattr(user, "is_authenticated", False):
        await send({"type": "websocket.close", "code": 4401})
        return

    org_id = getattr(tenant, "organization_id", None) if tenant else None
    actor_user_id = getattr(tenant, "actor_user_id", None) if tenant else None

    # Resolve *what* the slug names before asking whether the caller may
    # have it, so an unknown or foreign slug reports 4404 rather than
    # borrowing the permission code and reading as a missing grant.
    target = await _resolve_exec_target(
        target_slug=app_slug,
        tenant_org_id=org_id,
    )
    if target is None:
        await send({"type": "websocket.close", "code": 4404})
        return

    # Deny-by-default permission gate, per target kind. ``app.exec_pod``
    # is the operator-shell-into-pod capability for applications;
    # ``agent_box.attach`` is its counterpart for an agent box. RBAC
    # seeds grant them to different sets of roles, and the denial lands
    # at the handshake so the frontend never sees the terminal pane.
    granted = (
        await _check_box_attach_permission(
            box_slug=app_slug,
            tenant_org_id=org_id,
            actor_user_id=actor_user_id,
            api_token=api_token,
        )
        if target == TARGET_BOX
        else await _check_exec_permission(
            app_slug=app_slug,
            tenant_org_id=org_id,
            actor_user_id=actor_user_id,
            api_token=api_token,
        )
    )
    if not granted:
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
                # TTY allocation is the client's call: interactive consoles
                # ask for a PTY (raw mode, resize); piped/non-interactive
                # callers ask for a plain pipe so stdout isn't echo-doubled
                # or CRLF-mangled. Default True for older clients.
                tty = bool(frame.get("tty", True))
                session = await backend.open(
                    app_slug=app_slug,
                    workload_slug=workload_slug,
                    container=container,
                    command=command,
                    send_stdout=_send_stdout,
                    send_stderr=_send_stderr,
                    send_exit=_send_exit,
                    send_error=_send_error,
                    tty=tty,
                )
                if target == TARGET_BOX:
                    await _record_box_attach(
                        box_slug=app_slug,
                        pod_name=workload_slug,
                        container=container,
                        command=command,
                        tenant_org_id=org_id,
                        actor_user_id=actor_user_id,
                    )
                else:
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
            elif kind == "stdin_eof":
                # Half-close the remote stdin (the CLI sends this on its
                # local stdin EOF) so a piped read-to-EOF command finishes,
                # while keeping the session open for its output + exit code.
                if session is not None:
                    close_stdin = getattr(session, "close_stdin", None)
                    if close_stdin is not None:
                        await close_stdin()
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
