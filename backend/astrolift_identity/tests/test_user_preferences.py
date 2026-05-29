"""Tests for UserPreferences — user timezone preference (#775).

Covers:

* UserPreferences.for_user() creates a row on first call, is idempotent
* update_my_profile accepts a valid IANA timezone and persists it
* update_my_profile rejects an invalid IANA timezone name
* update_my_profile clears the timezone on empty-string input
* timezone is returned via astrolift_my_profile query
* passing timezone=None leaves the current value unchanged
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, UserPreferences
from astrolift_identity.schema.mutations import IdentityMutation, UpdateMyProfileInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info(user):
    request = SimpleNamespace(user=user, session={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


@pytest.fixture
def org():
    return Organization.objects.create(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
        allow_user_profile_edit=True,
    )


@pytest.fixture
def user():
    u = User(
        username=f"u-{uuid.uuid4().hex[:6]}",
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        first_name="Jane",
        last_name="Doe",
    )
    u.set_unusable_password()
    u.save()
    return u


# ─── Model tests ─────────────────────────────────────────────────────


def test_for_user_creates_prefs_lazily(user):
    """for_user() must create a row if none exists; the default timezone
    is an empty string (no override)."""
    prefs = UserPreferences.for_user(user)
    assert prefs.user_id == user.pk
    assert prefs.timezone == ""


def test_for_user_is_idempotent(user):
    """Calling for_user() twice must return the same row, not raise
    IntegrityError from a duplicate OneToOneField."""
    p1 = UserPreferences.for_user(user)
    p2 = UserPreferences.for_user(user)
    assert p1.pk == p2.pk


def test_for_user_persists_to_database(user):
    """The row written by for_user() must be readable via the ORM
    (not just an in-memory object)."""
    UserPreferences.for_user(user)
    assert UserPreferences.objects.filter(user=user).exists()


# ─── Mutation tests ──────────────────────────────────────────────────


def test_update_my_profile_saves_valid_timezone(user, org, permission_resolver):
    """A valid IANA timezone name must be persisted to UserPreferences
    and returned in the mutation payload."""
    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = IdentityMutation().update_my_profile(
            _info(user),
            input=UpdateMyProfileInput(timezone="America/New_York"),
        )
    assert result.ok is True
    assert result.data.timezone == "America/New_York"
    prefs = UserPreferences.objects.get(user=user)
    assert prefs.timezone == "America/New_York"


def test_update_my_profile_rejects_invalid_timezone(user, org, permission_resolver):
    """An unrecognised IANA timezone name must return ok=False with a
    VALIDATION error and must not touch the database."""
    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = IdentityMutation().update_my_profile(
            _info(user),
            input=UpdateMyProfileInput(timezone="Not/A/Zone"),
        )
    assert result.ok is False
    assert any("valid IANA" in (e.message or "") for e in result.errors)
    assert not UserPreferences.objects.filter(user=user).exists()


def test_update_my_profile_clears_timezone_on_empty_string(user, org, permission_resolver):
    """An empty string must clear a previously-saved timezone, reverting
    the user to browser-detected zone behavior."""
    UserPreferences.objects.create(user=user, timezone="Europe/London")

    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = IdentityMutation().update_my_profile(
            _info(user),
            input=UpdateMyProfileInput(timezone=""),
        )
    assert result.ok is True
    assert result.data.timezone is None  # empty string normalised to None in payload
    assert UserPreferences.objects.get(user=user).timezone == ""


def test_update_my_profile_timezone_none_leaves_value_unchanged(user, org, permission_resolver):
    """Passing timezone=None must not mutate the existing preference
    (None means 'I didn't touch this field')."""
    UserPreferences.objects.create(user=user, timezone="Asia/Tokyo")

    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = IdentityMutation().update_my_profile(
            _info(user),
            input=UpdateMyProfileInput(timezone=None),
        )
    assert result.ok is True
    assert result.data.timezone == "Asia/Tokyo"
    assert UserPreferences.objects.get(user=user).timezone == "Asia/Tokyo"


def test_my_profile_query_returns_timezone(user, org, permission_resolver):
    """astrolift_my_profile must return the saved timezone in the
    payload so the settings UI can initialise the picker correctly."""
    from astrolift_identity.schema.queries import IdentityQuery

    UserPreferences.objects.create(user=user, timezone="Pacific/Auckland")
    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        profile = IdentityQuery().astrolift_my_profile(_info(user))
    assert profile is not None
    assert profile.timezone == "Pacific/Auckland"


def test_my_profile_query_returns_none_timezone_when_no_prefs(user, org, permission_resolver):
    """When no UserPreferences row exists (new user, never saved a
    preference), the query must return timezone=None, not raise."""
    from astrolift_identity.schema.queries import IdentityQuery

    permission_resolver.grant(Permission.ORG_MEMBER)
    with tenant_context(TenantContext(organization_id=org.id)):
        profile = IdentityQuery().astrolift_my_profile(_info(user))
    assert profile is not None
    assert profile.timezone is None
