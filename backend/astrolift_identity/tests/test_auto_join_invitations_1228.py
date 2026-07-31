"""SSO auto-join must consume pending invitations (#1228).

Observed live: invited users signed in through the org's domain-allowlist
SSO path instead of the invite accept link. They became members (with the
allowlist default role), while their invitation sat "Pending" forever —
a confusing Members page, a dangling live accept token, and the invite's
intended role (e.g. org_owner) silently never applied.

These tests pin the convergence: joining via allowlist marks the matching
pending invitation accepted and applies its role; already-members get the
same resolution on their next login; expired invites flip to expired
rather than accepted; and review-gated joins don't receive the invite's
role early.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    OrganizationAllowlistedDomain,
    Role,
    RoleBinding,
)
from auth1.auto_join import maybe_auto_join_user

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _org() -> Organization:
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


def _user(email: str) -> User:
    return User.objects.create(username=email.split("@")[0] + uuid.uuid4().hex[:4], email=email)


def _role(slug: str) -> Role:
    return Role.objects.create(
        slug=f"{slug}-{uuid.uuid4().hex[:6]}",
        name=slug,
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )


def _allowlist(org: Organization, domain: str = "acme.dev", *, requires_review: bool = False):
    return OrganizationAllowlistedDomain.objects.create(
        organization=org,
        domain=domain,
        requires_review=requires_review,
    )


def _invite(org: Organization, email: str, *, role: Role | None = None, expired: bool = False):
    inv = Invitation.objects.create(
        email=email.lower(),
        scope_kind=Invitation.ScopeKind.ORG,
        scope_id=org.pk,
        role=role,
        status=Invitation.Status.PENDING,
        token_hash=uuid.uuid4().hex,
        expires_at=timezone.now() + (timedelta(days=-1) if expired else timedelta(days=7)),
    )
    return inv


def test_allowlist_join_resolves_pending_invite_and_applies_role():
    org = _org()
    _allowlist(org)
    owner_role = _role("owner")
    user = _user("Pat.Evans@acme.dev")  # mixed case: matching is case-insensitive
    inv = _invite(org, "pat.evans@acme.dev", role=owner_role)

    assert maybe_auto_join_user(user) is True

    inv.refresh_from_db()
    assert inv.status == Invitation.Status.ACCEPTED
    assert inv.accepted_at is not None
    assert RoleBinding.objects.filter(user=user, role=owner_role, scope_kind="ORG", scope_id=org.pk).exists()


def test_existing_member_login_resolves_late_invite():
    """An invite issued AFTER the user already joined (or one that raced
    the join) resolves on their next login pass."""
    org = _org()
    _allowlist(org)
    user = _user("reba@acme.dev")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    inv = _invite(org, "reba@acme.dev")

    # Returns False (no new membership) but the invite must resolve.
    assert maybe_auto_join_user(user) is False
    inv.refresh_from_db()
    assert inv.status == Invitation.Status.ACCEPTED


def test_expired_invite_flips_to_expired_not_accepted():
    org = _org()
    _allowlist(org)
    user = _user("late@acme.dev")
    inv = _invite(org, "late@acme.dev", expired=True)

    maybe_auto_join_user(user)

    inv.refresh_from_db()
    assert inv.status == Invitation.Status.EXPIRED
    assert inv.accepted_at is None


def test_review_gated_join_defers_invite_role():
    """requires_review memberships must not receive the invite's role
    early — same gate as the allowlist default role."""
    org = _org()
    _allowlist(org, requires_review=True)
    owner_role = _role("owner-gated")
    user = _user("gated@acme.dev")
    inv = _invite(org, "gated@acme.dev", role=owner_role)

    maybe_auto_join_user(user)

    inv.refresh_from_db()
    # Invite is consumed (no dangling token) but the role waits for review.
    assert inv.status == Invitation.Status.ACCEPTED
    assert not RoleBinding.objects.filter(user=user, role=owner_role).exists()


def test_foreign_org_invite_untouched():
    """Only the joined org's invitations resolve — an invite for the same
    email in another org stays pending."""
    org = _org()
    other = _org()
    _allowlist(org)
    user = _user("cross@acme.dev")
    foreign = _invite(other, "cross@acme.dev")

    maybe_auto_join_user(user)

    foreign.refresh_from_db()
    assert foreign.status == Invitation.Status.PENDING


def test_invite_role_suppresses_allowlist_default_role():
    """When the resolved invitation carries a role, that is the operator's
    explicit intent — the allowlist default role is skipped instead of
    stacking both (the project_viewer chip-noise case)."""
    org = _org()
    default_role = _role("default-viewer")
    rule = _allowlist(org)
    rule.default_role = default_role
    rule.save()
    owner_role = _role("owner-intent")
    user = _user("intent@acme.dev")
    _invite(org, "intent@acme.dev", role=owner_role)

    assert maybe_auto_join_user(user) is True

    assert RoleBinding.objects.filter(user=user, role=owner_role).exists()
    assert not RoleBinding.objects.filter(user=user, role=default_role).exists()


def test_roleless_invite_still_gets_allowlist_default_role():
    org = _org()
    default_role = _role("default-viewer-2")
    rule = _allowlist(org)
    rule.default_role = default_role
    rule.save()
    user = _user("plain@acme.dev")
    inv = _invite(org, "plain@acme.dev")  # no role on the invite

    assert maybe_auto_join_user(user) is True

    inv.refresh_from_db()
    assert inv.status == Invitation.Status.ACCEPTED
    assert RoleBinding.objects.filter(user=user, role=default_role).exists()
