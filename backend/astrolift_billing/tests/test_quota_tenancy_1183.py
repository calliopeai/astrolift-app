"""Cross-tenant isolation for the org-scoped quota + budget lists (#1183).

``astroliftQuotas`` and ``astroliftBudgets`` listed every row in the table
(capped by a slice) with no ``organization_id`` filter, so any
``BILLING_READ`` holder saw every org's limits and spend. These tests seed
two orgs' rows and prove the caller only ever sees its own.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_billing.models import Budget, Quota
from astrolift_billing.schema.queries import BillingQuery
from astrolift_identity.models import Organization
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


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def org_a():
    return Organization.objects.create(name="Billing Org A", slug="bill-org-a")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="Billing Org B", slug="bill-org-b")


def _quota(org, resource):
    return Quota.objects.create(
        organization=org,
        scope_kind=Quota.ScopeKind.ORG,
        scope_id=org.id,
        resource=resource,
        hard_limit=10,
        soft_limit=8,
    )


def _budget(org, amount_cents):
    return Budget.objects.create(
        organization=org,
        scope_kind=Budget.ScopeKind.ORG,
        scope_id=org.id,
        amount_cents=amount_cents,
    )


def test_quotas_scoped_to_caller_org(permission_resolver, org_a, org_b):
    q_a = _quota(org_a, Quota.Resource.APPS)
    _quota(org_b, Quota.Resource.CPU)
    permission_resolver.grant(Permission.BILLING_READ)

    with _tenant(org_a):
        out = BillingQuery().astrolift_quotas(_info())

    ids = {str(row.id) for row in out}
    assert ids == {str(q_a.guid)}


def test_budgets_scoped_to_caller_org(permission_resolver, org_a, org_b):
    b_a = _budget(org_a, 100_00)
    _budget(org_b, 999_999_00)
    permission_resolver.grant(Permission.BILLING_READ)

    with _tenant(org_a):
        out = BillingQuery().astrolift_budgets(_info())

    ids = {str(row.id) for row in out}
    assert ids == {str(b_a.guid)}
