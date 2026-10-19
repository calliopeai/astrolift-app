"""Resource quota reads; legacy billing permission identifiers remain compatible."""

from __future__ import annotations

import datetime as dt

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_billing.models import Quota, QuotaIncreaseRequest, QuotaUsageSnapshot
from astrolift_billing.scopes import billing_org_scope
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


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


@strawberry.type(name="AstroliftQuotaUsagePoint")
class QuotaUsagePointType:
    """One point in a quota's usage history (#1182).

    ``used`` / ``limit`` are the values captured on ``date`` by the
    append-only snapshot collector. The quota detail view plots ``used``
    against ``limit`` as a sparkline so operators see pressure building
    well before it hits the cap. Empty history means "no snapshots in the
    window yet" and the UI degrades to an empty state."""

    date: dt.date
    used: float
    limit: float


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


_MAX_DAYS = 365


@strawberry.type
class BillingQuery:
    @strawberry.field
    @require_permission(Permission.BILLING_READ, scope=billing_org_scope)
    @tenant_scoped()
    def astrolift_quotas(self, info: Info) -> list[QuotaType]:
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        qs = Quota.objects.filter(organization_id=tenant.organization_id).order_by("scope_kind", "resource")[
            :200
        ]
        return [quota_to_type(q) for q in qs]

    @strawberry.field
    @require_permission(Permission.BILLING_READ, scope=billing_org_scope)
    @tenant_scoped()
    def astrolift_quota_usage_history(
        self,
        info: Info,
        quota_id: GUID,
        window_days: int = 90,
    ) -> list[QuotaUsagePointType]:
        """Point-in-time usage history for a single quota (#1182).

        Fail-closed: an out-of-scope ``quota_id`` (or an absent tenant)
        reads back as an empty history — indistinguishable from a quota
        that simply has no snapshots yet, so cross-org existence never
        leaks. The by-guid quota fetch carries the ``organization_id``
        clause the ``@tenant_scoped`` decorator does NOT add on its own.
        """
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return []

        quota = Quota.objects.filter(
            guid=str(quota_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if quota is None:
            return []

        days = max(1, min(window_days, _MAX_DAYS))
        today = timezone.now().date()
        start = today - dt.timedelta(days=days - 1)
        rows = QuotaUsageSnapshot.objects.filter(
            organization_id=org_id,
            quota=quota,
            captured_at__gte=start,
            captured_at__lte=today,
        ).order_by("captured_at")
        return [
            QuotaUsagePointType(date=r.captured_at, used=float(r.used), limit=float(r.limit)) for r in rows
        ]
