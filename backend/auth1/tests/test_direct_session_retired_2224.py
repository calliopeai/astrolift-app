"""Real HTTP/Postgres proof that direct claims cannot mint or replace a session."""

import uuid

import pytest
from authlib.integrations.base_client.errors import MismatchingStateError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from astrolift_identity.models import Member, RoleBinding
from auth1.models import Authentication, UserInfo
from auth1.sessions import Auth1SessionWorkflow

pytestmark = pytest.mark.django_db


def identity_snapshot():
    models = [get_user_model(), Group, UserInfo, Authentication, Member, RoleBinding, Session]
    return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}


@pytest.fixture
def user(settings):
    settings.DEBUG = False
    settings.CLIENT_SESSION_API_KEY = "controlled-legacy-relay-key"
    settings.CSRF_COOKIE_SECURE = False
    settings.CACHES = {
        **settings.CACHES,
        "direct_relay_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "direct_relay_ratelimit"
    return get_user_model().objects.create_user(
        username="retired-relay-user", email="relay-user@example.test"
    )


@pytest.mark.parametrize(
    "authorization", [None, "Bearer unrelated-token", "Bearer controlled-legacy-relay-key"]
)
@pytest.mark.parametrize("body", [b"not-json", b"null", b"[]", b'{"userinfo":{"sub":"controlled"}}'])
def test_direct_relay_has_no_identity_or_session_effects(user, authorization, body):
    client = Client()
    before = identity_snapshot()
    headers = {"HTTP_AUTHORIZATION": authorization} if authorization else {}
    response = client.post(reverse("session"), data=body, content_type="application/json", **headers)
    assert response.status_code == 410
    assert response.json() == {
        "code": "DIRECT_SESSION_RETIRED",
        "message": "Use the verified backend login flow.",
        "loginUrl": reverse("login"),
    }
    assert response["Cache-Control"] == "no-store"
    assert "Authorization" not in response
    assert "sessionid" not in response.cookies
    assert identity_snapshot() == before


@pytest.mark.parametrize("authorization", [None, "Bearer controlled-legacy-relay-key"])
def test_retired_relay_preserves_existing_verified_session(user, authorization):
    client = Client()
    client.force_login(user)
    session = client.session
    session["verified_session_marker"] = "kept"
    session.save()
    key = session.session_key
    before = identity_snapshot()
    headers = {"HTTP_AUTHORIZATION": authorization} if authorization else {}
    response = client.post(reverse("session"), data="{}", content_type="application/json", **headers)
    assert response.status_code == 410
    assert "Authorization" not in response
    assert client.session.session_key == key
    assert client.session["verified_session_marker"] == "kept"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert identity_snapshot() == before


def test_retired_relay_get_remains_method_refused_without_login_effects(user):
    before = identity_snapshot()
    response = Client().get(reverse("session"))
    assert response.status_code == 405
    assert response["Allow"] == "POST"
    assert "Authorization" not in response
    assert identity_snapshot() == before


def test_verified_session_header_still_authenticates_after_relay_retirement(user):
    authenticated = Client()
    authenticated.force_login(user)
    session_key = authenticated.session.session_key
    query = {"query": "query { astroliftServerInfo { version } }"}
    anonymous = Client().post("/app/gql/config/", query, content_type="application/json")
    assert anonymous.status_code == 403
    response = Client().post(
        "/app/gql/config/",
        query,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Session {session_key}",
    )
    assert response.status_code == 200
    assert not response.json().get("errors")
    assert response.json()["data"]["astroliftServerInfo"]["version"]
    assert response.wsgi_request.user.pk == user.pk


def test_failed_state_verification_precedes_callback_identity_effects(user, monkeypatch):
    def refused(request):
        raise MismatchingStateError()

    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", refused)
    before = identity_snapshot()
    client = Client(raise_request_exception=False)
    response = client.get(reverse("callback"))
    assert response.status_code == 500
    assert "Authorization" not in response
    assert "sessionid" not in response.cookies
    assert identity_snapshot() == before
