"""JSON-RPC WebSocket transport, sharing exec's handshake authentication."""

import asyncio
import json
import logging
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings

from astrolift_agents.services import agent_host_terminal as terminals
from astrolift_agents.services.agent_host import AgentHost, HostError
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from core.permissions import Permission, route_auth
from core.schema.ws_auth import (
    _bearer_from_scope,
    _header_from_scope,
    _parse_cookies,
    _resolve_bearer_identity,
    _resolve_tenant_for_user,
    _resolve_user_from_sessionid,
)
from core.tenancy import tenant_context

log = logging.getLogger(__name__)
MAX_FRAME_BYTES = 256 * 1024


@sync_to_async
def _bearer_identity(bearer, org):
    return _resolve_bearer_identity(bearer, org)


async def _identity(scope):
    bearer = _bearer_from_scope(scope)
    selected = _header_from_scope(scope, "x-astrolift-organization")
    if bearer:
        return await _bearer_identity(bearer, selected)
    key = _parse_cookies(scope).get(settings.SESSION_COOKIE_NAME, "")
    user, data = await _resolve_user_from_sessionid(key)
    if selected:
        data["astrolift_active_org"] = selected
    tenant = await _resolve_tenant_for_user(user, data)
    return user, tenant, None


@sync_to_async
def _run(host, token, method=None, params=None):
    marker = set_current_api_token(token)
    try:
        with tenant_context(host.tenant):
            return (
                host.catalog_notifications()
                if method == "_catalog_notifications"
                else host.command(method, params)
                if method
                else host.poll()
            )
    finally:
        reset_current_api_token(marker)


@sync_to_async
def _terminal_run(host, token, fn, *args):
    marker = set_current_api_token(token)
    try:
        with tenant_context(host.tenant):
            return fn(host, *args)
    finally:
        reset_current_api_token(marker)


@route_auth(
    credential="session cookie or alft_ API bearer with fresh per-message identity and scope checks",
    permissions=(
        Permission.AGENT_READ,
        Permission.AGENT_TASK_SEND_INPUT,
        Permission.AGENT_DISPATCH,
        Permission.AGENT_BOX_ATTACH,
    ),
    scope="task at its workload app, project, team or org; terminal at its box plus actor-private lease",
)
async def agent_host_ws_application(scope, receive, send):
    if scope.get("path") not in {"/app/ahp", "/app/ahp/"}:
        await send({"type": "websocket.close", "code": 4404})
        return
    first = await receive()
    if first["type"] != "websocket.connect":
        return
    user, tenant, token = await _identity(scope)
    if not getattr(user, "is_authenticated", False):
        await send({"type": "websocket.close", "code": 4401})
        return
    if tenant is None or not tenant.organization_id:
        await send({"type": "websocket.close", "code": 4403})
        return
    host = AgentHost(user, tenant)
    await send({"type": "websocket.accept"})

    async def output(data):
        await send({"type": "websocket.send", "text": json.dumps(data, separators=(",", ":"))})

    owner, sessions = uuid4(), {}

    async def open_terminals():
        for uri in set(sessions) - host.subscriptions:
            await sessions.pop(uri).close()
            await _terminal_run(host, token, terminals.release, owner, uri)
        for uri in host.subscriptions:
            if not uri.startswith("ahp-terminal:/"):
                continue
            if uri in sessions:
                if not await _terminal_run(host, token, terminals.renew, uri, owner):
                    raise HostError("Terminal attachment lease was lost")
                continue
            box = await _terminal_run(host, token, terminals.acquire, uri, owner)
            from astrolift_agents.services.agent_box import box_attach_command
            from core.schema.exec_ws import get_exec_backend

            async def data(value, resource=uri):
                await _terminal_run(
                    host, token, terminals.emit, resource, owner, {"type": "terminal/data", "data": value}
                )

            async def exited(code, resource=uri):
                await _terminal_run(
                    host,
                    token,
                    terminals.emit,
                    resource,
                    owner,
                    {"type": "terminal/exited", **({"exitCode": code} if code is not None else {})},
                )

            async def error(value, resource=uri):
                await data(value + "\r\n", resource)

            marker = set_current_api_token(token)
            try:
                with tenant_context(host.tenant):
                    sessions[uri] = await get_exec_backend().open(
                        app_slug=box.slug,
                        workload_slug=box.pod_name,
                        container="agent-box",
                        command=box_attach_command(box),
                        send_stdout=data,
                        send_stderr=data,
                        send_exit=exited,
                        send_error=error,
                        tty=True,
                    )
            finally:
                reset_current_api_token(marker)

    pending = asyncio.create_task(receive())
    try:
        while True:
            done, _ = await asyncio.wait([pending], timeout=1)
            # Authentication, membership and token scope are live for every
            # poll and write, including connections opened before revocation.
            fresh_user, fresh_tenant, token = await _identity(scope)
            if not getattr(fresh_user, "is_authenticated", False) or fresh_tenant != tenant:
                await send({"type": "websocket.close", "code": 4403})
                return
            host.user = fresh_user
            if pending in done:
                event = pending.result()
                if event["type"] == "websocket.disconnect":
                    return
                pending = asyncio.create_task(receive())
                raw = event.get("text")
                if raw is None or len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    await send({"type": "websocket.close", "code": 1009})
                    return
                request_id = None
                try:
                    request = json.loads(raw)
                    if (
                        not isinstance(request, dict)
                        or request.get("jsonrpc") != "2.0"
                        or not isinstance(request.get("method"), str)
                    ):
                        raise HostError("Invalid JSON-RPC message", -32600)
                    request_id = request.get("id")
                    if request_id is not None and (
                        isinstance(request_id, bool) or not isinstance(request_id, (str, int))
                    ):
                        raise HostError("Invalid request id", -32600)
                    method = request["method"]
                    params = request.get("params", {})
                    if (
                        method == "dispatchAction"
                        and isinstance(params, dict)
                        and str(params.get("channel", "")).startswith("ahp-terminal:/")
                    ):
                        uri = params["channel"]
                        if uri not in sessions:
                            raise HostError("Subscribe to the terminal before sending input")
                        row_id, result, fresh = await _terminal_run(
                            host, token, terminals.reserve_input, params
                        )
                        if fresh:
                            delivered = False
                            try:
                                action = params["action"]
                                if action["type"] == "terminal/input":
                                    await sessions[uri].stdin(action["data"])
                                else:
                                    await sessions[uri].resize(action["rows"], action["cols"])
                                delivered = True
                            finally:
                                result = await _terminal_run(
                                    host, token, terminals.finish_input, row_id, delivered
                                )
                    else:
                        result = await _run(host, token, method, params)
                    if method == "dispatchAction":
                        actions = await _run(host, token)
                        for action in actions:
                            await output({"jsonrpc": "2.0", "method": "action", "params": action})
                        if not any(action["serverSeq"] == result["serverSeq"] for action in actions):
                            await output({"jsonrpc": "2.0", "method": "action", "params": result})
                    elif request_id is not None:
                        await output({"jsonrpc": "2.0", "id": request_id, "result": result})
                except (json.JSONDecodeError, HostError) as exc:
                    error = (
                        {"code": exc.code, "message": str(exc)}
                        if isinstance(exc, HostError)
                        else {"code": -32700, "message": "Invalid JSON"}
                    )
                    if isinstance(exc, HostError) and exc.data is not None:
                        error["data"] = exc.data
                    await output({"jsonrpc": "2.0", "id": request_id, "error": error})
            try:
                await open_terminals()
                for action in await _run(host, token):
                    await output({"jsonrpc": "2.0", "method": "action", "params": action})
                for notification in await _run(host, token, "_catalog_notifications"):
                    await output({"jsonrpc": "2.0", **notification})
            except HostError as exc:
                await output(
                    {
                        "jsonrpc": "2.0",
                        "method": "astrolift/attachUnavailable",
                        "params": exc.data or {"reason": str(exc)},
                    }
                )
                await send({"type": "websocket.close", "code": 4403})
                return
    except Exception:
        log.exception("agent host connection failed")
        await send({"type": "websocket.close", "code": 1011})
    finally:
        for session in sessions.values():
            await session.close()
        await _terminal_run(host, token, terminals.release, owner)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
