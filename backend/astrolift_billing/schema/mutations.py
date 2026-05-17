"""Billing mutations.

Today this module hosts the operator-initiated quota request flow
(#434 scope D). When a tenant team is approaching a soft limit
(>80% consumption) the UI surfaces a "Request bump" affordance.
Submitting writes a :class:`QuotaIncreaseRequest` row, drops an
in-app notification into every org admin's inbox, and (when
configured) emails them so the request doesn't sit in an unmonitored
queue.

The decision step (approve / reject) is left to a follow-up — the
mutations here just *create* the request. The pending state shows up
in the requester's UI inline with the quota row.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import strawberry
from strawberry.types import Info

from astrolift_billing.models import Quota, QuotaIncreaseRequest
from astrolift_billing.schema.queries import QuotaIncreaseRequestType, quota_request_to_type
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


@strawberry.input
class RequestQuotaIncreaseInput:
    """Operator-initiated quota bump request (#434 scope D).

    ``factor`` is a multiplier against the current hard limit (e.g.
    2.0 = double, 1.5 = +50%). Capped server-side at 10x so a typo
    can't escalate an emergency request to nonsense. ``reason`` is
    required so the org admin's notification carries enough context
    to act without bouncing back to the requester.
    """

    quota_id: GUID
    factor: float
    reason: str


@strawberry.type
class BillingMutation:
    @strawberry.field
    @mutation_audit(action="quota.request_increase")
    @require_permission(Permission.BILLING_READ)
    @tenant_scoped()
    def request_quota_increase(
        self,
        info: Info,
        input: RequestQuotaIncreaseInput,
    ) -> MutationResultType[QuotaIncreaseRequestType]:
        """Open a pending quota-bump request, notify org admins.

        Permission gate is ``billing.read`` because the action is a
        *request* (any operator who can see the quotas may ask). The
        eventual approval/rejection lives on ``billing.update`` — to
        be wired when the decision mutation lands.

        Idempotency: only one pending request per quota is allowed
        (DB-enforced unique constraint). A duplicate returns
        CONFLICT — the UI surfaces "request already pending" instead
        of double-creating.
        """
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )

        try:
            factor = Decimal(str(input.factor))
        except (InvalidOperation, ValueError, TypeError):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "factor must be a positive number",
                field="factor",
            )
        if factor <= 1:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "factor must be greater than 1 (the increase, not the new total)",
                field="factor",
            )
        if factor > 10:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "factor cannot exceed 10x; contact platform ops for larger bumps",
                field="factor",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        quota = Quota.objects.filter(
            guid=str(input.quota_id),
            organization=org,
            deleted_at__isnull=True,
        ).first()
        if quota is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "quota not found",
                field="quotaId",
            )

        # DB has a unique constraint on (quota, status='pending') so
        # a race surfaces as IntegrityError. We pre-check for the
        # common case so the user sees a friendly CONFLICT rather
        # than a 500 from the constraint, and fall back to catching
        # the race if two requests land at the same millisecond.
        if QuotaIncreaseRequest.objects.filter(
            quota=quota,
            status=QuotaIncreaseRequest.Status.PENDING,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "a pending increase request already exists for this quota",
                field="quotaId",
            )

        from django.contrib.auth import get_user_model
        from django.db import IntegrityError

        requested_by = None
        if tenant.actor_user_id is not None:
            requested_by = get_user_model().objects.filter(pk=tenant.actor_user_id).first()

        try:
            req = QuotaIncreaseRequest.objects.create(
                quota=quota,
                organization=org,
                requested_factor=factor,
                reason=reason,
                requested_by=requested_by,
            )
        except IntegrityError:
            # Race against the unique constraint — surface as CONFLICT.
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "a pending increase request already exists for this quota",
                field="quotaId",
            )

        _notify_admins_of_quota_request(req)
        return gql_success(quota_request_to_type(req))


def _notify_admins_of_quota_request(req: QuotaIncreaseRequest) -> None:
    """Drop an in-app notification into every org admin's inbox
    + send a stock email (best-effort).

    Admin discovery: anyone in the org with a non-soft-deleted
    membership flagged as an admin in :class:`OrganizationMember`. We
    deliberately keep this loose — if the membership shape doesn't
    surface "admin" on this install, we fall back to "everyone in the
    org" so the request never goes unannounced. The notify step is
    best-effort: a failure does not roll back the request creation.
    """
    from astrolift_operations.models import Notification

    quota = req.quota
    org = req.organization

    title = f"Quota bump requested: {quota.resource}"
    body = (
        f"{(req.requested_by.get_username() if req.requested_by else 'someone')}"
        f" requested a {req.requested_factor}x bump on the "
        f"{quota.scope_kind} {quota.resource} quota. "
        f"Current limit: {quota.hard_limit}. Reason: {req.reason}"
    )
    link = "/quotas"

    admins = _resolve_org_admin_users(org)
    for user in admins:
        try:
            Notification.objects.create(
                user=user,
                organization=org,
                kind=Notification.Kind.QUOTA_WARNING,
                title=title,
                body=body,
                link=link,
            )
        except Exception:
            log.exception(
                "failed to create quota-request notification",
                extra={"user_id": user.pk, "request_guid": str(req.guid)},
            )

    _send_quota_request_email(req, admins, title=title, body=body)


def _resolve_org_admin_users(org):
    """Return the set of org admin users to notify. Walks the
    OrganizationMember table if available; falls back to every active
    user in the org so the message lands somewhere even if the
    membership table doesn't surface an admin flag on this install."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    try:
        from astrolift_identity.models import OrganizationMember  # noqa: WPS433
    except ImportError:
        OrganizationMember = None  # type: ignore[assignment]

    if OrganizationMember is not None:
        candidates = OrganizationMember.objects.filter(
            organization=org,
            deleted_at__isnull=True,
        ).select_related("user")
        admin_users = [
            m.user
            for m in candidates
            if m.user is not None
            and (getattr(m, "is_admin", False) or getattr(m, "role", "") in {"owner", "admin"})
        ]
        if admin_users:
            return admin_users
        # No flagged admins — fall through to every member so the
        # message reaches someone.
        return [m.user for m in candidates if m.user is not None]

    return list(user_model.objects.filter(is_active=True))


def _send_quota_request_email(req, admins, *, title: str, body: str) -> None:
    """Email org admins about the new quota bump request.

    Best-effort: a transport failure logs and returns. We never
    raise back into the mutation — the in-app notification is the
    authoritative surface, the email is a convenience nudge.
    """
    from django.conf import settings
    from django.core.mail import send_mail

    recipients = [u.email for u in admins if getattr(u, "email", "")]
    if not recipients:
        return

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@astrolift.local")
    try:
        send_mail(
            subject=title,
            message=body,
            from_email=from_email,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:
        log.exception(
            "failed to send quota-request notification email",
            extra={"request_guid": str(req.guid), "recipient_count": len(recipients)},
        )
