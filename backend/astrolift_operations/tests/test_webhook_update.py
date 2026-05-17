"""Tests for the updateWebhookSubscription mutation extensions (#426)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_operations.models import WebhookSubscription
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    UpdateWebhookSubscriptionInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _user(username: str = "operator"):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test")


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user("op-upd")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/webhook",
        secret_hash="abc",
        events=["deploy.completed"],
        is_active=True,
        format=WebhookSubscription.Format.GENERIC,
    )
    return org, user, sub


def _tenant(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def test_update_format_to_slack(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    with _tenant(org, user):
        result = OperationsMutation().update_webhook_subscription(
            _info(user),
            input=UpdateWebhookSubscriptionInput(id=str(sub.guid), format="slack"),
        )
    assert result.ok, result.errors
    sub.refresh_from_db()
    assert sub.format == "slack"


def test_update_format_rejects_unknown(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    with _tenant(org, user):
        result = OperationsMutation().update_webhook_subscription(
            _info(user),
            input=UpdateWebhookSubscriptionInput(id=str(sub.guid), format="msteams"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "format"


def test_disable_via_update_stamps_disabled_at(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    with _tenant(org, user):
        result = OperationsMutation().update_webhook_subscription(
            _info(user),
            input=UpdateWebhookSubscriptionInput(id=str(sub.guid), is_active=False),
        )
    assert result.ok
    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.disabled_at is not None
    assert sub.disabled_reason == "operator-disabled"


def test_reenable_via_update_clears_counters(permission_resolver):
    org, user, sub = _scaffold()
    sub.is_active = False
    sub.failure_count = 17
    sub.disabled_reason = "operator-disabled"
    sub.save(update_fields=["is_active", "failure_count", "disabled_reason"])
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with _tenant(org, user):
        result = OperationsMutation().update_webhook_subscription(
            _info(user),
            input=UpdateWebhookSubscriptionInput(id=str(sub.guid), is_active=True),
        )
    assert result.ok
    sub.refresh_from_db()
    assert sub.is_active is True
    assert sub.failure_count == 0
    assert sub.disabled_reason == ""
    assert sub.disabled_at is None
