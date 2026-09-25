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
    ResendInvitationInput,
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
    user, _ = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0]})
    return user


def _invite_role():
    return Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )


def _owner(org) -> User:
    """An inviter who holds every permission: a role on an invitation is
    capped at what the inviter holds (#1964)."""
    user = _admin_user()
    role = Role.objects.create(
        organization=org,
        slug="holds-all",
        name="Holds all",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[p.value for p in Permission],
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)
    return user


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


# ---- resend -----------------------------------------------------------


def test_resend_invitation_rotates_token_and_refreshes_expiry(permission_resolver):
    """Resend mints a fresh token (the old link dies) and pushes the
    expiry out to a new window, returning the new plaintext exactly
    once so the copy-link durable channel survives the resend."""
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with tenant_context(TenantContext(organization_id=org.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user()),
            input=CreateInvitationInput(email="resend@astrolift.dev"),
        )
    original_plaintext = c.data.plaintext_token
    inv = Invitation.objects.get()
    old_hash = inv.token_hash

    # Backdate expiry so the refresh is an observable strict increase and
    # mirrors the real "resend a pending invite that's past its TTL" case.
    Invitation.objects.filter(pk=inv.pk).update(expires_at=timezone.now() - dt.timedelta(hours=1))
    old_expiry = Invitation.objects.get(pk=inv.pk).expires_at

    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().resend_invitation(
            _info(_admin_user()),
            input=ResendInvitationInput(id=c.data.invitation.id),
        )
    assert r.ok, r.errors

    # A fresh plaintext comes back exactly once (the durable copy-link).
    new_plaintext = r.data.plaintext_token
    assert new_plaintext.startswith("alft_")
    assert new_plaintext != original_plaintext
    assert r.data.accept_url_path == f"/auth/invitation/{new_plaintext}"

    inv.refresh_from_db()
    # Token rotated: hash changed, matches the new plaintext, and the
    # original link no longer validates.
    assert inv.token_hash != old_hash
    assert inv.token_hash == hashlib.sha256(new_plaintext.encode()).hexdigest()
    assert inv.token_hash != hashlib.sha256(original_plaintext.encode()).hexdigest()
    # Expiry refreshed into the future.
    assert inv.expires_at > old_expiry
    assert inv.expires_at > timezone.now()
    # Still a pending invitation.
    assert inv.status == Invitation.Status.PENDING


def test_resend_invitation_rejects_accepted(permission_resolver):
    """An accepted invitation is terminal — resend must not revive it."""
    org = Organization.objects.create(name="X", slug="x")
    role = _invite_role()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    owner = _owner(org)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=owner.id)):
        c = IdentityMutation().create_invitation(
            _info(owner),
            input=CreateInvitationInput(email="accepted@astrolift.dev", role_slug=role.slug),
        )
    accepting = User.objects.create_user(username="accepted", email="accepted@astrolift.dev")
    accepted = IdentityMutation().accept_invitation(
        _info(accepting), input=AcceptInvitationInput(token=c.data.plaintext_token)
    )
    assert accepted.ok

    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().resend_invitation(
            _info(_admin_user()),
            input=ResendInvitationInput(id=c.data.invitation.id),
        )
    assert not r.ok
    assert r.errors[0].code == "PRECONDITION"
    # The accepted row is left untouched.
    assert Invitation.objects.get().status == Invitation.Status.ACCEPTED


def test_resend_invitation_other_org_cannot_see(permission_resolver):
    """Cross-org isolation: resend can't reach into another org, and a
    denied cross-tenant call must not rotate the target's token."""
    org_a = Organization.objects.create(name="A", slug="org-a")
    org_b = Organization.objects.create(name="B", slug="org-b")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org_a.id)):
        c = IdentityMutation().create_invitation(
            _info(_admin_user("aa@astrolift.dev")),
            input=CreateInvitationInput(email="xorg@astrolift.dev"),
        )
    a_hash = Invitation.objects.get().token_hash

    with tenant_context(TenantContext(organization_id=org_b.id)):
        r = IdentityMutation().resend_invitation(
            _info(_admin_user("bb@astrolift.dev")),
            input=ResendInvitationInput(id=c.data.invitation.id),
        )
    assert not r.ok
    assert r.errors[0].code == "NOT_FOUND"
    assert Invitation.objects.get().token_hash == a_hash


def test_resend_invitation_requires_permission(permission_resolver):
    """Without ORG_MANAGE_MEMBERS the resend fails closed and the token
    is not rotated."""
    org = Organization.objects.create(name="X", slug="x")
    inv = Invitation.objects.create(
        email="perm@astrolift.dev",
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.id,
        token_hash="x" * 64,
        status=Invitation.Status.PENDING,
    )
    # permission_resolver installed but no grant → deny-by-default.
    with tenant_context(TenantContext(organization_id=org.id)):
        r = IdentityMutation().resend_invitation(
            _info(_admin_user()),
            input=ResendInvitationInput(id=str(inv.guid)),
        )
    assert not r.ok
    assert r.errors[0].code == "PERMISSION_DENIED"
    assert Invitation.objects.get().token_hash == "x" * 64


# ---- accept -----------------------------------------------------------


def test_accept_invitation_creates_member_and_role_binding(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    role = _invite_role()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    owner = _owner(org)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=owner.id)):
        c = IdentityMutation().create_invitation(
            _info(owner),
            input=CreateInvitationInput(email="newbie@astrolift.dev", role_slug=role.slug),
        )
    plaintext = c.data.plaintext_token

    # The accepting user signs in fresh and clicks the link.
    accepting = User.objects.create_user(username="newbie", email="newbie@astrolift.dev")
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
    r = IdentityMutation().accept_invitation(_info(None), input=AcceptInvitationInput(token="alft_anything"))
    assert not r.ok
    assert r.errors[0].code == "PERMISSION_DENIED"


def test_accept_rejects_unknown_token():
    user = User.objects.create_user(username="u", email="u@astrolift.dev")
    r = IdentityMutation().accept_invitation(_info(user), input=AcceptInvitationInput(token="alft_garbage"))
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


# ---- delete_invitation (resolved-row cleanup) ------------------------


def test_delete_invitation_removes_resolved_row(permission_resolver):
    from django.utils import timezone as _tz

    org = Organization.objects.create(name="Del", slug="del-inv")
    actor = _admin_user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    inv = Invitation.objects.create(
        email="gone@astrolift.dev",
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.id,
        status=Invitation.Status.REVOKED,
        token_hash="x" * 64,
        expires_at=_tz.now(),
    )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().delete_invitation(
            _info(actor), input=RevokeInvitationInput(id=str(inv.guid))
        )
    assert result.ok, result.errors
    inv.refresh_from_db()
    assert inv.deleted_at is not None


def test_delete_invitation_refuses_pending(permission_resolver):
    from datetime import timedelta

    from django.utils import timezone as _tz

    org = Organization.objects.create(name="Del2", slug="del-inv-2")
    actor = _admin_user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    inv = Invitation.objects.create(
        email="live@astrolift.dev",
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.id,
        status=Invitation.Status.PENDING,
        token_hash="y" * 64,
        expires_at=_tz.now() + timedelta(days=7),
    )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = IdentityMutation().delete_invitation(
            _info(actor), input=RevokeInvitationInput(id=str(inv.guid))
        )
    assert result.ok is False
    assert "pending" in result.errors[0].message
    inv.refresh_from_db()
    assert inv.deleted_at is None
