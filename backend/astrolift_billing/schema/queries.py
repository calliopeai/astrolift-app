"""Read-only queries for the billing app."""

from __future__ import annotations

import datetime as dt

import strawberry
from strawberry.types import Info

from astrolift_billing.models import Budget, CostSnapshot, Quota
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type(name="AstroliftQuota")
class QuotaType:
    id: GUID
    scope_kind: str
    scope_id: str
    resource: str
    hard_limit: float
    soft_limit: float
    current_usage: float


@strawberry.type(name="AstroliftBudget")
class BudgetType:
    id: GUID
    scope_kind: str
    scope_id: str
    amount_cents: int
    currency: str
    period: str
    current_spend_cents: int
    alerts_at_pct: list[int]


@strawberry.type(name="AstroliftCostSnapshot")
class CostSnapshotType:
    id: GUID
    project_id: str | None
    registered_app_id: str | None
    taken_at: dt.date
    by: str
    amount_cents: int
    currency: str
    source: str


def quota_to_type(q) -> QuotaType:
    return QuotaType(
        id=GUID(str(q.guid)),
        scope_kind=q.scope_kind,
        scope_id=str(q.scope_id),
        resource=q.resource,
        hard_limit=float(q.hard_limit),
        soft_limit=float(q.soft_limit),
        current_usage=float(q.current_usage),
    )


def budget_to_type(b) -> BudgetType:
    return BudgetType(
        id=GUID(str(b.guid)),
        scope_kind=b.scope_kind,
        scope_id=str(b.scope_id),
        amount_cents=b.amount_cents,
        currency=b.currency,
        period=b.period,
        current_spend_cents=b.current_spend_cents,
        alerts_at_pct=list(b.alerts_at_pct or []),
    )


def cost_to_type(c) -> CostSnapshotType:
    return CostSnapshotType(
        id=GUID(str(c.guid)),
        project_id=str(c.project_id) if c.project_id else None,
        registered_app_id=str(c.registered_app_id) if c.registered_app_id else None,
        taken_at=c.taken_at,
        by=c.by,
        amount_cents=c.amount_cents,
        currency=c.currency,
        source=c.source,
    )


@strawberry.type
class BillingQuery:
    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_quotas(self, info: Info) -> list[QuotaType]:
        qs = Quota.objects.order_by("scope_kind", "resource")[:200]
        return [quota_to_type(q) for q in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_budgets(self, info: Info) -> list[BudgetType]:
        qs = Budget.objects.order_by("-current_spend_cents")[:100]
        return [budget_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_cost_snapshots(
        self, info: Info, days: int = 30, limit: int = 200
    ) -> list[CostSnapshotType]:
        from datetime import timedelta

        from django.utils import timezone

        cutoff = (timezone.now() - timedelta(days=max(1, min(days, 365)))).date()
        qs = CostSnapshot.objects.filter(taken_at__gte=cutoff).order_by("-taken_at")[
            : max(1, min(limit, 1000))
        ]
        return [cost_to_type(c) for c in qs]
