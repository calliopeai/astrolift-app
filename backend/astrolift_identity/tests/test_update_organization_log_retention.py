"""``update_organization`` writes the observability log-retention override.

``Organization.log_retention_days_default`` was readable on the GraphQL
type but no mutation could set it, so the historical Logs surface always
resolved the platform default. The setter is bounded by the platform log
retention window so this and the surface that reads the column cannot
drift apart.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_identity.schema.mutations import (
    IdentityMutation,
    UpdateOrganizationInput,
)
from astrolift_operations.observability_profile import RETENTION_LOGS
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-log-retention")


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _update(org, days):
    with _ctx(org):
        return IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(id=str(org.guid), log_retention_days_default=days),
        )


def test_sets_the_override(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    result = _update(org, 90)
    assert result.ok is True, result.errors
    assert result.data.log_retention_days_default == 90
    org.refresh_from_db()
    assert org.log_retention_days_default == 90


def test_accepts_the_platform_window_bounds(org, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    for good in (1, RETENTION_LOGS.max_days):
        result = _update(org, good)
        assert result.ok is True, good
        org.refresh_from_db()
        assert org.log_retention_days_default == good


@pytest.mark.parametrize("offset", [0, 1])
def test_rejects_above_the_platform_window(org, permission_resolver, offset):
    permission_resolver.grant(Permission.ORG_UPDATE)
    result = _update(org, RETENTION_LOGS.max_days + 1 + offset)
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "logRetentionDaysDefault"
    assert str(RETENTION_LOGS.max_days) in result.errors[0].message
    org.refresh_from_db()
    assert org.log_retention_days_default == 30


@pytest.mark.parametrize("bad_days", [0, -1])
def test_rejects_non_positive(org, permission_resolver, bad_days):
    permission_resolver.grant(Permission.ORG_UPDATE)
    result = _update(org, bad_days)
    assert result.ok is False
    assert result.errors[0].field == "logRetentionDaysDefault"
    org.refresh_from_db()
    assert org.log_retention_days_default == 30


def test_requires_org_update_permission(org, permission_resolver):
    result = _update(org, 90)
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    org.refresh_from_db()
    assert org.log_retention_days_default == 30
