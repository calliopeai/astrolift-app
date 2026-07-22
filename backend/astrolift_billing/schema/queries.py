"""Read-only queries for the billing app.

Cost panel surface (#432):

* ``astroliftCostSnapshots`` — flat list, accepts ``window`` enum
  (24H / 7D / 30D / MTD) plus the legacy ``days`` int for
  back-compat with the original 30-day panel.
* ``astroliftCostTrend`` — per-day rollup over the window, with a
  ``isAnomaly`` flag set on days whose day-over-day delta exceeds
  2 standard deviations. The chart paints those points distinctly.
* ``astroliftCostForecast`` — projected month-end spend (MTD-burn
  extrapolated by linear regression on the trend) + MTD-vs-prior-
  month delta. Surfaced as a KPI card.
* ``astroliftCostByBinding`` — per-binding rollup over the window
  for the per-resource attribution drill-down.

All cost numbers originate from ``CostSnapshot`` rows; the daily
collector in ``astrolift_workflows.activities.scheduled`` populates
those rows from each provider's live pricing/billing API
(``providers/<cloud>/cost.py``). The resolvers
never invent or hard-code prices — empty trend / forecast means
"no snapshots in the window" and the UI degrades to an empty state.
"""

from __future__ import annotations

import datetime as dt
import enum
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import strawberry
from django.db.models import Sum
from django.utils import timezone
from strawberry.types import Info

from astrolift_billing.models import Budget, CostSnapshot, Quota, QuotaIncreaseRequest
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission

# ---- enums + scalars ------------------------------------------------


@strawberry.enum
class CostWindow(enum.Enum):
    """Selectable rollup window for the cost panel.

    ``MTD`` is calendar-aware (start of the current month in the
    server's tz) so the resolver doesn't ask the UI to compute the
    cutoff. The numeric windows are exact day counts ending at the
    current day inclusive.
    """

    H24 = "H24"
    D7 = "D7"
    D30 = "D30"
    MTD = "MTD"


# ---- response types -------------------------------------------------


@strawberry.type(name="AstroliftQuotaIncreaseRequest")
class QuotaIncreaseRequestType:
    """An operator-initiated quota bump (#434 scope D).

    ``status`` is one of ``pending`` | ``approved`` | ``rejected``.
    The frontend surfaces a pending request inline with its quota row
    so the requester can tell "we already asked, awaiting decision"
    without scanning a separate queue.
    """

    id: GUID
    quota_id: GUID
    requested_factor: float
    reason: str
    status: str
    requested_by_display: str
    decided_by_display: str | None
    decided_at: dt.datetime | None
    decision_note: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftQuota")
class QuotaType:
    id: GUID
    scope_kind: str
    scope_id: str
    resource: str
    hard_limit: float
    soft_limit: float
    current_usage: float
    pending_request: QuotaIncreaseRequestType | None
    """The active pending :class:`AstroliftQuotaIncreaseRequest` for
    this quota, or ``null`` when none is in flight. Surfaces inline in
    the quotas table so the requester sees "request submitted, awaiting
    approval" without having to scan a separate queue."""


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
    managed_service_binding_id: str | None
    taken_at: dt.date
    by: str
    amount_cents: int
    currency: str
    source: str


@strawberry.type(name="AstroliftCostTrendPoint")
class CostTrendPointType:
    """One day in the trend chart.

    ``is_anomaly`` is true when the day-over-day delta exceeded 2
    standard deviations of the trailing delta series — the chart
    paints a marker at these points.
    """

    date: dt.date
    amount_cents: int
    currency: str
    is_anomaly: bool


@strawberry.enum
class ForecastConfidence(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@strawberry.type(name="AstroliftCostForecast")
class CostForecastType:
    """Month-end projection + MTD-vs-prior-month delta.

    The projection is a linear regression on the last 14 days of
    daily totals extrapolated to month-end. ``confidence`` is the
    resolver's own self-grade so the UI can soften the headline
    when there isn't enough signal yet.
    """

    mtd_cents: int
    previous_month_cents: int
    delta_pct: float
    """MTD-vs-previous-month percent change. Positive = up;
    ``0`` when previous-month spend is 0 (no signal)."""

    projected_monthly_cents: int
    """Forecast for the calendar month containing today, based on
    YTD burn rate + 14-day linear regression. ``0`` when no
    snapshot points exist in the window."""

    confidence: ForecastConfidence
    """``LOW`` until at least 7 daily points exist;
    ``MEDIUM`` 7-13 points; ``HIGH`` at 14+ points with low
    relative variance."""

    currency: str


@strawberry.type(name="AstroliftCostBindingRow")
class CostBindingRowType:
    """Per-binding cost row for the per-resource attribution panel.

    ``managed_service_binding_id`` is null when the cost row was
    written without binding attribution (driver hasn't tagged the
    cloud resource yet; see #432 from:backend gap on
    ``astrolift.io/binding`` not being applied by providers).
    """

    managed_service_binding_id: str | None
    managed_service_id: str | None
    managed_service_name: str | None
    managed_service_kind: str | None
    registered_app_slug: str | None
    by: str
    amount_cents: int
    currency: str


@strawberry.type(name="AstroliftCostAttribution")
class CostAttributionType:
    """Wrapper for the per-binding view. ``attributed_rows`` carry
    a non-null binding id; ``unattributed_cents`` is the spend
    that couldn't be tagged to a binding (e.g. shared egress, or
    providers that haven't adopted binding tagging yet)."""

    attributed_rows: list[CostBindingRowType]
    unattributed_cents: int
    total_cents: int
    currency: str


# ---- shapers --------------------------------------------------------


def quota_to_type(q) -> QuotaType:
    pending = (
        QuotaIncreaseRequest.objects.filter(
            quota=q,
            status=QuotaIncreaseRequest.Status.PENDING,
            deleted_at__isnull=True,
        )
        .select_related("requested_by", "decided_by")
        .order_by("-created_at")
        .first()
    )
    return QuotaType(
        id=GUID(str(q.guid)),
        scope_kind=q.scope_kind,
        scope_id=str(q.scope_id),
        resource=q.resource,
        hard_limit=float(q.hard_limit),
        soft_limit=float(q.soft_limit),
        current_usage=float(q.current_usage),
        pending_request=quota_request_to_type(pending) if pending is not None else None,
    )


def _user_display(user) -> str:
    """Best-effort display label for a quota-request user. Mirrors
    the shape in ``shape_activity_item`` so labels read the same
    across surfaces: full name → email → username → ``"system"``."""
    if user is None:
        return "system"
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = (first + " " + last).strip()
    if full:
        return full
    return (
        (getattr(user, "email", "") or "").strip()
        or (getattr(user, "username", "") or "").strip()
        or "system"
    )


def quota_request_to_type(r) -> QuotaIncreaseRequestType:
    return QuotaIncreaseRequestType(
        id=GUID(str(r.guid)),
        quota_id=GUID(str(r.quota.guid)),
        requested_factor=float(r.requested_factor),
        reason=r.reason or "",
        status=r.status,
        requested_by_display=_user_display(r.requested_by),
        decided_by_display=(_user_display(r.decided_by) if r.decided_by_id else None),
        decided_at=r.decided_at,
        decision_note=r.decision_note or "",
        created_at=r.created_at,
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
        managed_service_binding_id=(
            str(c.managed_service_binding_id) if c.managed_service_binding_id else None
        ),
        taken_at=c.taken_at,
        by=c.by,
        amount_cents=c.amount_cents,
        currency=c.currency,
        source=c.source,
    )


# ---- window resolution ----------------------------------------------


@dataclass(frozen=True)
class _ResolvedWindow:
    start: dt.date
    end: dt.date  # inclusive
    label: str


_DEFAULT_DAYS = 30
_MAX_DAYS = 365


def _resolve_window(
    *,
    window: CostWindow | None,
    days: int | None,
    today: dt.date,
) -> _ResolvedWindow:
    """Map (window enum, legacy days int) → concrete date range.

    Precedence: ``window`` wins when both are passed. ``H24`` is a
    1-day window ending today. ``MTD`` starts on the first day of
    the current calendar month.
    """
    if window is None:
        eff_days = days if days is not None else _DEFAULT_DAYS
        eff_days = max(1, min(eff_days, _MAX_DAYS))
        return _ResolvedWindow(
            start=today - dt.timedelta(days=eff_days - 1),
            end=today,
            label=f"{eff_days}d",
        )
    if window == CostWindow.H24:
        return _ResolvedWindow(start=today, end=today, label="24h")
    if window == CostWindow.D7:
        return _ResolvedWindow(start=today - dt.timedelta(days=6), end=today, label="7d")
    if window == CostWindow.D30:
        return _ResolvedWindow(start=today - dt.timedelta(days=29), end=today, label="30d")
    if window == CostWindow.MTD:
        return _ResolvedWindow(start=today.replace(day=1), end=today, label="mtd")
    # Defensive fallback — Strawberry enforces enum membership.
    return _ResolvedWindow(
        start=today - dt.timedelta(days=_DEFAULT_DAYS - 1),
        end=today,
        label=f"{_DEFAULT_DAYS}d",
    )


# ---- trend / anomaly ------------------------------------------------


def _daily_totals(
    snapshots: Sequence[CostSnapshot],
    *,
    start: dt.date,
    end: dt.date,
) -> list[tuple[dt.date, int]]:
    """Sum amount_cents per day, then forward-fill missing days with
    0 so the chart has a continuous x-axis."""
    by_day: dict[dt.date, int] = {}
    for s in snapshots:
        by_day[s.taken_at] = by_day.get(s.taken_at, 0) + int(s.amount_cents)
    out: list[tuple[dt.date, int]] = []
    cur = start
    while cur <= end:
        out.append((cur, by_day.get(cur, 0)))
        cur += dt.timedelta(days=1)
    return out


def _flag_anomalies(
    totals: Sequence[tuple[dt.date, int]],
) -> list[bool]:
    """A day is an anomaly when ``|delta_today - mean_delta| >
    2 * stddev_delta`` across the prior deltas in the window. We
    need at least 4 daily points to make the call (3 deltas + 1
    for stddev to be defined non-trivially)."""
    flags = [False] * len(totals)
    if len(totals) < 4:
        return flags
    deltas = [totals[i][1] - totals[i - 1][1] for i in range(1, len(totals))]
    try:
        mean_d = statistics.fmean(deltas)
        sd_d = statistics.pstdev(deltas)
    except statistics.StatisticsError:
        return flags
    if sd_d <= 0:
        return flags
    for i, d in enumerate(deltas, start=1):
        if abs(d - mean_d) > 2 * sd_d:
            flags[i] = True
    return flags


# ---- forecast -------------------------------------------------------


def _linear_regression_slope_intercept(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Least-squares slope + intercept. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return (0.0, ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return (0.0, mean_y)
    slope = num / den
    intercept = mean_y - slope * mean_x
    return (slope, intercept)


def _project_month_end(
    totals: Sequence[tuple[dt.date, int]],
    *,
    today: dt.date,
) -> tuple[int, ForecastConfidence]:
    """Linear-regress the last 14 daily totals; extrapolate to
    month-end; sum the MTD portion + extrapolated remainder.

    Returns (projected_cents, confidence)."""
    if not totals:
        return (0, ForecastConfidence.LOW)
    recent = list(totals[-14:])
    xs = [float(i) for i in range(len(recent))]
    ys = [float(t[1]) for t in recent]
    # No real signal yet — every total is zero (the daily collector
    # has been running but the cost driver hasn't reported anything).
    # Bail with LOW confidence so the UI doesn't pretend it has a
    # forecast.
    if not any(y > 0 for y in ys):
        return (0, ForecastConfidence.LOW)
    slope, intercept = _linear_regression_slope_intercept(xs, ys)

    # Build the month: MTD actual (sum of days in current month
    # we already have) + extrapolated remainder.
    month_start = today.replace(day=1)
    # last day of current month
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    month_end = next_month - dt.timedelta(days=1)

    mtd_actual = sum(c for d, c in totals if month_start <= d <= today)
    days_remaining = (month_end - today).days
    # Forecast each remaining day by extrapolating the regression
    # one step further. We start at the next x past the regression
    # window.
    next_x = len(recent)
    projected_remainder = 0.0
    for offset in range(1, days_remaining + 1):
        x = next_x + offset - 1
        y = max(0.0, slope * x + intercept)
        projected_remainder += y
    projected = mtd_actual + int(round(projected_remainder))

    # Confidence grading:
    n = len(recent)
    if n < 7:
        confidence = ForecastConfidence.LOW
    elif n < 14:
        confidence = ForecastConfidence.MEDIUM
    else:
        mean = statistics.fmean(ys) if ys else 0
        sd = statistics.pstdev(ys) if len(ys) > 1 else 0
        rel_var = (sd / mean) if mean > 0 else math.inf
        confidence = ForecastConfidence.HIGH if rel_var < 0.6 else ForecastConfidence.MEDIUM
    return (max(0, projected), confidence)


# ---- query root -----------------------------------------------------


@strawberry.type
class BillingQuery:
    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_quotas(self, info: Info) -> list[QuotaType]:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        qs = Quota.objects.filter(organization_id=tenant.organization_id).order_by("scope_kind", "resource")[
            :200
        ]
        return [quota_to_type(q) for q in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_budgets(self, info: Info) -> list[BudgetType]:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        qs = Budget.objects.filter(organization_id=tenant.organization_id).order_by("-current_spend_cents")[
            :100
        ]
        return [budget_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_cost_snapshots(
        self,
        info: Info,
        days: int | None = None,
        window: CostWindow | None = None,
        limit: int = 200,
    ) -> list[CostSnapshotType]:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        today = timezone.now().date()
        w = _resolve_window(window=window, days=days, today=today)
        qs = CostSnapshot.objects.filter(
            organization_id=tenant.organization_id,
            taken_at__gte=w.start,
            taken_at__lte=w.end,
        ).order_by("-taken_at")[: max(1, min(limit, 1000))]
        return [cost_to_type(c) for c in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_cost_trend(
        self,
        info: Info,
        window: CostWindow | None = None,
        days: int | None = None,
    ) -> list[CostTrendPointType]:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        today = timezone.now().date()
        w = _resolve_window(window=window, days=days, today=today)
        qs = list(
            CostSnapshot.objects.filter(
                organization_id=tenant.organization_id,
                taken_at__gte=w.start,
                taken_at__lte=w.end,
            )
        )
        currency = qs[0].currency if qs else "USD"
        totals = _daily_totals(qs, start=w.start, end=w.end)
        flags = _flag_anomalies(totals)
        return [
            CostTrendPointType(
                date=d,
                amount_cents=amt,
                currency=currency,
                is_anomaly=flag,
            )
            for (d, amt), flag in zip(totals, flags, strict=True)
        ]

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_cost_forecast(self, info: Info) -> CostForecastType:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        today = timezone.now().date()

        # Forecast pulls a 30-day window so the regression has
        # enough signal regardless of which UI tab is active.
        start = today - dt.timedelta(days=29)
        qs = list(
            CostSnapshot.objects.filter(
                organization_id=tenant.organization_id,
                taken_at__gte=start,
                taken_at__lte=today,
            )
        )
        currency = qs[0].currency if qs else "USD"
        totals = _daily_totals(qs, start=start, end=today)
        projected, confidence = _project_month_end(totals, today=today)

        month_start = today.replace(day=1)
        mtd_cents = sum(c for d, c in totals if month_start <= d <= today)

        # Previous month: full calendar month
        prev_month_end = month_start - dt.timedelta(days=1)
        prev_month_start = prev_month_end.replace(day=1)
        prev_qs = CostSnapshot.objects.filter(
            organization_id=tenant.organization_id,
            taken_at__gte=prev_month_start,
            taken_at__lte=prev_month_end,
        ).aggregate(total=Sum("amount_cents"))
        previous_month_cents = int(prev_qs["total"] or 0)

        if previous_month_cents > 0:
            delta_pct = (mtd_cents - previous_month_cents) / previous_month_cents * 100.0
        else:
            delta_pct = 0.0

        return CostForecastType(
            mtd_cents=mtd_cents,
            previous_month_cents=previous_month_cents,
            delta_pct=round(delta_pct, 2),
            projected_monthly_cents=projected,
            confidence=confidence,
            currency=currency,
        )

    @strawberry.field
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def astrolift_cost_by_binding(
        self,
        info: Info,
        window: CostWindow | None = None,
        days: int | None = None,
        registered_app_slug: str | None = None,
    ) -> CostAttributionType:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        today = timezone.now().date()
        w = _resolve_window(window=window, days=days, today=today)

        qs = CostSnapshot.objects.filter(
            organization_id=tenant.organization_id,
            taken_at__gte=w.start,
            taken_at__lte=w.end,
        ).select_related(
            "managed_service_binding__managed_service",
            "registered_app",
        )
        if registered_app_slug:
            qs = qs.filter(registered_app__slug=registered_app_slug)

        currency = "USD"
        attributed: dict[tuple[int, str], dict] = {}  # (binding_id, by) → aggregate row
        unattributed = 0
        total = 0
        for c in qs:
            currency = c.currency
            total += int(c.amount_cents)
            if c.managed_service_binding_id is None:
                unattributed += int(c.amount_cents)
                continue
            key = (c.managed_service_binding_id, c.by)
            row = attributed.setdefault(
                key,
                {
                    "binding": c.managed_service_binding,
                    "app": c.registered_app,
                    "by": c.by,
                    "amount_cents": 0,
                    "currency": c.currency,
                },
            )
            row["amount_cents"] += int(c.amount_cents)

        rows = sorted(
            attributed.values(),
            key=lambda r: r["amount_cents"],
            reverse=True,
        )

        def _row(r) -> CostBindingRowType:
            binding = r["binding"]
            svc = getattr(binding, "managed_service", None) if binding else None
            app = r["app"]
            return CostBindingRowType(
                managed_service_binding_id=(str(binding.guid) if binding else None),
                managed_service_id=(str(svc.guid) if svc else None),
                managed_service_name=(svc.name if svc else None),
                managed_service_kind=(svc.kind if svc else None),
                registered_app_slug=(app.slug if app else None),
                by=r["by"],
                amount_cents=r["amount_cents"],
                currency=r["currency"],
            )

        return CostAttributionType(
            attributed_rows=[_row(r) for r in rows],
            unattributed_cents=unattributed,
            total_cents=total,
            currency=currency,
        )
