"""A finished sign-in reaches the frontend as a one-time code, never as its session (#2429).

The callback appended ``token=Session <key>`` to the frontend's
``/auth/callback``. Every hop that saw the URL logged it (the load balancer,
the nginx sidecar, CloudWatch), and the browser kept it in its history, so
whoever could read those logs could take over the session.

These tests run the real login, callback and hand-off views on Postgres and
the cache the install uses, with only the IdP's two hops stubbed.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from auth1 import handoff
from auth1.sessions import Auth1SessionWorkflow

pytestmark = pytest.mark.django_db
User = get_user_model()

FRONTEND = "https://astro.example"
HANDOFF = f"/{django_settings.BASE_URL}auth1/handoff"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    # The login and the hand-off are rate limited per IP; keep the counters
    # out of the cache other test runs share.
    settings.CACHES = {
        **settings.CACHES,
        "handoff_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "handoff_ratelimit"


def _user(name: str = "signer") -> User:
    return User.objects.create(
        username=f"{name}-{uuid.uuid4().hex[:6]}", email=f"{name}-{uuid.uuid4().hex[:6]}@acme.example"
    )


def _claims(user: User) -> dict:
    return {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": f"sub-{uuid.uuid4().hex}",
            "iss": "https://issuer.test",
            "email": user.email,
            "email_verified": True,
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
        },
    }


def _sign_in(client: Client, monkeypatch, user: User, next_url: str | None):
    """The real login then callback views, with the IdP's two hops stubbed."""
    from django.http import HttpResponse

    claims = _claims(user)
    monkeypatch.setattr(
        Auth1SessionWorkflow._client.auth0,
        "authorize_redirect",
        lambda request, redirect_uri, **kw: HttpResponse(
            status=302, headers={"Location": "https://idp.test/"}
        ),
    )
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", lambda request: claims)
    client.get(reverse("login"), {"next": next_url} if next_url is not None else {})
    return client.get(reverse("callback"))


def _trade(client: Client, code, **headers):
    return client.post(
        HANDOFF,
        data=json.dumps({"code": code}),
        content_type="application/json",
        **({"HTTP_X_PLATFORM": "web"} | headers),
    )


def _code(response) -> str:
    return parse_qs(urlsplit(response["Location"]).query)["code"][0]


@pytest.mark.parametrize(
    ("next_url", "kept"),
    [
        (
            f"{FRONTEND}/auth/callback?next=%2Fauth%2Finvitation%2Falft_abc",
            {"next": ["/auth/invitation/alft_abc"]},
        ),
        (None, {}),
    ],
)
def test_the_frontend_callback_gets_a_code_and_no_session(monkeypatch, next_url, kept):
    client = Client()
    response = _sign_in(client, monkeypatch, _user(), next_url)

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "astro.example", "/auth/callback")
    query = parse_qs(location.query)
    assert set(query) == {"code", *kept}
    assert {key: value for key, value in query.items() if key != "code"} == kept
    session_key = client.session.session_key
    assert session_key and session_key not in response["Location"]
    assert "token" not in query and "Session" not in response["Location"]


def test_the_code_trades_once_for_the_session_of_the_browser_that_signed_in(monkeypatch):
    client = Client()
    response = _sign_in(client, monkeypatch, _user(), f"{FRONTEND}/auth/callback")
    code = _code(response)

    first = _trade(client, code)
    assert first.status_code == 200
    assert first.json() == {"Authorization": f"Session {client.session.session_key}"}
    assert "no-store" in first["Cache-Control"]

    again = _trade(client, code)
    assert again.status_code == 400
    assert again.json()["code"] == "HANDOFF_REFUSED"


def test_a_code_read_from_a_log_is_useless_elsewhere_and_is_not_used_up(monkeypatch):
    client = Client()
    code = _code(_sign_in(client, monkeypatch, _user("victim"), f"{FRONTEND}/auth/callback"))

    # No session at all, and another person's signed-in session.
    stranger = Client()
    assert _trade(stranger, code).status_code == 400
    other = Client()
    other.force_login(_user("other"))
    refused = _trade(other, code)
    assert refused.status_code == 400
    assert client.session.session_key not in refused.content.decode()

    # The browser that signed in still gets its session.
    assert _trade(client, code).json() == {"Authorization": f"Session {client.session.session_key}"}


def test_the_session_comes_from_the_cookie_alone(monkeypatch):
    client = Client()
    code = _code(_sign_in(client, monkeypatch, _user(), f"{FRONTEND}/auth/callback"))

    # A credential header could make the request authenticated as someone
    # the cookie does not name, so the hand-off refuses any.
    refused = _trade(client, code, HTTP_AUTHORIZATION="Bearer opaque-token")
    assert refused.status_code == 400 and refused.json()["code"] == "HANDOFF_REFUSED"
    assert _trade(client, code).status_code == 200


def test_a_cookie_post_without_the_ui_header_is_refused_before_the_code_is_read(monkeypatch):
    client = Client()
    code = _code(_sign_in(client, monkeypatch, _user(), f"{FRONTEND}/auth/callback"))

    response = client.post(HANDOFF, data=json.dumps({"code": code}), content_type="application/json")
    assert response.status_code == 403
    assert _trade(client, code).status_code == 200


def test_a_code_lasts_sixty_seconds(monkeypatch):
    assert handoff.TTL_SECONDS == 60
    client = Client()
    monkeypatch.setattr(handoff, "TTL_SECONDS", 1)
    code = _code(_sign_in(client, monkeypatch, _user(), f"{FRONTEND}/auth/callback"))
    time.sleep(1.5)
    assert _trade(client, code).status_code == 400


@pytest.mark.parametrize("code", ["", "x", "a" * 42, "a" * 44, "a" * 42 + "!", None, 7, ["a" * 43]])
def test_malformed_codes_are_refused(monkeypatch, code):
    client = Client()
    _sign_in(client, monkeypatch, _user(), f"{FRONTEND}/auth/callback")
    assert _trade(client, code).status_code == 400


def test_of_two_redemptions_racing_with_one_code_only_one_gets_the_session(monkeypatch):
    session_key = "s" * 32
    code = handoff.issue(session_key)
    bound = handoff.cache.get(handoff._CACHE_PREFIX + handoff._digest(code))
    # Both racers read the entry before either deletes it.
    monkeypatch.setattr(handoff.cache, "get", lambda key, default=None: bound)
    assert handoff.redeem(code, session_key) is True
    assert handoff.redeem(code, session_key) is False


@pytest.mark.parametrize(
    "next_url",
    [
        "/auth/callback?code=EVIL&token=Session+EVIL&error=x&next=%2Fdashboard",
        f"{FRONTEND}/auth/callback?code=EVIL&token=Session+EVIL&error=x&next=%2Fdashboard",
    ],
)
def test_a_return_address_cannot_bring_its_own_code(monkeypatch, next_url):
    client = Client()
    response = _sign_in(client, monkeypatch, _user(), next_url)

    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["next"] == ["/dashboard"]
    assert len(query["code"]) == 1 and query["code"] != ["EVIL"]
    assert "token" not in query and "error" not in query
    assert _trade(client, query["code"][0]).status_code == 200


def test_a_page_on_this_host_gets_no_code(monkeypatch):
    approval = f"/{django_settings.BASE_URL}cli/auth/device/pending-session/"
    response = _sign_in(Client(), monkeypatch, _user(), approval)
    assert response["Location"] == approval
