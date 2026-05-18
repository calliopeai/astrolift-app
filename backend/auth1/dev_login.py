"""
Dev-only login bypass.

Logs in the seeded ``dev@local.astrolift.net`` superuser via Django's
session machinery — no Auth0 round-trip — and redirects to ``next``.

Gated behind ``settings.DEBUG``: an authenticated request to this view
in a non-debug build returns 404 so it can't accidentally ship to
production. Rate-limited per IP for the same reason.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit


def _safe_next(raw: str | None) -> str:
    """Constrain ``?next=`` to the local dev origin set we expect."""
    if not raw:
        return "/app/admin/"
    parsed = urlparse(raw)
    # Empty netloc => relative path on this server.
    if parsed.netloc == "":
        return raw
    if parsed.netloc.startswith(("localhost:", "127.0.0.1:")) or parsed.netloc in {
        "localhost",
        "127.0.0.1",
    }:
        return raw
    return "/app/admin/"


@csrf_exempt
@ratelimit(key="ip", rate="20/m", block=True)
def dev_login(request):
    if not settings.DEBUG:
        return JsonResponse({"detail": "not found"}, status=404)

    User = get_user_model()
    email = request.GET.get("as", "dev@local.astrolift.net")
    user = User.objects.filter(username=email).first()
    if user is None:
        return JsonResponse(
            {"detail": f"no user {email!r}; run `manage.py seed_dev_identity`"},
            status=400,
        )

    # Skip the authenticate() round-trip (no password check) — DEBUG-only.
    user.backend = f"{ModelBackend.__module__}.{ModelBackend.__qualname__}"
    login(request, user)
    # #526 — dev-login impersonates a password-backed local session;
    # carry that through so the step-up modal renders the password
    # form (not the SSO redirect button) in local dev.
    try:
        from astrolift_identity.sessions import SESSION_LOGIN_METHOD_KEY

        request.session[SESSION_LOGIN_METHOD_KEY] = "password"
    except Exception:  # noqa: BLE001 — metadata stamping must never break dev-login
        pass
    # auth1.Auth0SessionMiddleware.process_response intentionally
    # drops SessionMiddleware's auto-save behavior, so we have to
    # save + set-cookie ourselves. Without this, login() succeeds
    # in-memory but the response carries no sessionid to the browser.
    request.session.save()

    next_url = _safe_next(request.GET.get("next"))
    response = HttpResponseRedirect(next_url)
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        request.session.session_key,
        max_age=settings.SESSION_COOKIE_AGE,
        domain=settings.SESSION_COOKIE_DOMAIN,
        path=settings.SESSION_COOKIE_PATH,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    return response
