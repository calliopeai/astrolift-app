"""Tests for the global API-token surface (#428).

Covers:

* ``create_api_token`` mutation applies the default scope set, echoes
  caller-provided scopes, rejects unknowns with VALIDATION.
* ``verify_token`` honours revocation + expiry; the hash, not the
  plaintext, is what hits Postgres.
* ``touch_token`` writes ``last_used_*`` triplet without clobbering
  other row state.
* ``client_ip_from_request`` returns the left-most X-Forwarded-For
  hop, falls back to REMOTE_ADDR, returns None on garbage input.
* ``ApiTokenAuthMiddleware`` authenticates a valid bearer, swaps
  ``request.user``, stamps the row; rejects revoked / expired with
  401; noops on session-cookie / non-prefix bearers.
* ``has_scope`` / ``enforce_scopes`` treat the ``admin`` scope as a
  wildcard.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    DEFAULT_SCOPES,
    PLAINTEXT_PREFIX,
    SCOPE_ADMIN,
    SCOPE_READ_APPS,
    SCOPE_WRITE_APPS,
    client_ip_from_request,
    enforce_scopes,
    has_scope,
    mint_token,
    normalize_scopes,
    touch_token,
    validate_scopes,
    verify_token,
)
from astrolift_identity.middleware import ApiTokenAuthMiddleware
from astrolift_identity.models import ApiToken, Organization, Team
from astrolift_identity.schema.mutations import (
    CreateApiTokenInput,
    IdentityMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info(user=None):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _admin_user(email: str | None = None):
    if email is None:
        import uuid

        email = f"admin-{uuid.uuid4().hex[:8]}@astrolift.dev"
    user, _ = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0]})
    return user


def _ctx(org, user=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id if user else None))


# ---- scope normalization + validation --------------------------------


def test_normalize_scopes_applies_default_when_none():
    assert normalize_scopes(None) == list(DEFAULT_SCOPES)


def test_normalize_scopes_applies_default_when_empty():
    assert normalize_scopes([]) == list(DEFAULT_SCOPES)
    assert normalize_scopes(["", "  "]) == list(DEFAULT_SCOPES)


def test_normalize_scopes_dedupes_preserving_order():
    out = normalize_scopes([SCOPE_READ_APPS, SCOPE_ADMIN, SCOPE_READ_APPS])
    assert out == [SCOPE_READ_APPS, SCOPE_ADMIN]


def test_validate_scopes_returns_unknown_set():
    assert validate_scopes([SCOPE_READ_APPS, "bogus", "admin"]) == ["bogus"]
    assert validate_scopes(DEFAULT_SCOPES) == []


def test_default_scopes_are_subset_of_allowed():
    assert set(DEFAULT_SCOPES).issubset(ALLOWED_SCOPES)


# ---- create_api_token mutation ---------------------------------------


def test_create_api_token_applies_default_scopes(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    user = _admin_user()

    with _ctx(org, user):
        r = IdentityMutation().create_api_token(
            _info(user),
            input=CreateApiTokenInput(name="cli"),
        )

    assert r.ok, r.errors
    token = ApiToken.objects.get()
    assert token.scopes == list(DEFAULT_SCOPES)
    assert r.data.api_token.scopes == list(DEFAULT_SCOPES)


def test_create_api_token_echoes_caller_scopes(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    user = _admin_user()

    with _ctx(org, user):
        r = IdentityMutation().create_api_token(
            _info(user),
            input=CreateApiTokenInput(
                name="readonly",
                scopes=[SCOPE_READ_APPS],
            ),
        )

    assert r.ok, r.errors
    token = ApiToken.objects.get()
    assert token.scopes == [SCOPE_READ_APPS]


def test_create_api_token_rejects_unknown_scope(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    user = _admin_user()

    with _ctx(org, user):
        r = IdentityMutation().create_api_token(
            _info(user),
            input=CreateApiTokenInput(name="bad", scopes=[SCOPE_READ_APPS, "delete:everything"]),
        )

    assert not r.ok
    assert r.errors[0].code == "VALIDATION"
    assert "delete:everything" in r.errors[0].message
    assert r.errors[0].field == "scopes"
    # Mutation rolls back even partial state — no row persisted.
    assert ApiToken.objects.count() == 0


def test_create_api_token_persists_only_hash(permission_resolver):
    """Plaintext must never hit the DB."""
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    user = _admin_user()

    with _ctx(org, user):
        r = IdentityMutation().create_api_token(
            _info(user),
            input=CreateApiTokenInput(name="cli"),
        )

    plaintext = r.data.plaintext
    assert plaintext.startswith(PLAINTEXT_PREFIX)
    token = ApiToken.objects.get()
    assert token.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert token.token_hash != plaintext
    assert token.token_last_4 == plaintext[-4:]


def test_create_api_token_resolves_team(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    team = Team.objects.create(organization=org, slug="eng", name="Engineering")
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    user = _admin_user()

    with _ctx(org, user):
        r = IdentityMutation().create_api_token(
            _info(user),
            input=CreateApiTokenInput(name="cli", team_slug="eng"),
        )

    assert r.ok, r.errors
    assert ApiToken.objects.get().team_id == team.id


# ---- verify_token ----------------------------------------------------


def _make_token(user, org, *, expires_at=None, is_revoked=False, scopes=None):
    minted = mint_token()
    return (
        minted.plaintext,
        ApiToken.objects.create(
            user=user,
            organization=org,
            name="t",
            token_hash=minted.token_hash,
            token_last_4=minted.last4,
            scopes=list(scopes or DEFAULT_SCOPES),
            expires_at=expires_at,
            is_revoked=is_revoked,
        ),
    )


def test_verify_token_returns_row_for_live_token():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, row = _make_token(user, org)

    found = verify_token(plaintext)
    assert found is not None
    assert found.id == row.id


def test_verify_token_rejects_revoked():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, _ = _make_token(user, org, is_revoked=True)
    assert verify_token(plaintext) is None


def test_verify_token_rejects_expired():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, _ = _make_token(user, org, expires_at=timezone.now() - dt.timedelta(minutes=1))
    assert verify_token(plaintext) is None


def test_verify_token_rejects_soft_deleted():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, row = _make_token(user, org)
    row.deleted_at = timezone.now()
    row.save(update_fields=["deleted_at", "updated_at", "version"])
    assert verify_token(plaintext) is None


def test_verify_token_rejects_wrong_prefix():
    assert verify_token("alft_dt_abc") is None  # deploy token prefix
    assert verify_token("bearer-blah") is None
    assert verify_token("") is None


def test_verify_token_rejects_unknown_secret():
    assert verify_token(f"{PLAINTEXT_PREFIX}does-not-exist") is None


# ---- touch_token -----------------------------------------------------


def test_touch_token_writes_last_used_triplet():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    _, row = _make_token(user, org)

    touch_token(row, ip="203.0.113.5", user_agent="curl/8.4.0")
    row.refresh_from_db()
    assert row.last_used_at is not None
    assert row.last_used_ip == "203.0.113.5"
    assert row.last_used_agent == "curl/8.4.0"


def test_touch_token_coerces_bad_ip_to_null():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    _, row = _make_token(user, org)

    touch_token(row, ip="not-an-ip", user_agent="x")
    row.refresh_from_db()
    assert row.last_used_ip is None


def test_touch_token_truncates_long_agent():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    _, row = _make_token(user, org)

    touch_token(row, ip=None, user_agent="A" * 2000)
    row.refresh_from_db()
    assert len(row.last_used_agent) == 512


# ---- client_ip_from_request -----------------------------------------


def test_client_ip_prefers_first_xff_hop():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1, 10.0.0.2",
        REMOTE_ADDR="10.0.0.3",
    )
    assert client_ip_from_request(request) == "203.0.113.5"


def test_client_ip_falls_back_to_remote_addr():
    request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.7")
    assert client_ip_from_request(request) == "198.51.100.7"


def test_client_ip_returns_none_on_garbage_xff():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="not-an-ip",
        REMOTE_ADDR="bogus-too",
    )
    assert client_ip_from_request(request) is None


# ---- scope check helpers --------------------------------------------


def test_admin_scope_implies_everything():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    _, row = _make_token(user, org, scopes=[SCOPE_ADMIN])
    assert has_scope(row, SCOPE_READ_APPS)
    assert has_scope(row, "anything:at:all")
    assert enforce_scopes(row, (SCOPE_READ_APPS, SCOPE_WRITE_APPS)) is None


def test_enforce_scopes_returns_first_missing():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    _, row = _make_token(user, org, scopes=[SCOPE_READ_APPS])
    assert enforce_scopes(row, (SCOPE_READ_APPS,)) is None
    assert enforce_scopes(row, (SCOPE_WRITE_APPS,)) == SCOPE_WRITE_APPS


# ---- ApiTokenAuthMiddleware -----------------------------------------


def _middleware_call(request):
    """Build a middleware around a trivial 200-OK view, return the
    response so tests can introspect the swapped-in request.user."""
    captured: dict[str, object] = {}

    def view(req):
        captured["request"] = req
        return HttpResponse(b"ok", status=200)

    mw = ApiTokenAuthMiddleware(view)
    response = mw(request)
    return response, captured.get("request")


def test_middleware_noops_when_no_authorization_header():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    response, after = _middleware_call(request)
    assert response.status_code == 200
    assert after is not None
    assert after.user.is_anonymous


def test_middleware_noops_on_non_apitoken_bearer():
    request = RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer alft_dt_someotherprefix")
    request.user = AnonymousUser()

    response, after = _middleware_call(request)
    assert response.status_code == 200
    assert after is not None
    assert after.user.is_anonymous
    assert getattr(after, "_api_token", None) is None


def test_middleware_authenticates_valid_token_and_stamps_row():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, row = _make_token(user, org)

    request = RequestFactory().get(
        "/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        HTTP_X_FORWARDED_FOR="203.0.113.99",
        HTTP_USER_AGENT="astrolift-cli/1.2.3",
    )
    request.user = AnonymousUser()

    response, after = _middleware_call(request)
    assert response.status_code == 200
    assert after is not None
    assert after.user.id == user.id
    assert after._api_token.id == row.id

    row.refresh_from_db()
    assert row.last_used_at is not None
    assert row.last_used_ip == "203.0.113.99"
    assert row.last_used_agent == "astrolift-cli/1.2.3"


def test_middleware_rejects_revoked_token_with_401():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, _ = _make_token(user, org, is_revoked=True)

    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    request.user = AnonymousUser()

    response, _ = _middleware_call(request)
    assert response.status_code == 401


def test_middleware_rejects_expired_token_with_401():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, _ = _make_token(user, org, expires_at=timezone.now() - dt.timedelta(seconds=1))

    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    request.user = AnonymousUser()

    response, _ = _middleware_call(request)
    assert response.status_code == 401


def test_middleware_rejects_inactive_user():
    org = Organization.objects.create(name="X", slug="x")
    user = _admin_user()
    plaintext, _ = _make_token(user, org)
    user.is_active = False
    user.save(update_fields=["is_active"])

    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    request.user = AnonymousUser()

    response, _ = _middleware_call(request)
    assert response.status_code == 401
