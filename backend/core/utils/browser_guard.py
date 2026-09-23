"""Cross-site request guards for session-authenticated surfaces (#1926).

GraphQL and the WebSocket relays accept the browser session cookie.
Apps and previews can run user code on a host that is same-site with the
control plane, where a ``SameSite=Lax`` cookie still rides along, so a
cookie alone does not prove the request came from the Astrolift UI.
"""

from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse

# Every UI GraphQL client sends this. A form cannot set a custom header,
# and a cross-origin fetch that does needs a CORS preflight, which only
# ``CORS_ALLOWED_ORIGINS`` passes.
UI_HEADER = "X-Platform"


def require_ui_header_for_session(view_func):
    """Refuse a session-cookie POST that lacks the UI header.

    Bearer callers (CLI, mobile, agents) and cookieless requests pass
    through unchanged.
    """

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if (
            request.method == "POST"
            and settings.SESSION_COOKIE_NAME in request.COOKIES
            and not request.headers.get("Authorization")
            and not request.headers.get(UI_HEADER)
        ):
            return JsonResponse(
                {"errors": [{"message": f"Session requests must send the {UI_HEADER} header."}]},
                status=403,
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def websocket_origin_allowed(origin: str, host: str) -> bool:
    """Whether a cookie-authenticated WebSocket handshake may proceed.

    Browsers always send ``Origin`` on a WebSocket handshake and CORS does
    not apply to WebSockets, so the origin must be checked here: it must
    be the host serving the request or one of ``CORS_ALLOWED_ORIGINS``.
    """
    origin = (origin or "").strip().rstrip("/")
    if not origin:
        return False
    if origin in {str(value).rstrip("/") for value in settings.CORS_ALLOWED_ORIGINS}:
        return True
    netloc = urlsplit(origin).netloc
    return bool(netloc) and netloc.lower() == (host or "").strip().lower()
