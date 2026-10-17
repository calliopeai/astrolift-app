"""An invitee who is signed out, or has no account yet, can see and take up an invitation.

Live on CONFLICT the invitation page only offered **Accept invitation**,
which needs a session, and a person who had never signed in got no account
at all: the IdP sign-in succeeded, auth1 found nobody by that email and handed
the frontend an anonymous session, so every request bounced back to sign-in.

These tests pin, over Django's real HTTP routing and Postgres:

* the preview a signed-out browser reads, which answers for exactly the one
  invitation its token names and writes nothing;
* the account a live invitation creates at the invitee's first sign-in, and
  only a live one;
* the callback for an identity with no account, which says so instead of
  issuing a session.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import django_ratelimit.core as ratelimit_core
import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from astrolift_identity.invitees import invited_account, mask_email
from astrolift_identity.models import Invitation, Member, Organization, Role, RoleBinding
from astrolift_identity.schema.mutations import AcceptInvitationInput, IdentityMutation
from astrolift_operations.models import AuditEvent
from auth1.models import UserInfo
from auth1.sessions import Auth1SessionWorkflow

pytestmark = pytest.mark.django_db
User = get_user_model()

PREVIEW = f"/{django_settings.BASE_URL}auth1/invitation-preview"
FRONTEND = "https://astro.example"
INVITEE = "new.person@invitee.example"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    # The preview and the login are rate limited per IP; keep the counters
    # out of the cache other test runs share.
    settings.CACHES = {
        **settings.CACHES,
        "invitee_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "invitee_ratelimit"


def _org(name: str = "Acme") -> Organization:
    return Organization.objects.create(name=name, slug=f"org-{uuid.uuid4().hex[:8]}")


def _inviter() -> User:
    return User.objects.create(
        username=f"inviter-{uuid.uuid4().hex[:6]}",
        email=f"inviter-{uuid.uuid4().hex[:6]}@acme.example",
        first_name="Ada",
        last_name="Admin",
    )


def _role() -> Role:
    return Role.objects.create(
        slug=f"developer-{uuid.uuid4().hex[:6]}",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )


def _invite(org, email=INVITEE, *, role=None, inviter=None, **fields) -> tuple[Invitation, str]:
    token = "alft_" + secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        email=email,
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.pk,
        role=role,
        invited_by=inviter,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        **fields,
    )
    return invitation, token


def _preview(client: Client, token, **headers):
    return client.post(
        PREVIEW,
        data=json.dumps({"token": token}),
        content_type="application/json",
        HTTP_X_PLATFORM="web",
        **headers,
    )


def _footprint():
    return {
        "invitations": list(Invitation.all_objects.order_by("pk").values()),
        "users": User.objects.count(),
        "members": Member.objects.count(),
        "bindings": RoleBinding.objects.count(),
        "audit": AuditEvent.objects.count(),
    }


# ---- the preview ------------------------------------------------------------


def test_signed_out_preview_answers_only_for_the_tokens_invitation():
    org = _org("Acme Robotics")
    inviter = _inviter()
    invitation, token = _invite(org, role=_role(), inviter=inviter)
    # Another organization's invitation to the same address, and members
    # with their own data, must not leak into the answer.
    other = _org("Globex")
    _invite(other, role=_role(), inviter=_inviter())
    Member.objects.create(user=inviter, scope_kind="ORG", scope_id=org.pk)

    response = _preview(Client(), token)

    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert response.json() == {
        "status": "pending",
        "invitation": {
            "organizationName": "Acme Robotics",
            "inviterName": "Ada Admin",
            "roleName": "Developer",
            "emailHint": "n•••@invitee.example",
            "expiresAt": invitation.expires_at.isoformat(),
        },
        "viewer": {"signedIn": False},
    }


def test_preview_writes_nothing_and_does_not_expire_a_lapsed_invitation():
    org = _org()
    invitation, token = _invite(org, expires_at=timezone.now() - timedelta(minutes=1))
    before = _footprint()

    response = _preview(Client(), token)

    assert response.json()["status"] == "expired"
    assert _footprint() == before
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.PENDING


@pytest.mark.parametrize(
    ("status", "shown"),
    [
        (Invitation.Status.ACCEPTED, "accepted"),
        (Invitation.Status.REVOKED, "revoked"),
        (Invitation.Status.EXPIRED, "expired"),
    ],
)
def test_preview_reports_a_used_revoked_or_expired_invitation(status, shown):
    _, token = _invite(_org(), status=status)
    body = _preview(Client(), token).json()
    assert body["status"] == shown
    assert body["invitation"]["organizationName"] == "Acme"


def test_an_unknown_deleted_or_orphaned_token_reads_as_not_found_and_nothing_else():
    org = _org()
    deleted, deleted_token = _invite(org)
    deleted.soft_delete()
    gone_org = _org("Gone")
    _, orphan_token = _invite(gone_org)
    gone_org.soft_delete()
    for token in ("alft_" + secrets.token_urlsafe(32), deleted_token, orphan_token):
        assert _preview(Client(), token).json() == {"status": "not_found", "viewer": {"signedIn": False}}


def test_preview_says_whether_the_signed_in_browser_belongs_to_the_invitee():
    org = _org()
    _, token = _invite(org)
    invitee = User.objects.create(username="invitee-user", email="New.Person@Invitee.example")
    someone = User.objects.create(username="someone-else", email="someone@elsewhere.example")

    as_invitee = Client()
    as_invitee.force_login(invitee)
    assert _preview(as_invitee, token).json()["viewer"] == {
        "signedIn": True,
        "email": "New.Person@Invitee.example",
        "isInvitee": True,
    }
    as_someone = Client()
    as_someone.force_login(someone)
    assert _preview(as_someone, token).json()["viewer"] == {
        "signedIn": True,
        "email": "someone@elsewhere.example",
        "isInvitee": False,
    }


def test_the_invitation_is_single_use():
    org = _org()
    _, token = _invite(org)
    invitee = User.objects.create(username="invitee-once", email=INVITEE)
    info = SimpleNamespace(context=SimpleNamespace(user=invitee, request=SimpleNamespace(user=invitee)))

    assert IdentityMutation().accept_invitation(info, input=AcceptInvitationInput(token=token)).ok
    again = IdentityMutation().accept_invitation(info, input=AcceptInvitationInput(token=token))

    assert not again.ok
    assert _preview(Client(), token).json()["status"] == "accepted"


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"token": ""}',
        b'{"token": 12}',
        json.dumps({"token": "x" * 257}).encode(),
    ],
)
def test_a_malformed_request_is_refused(body):
    response = Client().post(PREVIEW, data=body, content_type="application/json", HTTP_X_PLATFORM="web")
    assert response.status_code == 400


def test_only_post_is_served():
    assert Client().get(PREVIEW).status_code == 405


def test_a_cookie_post_without_the_ui_header_is_refused():
    _, token = _invite(_org())
    client = Client()
    client.force_login(User.objects.create(username="cookie-only", email="cookie@example.test"))
    response = client.post(PREVIEW, data=json.dumps({"token": token}), content_type="application/json")
    assert response.status_code == 403


def test_preview_is_rate_limited_per_ip(settings, monkeypatch):
    settings.RATELIMIT_ENABLE = True
    now = [1_790_000_000]
    monkeypatch.setattr(ratelimit_core, "time", SimpleNamespace(time=lambda: now[0]))
    token = "alft_" + secrets.token_urlsafe(32)
    responses = [_preview(Client(), token, REMOTE_ADDR="192.0.2.50") for _ in range(31)]
    assert all(r.status_code == 200 for r in responses[:30])
    assert responses[-1].status_code == 429
    assert _preview(Client(), token, REMOTE_ADDR="192.0.2.51").status_code == 200


def test_mask_email_keeps_the_first_letter_and_the_domain():
    assert mask_email("jane.doe@example.com") == "j•••@example.com"
    assert mask_email("not-an-address") == "•••"


# ---- the account a live invitation creates --------------------------------


def test_a_live_invitation_creates_its_account_and_nothing_more():
    org = _org()
    invitation, _ = _invite(org, role=_role())
    before = (Member.objects.count(), RoleBinding.objects.count())

    user = invited_account(INVITEE, first_name="New", last_name="Person")

    assert user is not None
    assert (user.username, user.email, user.first_name, user.last_name) == (INVITEE, INVITEE, "New", "Person")
    assert not user.has_usable_password()
    assert (Member.objects.count(), RoleBinding.objects.count()) == before
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.PENDING
    audit = AuditEvent.objects.get(action="auth.invitation.account")
    assert (audit.organization_id, audit.target_id, audit.actor_id) == (
        org.pk,
        str(invitation.guid),
        str(user.pk),
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"status": Invitation.Status.ACCEPTED},
        {"status": Invitation.Status.REVOKED},
        {"status": Invitation.Status.EXPIRED},
        {"expires_at": timezone.now() - timedelta(seconds=1)},
    ],
)
def test_an_invitation_that_is_not_live_creates_no_account(fields):
    _invite(_org(), **fields)
    count = User.objects.count()
    assert invited_account(INVITEE) is None
    assert User.objects.count() == count


def test_a_deleted_invitation_or_organization_creates_no_account():
    invitation, _ = _invite(_org())
    invitation.soft_delete()
    org = _org()
    _invite(org, email="other.person@invitee.example")
    org.soft_delete()
    count = User.objects.count()
    assert invited_account(INVITEE) is None
    assert invited_account("other.person@invitee.example") is None
    assert invited_account("never.invited@invitee.example") is None
    assert User.objects.count() == count


def test_an_account_holding_the_address_as_its_username_is_never_taken_over():
    _invite(_org())
    holder = User.objects.create(username=INVITEE, email="holder@elsewhere.example")
    assert invited_account(INVITEE) is None
    holder.refresh_from_db()
    assert holder.email == "holder@elsewhere.example"


# ---- the IdP callback ---------------------------------------------------------


def _claims(email, sub=None):
    return {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": sub or f"sub-{uuid.uuid4().hex}",
            "iss": "https://issuer.test",
            "email": email,
            "email_verified": True,
            "given_name": "New",
            "family_name": "Person",
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
        },
    }


def _sign_in(client: Client, monkeypatch, claims, next_url):
    """Run the real login then callback views with the IdP's two hops stubbed."""
    from django.http import HttpResponse

    monkeypatch.setattr(
        Auth1SessionWorkflow._client.auth0,
        "authorize_redirect",
        lambda request, redirect_uri, **kw: HttpResponse(
            status=302, headers={"Location": "https://idp.test/"}
        ),
    )
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", lambda request: claims)
    client.get(reverse("login"), {"next": next_url})
    return client.get(reverse("callback"))


def test_a_new_invitee_signs_in_returns_to_the_invitation_and_accepts(monkeypatch):
    org = _org()
    invitation, token = _invite(org, role=_role())
    path = f"/auth/invitation/{token}"
    client = Client()

    response = _sign_in(client, monkeypatch, _claims(INVITEE), f"{FRONTEND}/auth/callback?next={path}")

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "astro.example", "/auth/callback")
    query = parse_qs(location.query)
    assert query["next"] == [path]
    user = User.objects.get(email=INVITEE)
    assert query["token"] == [f"Session {client.session.session_key}"]
    assert client.session["_auth_user_id"] == str(user.pk)
    assert UserInfo.objects.get(email=INVITEE).internal_user_id == user.pk
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.PENDING
    assert not Member.objects.filter(user=user).exists()

    assert _preview(client, token).json()["viewer"]["isInvitee"] is True
    info = SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))
    assert IdentityMutation().accept_invitation(info, input=AcceptInvitationInput(token=token)).ok
    assert Member.objects.filter(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True).exists()


def test_an_identity_with_no_account_is_told_so_and_gets_no_session(monkeypatch):
    org = _org()
    _, token = _invite(org)
    path = f"/auth/invitation/{token}"
    client = Client()
    users = User.objects.count()

    response = _sign_in(
        client, monkeypatch, _claims("someone.else@invitee.example"), f"{FRONTEND}/auth/callback?next={path}"
    )

    location = urlsplit(response["Location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "astro.example", "/auth/callback")
    assert parse_qs(location.query) == {"next": [path], "error": ["no_account"]}
    assert "token=" not in response["Location"]
    assert "_auth_user_id" not in client.session
    assert User.objects.count() == users


def test_a_session_already_open_ends_when_the_idp_names_someone_without_an_account(monkeypatch):
    signed_in = User.objects.create(username="already-in", email="already@acme.example")
    client = Client()
    client.force_login(signed_in)

    response = _sign_in(client, monkeypatch, _claims("unknown@invitee.example"), f"{FRONTEND}/auth/callback")

    assert response["Location"] == f"{FRONTEND}/auth/callback?error=no_account"
    assert "_auth_user_id" not in client.session


def test_without_a_return_address_no_account_goes_to_the_sign_in_page(monkeypatch):
    response = _sign_in(Client(), monkeypatch, _claims("unknown@invitee.example"), "")
    assert response["Location"] == f"{FRONTEND}/auth/login?error=no_account"
