"""Tests for the webhook delivery history persistence + query (#426)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.delivery import (
    record_delivery_outcome,
    should_skip_delivery,
)
from astrolift_operations.models import WebhookDelivery, WebhookSubscription
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _user(username: str = "viewer"):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test")


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user("op-deliv")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/webhook",
        secret_hash="abc",
        events=["deploy.completed"],
        is_active=True,
    )
    return org, user, sub


def _tenant(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---- persistence ----------------------------------------------------


def test_record_delivery_persists_history_row_on_success():
    _, _, sub = _scaffold()
    out = record_delivery_outcome(
        sub,
        status_code=200,
        succeeded=True,
        event_type="deploy.completed",
        latency_ms=42,
        response_body_excerpt='{"ok":true}',
        delivery_id="d-1",
    )
    assert out.succeeded
    row = WebhookDelivery.objects.get(subscription=sub)
    assert row.event_type == "deploy.completed"
    assert row.status_code == 200
    assert row.latency_ms == 42
    assert row.success is True
    assert row.is_test is False
    assert row.delivery_id == "d-1"


def test_record_delivery_persists_history_row_on_failure():
    _, _, sub = _scaffold()
    record_delivery_outcome(
        sub,
        status_code=502,
        succeeded=False,
        event_type="deploy.completed",
        latency_ms=11000,
        error="upstream timed out",
    )
    row = WebhookDelivery.objects.get(subscription=sub)
    assert row.status_code == 502
    assert row.success is False
    assert "timed out" in row.error


def test_record_delivery_truncates_oversize_excerpts():
    _, _, sub = _scaffold()
    large = "x" * 50_000
    record_delivery_outcome(
        sub,
        status_code=200,
        succeeded=True,
        response_body_excerpt=large,
        request_payload_excerpt=large,
    )
    row = WebhookDelivery.objects.get(subscription=sub)
    assert len(row.response_body_excerpt) <= 8192
    assert len(row.request_payload_excerpt) <= 8192


# ---- disabled-skip --------------------------------------------------


def test_should_skip_delivery_when_inactive():
    _, _, sub = _scaffold()
    sub.is_active = False
    sub.save(update_fields=["is_active"])
    assert should_skip_delivery(sub) is True


def test_should_skip_delivery_when_active():
    _, _, sub = _scaffold()
    assert should_skip_delivery(sub) is False


def test_should_skip_delivery_when_soft_deleted():
    _, _, sub = _scaffold()
    sub.soft_delete()
    assert should_skip_delivery(sub) is True


# ---- query ---------------------------------------------------------


def test_deliveries_query_returns_most_recent_first(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    now = timezone.now()
    for i in range(5):
        WebhookDelivery.objects.create(
            subscription=sub,
            event_type=f"deploy.evt.{i}",
            retry_attempt=1,
            status_code=200,
            latency_ms=10 * i,
            success=True,
            delivered_at=now - dt.timedelta(seconds=i),
        )
    with _tenant(org, user):
        rows = OperationsQuery().astrolift_webhook_deliveries(
            _info(user), subscription_id=str(sub.guid), limit=3
        )
    assert [r.event_type for r in rows] == [
        "deploy.evt.0",
        "deploy.evt.1",
        "deploy.evt.2",
    ]


def test_deliveries_query_caps_limit(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    now = timezone.now()
    for i in range(150):
        WebhookDelivery.objects.create(
            subscription=sub,
            event_type="deploy.evt",
            retry_attempt=1,
            status_code=200,
            latency_ms=1,
            success=True,
            delivered_at=now - dt.timedelta(seconds=i),
        )
    with _tenant(org, user):
        rows = OperationsQuery().astrolift_webhook_deliveries(
            _info(user), subscription_id=str(sub.guid), limit=500
        )
    # Capped to 100.
    assert len(rows) == 100


def test_deliveries_query_cross_org_returns_empty(permission_resolver):
    """A query for a subscription owned by another org returns []
    rather than leaking row counts or 404 distinguishing them."""
    org_a, user_a, sub_a = _scaffold()
    org_b = Organization.objects.create(name="Other", slug="other")
    sub_b = WebhookSubscription.objects.create(
        organization=org_b,
        url="https://other.invalid/wh",
        secret_hash="abc",
        events=[],
        is_active=True,
    )
    WebhookDelivery.objects.create(
        subscription=sub_b,
        event_type="x",
        retry_attempt=1,
        status_code=200,
        latency_ms=1,
        success=True,
        delivered_at=timezone.now(),
    )
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    with _tenant(org_a, user_a):
        rows = OperationsQuery().astrolift_webhook_deliveries(
            _info(user_a), subscription_id=str(sub_b.guid), limit=10
        )
    assert rows == []


def test_deliveries_query_requires_permission(permission_resolver):
    """Without ``webhook.create``, the decorator raises PermissionDenied.
    Read queries don't wrap into the MutationResult envelope — the
    error propagates to the GraphQL execution layer the same way as
    every other gated read."""
    from core.permissions import PermissionDenied

    org, user, sub = _scaffold()
    # No grant.
    with _tenant(org, user):
        with pytest.raises(PermissionDenied):
            OperationsQuery().astrolift_webhook_deliveries(
                _info(user), subscription_id=str(sub.guid), limit=10
            )
