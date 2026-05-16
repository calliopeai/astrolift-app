"""
Tests for the first-class ``astrolift_anonymize_user`` mutation (#312).

The legacy ``profile_request_delete_user`` mutation on the core schema
already wraps ``Profile.anonymize_user``; the new
``astrolift_identity`` surface adds structured permission gating, an
idempotent already-anonymized case, and a typed payload the operator
UI can render.

Tests hit the real Postgres DB and exercise the mutation class
directly (matching the connected_accounts test pattern in this same
folder) — the schema-level wiring is verified by the SDL regeneration
step, not here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.anonymize import (
    AstroliftAnonymizeUserInput,
    IdentityAnonymizeUserMutation,
)
from astrolift_identity.models import Member, Organization
from core.models import Profile
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_external_side_effects(monkeypatch):
    """``Profile.anonymize_user`` indirectly triggers profile-save
    signals that try to talk to OpenSearch / Auth0 / RocketChat in
    full-stack runs. Stub them out so the test container doesn't have
    to host those side-services."""
    # Auth0 register hook on User post_save.
    monkeypatch.setattr(
        "core.models.user.Profile.register_in_auth0",
        lambda self, reset_password=True: None,
    )
    # OpenSearch profile indexing — best-effort skip if the path exists.
    try:
        monkeypatch.setattr(
            "core.documents.ProfileDocument.index_profile",
            classmethod(lambda cls, p: None),
        )
        monkeypatch.setattr(
            "core.documents.ProfileDocument.delete_profile",
            classmethod(lambda cls, gid: None),
        )
    except Exception:  # noqa: BLE001 — module may not exist in all configs
        pass


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-anon-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def actor():
    """The user *performing* the mutation."""
    return User.objects.create(
        username=f"actor-{uuid.uuid4().hex[:8]}",
        email=f"actor-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Ada",
        last_name="Actor",
    )


@pytest.fixture
def target():
    """The user being anonymized (distinct from the actor)."""
    return User.objects.create(
        username=f"target-{uuid.uuid4().hex[:8]}",
        email=f"target-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Tina",
        last_name="Target",
    )


def _info(user=None):
    if user is None:
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False, pk=None))
    else:
        request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _make_member(user, org, *, scope_kind=Member.ScopeKind.ORG, scope_id=None):
    return Member.objects.create(
        user=user,
        scope_kind=scope_kind,
        scope_id=scope_id if scope_id is not None else org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Self-anonymization
# ---------------------------------------------------------------------------


def test_self_anonymization_succeeds_and_requires_logout(actor, org):
    """Any authenticated user can anonymize themselves; the payload
    must signal that the FE needs to drop the session."""
    membership = _make_member(actor, org)
    pre_email = actor.email
    pre_first_name = actor.first_name

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(actor.pk)),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload is not None
    assert str(payload.anonymized_user_id) == str(actor.pk)
    assert payload.was_self is True
    assert payload.requires_logout is True
    assert payload.lifecycle == Member.Lifecycle.DEACTIVATED.value

    # Verify Profile.anonymize_user actually ran — PII scrubbed.
    actor.refresh_from_db()
    assert actor.email != pre_email
    assert actor.email.endswith("@anon-astrolift.net")
    assert actor.first_name != pre_first_name
    assert actor.is_active is False

    # Membership lifecycle flipped.
    membership.refresh_from_db()
    assert membership.lifecycle == Member.Lifecycle.DEACTIVATED.value
    assert membership.is_active is False


def test_self_anonymization_works_without_org_manage_members(actor, org, permission_resolver):
    """Explicit guard: the permission resolver denies everything by
    default in this fixture, so a successful self-anon proves the
    code path doesn't require ``org.manage_members``."""
    # Note: no .grant() call — actor has no perms at all.
    _make_member(actor, org)

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(actor.pk)),
        )

    assert result.ok, result.errors
    assert result.data.was_self is True


# ---------------------------------------------------------------------------
# Anonymizing another user — permission gating
# ---------------------------------------------------------------------------


def test_anonymizing_another_user_with_permission_succeeds(
    actor, target, org, permission_resolver
):
    """Operator with ``org.manage_members`` can anonymize another user."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    _make_member(target, org)
    pre_email = target.email

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(target.pk)),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload.was_self is False
    assert payload.requires_logout is False
    assert str(payload.anonymized_user_id) == str(target.pk)

    target.refresh_from_db()
    assert target.email != pre_email
    assert target.email.endswith("@anon-astrolift.net")
    assert target.is_active is False


def test_anonymizing_another_user_without_permission_denied(actor, target, org):
    """Operator without ``org.manage_members`` is denied; target left intact."""
    _make_member(target, org)
    pre_email = target.email
    pre_active = target.is_active

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(target.pk)),
        )

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"

    # Target was not touched.
    target.refresh_from_db()
    assert target.email == pre_email
    assert target.is_active is pre_active


# ---------------------------------------------------------------------------
# Unauthenticated / unknown / idempotency
# ---------------------------------------------------------------------------


def test_unauthenticated_caller_denied(target, org):
    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        result = m.astrolift_anonymize_user(
            _info(user=None),
            input=AstroliftAnonymizeUserInput(user_gid=str(target.pk)),
        )

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"

    # Target untouched.
    pre = target.email
    target.refresh_from_db()
    assert target.email == pre


def test_unknown_user_returns_not_found(actor, org, permission_resolver):
    """A user_gid that doesn't resolve to a row returns NOT_FOUND, not
    INTERNAL, even when the operator has perms."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid="99999999"),
        )

    assert result.ok is False
    [err] = result.errors
    assert err.code == "NOT_FOUND"
    assert err.field == "userGid"


def test_malformed_user_gid_returns_not_found(actor, org, permission_resolver):
    """Non-numeric user_gid is treated as NOT_FOUND, not as a 500."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid="not-a-pk"),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"


def test_already_anonymized_user_is_idempotent(actor, target, org, permission_resolver):
    """Running the mutation a second time on the same user returns
    ok=True with requires_logout=False and does not re-run the
    placeholder generator (which would otherwise mint a fresh
    short_uuid and churn the row)."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    _make_member(target, org)

    # First pass: real anonymization.
    Profile.anonymize_user(target)
    target.refresh_from_db()
    first_email = target.email
    first_username = target.username
    assert first_email.endswith("@anon-astrolift.net")

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(target.pk)),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload.was_self is False
    assert payload.requires_logout is False
    assert str(payload.anonymized_user_id) == str(target.pk)

    # Critical: the placeholder values were NOT regenerated.
    target.refresh_from_db()
    assert target.email == first_email
    assert target.username == first_username


def test_already_anonymized_self_returns_ok_without_requiring_logout(
    actor, org, permission_resolver
):
    """If the actor is themselves already anonymized (somehow they
    still have a session), the idempotent path runs and does NOT ask
    for another logout."""
    _make_member(actor, org)
    Profile.anonymize_user(actor)
    actor.refresh_from_db()

    m = IdentityAnonymizeUserMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = m.astrolift_anonymize_user(
            _info(actor),
            input=AstroliftAnonymizeUserInput(user_gid=str(actor.pk)),
        )

    assert result.ok, result.errors
    assert result.data.was_self is True
    assert result.data.requires_logout is False
