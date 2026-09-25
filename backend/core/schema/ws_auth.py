"""Shared WebSocket auth: sessionid cookie -> user + tenant.

The exec WS relay (:mod:`core.schema.exec_ws`) and the VNC WS relay
(:mod:`core.schema.vnc_ws`) both need to turn an ASGI WebSocket
handshake into an authenticated, tenant-scoped identity before they
accept the connection. That resolution chain used to live inline in
``ws_views.py`` (and was lazily imported by ``exec_ws.py``); it is
extracted here so every WS surface shares one code path.

The four primitives are:

  * :func:`_parse_cookies` / :func:`_split_cookie_header` — pull the
    cookie jar out of an ASGI scope dict or a Starlette request/WS.
  * :func:`_resolve_user_from_sessionid` — Django session -> user.
  * :func:`_resolve_tenant_for_user` — user + session -> TenantContext,
    mirroring ``TenantContextMiddleware``.

``ws_views.py`` re-exports these so the GraphQL-WS shim keeps importing
them from its old home; new relays import from here directly.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

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


def _header_from_scope(scope_or_request: Any, name: str) -> str:
    if hasattr(scope_or_request, "headers"):
        return scope_or_request.headers.get(name) or ""
    if isinstance(scope_or_request, dict):
        for key, value in scope_or_request.get("headers", []):
            if key.lower() == name.encode("ascii"):
                return value.decode("latin-1")
    return ""


def _bearer_from_scope(scope_or_request: Any) -> str:
    """Pull a plaintext ``Authorization: Bearer <token>`` value out of an
    ASGI scope or Starlette request. Empty string when absent.

    CLI clients (``astro exec``) authenticate the WS handshake with the
    same ``alft_`` API token they use for GraphQL rather than a browser
    session cookie, so the exec/VNC relays accept either credential."""
    raw = _header_from_scope(scope_or_request, "authorization")
    if raw[:7].lower() == "bearer ":
        return raw[7:].strip()
    return ""


def _bearer_from_connection_params(payload: Any) -> str:
    """Pull a plaintext bearer out of a graphql-ws / graphql-transport-ws
    ``connection_init`` payload — the wire form of the client's
    ``connectionParams``. Empty string when absent or malformed.

    The mobile app never sends the ``sessionid`` cookie
    ``CookieAwareGraphQLWs`` reads its identity from — it has no cookie
    jar — so it carries its ``alft_`` bearer in ``connectionParams``
    instead: ``{"authorization": "Bearer <token>"}`` (astrolift-mobile's
    Apollo client factory, #1943). The key is matched case-insensitively
    because ``connectionParams`` is a client-chosen JSON object, not real
    HTTP headers."""
    if not isinstance(payload, dict):
        return ""
    for key, value in payload.items():
        if not isinstance(key, str) or key.lower() != "authorization":
            continue
        if isinstance(value, str) and value[:7].lower() == "bearer ":
            return value[7:].strip()
    return ""


@sync_to_async
def _resolve_user_and_tenant_from_bearer(token: str, organization_guid: str = ""):
    """``(user, TenantContext)`` from an ``alft_`` API token; see
    :func:`_resolve_bearer_identity`."""
    user, tenant, _row = _resolve_bearer_identity(token, organization_guid)
    return user, tenant


_resolve_bearer_identity_async = sync_to_async(lambda token: _resolve_bearer_identity(token))


def _resolve_bearer_identity(token: str, organization_guid: str = ""):
    """Resolve ``(user, TenantContext, token row)`` from an ``alft_`` API token.

    The row is what carries the token's scope ceiling: HTTP pins it on the
    ``current_api_token`` contextvar so ``check_permission`` caps the user's
    grants at the token's scopes, and a WS subscription must do the same
    (#1943) or a scoped-down token would act with the user's full grants.

    Mirrors ``ApiTokenMiddleware`` for the WS path: the token's user +
    organization become the authenticated, tenant-scoped identity.
    Returns ``(AnonymousUser, None)`` for a missing/invalid/revoked
    token so the relay closes with the right code. A conflicting selected
    organization returns the authenticated user with no tenant, so the
    relay rejects the target instead of retargeting the credential."""
    if not token:
        return AnonymousUser(), None, None
    try:
        from astrolift_identity.api_tokens import (
            token_matches_organization,
            touch_token,
            verify_token,
        )
        from core.tenancy import TenantContext

        row = verify_token(token)
        if row is None:
            return AnonymousUser(), None, None
        user = row.user
        if user is None or not getattr(user, "is_active", True):
            return AnonymousUser(), None, None
        if not token_matches_organization(row, organization_guid):
            return user, None, row
        try:
            touch_token(row)
        except Exception:  # noqa: BLE001
            pass
        return (
            user,
            TenantContext(organization_id=row.organization_id, actor_user_id=user.pk),
            row,
        )
    except Exception:  # noqa: BLE001
        logger.exception("ws bearer resolution failed")
        return AnonymousUser(), None, None


def pin_ws_identity(context) -> None:
    """Pin what the WS handshake resolved onto this task's contextvars: the
    tenant, and the API token whose scopes cap every permission check
    (#1943). Subscription resolvers call it first, as HTTP middleware does
    for a request."""
    from astrolift_identity.api_tokens import set_current_api_token
    from core.tenancy import set_current_tenant

    tenant = getattr(context, "_ws_tenant", None)
    if tenant is not None:
        set_current_tenant(tenant)
    token = getattr(context, "_ws_api_token", None)
    if token is not None:
        set_current_api_token(token)


@sync_to_async
def _resolve_tenant_for_user(user, session_data: dict) -> Any:
    """Mirror what TenantContextMiddleware does, but synchronously
    in a sync_to_async wrapper so we don't block the loop."""
    from astrolift_identity.models import Organization
    from core.tenancy import TenantContext

    if user is None or not getattr(user, "is_authenticated", False):
        return None

    # Active org guid pinned on the X-Astrolift-Organization header
    # in HTTP requests; on WS we read it from the session if a
    # client put it there, otherwise fall back to single-membership.
    from astrolift_identity.api_tokens import active_member_organizations, session_may_act_in

    active_org_guid = session_data.get("astrolift_active_org") or ""
    if active_org_guid:
        # Pinned by the client, so honoured only for an org the user is an
        # active member of (or the platform operator), as over HTTP (#1925).
        org_id = Organization.objects.filter(guid=active_org_guid).values_list("pk", flat=True).first()
        return (
            TenantContext(organization_id=org_id, actor_user_id=user.pk)
            if session_may_act_in(user, org_id)
            else None
        )

    # Membership inference, live memberships only (#1925). The web client
    # pins its org in a cookie the relay does not read, so a multi-org user
    # has always landed on their first org here; it is still an org they
    # belong to, and a task in another of their orgs is refused as before.
    org_id = active_member_organizations(user.pk).order_by("pk").values_list("pk", flat=True).first()
    if org_id is None:
        return None
    return TenantContext(organization_id=org_id, actor_user_id=user.pk)
