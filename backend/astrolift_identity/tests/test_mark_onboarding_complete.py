"""
Tests for ``markOnboardingComplete`` — first-run wizard state.

Covers the MutationResult envelope shape:
* permission denial when the actor lacks ``org.update``
* happy path flips ``onboarding_completed_at`` and returns
  ``already_completed=False``
* second call is idempotent and returns ``already_completed=True``
  without re-writing the timestamp
* the field round-trips on ``astroliftOrganization``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_identity.schema.mutations import (
    IdentityMutation,
    MarkOnboardingCompleteInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

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


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-onboarding")


@pytest.fixture
def user():
    User = get_user_model()
    return User.objects.create(username="op@onboarding", email="op@onboarding")


@pytest.fixture
def fake_info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _grant_org_update(user, org):
    role = Role.objects.create(
        name="ob-admin",
        slug="ob-admin",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.ORG_UPDATE.value, Permission.ORG_READ.value],
        is_system=False,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def test_default_is_null(org):
    assert org.onboarding_completed_at is None


def test_mark_complete_denied_without_permission(org, user, fake_info):
    """Operator with no org.update grant is rejected via the
    structured envelope — never raised — and the column stays null."""
    m = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput(skip=False))
    assert result.ok is False
    assert any(err.code == "PERMISSION_DENIED" for err in result.errors)
    org.refresh_from_db()
    assert org.onboarding_completed_at is None


def test_mark_complete_happy_path_flips_timestamp(org, user, fake_info):
    _grant_org_update(user, org)
    m = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput(skip=False))
    assert result.ok is True
    assert result.data is not None
    assert result.data.already_completed is False
    assert result.data.organization.onboarding_completed_at is not None
    org.refresh_from_db()
    assert org.onboarding_completed_at is not None


def test_mark_complete_is_idempotent(org, user, fake_info):
    _grant_org_update(user, org)
    m = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        first = m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput())
        assert first.ok is True
        first_ts = first.data.organization.onboarding_completed_at

        # Second call: should not move the timestamp, should report
        # already_completed=True. The FE relies on this so a manual
        # re-run can race the auto-open path without breaking.
        second = m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput(skip=True))
        assert second.ok is True
        assert second.data.already_completed is True
        assert second.data.organization.onboarding_completed_at == first_ts


def test_skip_flag_does_not_affect_timestamp(org, user, fake_info):
    """``skip=True`` is an audit-log signal only; the persisted
    state is the same as a completed wizard. The FE chooses based on
    the audit entry, not the field value."""
    _grant_org_update(user, org)
    m = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput(skip=True))
    assert result.ok is True
    assert result.data.organization.onboarding_completed_at is not None


def test_organization_query_exposes_onboarding_completed_at(org, user, fake_info):
    """The new field must surface on `astroliftOrganization(slug)` so
    the dashboard can decide whether to auto-open the wizard."""
    _grant_org_update(user, org)
    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        # Pre-mutation
        before = q.astrolift_organization(fake_info, slug=org.slug)
        assert before is not None
        assert before.onboarding_completed_at is None

        # Mutate
        m = IdentityMutation()
        m.mark_onboarding_complete(fake_info, MarkOnboardingCompleteInput())

        # Post-mutation
        after = q.astrolift_organization(fake_info, slug=org.slug)
        assert after is not None
        assert after.onboarding_completed_at is not None
