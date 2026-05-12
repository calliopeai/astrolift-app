"""
Local username/password login.

Used when an organization's primary IdP has ``kind=local``. Plain
Django auth: ``authenticate(username, password)``, then ``login()``
to set the session.

Same set-cookie workaround as ``dev_login.py`` — the auth1
SessionMiddleware overrides ``process_response`` without calling super,
so we save + set the cookie explicitly.

Gated so production builds without an explicit
``ASTROLIFT_LOCAL_LOGIN_ENABLED=true`` return 404. The local kind is
otherwise an attractive nuisance.
"""

from __future__ import annotations

import json
import os

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit


def _enabled() -> bool:
    if settings.DEBUG:
        return True
    return os.environ.get("ASTROLIFT_LOCAL_LOGIN_ENABLED", "").lower() in {"1", "true", "yes"}


def _ok(payload: dict) -> JsonResponse:
    return JsonResponse(payload)


def _bad(reason: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"detail": reason}, status=status)


@csrf_exempt
@ratelimit(key="ip", rate="10/m", block=True)
def local_login(request):
    if not _enabled():
        return JsonResponse({"detail": "not found"}, status=404)

    if request.method != "POST":
        return _bad("POST required", status=405)

    # Accept either form-encoded or JSON.
    username = ""
    password = ""
    next_url = "/app/admin/"
    try:
        if request.content_type and "json" in request.content_type:
            body = json.loads(request.body or b"{}")
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            next_url = body.get("next") or next_url
        else:
            username = (request.POST.get("username") or "").strip()
            password = request.POST.get("password") or ""
            next_url = request.POST.get("next") or next_url
    except json.JSONDecodeError:
        return _bad("invalid JSON body")

    if not username or not password:
        return _bad("username + password required")

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        return _bad("invalid credentials", status=401)

    login(request, user)
    request.session.save()

    # The UI accepts either a JSON or a redirect response shape; we
    # detect by Accept header so curl + form posts both work.
    accept = request.META.get("HTTP_ACCEPT", "")
    if "application/json" in accept:
        response = _ok({"ok": True, "next": next_url})
    else:
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
