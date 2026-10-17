"""A finished sign-in sends its session only to this host or the frontend callback.

The callback appended ``token=Session <key>`` to the stored return address
(a one-time code since #2429), so before this a
``/app/auth1/login?next=https://elsewhere/`` link handed ``elsewhere`` the
session of whoever signed in through it.
"""

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from auth1.return_urls import safe_relative_path, safe_return_url
from auth1.sessions import Auth1SessionWorkflow

FRONTEND = "https://astro.example"


@pytest.fixture
def frontend(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    # The login view is rate limited per IP; keep its counters out of the shared cache.
    settings.CACHES = {
        **settings.CACHES,
        "return_url_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "return_url_ratelimit"
    return settings


@pytest.mark.parametrize(
    "value",
    [
        "/app/admin/",
        "/app/cli/auth/device/abc_DEF-123/",
        "/app/admin/?next=/app/admin/auth/",
    ],
)
def test_a_path_on_this_host_passes_as_given(frontend, value):
    assert safe_return_url(value) == value
    assert safe_relative_path(value) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"{FRONTEND}/auth/callback", f"{FRONTEND}/auth/callback"),
        (
            f"{FRONTEND}/auth/callback?next=%2Fauth%2Finvitation%2Falft_abc",
            f"{FRONTEND}/auth/callback?next=%2Fauth%2Finvitation%2Falft_abc",
        ),
        ("HTTPS://astro.example/auth/callback", f"{FRONTEND}/auth/callback"),
        (f"{FRONTEND}:443/auth/callback", "https://astro.example:443/auth/callback"),
        (f"{FRONTEND}/auth/callback#fragment", f"{FRONTEND}/auth/callback"),
        # The callback writes these itself; a link may not pre-seed them.
        (
            f"{FRONTEND}/auth/callback?token=Session+x&code=c&error=y&next=%2Fdashboard",
            f"{FRONTEND}/auth/callback?next=%2Fdashboard",
        ),
    ],
)
def test_the_frontend_callback_passes_rebuilt(frontend, value, expected):
    assert safe_return_url(value) == expected


def test_the_host_serving_the_request_counts_as_the_frontend(frontend):
    own = ("https://install.example/",)
    assert (
        safe_return_url("https://install.example/auth/callback", own)
        == "https://install.example/auth/callback"
    )
    assert safe_return_url("https://install.example/auth/callback") is None


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/auth/callback",
        "//evil.example/auth/callback",
        "///evil.example/auth/callback",
        "/\\evil.example/auth/callback",
        "\\\\evil.example/auth/callback",
        "/\t/evil.example/auth/callback",
        "\t/app/admin/",
        " /app/admin/",
        "/app/admin/\n",
        "https://astro.example.evil.example/auth/callback",
        "https://ASTRO.EXAMPLE/auth/callback",
        "https://evil.example/auth/callback?next=https://astro.example/auth/callback",
        "https://astro.example@evil.example/auth/callback",
        "https://evil.example@astro.example/auth/callback",
        "https://astro.example:@evil.example/auth/callback",
        "http://astro.example/auth/callback",
        "https://astro.example:8443/auth/callback",
        "https://astro.example/dashboard",
        "https://astro.example/auth/callback/",
        "https://astro.example/auth/callback/../dashboard",
        "https://astro.example/%2e%2e/auth/callback",
        "https://astro.example．evil.example/auth/callback",
        "https://astro.example/auth/callback?next=é",
        "javascript:alert(1)",
        "javascript://astro.example/%0aalert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://astro.example/auth/callback",
        "astro.example/auth/callback",
        "",
        None,
        12,
        "/" + "a" * 2048,
    ],
)
def test_anything_else_is_refused(frontend, value):
    assert safe_return_url(value, ("https://install.example/",)) is None


def _authorize_redirect(request, redirect_uri, **kwargs):
    return HttpResponse(status=302, headers={"Location": "https://idp.example/authorize"})


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("next_url", "kept"),
    [
        (f"{FRONTEND}/auth/callback?next=%2Fauth%2Finvitation%2Falft_abc", True),
        ("/app/admin/", True),
        ("https://evil.example/auth/callback", False),
        ("//evil.example/", False),
    ],
)
def test_login_keeps_only_a_safe_return_address(frontend, monkeypatch, next_url, kept):
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_redirect", _authorize_redirect)
    client = Client()
    client.get(reverse("login"), {"next": next_url})
    assert ("auth_next" in client.session) is kept
    if kept:
        assert client.session["auth_next"] == safe_return_url(next_url)


@pytest.mark.django_db
def test_a_new_login_without_a_return_address_drops_an_earlier_one(frontend, monkeypatch):
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_redirect", _authorize_redirect)
    client = Client()
    client.get(reverse("login"), {"next": f"{FRONTEND}/auth/callback"})
    client.get(reverse("login"))
    assert "auth_next" not in client.session


def _claims(email):
    return {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": f"sub-{email}",
            "iss": "https://issuer.test",
            "email": email,
            "email_verified": True,
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
        },
    }


@pytest.mark.django_db
def test_callback_ignores_a_return_address_stored_before_the_check(frontend, monkeypatch):
    user = get_user_model().objects.create_user(username="stored-next", email="stored-next@example.test")
    claims = _claims(user.email)
    monkeypatch.setattr(
        Auth1SessionWorkflow,
        "_client",
        SimpleNamespace(auth0=SimpleNamespace(authorize_access_token=lambda r: claims)),
    )
    client = Client()
    session = SessionStore()
    session["auth_next"] = "https://evil.example/collect"
    session.create()
    client.cookies["sessionid"] = session.session_key

    response = client.get(reverse("callback"))

    location = urlsplit(response["Location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "astro.example", "/auth/callback")
    assert set(parse_qs(location.query)) == {"code"}
    assert "evil.example" not in response["Location"]
