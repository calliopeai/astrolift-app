"""
ASGI config — multi-protocol routing.

HTTP requests fall through to Django (the existing WSGI surface,
served as ASGI by ``get_asgi_application``). WebSocket requests for
``/app/gql/config/ws/`` are handled by Strawberry's GraphQL-WS
ASGI app, which delivers GraphQL subscriptions.

Why split: Django's URL routing is HTTP-only; WebSocket scope has
to be dispatched at the ASGI layer before Django sees the request.
The ``_select`` callable here is the simplest dispatch — switch on
``scope['type']`` and ``scope['path']``.

Setting ``ASGI_APPLICATION = 'config.asgi.application'`` in
settings.py makes ``runserver`` (Django 6+) auto-switch to ASGI.
For production, use ``daphne`` or ``uvicorn`` against this same
``application`` callable.
"""

import os
from importlib import import_module

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django before importing anything that touches models.
django_application = get_asgi_application()


def _build_websocket_app():
    """Build the Strawberry WebSocket app lazily.

    Imports happen here (not at module top) so the app loader doesn't
    pull strawberry.asgi (which depends on starlette) into every Django
    management command.

    Uses the cookie-aware subclass so the WS handshake picks up the
    sessionid cookie and resolves user + tenant — without that the
    subscription resolver sees an empty tenant context and bails.
    """
    from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL

    from config.schema import schema
    from core.schema.ws_views import CookieAwareGraphQLWs

    return CookieAwareGraphQLWs(schema, subscription_protocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL])


_websocket_app = None

# The WebSocket routes besides GraphQL's, as (path prefix, module, handler).
# GraphQL serves the schema, whose fields the surface guardrail walks one by
# one; each handler here declares its own ``route_auth`` (#1866).
WEBSOCKET_ROUTES = (
    ("/app/ahp", "astrolift_agents.agent_host_ws", "agent_host_ws_application"),
    ("/app/exec/", "core.schema.exec_ws", "exec_ws_application"),
    ("/app/vnc/", "core.schema.vnc_ws", "vnc_ws_application"),
)


def _websocket_origin_ok(scope) -> bool:
    """Refuse a cookie-authenticated handshake from a foreign origin.

    CORS does not cover WebSockets, and the session cookie rides a
    handshake from any same-site host, including app and preview hosts
    that run user code (#1926). Only the cookie is ambient: a bearer
    handshake (CLI) or a cookieless one (mobile sends its token in
    ``connectionParams``) proves nothing about the page, so it skips.
    """
    from django.conf import settings

    from core.schema.ws_auth import _bearer_from_scope, _header_from_scope, _parse_cookies
    from core.utils.browser_guard import websocket_origin_allowed

    if _bearer_from_scope(scope) or settings.SESSION_COOKIE_NAME not in _parse_cookies(scope):
        return True
    return websocket_origin_allowed(_header_from_scope(scope, "origin"), _header_from_scope(scope, "host"))


async def application(scope, receive, send):
    global _websocket_app
    if scope["type"] == "websocket":
        path = scope.get("path", "")
        if not _websocket_origin_ok(scope):
            await send({"type": "websocket.close", "code": 4403})
            return
        if path.startswith("/app/gql/config/ws"):
            if _websocket_app is None:
                _websocket_app = _build_websocket_app()
            return await _websocket_app(scope, receive, send)
        for prefix, module, handler in WEBSOCKET_ROUTES:
            if path.startswith(prefix):
                return await getattr(import_module(module), handler)(scope, receive, send)
        # Unknown WS path — close politely.
        await send({"type": "websocket.close", "code": 4404})
        return

    # HTTP / lifespan → Django.
    return await django_application(scope, receive, send)
