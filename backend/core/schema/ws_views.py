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

This is intentionally a thin shim over the StrawberryContext that
HTTP resolvers already use — we don't want WS subscriptions to drift
from HTTP queries on what ``info.context.user`` means.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from importlib import import_module
from strawberry.asgi import GraphQL

from core.schema.context import StrawberryContext

logger = logging.getLogger(__name__)


def _parse_cookies(scope_or_request: Any) -> dict[str, str]:
    """Pull cookies out of an ASGI scope or Starlette request."""
    cookies: dict[str, str] = {}
    headers = []
    if hasattr(scope_or_request, "headers"):
        # Starlette Request / WebSocket exposes headers as a Headers
        # mapping; iterating yields lowercase names.
        try:
            cookie_header = scope_or_request.headers.get("cookie") or ""
        except Exception:
            cookie_header = ""
        if cookie_header:
            return _split_cookie_header(cookie_header)
        headers = scope_or_request.headers
    elif isinstance(scope_or_request, dict):
        headers = scope_or_request.get("headers", [])
        for name, value in headers:
            if name.lower() == b"cookie":
                return _split_cookie_header(value.decode("latin-1"))
    return cookies


def _split_cookie_header(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        out[name.strip()] = value.strip()
    return out


@sync_to_async
def _resolve_user_from_sessionid(session_key: str):
    """Look up the Django user for a sessionid value.

    Returns AnonymousUser when the session is missing/expired/has
    no user. Wrapped in sync_to_async because Django's session +
    user lookup hit the ORM.
    """
    if not session_key:
        return AnonymousUser(), {}
    try:
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        store = engine.SessionStore(session_key)
        if not store.exists(session_key):
            return AnonymousUser(), {}
        session_data = dict(store.items())
        user_id = session_data.get("_auth_user_id")
        if not user_id:
            return AnonymousUser(), session_data
        User = get_user_model()
        user = User.objects.filter(pk=user_id).first()
        if user is None or not getattr(user, "is_active", True):
            return AnonymousUser(), session_data
        return user, session_data
    except Exception:  # noqa: BLE001
        logger.exception("ws session resolution failed")
        return AnonymousUser(), {}


@sync_to_async
def _resolve_tenant_for_user(user, session_data: dict) -> Any:
    """Mirror what TenantContextMiddleware does, but synchronously
    in a sync_to_async wrapper so we don't block the loop."""
    from astrolift_identity.models import Member, Organization
    from core.tenancy import TenantContext

    if user is None or not getattr(user, "is_authenticated", False):
        return None

    # Active org guid pinned on the X-Astrolift-Organization header
    # in HTTP requests; on WS we read it from the session if a
    # client put it there, otherwise fall back to single-membership.
    active_org_guid = session_data.get("astrolift_active_org") or ""
    org_id = None
    if active_org_guid:
        org_id = (
            Organization.objects.filter(guid=active_org_guid)
            .values_list("pk", flat=True)
            .first()
        )

    if org_id is None:
        # Single-membership inference.
        org_id = (
            Member.objects.filter(
                user=user, scope_kind="ORG", is_active=True
            )
            .order_by("scope_id")
            .values_list("scope_id", flat=True)
            .first()
        )

    if org_id is None:
        return None
    return TenantContext(organization_id=org_id, actor_user_id=user.pk)


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
        return ctx
