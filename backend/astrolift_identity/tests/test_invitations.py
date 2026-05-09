"""Tests for member invitation lifecycle (#77).

Covers create / revoke / accept paths plus the security-critical
properties: token is hashed at rest, plaintext is shown exactly once,
mismatched-email accept is rejected, expired/already-accepted
invitations cannot be redeemed twice, idempotent member creation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    Role,
    RoleBinding,
)
from astrolift_identity.schema.mutations import (
    AcceptInvitationInput,
    CreateInvitationInput,
    IdentityMutation,
    RevokeInvitationInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info(user=None):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _admin_user(email: str | None = None) -> User:
    """Create a unique admin user. Tests that need a stable identity
    pass an explicit email; the default generates a unique username
    each call so multiple admins in the same test don't collide on
    the username unique constraint."""
    if email is None:
        import uuid

        email = f"admin-{uuid.uuid4().hex[:8]}@astrolift.dev"
    user, _ = User.objects.get_or_create(
        email=email, defaults={"username": email.split("@")[0]}
    )
    return user


def _invite_role():
    return Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )


# ---- create -----------------------------------------------------------


def test_create_invitation_persists_only_hash(permission_resolver):
    """The plaintext token must NEVER hit the DB. We assert by digest."""
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    invited_by = _admin_user()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=invited_by.id)):
        result = IdentityMutation().create_invitation(
            _info(invited_by),
            input=CreateInvitationInput(email="newcomer@astrolift.dev"),
        )

    assert result.ok, result.errors
    plaintext = result.data.plaintext_token
    assert plaintext.startswith("alft_")
    assert len(plaintext) > 30

    inv = Invitation.objects.get(email="newcomer@astrolift.dev")
    expected_digest = hashlib.sha256(plaintext.encode()).hexdigest()
    assert inv.token_hash == expected_digest
    # Token is hashed; the stored value MUST NOT be the plaintext.
    assert inv.token_hash != plaintext

    assert inv.status == Invitation.Status.PENDING
    assert inv.invited_by_id == invited_by.id
    assert inv.scope_kind == Invitation.ScopeKind.ORG
    assert inv.scope_id == org.id
    assert result.data.accept_url_path == f"/auth/invitation/{plaintext}"


def test_create_invitation_normalizes_email(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="  Mixed.Case@Example.COM "),
        )
    assert r.ok
    assert Invitation.objects.get().email == "mixed.case@example.com"


def test_create_invitation_rejects_duplicate_pending(permission_resolver):
    """Two pending invites at the same scope is a footgun; the second
    create call should fail with CONFLICT so callers route to a
    'rotate token' flow instead of silently doubling up."""
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        a = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="dup@astrolift.dev"),
        )
        b = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="dup@astrolift.dev"),
        )
    assert a.ok
    assert not b.ok
    assert b.errors[0].code == "CONFLICT"


def test_create_invitation_validates_email(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="not-an-email"),
        )
    assert not r.ok
    assert r.errors[0].field == "email"


def test_create_invitation_requires_permission():
    """Without ORG_MANAGE_MEMBERS the mutation must fail."""
    org = Organization.objects.create(name="X", slug="x")
    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="nope@astrolift.dev"),
        )
    assert not r.ok
    assert r.errors[0].code == "PERMISSION_DENIED"


# ---- revoke -----------------------------------------------------------


def test_revoke_invitation_marks_revoked(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="rev@astrolift.dev"),
        )
        r = IdentityMutation().revoke_invitation(
            _info(_admin_user()),
            input=RevokeInvitationInput(id=c.data.invitation.id),
        )
    assert r.ok
    inv = Invitation.objects.get()
    assert inv.status == Invitation.Status.REVOKED


def test_revoke_invitation_other_org_cannot_see(permission_resolver):
    """Cross-org isolation: revoke can't reach into another org."""
    org_a = Organization.objects.create(name="A", slug="org-a")
    org_b = Organization.objects.create(name="B", slug="org-b")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org_a.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user("a@astrolift.dev")),
            input=CreateInvitationInput(email="cross@astrolift.dev"),
        )

    # Same caller, different active org → cannot see the invitation.
    with tenant_context(TenantContext(organization_id=org_b.id)):
        r = IdentityMutation().revoke_invitation(
            _info(_admin_user("b@astrolift.dev")),
            input=RevokeInvitationInput(id=c.data.invitation.id),
        )
    assert not r.ok
    assert r.errors[0].code == "NOT_FOUND"


# ---- accept -----------------------------------------------------------


def test_accept_invitation_creates_member_and_role_binding(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    role = _invite_role()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(
                email="newbie@astrolift.dev", role_slug=role.slug
            ),
        )
    plaintext = c.data.plaintext_token

    # The accepting user signs in fresh and clicks the link.
    accepting = User.objects.create_user(
        username="newbie", email="newbie@astrolift.dev"
    )
    # Accept resolver runs without a tenant context (matches reality).
    r = IdentityMutation().accept_invitation(
        _info(accepting),
        input=AcceptInvitationInput(token=plaintext),
    )
    assert r.ok, r.errors

    member = Member.objects.get(user=accepting)
    assert member.scope_kind == Member.ScopeKind.ORG
    assert member.scope_id == org.id
    assert member.lifecycle == Member.Lifecycle.ACTIVE

    binding = RoleBinding.objects.get(user=accepting)
    assert binding.role_id == role.id
    assert binding.scope_kind == RoleBinding.ScopeKind.ORG
    assert binding.scope_id == org.id

    inv = Invitation.objects.get()
    assert inv.status == Invitation.Status.ACCEPTED
    assert inv.accepted_at is not None


def test_accept_rejects_mismatched_email(permission_resolver):
    """A leaked token must not be redeemable against a different
    account — defense in depth on top of the token's secrecy."""
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="alice@astrolift.dev"),
        )
    eve = User.objects.create_user(username="eve", email="eve@astrolift.dev")

    r = IdentityMutation().accept_invitation(
        _info(eve), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    assert not r.ok
    assert r.errors[0].code == "PERMISSION_DENIED"
    # And no Member row was leaked into existence
    assert not Member.objects.filter(user=eve).exists()


def test_accept_rejects_unauthenticated():
    r = IdentityMutation().accept_invitation(
        _info(None), input=AcceptInvitationInput(token="alft_anything")
    )
    assert not r.ok
    assert r.errors[0].code == "PERMISSION_DENIED"


def test_accept_rejects_unknown_token():
    user = User.objects.create_user(username="u", email="u@astrolift.dev")
    r = IdentityMutation().accept_invitation(
        _info(user), input=AcceptInvitationInput(token="alft_garbage")
    )
    assert not r.ok
    assert r.errors[0].code == "NOT_FOUND"


def test_accept_rejects_already_accepted(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="one@astrolift.dev"),
        )
    accepting = User.objects.create_user(username="one", email="one@astrolift.dev")
    first = IdentityMutation().accept_invitation(
        _info(accepting), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    second = IdentityMutation().accept_invitation(
        _info(accepting), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    assert first.ok
    assert not second.ok
    assert second.errors[0].code == "PRECONDITION"


def test_accept_rejects_expired(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="late@astrolift.dev"),
        )
    # Backdate expiry to put the row past its TTL.
    Invitation.objects.filter(pk=Invitation.objects.get().pk).update(
        expires_at=timezone.now() - dt.timedelta(hours=1)
    )

    accepting = User.objects.create_user(username="late", email="late@astrolift.dev")
    r = IdentityMutation().accept_invitation(
        _info(accepting), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    assert not r.ok
    assert r.errors[0].code == "PRECONDITION"
    assert "expired" in r.errors[0].message.lower()
    # The row's status flips to EXPIRED on the read so subsequent
    # accept attempts fail fast at the status check, not the
    # expiry-time check.
    assert Invitation.objects.get().status == Invitation.Status.EXPIRED


def test_accept_rejects_revoked(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="zip@astrolift.dev"),
        )
        IdentityMutation().revoke_invitation(
            _info(_admin_user()),
            input=RevokeInvitationInput(id=c.data.invitation.id),
        )
    user = User.objects.create_user(username="zip", email="zip@astrolift.dev")
    r = IdentityMutation().accept_invitation(
        _info(user), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    assert not r.ok
    assert r.errors[0].code == "PRECONDITION"
