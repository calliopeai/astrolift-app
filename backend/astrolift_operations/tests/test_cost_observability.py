"""Tests for cost observability policy (#30, spec 08 §14)."""

from __future__ import annotations

from datetime import date

import pytest

from astrolift_operations.cost_observability import (
    BudgetPolicy,
    BudgetSeverity,
    CostCategory,
    CostSnapshot,
    by_category,
    by_provider,
    evaluate_budget,
    monthly_trend,
    top_apps_by_cost,
    total_for_org,
)


def _snap(**kw) -> CostSnapshot:
    base = {
        "snapshot_date": date(2026, 5, 1),
        "org_id": 1,
        "cost_usd": 100.0,
        "category": CostCategory.COMPUTE,
        "team_id": None,
        "project_id": None,
        "app_id": None,
        "provider": "",
    }
    base.update(kw)
    return CostSnapshot(**base)


# ---- snapshot guards -----------------------------------------------


def test_snapshot_rejects_negative_cost():
    with pytest.raises(ValueError):
        CostSnapshot(
            snapshot_date=date(2026, 5, 1),
            org_id=1,
            cost_usd=-1.0,
            category=CostCategory.COMPUTE,
        )


def test_snapshot_requires_positive_org_id():
    with pytest.raises(ValueError, match="per-tenant scope"):
        CostSnapshot(
            snapshot_date=date(2026, 5, 1),
            org_id=0,
            cost_usd=10.0,
            category=CostCategory.COMPUTE,
        )


# ---- budget guards -------------------------------------------------


def test_budget_rejects_inverted_thresholds():
    """Soft >= hard would never trigger soft."""
    with pytest.raises(ValueError, match="soft_threshold_pct"):
        BudgetPolicy(period_budget_usd=1000, soft_threshold_pct=1.0, hard_threshold_pct=1.0)
    with pytest.raises(ValueError, match="soft_threshold_pct"):
        BudgetPolicy(period_budget_usd=1000, soft_threshold_pct=1.5, hard_threshold_pct=1.0)


def test_budget_rejects_zero_or_negative():
    with pytest.raises(ValueError):
        BudgetPolicy(period_budget_usd=0)
    with pytest.raises(ValueError):
        BudgetPolicy(period_budget_usd=1000, soft_threshold_pct=0)


# ---- evaluate_budget -----------------------------------------------


def _policy() -> BudgetPolicy:
    return BudgetPolicy(period_budget_usd=1000.0)


def test_below_soft_is_ok():
    out = evaluate_budget(spent_usd=500.0, policy=_policy())
    assert out.severity == BudgetSeverity.OK


def test_at_soft_threshold_is_warning():
    out = evaluate_budget(spent_usd=800.0, policy=_policy())
    assert out.severity == BudgetSeverity.SOFT_WARNING


def test_just_below_hard_is_still_warning():
    out = evaluate_budget(spent_usd=999.0, policy=_policy())
    assert out.severity == BudgetSeverity.SOFT_WARNING


def test_at_or_above_hard_is_breach():
    """100% triggers hard breach. Critical alert + potentially
    auto-remediation downstream in the alert pipeline."""
    assert evaluate_budget(spent_usd=1000.0, policy=_policy()).severity == BudgetSeverity.HARD_BREACH
    assert evaluate_budget(spent_usd=1500.0, policy=_policy()).severity == BudgetSeverity.HARD_BREACH


def test_evaluate_returns_percent_spent():
    out = evaluate_budget(spent_usd=400.0, policy=_policy())
    assert out.percent_spent == 0.4
    assert out.budget_usd == 1000.0


def test_evaluate_rejects_negative_spent():
    with pytest.raises(ValueError):
        evaluate_budget(spent_usd=-1.0, policy=_policy())


# ---- aggregations --------------------------------------------------


def test_total_for_org_filters_by_org():
    snapshots = [
        _snap(org_id=1, cost_usd=100),
        _snap(org_id=1, cost_usd=50),
        _snap(org_id=2, cost_usd=999),  # different org
    ]
    assert total_for_org(snapshots, org_id=1) == 150
    assert total_for_org(snapshots, org_id=2) == 999


def test_top_apps_excludes_org_level_snapshots():
    """Snapshots without an app_id are aggregates — they're not
    'apps' for the top-N panel."""
    snapshots = [
        _snap(app_id=10, cost_usd=200),
        _snap(app_id=11, cost_usd=100),
        _snap(app_id=None, cost_usd=999),  # org-level, excluded
    ]
    out = top_apps_by_cost(snapshots, org_id=1, n=10)
    app_ids = {a for a, _ in out}
    assert app_ids == {10, 11}


def test_top_apps_sorted_descending():
    snapshots = [
        _snap(app_id=10, cost_usd=50),
        _snap(app_id=11, cost_usd=200),
        _snap(app_id=12, cost_usd=100),
    ]
    out = top_apps_by_cost(snapshots, org_id=1, n=3)
    assert [a for a, _ in out] == [11, 12, 10]


def test_top_apps_sums_across_snapshots():
    """Multiple snapshots for the same app aggregate."""
    snapshots = [
        _snap(app_id=10, cost_usd=50),
        _snap(app_id=10, cost_usd=70),
        _snap(app_id=11, cost_usd=100),
    ]
    out = top_apps_by_cost(snapshots, org_id=1, n=2)
    assert out[0] == (10, 120.0)


def test_top_apps_n_caps_results():
    snapshots = [_snap(app_id=i, cost_usd=i) for i in range(1, 11)]
    out = top_apps_by_cost(snapshots, org_id=1, n=3)
    assert len(out) == 3


def test_top_apps_filters_by_org():
    snapshots = [
        _snap(org_id=1, app_id=10, cost_usd=100),
        _snap(org_id=2, app_id=20, cost_usd=999),
    ]
    out = top_apps_by_cost(snapshots, org_id=1, n=10)
    assert {a for a, _ in out} == {10}


def test_top_apps_n_must_be_positive():
    with pytest.raises(ValueError):
        top_apps_by_cost([], org_id=1, n=0)


def test_by_category_initializes_all_categories():
    """The breakdown panel always shows every category, with 0
    for ones that have no data."""
    out = by_category(
        [
            _snap(cost_usd=100, category=CostCategory.COMPUTE),
            _snap(cost_usd=50, category=CostCategory.STORAGE),
        ],
        org_id=1,
    )
    assert out[CostCategory.COMPUTE] == 100
    assert out[CostCategory.STORAGE] == 50
    # Every category present, even if 0
    for c in CostCategory:
        assert c in out


def test_by_provider_groups_unknown():
    """Snapshots without a provider string roll up under 'unknown'
    so the UI can show what's not classified."""
    out = by_provider(
        [
            _snap(provider="aws-rds", cost_usd=100),
            _snap(provider="", cost_usd=50),
            _snap(provider="aws-rds", cost_usd=20),
        ],
        org_id=1,
    )
    assert out["aws-rds"] == 120
    assert out["unknown"] == 50


def test_monthly_trend_groups_and_sorts():
    snapshots = [
        _snap(snapshot_date=date(2026, 5, 1), cost_usd=100),
        _snap(snapshot_date=date(2026, 5, 15), cost_usd=50),
        _snap(snapshot_date=date(2026, 4, 30), cost_usd=200),
        _snap(snapshot_date=date(2026, 6, 1), cost_usd=10),
    ]
    out = monthly_trend(snapshots, org_id=1)
    assert out == (
        ("2026-04", 200.0),
        ("2026-05", 150.0),
        ("2026-06", 10.0),
    )
