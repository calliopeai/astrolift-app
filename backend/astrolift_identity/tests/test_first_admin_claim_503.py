"""The first administrator is pending, claimed once by a verified sign-in, and audited.

``bootstrap_admin`` used to create a live user and re-apply its ``org_owner``
binding on every start, so removing that administrator lasted until the next
container restart. The contract the installer relies on (calliope-installer
docs/astrolift-sign-in.md, #503): ``ASTROLIFT_ADMIN_EMAIL`` leaves a pending
record; the first sign-in whose provider-verified email matches claims it;
the claim is audited; and once claimed, later starts change nothing.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from astrolift_identity.first_admin import claim
from astrolift_identity.models import FirstAdministrator, Member, Organization, Role, RoleBinding
from astrolift_operations.models import AuditEvent
from auth1.sessions import Auth1SessionWorkflow

User = get_user_model()

ADMIN = "admin@acme.example"
FRONTEND = "https://astro.example"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    settings.CACHES = {
        **settings.CACHES,
        "first_admin_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "first_admin_ratelimit"


def _bootstrap(email=ADMIN, **kw):
    call_command("bootstrap_admin", org="acme", org_name="Acme Corp", email=email, **kw)
    return Organization.objects.get(slug="acme")


def _owner_bindings(org):
    return RoleBinding.objects.filter(role__slug="org_owner", scope_kind="ORG", scope_id=org.pk)


def _claims(email, *, verified=True, sub=None, iss="https://issuer.test"):
    return {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": sub or f"sub-{uuid.uuid4().hex}",
            "iss": iss,
            "email": email,
            "email_verified": verified,
            "given_name": "Ada",
            "family_name": "Admin",
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
        },
    }


def _sign_in(monkeypatch, claims):
    from django.http import HttpResponse

    monkeypatch.setattr(
        Auth1SessionWorkflow._client.auth0,
        "authorize_redirect",
        lambda request, redirect_uri, **kw: HttpResponse(
            status=302, headers={"Location": "https://idp.test/"}
        ),
    )
    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_access_token", lambda request: claims)
    client = Client()
    client.get(reverse("login"), {"next": f"{FRONTEND}/auth/callback"})
    return client, client.get(reverse("callback"))


@pytest.mark.django_db
def test_bootstrap_leaves_a_pending_administrator_and_grants_nothing(system_role_catalog):
    org = _bootstrap(email="  Admin@Acme.Example ", is_superuser=True)

    record = FirstAdministrator.objects.get(organization=org)
    assert (record.email, record.is_superuser, record.claimed_at) == (ADMIN, True, None)
    assert not User.objects.filter(email__iexact=ADMIN).exists()
    assert not _owner_bindings(org).exists()
    assert not Member.objects.filter(scope_kind="ORG", scope_id=org.pk).exists()

    # Before the claim the operator may still correct the email.
    _bootstrap(email="other@acme.example")
    record.refresh_from_db()
    assert (record.email, record.is_superuser) == ("other@acme.example", False)


@pytest.mark.django_db
def test_an_unverified_email_does_not_claim(system_role_catalog, monkeypatch):
    org = _bootstrap()

    # auth1 refuses an address the IdP has not verified, Cognito's included
    # (#2430), before the claim is reached.
    cognito = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test"
    client, response = _sign_in(monkeypatch, _claims(ADMIN, verified=False, iss=cognito))

    assert response["Location"] == f"{FRONTEND}/auth/callback?error=email_unverified"
    assert "_auth_user_id" not in client.session
    assert FirstAdministrator.objects.get(organization=org).claimed_at is None
    assert not User.objects.filter(email__iexact=ADMIN).exists()
    assert claim(None, "sub-x", ADMIN, False) is None
    assert claim(None, "sub-x", ADMIN, "true") is None
    assert not AuditEvent.objects.filter(action="identity.first_admin.claimed").exists()


@pytest.mark.django_db
def test_a_matching_verified_sign_in_claims_once_and_is_audited(system_role_catalog, monkeypatch):
    org = _bootstrap(is_superuser=True)

    client, response = _sign_in(monkeypatch, _claims("ADMIN@acme.example", sub="sub-first"))

    user = User.objects.get(email__iexact=ADMIN)
    assert client.session["_auth_user_id"] == str(user.pk)
    assert "error=" not in response["Location"]
    assert not user.has_usable_password()
    assert (user.is_staff, user.is_superuser) == (True, True)
    assert list(_owner_bindings(org).values_list("user_id", flat=True)) == [user.pk]
    assert Member.objects.filter(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True).exists()
    record = FirstAdministrator.objects.get(organization=org)
    assert (record.claimed_subject, record.claimed_user_id) == ("sub-first", user.pk)
    assert record.claimed_at is not None
    audit = AuditEvent.objects.get(action="identity.first_admin.claimed")
    assert (audit.organization_id, audit.actor_id, audit.target_id) == (org.pk, str(user.pk), str(record.pk))
    assert audit.data["email"] == ADMIN
    assert audit.data["subject"] == "sub-first"
    assert audit.data["claimed_at"] == record.claimed_at.isoformat()
    assert "token" not in str(audit.data)

    # The owner is removed; signing in again re-grants nothing.
    _owner_bindings(org).delete()
    _sign_in(monkeypatch, _claims(ADMIN, sub="sub-first"))
    assert not _owner_bindings(org).exists()
    assert AuditEvent.objects.filter(action="identity.first_admin.claimed").count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("verified", ["true", "TRUE"])
def test_the_claim_takes_auth1s_reading_of_email_verified(system_role_catalog, monkeypatch, verified):
    """Cognito sends a mapped attribute as a string. An address auth1 signs in
    as verified (#2430) claims; the claim does not read the raw claim again."""
    org = _bootstrap()

    client, response = _sign_in(monkeypatch, _claims(ADMIN, verified=verified, sub="sub-string"))

    user = User.objects.get(email__iexact=ADMIN)
    assert client.session["_auth_user_id"] == str(user.pk)
    assert "error=" not in response["Location"]
    assert FirstAdministrator.objects.get(organization=org).claimed_user_id == user.pk
    assert list(_owner_bindings(org).values_list("user_id", flat=True)) == [user.pk]


@pytest.mark.django_db
def test_after_the_claim_restarts_and_env_changes_do_nothing(system_role_catalog):
    org = _bootstrap()
    user = claim(None, "sub-first", ADMIN, True)
    assert user is not None

    # Removing the administrator, then restarting: nobody is re-created.
    user.delete()
    _bootstrap()
    assert not User.objects.filter(email__iexact=ADMIN).exists()
    assert not _owner_bindings(org).exists()

    # Changing the variable after the claim is ignored, and the new address
    # has nothing to claim.
    _bootstrap(email="new@acme.example", is_superuser=True)
    record = FirstAdministrator.objects.get(organization=org)
    assert (record.email, record.is_superuser, record.claimed_subject) == (ADMIN, False, "sub-first")
    assert claim(None, "sub-new", "new@acme.example", True) is None
    assert not _owner_bindings(org).exists()


@pytest.mark.django_db
def test_an_org_that_already_has_an_owner_is_recorded_as_claimed(system_role_catalog):
    """Installs bootstrapped before the claim existed keep their owner and grant no one."""
    org = Organization.objects.create(slug="acme", name="Acme Corp")
    owner = User.objects.create(username="owner@acme.example", email="owner@acme.example")
    org_owner = Role.objects.get(slug="org_owner", is_system=True, organization=None)
    RoleBinding.objects.create(user=owner, role=org_owner, scope_kind="ORG", scope_id=org.pk)

    _bootstrap()

    record = FirstAdministrator.objects.get(organization=org)
    assert record.claimed_at is not None
    assert record.claimed_user_id == owner.pk
    assert claim(None, "sub-x", ADMIN, True) is None
    assert list(_owner_bindings(org).values_list("user_id", flat=True)) == [owner.pk]


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_produce_one_winner(system_role_catalog):
    org = _bootstrap()
    barrier = threading.Barrier(2)
    results, errors = [], []

    def sign_in(subject):
        try:
            with transaction.atomic():
                barrier.wait(timeout=10)
                results.append((subject, claim(None, subject, ADMIN, True)))
        except Exception as exc:  # noqa: BLE001 -- surfaced by the assert below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=sign_in, args=(f"sub-{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(results) == 2
    record = FirstAdministrator.objects.get(organization=org)
    assert record.claimed_subject in {"sub-0", "sub-1"}
    assert User.objects.filter(email__iexact=ADMIN).count() == 1
    assert _owner_bindings(org).count() == 1
    assert AuditEvent.objects.filter(action="identity.first_admin.claimed").count() == 1
