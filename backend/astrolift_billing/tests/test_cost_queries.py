"""Retired costing stays absent while historical rows and resource quotas survive."""

import pytest
from django.contrib import admin
from django.utils import timezone

from astrolift_billing.admin import BudgetAdmin, CostSnapshotAdmin
from astrolift_billing.models import Budget, CostSnapshot
from astrolift_billing.schema.queries import BillingQuery
from astrolift_identity.models import Organization
from astrolift_workflows.activities.scheduled import _capture_platform_cost_snapshot_sync
from config.schema import schema


def test_only_quota_reads_remain_and_money_contracts_are_absent():
    sdl = schema.as_str()
    for removed in (
        "astroliftBudgets",
        "astroliftCostSnapshots",
        "astroliftCostTrend",
        "astroliftCostForecast",
        "astroliftCostByBinding",
        "AstroliftBudget",
        "AstroliftCostSnapshot",
        "AstroliftCostForecast",
        "AstroliftCostTrendPoint",
        "AstroliftCostBindingRow",
        "AstroliftCostAttribution",
        "CostWindow",
        "ForecastConfidence",
        "astroliftManagedServiceCostPreview",
        "AstroliftManagedServiceCostPreview",
        "estimatedDailyCostUsd",
        "estimatedCostNotes",
        "estimatedCostApproximate",
        "costUsd",
        "includeRuntimeCost",
    ):
        assert removed not in sdl, removed
    for retained in (
        "astroliftQuotas",
        "astroliftQuotaUsageHistory",
        "requestQuotaIncrease",
        "includeRuntimeResources",
        "aggregateResources",
        "AstroliftZentinelleUsageRow",
    ):
        assert retained in sdl, retained
    assert hasattr(BillingQuery, "astrolift_quotas")
    assert not hasattr(BillingQuery, "astrolift_budgets")


@pytest.mark.django_db
def test_retired_collector_does_not_touch_historical_cost_or_budget_rows(monkeypatch):
    from aws.cost import AWSBillingActuals
    from azure.cost import AzureBillingActuals
    from gcp.cost import GCPBillingActuals

    def forbidden(*args, **kwargs):
        pytest.fail("retired collector invoked provider financial API")

    for adapter in (AWSBillingActuals, GCPBillingActuals, AzureBillingActuals):
        monkeypatch.setattr(adapter, "query_actuals_by_service", forbidden)
    org = Organization.objects.create(name="Historical", slug="historical")
    Budget.objects.create(
        organization=org,
        scope_kind="ORG",
        scope_id=org.pk,
        amount_cents=1234,
        current_spend_cents=567,
        alerts_at_pct=[90],
    )
    CostSnapshot.objects.create(
        organization=org, taken_at=timezone.localdate(), by="other", amount_cents=789, source="platform_meter"
    )
    before = (list(Budget.objects.values()), list(CostSnapshot.objects.values()))
    assert _capture_platform_cost_snapshot_sync() == 0
    assert before == (list(Budget.objects.values()), list(CostSnapshot.objects.values()))


@pytest.mark.parametrize("model,admin_type", [(Budget, BudgetAdmin), (CostSnapshot, CostSnapshotAdmin)])
def test_historical_monetary_admin_has_no_write_or_delete_actions(model, admin_type):
    model_admin = admin_type(model, admin.site)
    assert not model_admin.has_add_permission(None)
    assert not model_admin.has_change_permission(None)
    assert not model_admin.has_delete_permission(None)


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def test_seeded_resource_quota_role_retains_legacy_identity_and_permission_ceilings():
    from astrolift_identity.system_roles import SYSTEM_ROLES
    from core.permissions import Permission

    role = next(row for row in SYSTEM_ROLES if row[0] == "org_billing")
    assert role[1:3] == ("ORG", "Organization Resource Quotas")
    assert role[4] == (Permission.BILLING_READ, Permission.BILLING_UPDATE)
