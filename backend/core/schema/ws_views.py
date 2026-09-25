"""
GraphQL-WS handler with cookie-based authentication.

Strawberry's vanilla ``strawberry.asgi.GraphQL`` doesn't know how
to authenticate WebSocket connections — its ``get_context`` returns
the bare Starlette request/websocket. We need:

  1. Pull the ``sessionid`` cookie out of the WS handshake.
  2. Resolve it to a user via Django's session machinery.
  3. Build a request-shaped object so ``StrawberryContext`` works
     unchanged across HTTP and WS.
  4. Resolve the tenant context the same way TenantContextMiddleware
     does for HTTP — via Member/single-membership inference.

Authenticated subscribers get a populated ``info.context.user`` and
the ``@tenant_scoped`` decorator passes; unauthenticated WS clients
fall through to anonymous, and most subscriptions return ``complete``
because the tenant guard fires.

The mobile app has no cookie jar, so it cannot ride the sessionid path
above. It sends its ``alft_`` bearer in the graphql-ws ``connectionParams``
instead, which only becomes readable once the client's ``connection_init``
message arrives — after ``get_context`` already ran. ``on_ws_connect``
(below) is Strawberry's hook for that moment; a bearer there resolves
through the same ``_resolve_user_and_tenant_from_bearer`` the exec/VNC
relays use and replaces whatever the cookie resolved (#1943).

This is intentionally a thin shim over the StrawberryContext that
HTTP resolvers already use — we don't want WS subscriptions to drift
from HTTP queries on what ``info.context.user`` means.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from strawberry.asgi import GraphQL

from core.schema.context import StrawberryContext

# The cookie/session/tenant resolution chain lives in ``ws_auth`` so
# every WS surface (this GraphQL-WS shim + the exec/VNC relays) shares
# one implementation. Re-exported here for back-compat — existing
# importers (and tests that monkeypatch these names on this module)
# keep working unchanged.
from core.schema.ws_auth import (  # noqa: F401
    _bearer_from_connection_params,
    _parse_cookies,
    _resolve_bearer_identity_async,
    _resolve_tenant_for_user,
    _resolve_user_and_tenant_from_bearer,
    _resolve_user_from_sessionid,
    _split_cookie_header,
)

logger = logging.getLogger(__name__)


class _WsRequestShim:
    """HttpRequest-shaped proxy so StrawberryContext (and downstream
    cached properties like ``user``, ``session``) work the same on
    WS as on HTTP. Only attributes the resolver chain actually
    reaches are populated."""

    def __init__(self, ws, user, session_data: dict):
        self._ws = ws
        self.user = user
        # session_data is read-only on WS; resolvers that try to
        # write would fail, which is the right behavior — session
        # writes belong to the HTTP path.
        self.session = SimpleNamespace(**session_data)
        self.session.get = session_data.get  # type: ignore[attr-defined]
        self.headers = ws.headers
        self.method = "WEBSOCKET"
        self.path = ws.url.path
        # core.schema.context references request.user.profile in some
        # paths; an AnonymousUser already raises on .profile access
        # which is fine — those paths aren't reachable from
        # subscription resolvers we ship today.

    def get_host(self):
        return self._ws.url.hostname or ""


class CookieAwareGraphQLWs(GraphQL):
    """Strawberry ASGI wrapper that pulls the sessionid cookie out
    of the WS handshake, resolves the user + tenant, and stuffs them
    into the StrawberryContext so subscription resolvers see the
    same ``info.context`` shape as HTTP queries."""

    async def get_context(self, request, response):
        # WebSocket connections — request is a starlette WebSocket.
        # HTTP requests fall through to the parent (we still serve
        # GraphiQL + introspection via this same handler, even
        # though the production HTTP path is the Django CoreStrawberryView).
        is_ws = response is request  # strawberry passes ws as both
        if not is_ws:
            return await super().get_context(request, response)

        cookies = _parse_cookies(request)
        sessionid = cookies.get("sessionid", "")
        user, session_data = await _resolve_user_from_sessionid(sessionid)
        tenant = await _resolve_tenant_for_user(user, session_data)

        shim = _WsRequestShim(request, user, session_data)
        ctx = StrawberryContext(shim)
        # Pin the resolved tenant directly on the context so the
        # subscription resolver picks it up via core.tenancy
        # (which reads from a contextvar). We set the contextvar at
        # the resolver-entry layer.
        ctx._ws_tenant = tenant  # type: ignore[attr-defined]
        # Strawberry only stashes the connection_init payload onto the
        # context when it already has a ``connection_params`` attribute
        # (graphql_transport_ws.handlers.handle_connection_init checks
        # with hasattr before writing it). Pre-declare it so on_ws_connect
        # below can read the mobile app's connectionParams once the
        # client's first WS message arrives (#1943).
        ctx.connection_params = {}  # type: ignore[attr-defined]
        return ctx

    async def on_ws_connect(self, context):
        """Runs once graphql-ws / graphql-transport-ws receives the
        client's ``connection_init`` message, with its payload already
        stashed on ``context.connection_params`` by the base handler (see
        ``get_context`` above — this is the earliest point a WS handshake
        can see connectionParams at all).

        A bearer here goes through ``_resolve_user_and_tenant_from_bearer``,
        the exact function ``ApiTokenAuthMiddleware`` and the exec/VNC
        relays use, so it carries every rule an HTTP bearer does: scope
        ceilings at the resolver layer, revocation, and the active-org-
        membership check inside ``verify_token`` (#1910/#1925). It replaces
        whatever the cookie resolved in ``get_context`` — matching the
        exec/VNC relays' rule that a presented bearer is the only credential
        tried, so a bad one cannot quietly fall back to a coincidental
        cookie session. No bearer in ``connectionParams`` leaves the
        cookie-resolved identity from ``get_context`` untouched.
        """
        bearer = _bearer_from_connection_params(getattr(context, "connection_params", None))
        if bearer:
            user, tenant, row = await _resolve_bearer_identity_async(bearer)
            context.request.user = user
            context._ws_tenant = tenant  # type: ignore[attr-defined]
            # The token's scope ceiling travels with it (see pin_ws_identity).
            context._ws_api_token = row  # type: ignore[attr-defined]
        return await super().on_ws_connect(context)
