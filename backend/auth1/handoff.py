"""The one-time code a finished sign-in gives the frontend instead of its session (#2429).

The IdP callback used to append ``token=Session <key>`` to the frontend's
``/auth/callback``. Every hop that sees a query string writes it down: the
load balancer, the nginx sidecar, the browser's history and the ``Referer`` of
the page's next requests. Anyone who could read those logs could take over
the session of whoever had just signed in.

The callback now appends ``code=<code>`` instead. A code is good once, for
:data:`TTL_SECONDS`, and only for the browser that signed in. The cache keeps
a digest of the code, mapped to a digest of that browser's session key.
``POST /app/auth1/handoff`` answers with the session only when the request's
own session cookie matches. That cookie is HttpOnly, set by the same callback
response, and is the cookie that carries the OAuth ``state`` across the IdP
round trip, so the hand-off works wherever sign-in does. A logged code is
useless to whoever reads the log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from core.permissions import route_auth

TTL_SECONDS = 60
_CODE = re.compile(r"[A-Za-z0-9_-]{43}")
_CACHE_PREFIX = "auth1:handoff:"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue(session_key: str) -> str:
    """A new code for the browser whose signed-in session is ``session_key``."""
    code = secrets.token_urlsafe(32)
    cache.set(_CACHE_PREFIX + _digest(code), _digest(session_key), timeout=TTL_SECONDS)
    return code


def redeem(code: object, session_key: str | None) -> bool:
    """Whether ``code`` was issued to ``session_key`` and is unused. Using it ends it.

    A request without the matching session leaves the code alone, so someone
    who read it in a log cannot spend it before the browser does.
    """
    if not session_key or not isinstance(code, str) or not _CODE.fullmatch(code):
        return False
    key = _CACHE_PREFIX + _digest(code)
    bound = cache.get(key)
    if not isinstance(bound, str) or not hmac.compare_digest(bound, _digest(session_key)):
        return False
    # Of two requests racing with one code, only one deletes the entry.
    return bool(cache.delete(key))


def _refused() -> JsonResponse:
    return JsonResponse(
        {"code": "HANDOFF_REFUSED", "detail": "This sign-in has expired or belongs to another browser."},
        status=400,
    )


@route_auth(
    credential="the sign-in's one-time code and the session cookie it was issued to",
    scope="the caller's own session: answers the key of the session its cookie names",
)
@never_cache
def handoff(request):
    """``{"code": ...}`` in, ``{"Authorization": "Session <key>"}`` out.

    The session comes from the cookie alone: a request that carries any
    ``Authorization`` header is refused, so a bearer cannot stand in for it.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _refused()
    code = body.get("code") if isinstance(body, dict) else None
    if request.headers.get("Authorization") or not request.user.is_authenticated:
        return _refused()
    session_key = request.session.session_key
    if not redeem(code, session_key):
        return _refused()
    return JsonResponse({"Authorization": f"Session {session_key}"})
