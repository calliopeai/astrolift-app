"""
Cost observability policy (#30, spec 08 §14).

Pure-Python module. The actual provider billing-API queries live
in driver land (one per cloud); this module owns:

* The **CostSnapshot shape** — what the daily collection job
  writes per (date, scope, category).
* **Budget evaluation** — soft + hard thresholds, alert
  decisions per the alert pipeline (#159).
* **Aggregation helpers** — top-N by cost, monthly trend,
  per-category rollup. The UI cost panels read these.
* **Tenant scoping invariants** — every cost query carries an
  org_id so results can't leak across tenants.

The CostSnapshot model + data-collection workflow itself are
plumbing on top of this; the policy decisions all live here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum


class CostCategory(StrEnum):
    """Lock-down vocabulary so UI panels can rely on the strings."""

    COMPUTE = "compute"
    STORAGE = "storage"
    EGRESS = "egress"
    MANAGED_SERVICES = "managed_services"
    NETWORKING = "networking"
    OTHER = "other"


@dataclasses.dataclass(frozen=True, slots=True)
class CostSnapshot:
    """One daily row in the cost ledger.

    Scope is structured as (org, team, project, app, binding) —
    empty levels mean 'aggregate at this level'. Currency is fixed
    USD because every cloud bills in USD; conversion to operator
    contract currency is a billing concern.

    Per-binding attribution (``managed_service_binding_id``)
    requires the corresponding cloud resources to be tagged with
    the binding's id at provision time — every plugin should
    propagate ``ProvisionSpec.tags`` (#11) to its cloud objects,
    then the cloud-side cost-collection driver queries the
    provider's billing API by tag.
    """

    snapshot_date: date
    org_id: int
    cost_usd: float
    category: CostCategory
    team_id: int | None = None
    project_id: int | None = None
    app_id: int | None = None
    managed_service_binding_id: int | None = None
    """When set, cost is attributed to a specific managed-service
    binding (e.g. 'main_db' on the api app). Powers the
    per-binding cost panel — cloud-driver cost queries use the
    binding id as a tag."""

    provider: str = ""
    """e.g. 'aws-rds', 'aws-eks'. For the per-driver
    breakdown panel."""

    def __post_init__(self) -> None:
        if self.cost_usd < 0:
            raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
        if self.org_id <= 0:
            raise ValueError("org_id must be positive (per-tenant scope)")


# ---- budget --------------------------------------------------------


class BudgetSeverity(StrEnum):
    OK = "ok"
    SOFT_WARNING = "soft_warning"
    HARD_BREACH = "hard_breach"


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Per-(org, scope) budget config.

    soft_threshold + hard_threshold are absolute USD amounts for
    the period (typically monthly). Soft fires a warning to the
    operator; hard fires a critical alert and may trigger
    auto-remediation in the alert pipeline (#159) — e.g. block
    new app provisioning until acknowledged.
    """

    period_budget_usd: float
    soft_threshold_pct: float = 0.8
    """Fire at 80% of budget by default."""

    hard_threshold_pct: float = 1.0
    """Fire at 100% of budget by default."""

    def __post_init__(self) -> None:
        if self.period_budget_usd <= 0:
            raise ValueError("period_budget_usd must be positive")
        if not 0 < self.soft_threshold_pct <= 1.0:
            raise ValueError("soft_threshold_pct must be in (0, 1]")
        if not 0 < self.hard_threshold_pct:
            raise ValueError("hard_threshold_pct must be positive")
        if self.soft_threshold_pct >= self.hard_threshold_pct:
            raise ValueError("soft_threshold_pct must be < hard_threshold_pct")


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    severity: BudgetSeverity
    spent_usd: float
    budget_usd: float
    percent_spent: float
    reason: str


def evaluate_budget(
    *,
    spent_usd: float,
    policy: BudgetPolicy,
) -> BudgetEvaluation:
    """Bucket current spend into OK / SOFT_WARNING / HARD_BREACH."""
    if spent_usd < 0:
        raise ValueError("spent_usd must be non-negative")
    pct = spent_usd / policy.period_budget_usd

    if pct >= policy.hard_threshold_pct:
        severity = BudgetSeverity.HARD_BREACH
        reason = f"spent ${spent_usd:.2f} of ${policy.period_budget_usd:.2f} ({pct:.1%}) — hard breach"
    elif pct >= policy.soft_threshold_pct:
        severity = BudgetSeverity.SOFT_WARNING
        reason = f"spent ${spent_usd:.2f} of ${policy.period_budget_usd:.2f} ({pct:.1%}) — soft warning"
    else:
        severity = BudgetSeverity.OK
        reason = "within budget"

    return BudgetEvaluation(
        severity=severity,
        spent_usd=spent_usd,
        budget_usd=policy.period_budget_usd,
        percent_spent=pct,
        reason=reason,
    )


# ---- aggregations --------------------------------------------------


def total_for_org(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
) -> float:
    """Sum cost across snapshots for one org. Tenant-scoped: any
    cross-org snapshot is a programming bug — caller's query
    should have filtered already, but we double-check here."""
    return sum(s.cost_usd for s in snapshots if s.org_id == org_id)


def top_apps_by_cost(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
    n: int = 10,
) -> tuple[tuple[int, float], ...]:
    """Top N apps by total cost, descending. Returns (app_id, total)
    pairs. Snapshots without an app_id are excluded (org-level
    aggregates aren't 'apps')."""
    if n <= 0:
        raise ValueError("n must be positive")
    by_app: dict[int, float] = {}
    for s in snapshots:
        if s.org_id != org_id or s.app_id is None:
            continue
        by_app[s.app_id] = by_app.get(s.app_id, 0.0) + s.cost_usd
    sorted_apps = sorted(by_app.items(), key=lambda kv: kv[1], reverse=True)
    return tuple(sorted_apps[:n])


def by_category(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
) -> Mapping[CostCategory, float]:
    """Per-category rollup for the cost-breakdown panel."""
    out: dict[CostCategory, float] = dict.fromkeys(CostCategory, 0.0)
    for s in snapshots:
        if s.org_id != org_id:
            continue
        out[s.category] += s.cost_usd
    return out


def top_bindings_by_cost(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
    app_id: int | None = None,
    n: int = 10,
) -> tuple[tuple[int, float], ...]:
    """Top-N managed-service bindings by total cost. Optional
    ``app_id`` filter scopes to one app's bindings (the
    per-app drill-down panel)."""
    if n <= 0:
        raise ValueError("n must be positive")
    by_binding: dict[int, float] = {}
    for s in snapshots:
        if s.org_id != org_id:
            continue
        if s.managed_service_binding_id is None:
            continue
        if app_id is not None and s.app_id != app_id:
            continue
        by_binding[s.managed_service_binding_id] = (
            by_binding.get(s.managed_service_binding_id, 0.0) + s.cost_usd
        )
    sorted_bindings = sorted(by_binding.items(), key=lambda kv: kv[1], reverse=True)
    return tuple(sorted_bindings[:n])


def by_provider(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
) -> Mapping[str, float]:
    """Per-provider rollup. Empty provider strings rolled up under
    'unknown' so the UI can show what's missing."""
    out: dict[str, float] = {}
    for s in snapshots:
        if s.org_id != org_id:
            continue
        key = s.provider or "unknown"
        out[key] = out.get(key, 0.0) + s.cost_usd
    return out


def monthly_trend(
    snapshots: Sequence[CostSnapshot],
    *,
    org_id: int,
) -> tuple[tuple[str, float], ...]:
    """Per-month totals as (yyyy-mm, total_usd) sorted ascending.
    Used by the trend chart on the cost dashboard."""
    by_month: dict[str, float] = {}
    for s in snapshots:
        if s.org_id != org_id:
            continue
        key = s.snapshot_date.strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + s.cost_usd
    return tuple(sorted(by_month.items()))
