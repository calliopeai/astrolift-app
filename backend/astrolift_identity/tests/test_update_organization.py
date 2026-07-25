"""Tests for ``update_organization`` audit-retention handling (#433).

The org-level ``audit_log_retention_days`` column is written from two
surfaces — /administration/organization and /administration/audit — so
the mutation guards the value server-side rather than trusting the
client-side min/max on either form.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_identity.schema.mutations import (
    IdentityMutation,
    UpdateOrganizationInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-update-org")


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_update_org_audit_retention_happy_path(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(id=str(org.guid), audit_log_retention_days=120),
        )
    assert result.ok is True
    assert result.data is not None
    assert result.data.audit_log_retention_days == 120
    org.refresh_from_db()
    assert org.audit_log_retention_days == 120


@pytest.mark.parametrize("bad_days", [0, -1, 2558, 100_000])
def test_update_org_audit_retention_rejects_out_of_range(org, permission_resolver, bad_days):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _ctx(org):
        result = IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(id=str(org.guid), audit_log_retention_days=bad_days),
        )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "auditLogRetentionDays"
    # A rejected write must not touch the row (still the 365-day default).
    org.refresh_from_db()
    assert org.audit_log_retention_days == 365


def test_update_org_audit_retention_accepts_bounds(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    for good in (1, 2557):
        with _ctx(org):
            result = IdentityMutation().update_organization(
                _info(),
                input=UpdateOrganizationInput(id=str(org.guid), audit_log_retention_days=good),
            )
        assert result.ok is True, good
        org.refresh_from_db()
        assert org.audit_log_retention_days == good


def test_update_org_retention_requires_org_update_permission(org, permission_resolver):
    # Resolver installed but ORG_UPDATE ungranted → the resolver-top
    # @require_permission gate denies; @mutation_audit translates the
    # PermissionDenied into the failure envelope. Audit config is
    # admin-only. The row is never touched.
    with _ctx(org):
        result = IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(id=str(org.guid), audit_log_retention_days=120),
        )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    org.refresh_from_db()
    assert org.audit_log_retention_days == 365
