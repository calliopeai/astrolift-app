"""A trusted domain creates accounts only when the org turned that on.

Trusted domains used to act on people who already had an account: a new
sign-in from one was refused as ``no_account`` before auto-join could run.
``Organization.trusted_domain_signup`` lets a verified sign-in under a trusted
domain create its own account, which auto-join then makes a member with the
domain's default role. Off by default.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from astrolift_identity.models import (
    Member,
    Organization,
    OrganizationAllowlistedDomain,
    Role,
    RoleBinding,
)
from astrolift_identity.schema.mutations import IdentityMutation
from astrolift_identity.schema.mutations.types import UpdateOrganizationInput
from auth1.sessions import Auth1SessionWorkflow
from core.mutations import AuditEntry, register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()

FRONTEND = "https://astro.example"
COGNITO = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_pool"


@pytest.fixture(autouse=True)
def isolated(settings, monkeypatch):
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
    monkeypatch.delenv("ASTROLIFT_AUTO_SIGNUP_DOMAINS", raising=False)


def _sign_in(client: Client, monkeypatch, email, verified=True):
    from django.http import HttpResponse

    claims = {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": f"sub-{uuid.uuid4().hex}",
            "iss": COGNITO,
            "email": email,
            "email_verified": verified,
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
        },
    }
    monkeypatch.setattr(
        Auth1SessionWorkflow._client.auth0,
        "authorize_redirect",
        lambda request, redirect_uri, **kw: HttpResponse(
            status=302, headers={"Location": "https://idp.test/"}
        ),
    )
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", lambda request: claims)
    client.get(reverse("login"), {"next": f"{FRONTEND}/auth/callback"})
    return client.get(reverse("callback"))


def _org(*, signup: bool) -> tuple[Organization, Role]:
    org = Organization.objects.create(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}", trusted_domain_signup=signup
    )
    viewer = Role.objects.create(
        slug=f"viewer-{uuid.uuid4().hex[:6]}",
        name="Viewer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    OrganizationAllowlistedDomain.objects.create(organization=org, domain="corp.example", default_role=viewer)
    return org, viewer


def _error(response) -> list[str]:
    return parse_qs(urlsplit(response["Location"]).query).get("error", [])


def test_off_by_default():
    assert (
        Organization.objects.create(name="Default", slug=f"d-{uuid.uuid4().hex[:6]}").trusted_domain_signup
        is False
    )


def test_a_trusted_domain_creates_no_account_while_signup_is_off(monkeypatch):
    _org(signup=False)
    client = Client()
    users = User.objects.count()

    response = _sign_in(client, monkeypatch, "newcomer@corp.example")

    assert _error(response) == ["no_account"]
    assert User.objects.count() == users


def test_a_trusted_domain_creates_a_member_with_the_default_role_when_signup_is_on(monkeypatch):
    org, viewer = _org(signup=True)
    client = Client()

    response = _sign_in(client, monkeypatch, "newcomer@corp.example")

    assert _error(response) == []
    user = User.objects.get(email="newcomer@corp.example")
    assert client.session["_auth_user_id"] == str(user.pk)
    assert not user.is_staff and not user.is_superuser
    assert Member.objects.filter(user=user, scope_id=org.pk, is_active=True).exists()
    assert list(RoleBinding.objects.filter(user=user).values_list("role_id", flat=True)) == [viewer.pk]


def test_signup_covers_only_trusted_domains(monkeypatch):
    _org(signup=True)
    client = Client()
    users = User.objects.count()

    response = _sign_in(client, monkeypatch, "outsider@elsewhere.example")

    assert _error(response) == ["no_account"]
    assert User.objects.count() == users


def test_signup_needs_a_verified_address(monkeypatch):
    _org(signup=True)
    client = Client()
    users = User.objects.count()

    response = _sign_in(client, monkeypatch, "newcomer@corp.example", verified=False)

    assert _error(response) == ["email_unverified"]
    assert User.objects.count() == users


@pytest.fixture
def audit_capture():
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as _orig_writer  # noqa: PLC2701

    register_audit_writer(captured.append)
    yield captured
    register_audit_writer(_orig_writer)


def test_changing_the_setting_is_audited(audit_capture, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org, _viewer = _org(signup=False)
    user = User.objects.create(username=f"admin-{uuid.uuid4().hex[:6]}", email="admin@corp.example")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    request = SimpleNamespace(user=user, session={})
    info = SimpleNamespace(context=SimpleNamespace(request=request, user=user))

    def update(**fields):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            return IdentityMutation().update_organization(
                info, UpdateOrganizationInput(id=str(org.guid), **fields)
            )

    assert update(trusted_domain_signup=True).data.trusted_domain_signup is True
    assert update(trusted_domain_signup=True).ok, "a no-op write"
    assert update(trusted_domain_signup=False).data.trusted_domain_signup is False

    changes = [e.extra for e in audit_capture if e.action == "org.trusted_domain_signup.changed"]
    assert changes == [{"trusted_domain_signup": True}, {"trusted_domain_signup": False}]
