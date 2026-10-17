"""An identity provider's unverified address never decides an Astrolift account (#2430).

auth1 finds the account for a new identity by its email, and since #2426 a
live invitation to that email creates one. It took every Cognito issuer's
email as verified, so on a pool with open sign-up anyone could register a
victim's address and sign in as the victim. The string ``"false"`` some
providers send for the claim passed too, being a non-empty string.

These tests run the real login and callback views on Postgres with only the
IdP's two hops stubbed.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from astrolift_identity.models import Invitation, Organization
from auth1.models import UserInfo
from auth1.sessions import Auth1SessionWorkflow

pytestmark = pytest.mark.django_db
User = get_user_model()

FRONTEND = "https://astro.example"
COGNITO = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_pool"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    settings.CACHES = {
        **settings.CACHES,
        "verified_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "verified_ratelimit"


def _claims(email, verified, *, issuer=COGNITO, sub=None):
    userinfo = {
        "sub": sub or f"sub-{uuid.uuid4().hex}",
        "iss": issuer,
        "email": email,
        "updated_at": timezone.now(),
        "aud": "controlled",
        "iat": 1,
        "exp": 2,
    }
    if verified is not None:
        userinfo["email_verified"] = verified
    return {"id_token": "controlled-unsaved-test-token", "userinfo": userinfo}


def _sign_in(client: Client, monkeypatch, claims, next_url=f"{FRONTEND}/auth/callback"):
    from django.http import HttpResponse

    monkeypatch.setattr(
        Auth1SessionWorkflow._client.auth0,
        "authorize_redirect",
        lambda request, redirect_uri, **kw: HttpResponse(
            status=302, headers={"Location": "https://idp.test/"}
        ),
    )
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", lambda request: claims)
    client.get(reverse("login"), {"next": next_url} if next_url else {})
    return client.get(reverse("callback"))


def _victim() -> User:
    return User.objects.create(
        username=f"victim-{uuid.uuid4().hex[:6]}", email=f"victim-{uuid.uuid4().hex[:6]}@acme.example"
    )


def _assert_refused(response, client, next_url=f"{FRONTEND}/auth/callback"):
    location = urlsplit(response["Location"])
    assert response.status_code == 302
    if next_url:
        assert (location.scheme, location.netloc, location.path) == (
            "https",
            "astro.example",
            "/auth/callback",
        )
    else:
        assert (location.scheme, location.netloc, location.path) == ("https", "astro.example", "/auth/login")
    assert parse_qs(location.query) == {"error": ["email_unverified"]}
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("verified", [False, "false", "False", None, "", "yes", 1, "1"])
def test_an_unverified_address_does_not_sign_in_as_its_owner(monkeypatch, verified):
    victim = _victim()
    client = Client()

    response = _sign_in(client, monkeypatch, _claims(victim.email, verified))

    _assert_refused(response, client)
    assert not UserInfo.objects.filter(internal_user=victim).exists()


@pytest.mark.parametrize("verified", [True, "true", "TRUE"])
def test_a_verified_address_still_signs_in_as_its_owner(monkeypatch, verified):
    victim = _victim()
    client = Client()

    response = _sign_in(client, monkeypatch, _claims(victim.email, verified))

    assert set(parse_qs(urlsplit(response["Location"]).query)) == {"code"}
    assert client.session["_auth_user_id"] == str(victim.pk)


def test_an_unverified_invitee_gets_no_account(monkeypatch):
    org = Organization.objects.create(name="Acme", slug=f"org-{uuid.uuid4().hex[:8]}")
    token = "alft_" + secrets.token_urlsafe(32)
    Invitation.objects.create(
        email="invitee@invitee.example",
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.pk,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    client = Client()
    users = User.objects.count()

    response = _sign_in(client, monkeypatch, _claims("invitee@invitee.example", False))

    _assert_refused(response, client)
    assert User.objects.count() == users


def test_an_unverified_address_under_an_auto_signup_domain_gets_no_account(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_AUTO_SIGNUP_DOMAINS", "corp.example")
    client = Client()
    users = User.objects.count()

    response = _sign_in(client, monkeypatch, _claims("newcomer@corp.example", "false"), next_url=None)

    _assert_refused(response, client, next_url=None)
    assert User.objects.count() == users


def test_an_identity_linked_under_the_old_rule_is_refused_once_its_address_is_unverified(monkeypatch):
    # A stranger's identity matched to the victim while every Cognito address
    # counted as verified keeps no access through that link.
    victim = _victim()
    stranger = _claims(victim.email, False)
    UserInfo.objects.create(internal_user=victim, **(stranger["userinfo"] | {"email_verified": True}))
    client = Client()

    response = _sign_in(client, monkeypatch, stranger)

    _assert_refused(response, client)


def test_a_session_already_open_ends_when_the_address_is_unverified(monkeypatch):
    signed_in = _victim()
    client = Client()
    client.force_login(signed_in)

    response = _sign_in(client, monkeypatch, _claims("someone@else.example", False))

    _assert_refused(response, client)
