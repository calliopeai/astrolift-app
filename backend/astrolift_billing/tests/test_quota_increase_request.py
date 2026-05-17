"""Tests for QuotaIncreaseRequest mutation + pending-request surfacing (#434 scope D)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from astrolift_billing.models import Quota, QuotaIncreaseRequest
from astrolift_billing.schema.mutations import (
    BillingMutation,
    RequestQuotaIncreaseInput,
)
from astrolift_billing.schema.queries import BillingQuery
from astrolift_identity.models import Organization
from astrolift_operations.models import Notification
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org, *, actor_user_id=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    quota = Quota.objects.create(
        organization=org,
        scope_kind=Quota.ScopeKind.ORG,
        scope_id=org.id,
        resource=Quota.Resource.APPS,
        hard_limit=Decimal("10"),
        soft_limit=Decimal("8"),
        current_usage=Decimal("7"),
    )
    return org, quota


# ---- happy path -------------------------------------------------


def test_request_quota_increase_creates_pending_row(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    user_model = get_user_model()
    user = user_model.objects.create(
        username="requester_434",
        email="requester-434@example.com",
    )

    with _ctx(org, actor_user_id=user.pk):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=2.0,
                reason="prod team onboarding next quarter",
            ),
        )
    assert result.ok, result.errors
    requests = QuotaIncreaseRequest.objects.filter(quota=quota)
    assert requests.count() == 1
    row = requests.get()
    assert row.status == QuotaIncreaseRequest.Status.PENDING
    assert row.requested_factor == Decimal("2.00")
    assert row.requested_by == user
    assert row.reason == "prod team onboarding next quarter"


def test_pending_request_surfaces_on_quota_type(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()

    QuotaIncreaseRequest.objects.create(
        quota=quota,
        organization=org,
        requested_factor=Decimal("1.5"),
        reason="x",
    )

    with _ctx(org):
        quotas = BillingQuery().astrolift_quotas(_info())
    assert len(quotas) == 1
    assert quotas[0].pending_request is not None
    assert quotas[0].pending_request.status == "pending"
    assert quotas[0].pending_request.requested_factor == 1.5


def test_request_quota_increase_notifies_in_app(permission_resolver):
    """A pending request drops an in-app notification."""
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    user_model = get_user_model()
    admin = user_model.objects.create(
        username="quota_admin_434",
        email="quota-admin-434@example.com",
        is_active=True,
    )

    with _ctx(org, actor_user_id=admin.pk):
        BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=2.0,
                reason="x",
            ),
        )
    notifs = Notification.objects.filter(organization=org, user=admin)
    assert notifs.exists()
    notif = notifs.first()
    assert notif.kind == Notification.Kind.QUOTA_WARNING
    assert "Quota bump requested" in notif.title


def test_request_quota_increase_sends_email(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    user_model = get_user_model()
    user_model.objects.create(
        username="ops_434",
        email="ops-434@example.com",
        is_active=True,
    )

    with _ctx(org):
        BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=2.0,
                reason="x",
            ),
        )
    # Django test backend collects outbound mail in ``mail.outbox``.
    assert any("Quota bump requested" in m.subject for m in mail.outbox)


# ---- validation ------------------------------------------------


def test_request_requires_reason(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=2.0,
                reason="   ",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "reason"


def test_request_rejects_factor_at_or_below_one(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=1.0,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "factor"


def test_request_rejects_factor_above_ten(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=11.0,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "factor"


def test_request_unknown_quota_returns_not_found(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org = Organization.objects.create(name="Acme", slug="acme")
    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id="00000000-0000-0000-0000-000000000000",
                factor=2.0,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_request_duplicate_pending_returns_conflict(permission_resolver):
    permission_resolver.grant(Permission.BILLING_READ)
    org, quota = _scaffold()
    QuotaIncreaseRequest.objects.create(
        quota=quota,
        organization=org,
        requested_factor=Decimal("2.0"),
        reason="first",
    )

    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=3.0,
                reason="second",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


def test_request_requires_permission():
    org, quota = _scaffold()
    with _ctx(org):
        result = BillingMutation().request_quota_increase(
            _info(),
            input=RequestQuotaIncreaseInput(
                quota_id=str(quota.guid),
                factor=2.0,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
