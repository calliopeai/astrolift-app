"""Tests for the email leg of the notification fan-out (real Postgres).

Covers the SES notice path added alongside push dispatch: the
``EMAIL_NOTIFICATIONS`` master gate, the per-user ``email``-channel
preference, and recipient-address validation. The transport itself
(``send_notice_email`` -> django_ses) is mocked -- these assert the
dispatch *decision*, not SES delivery.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model

import core.events as _events_mod
from astrolift_identity.models import Member, Organization
from astrolift_operations.models import NotificationPreference
from astrolift_operations.notification_dispatch import dispatch_event
from core.events import Event as EventEmitter
from core.events import register_event_subscriber
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def subscribed():
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    yield
    _events_mod._subscribers[:] = snapshot


def _admin(org, email: str = "ops-admin@astrolift.dev"):
    # NB: not admin@ — the derived username would collide with the
    # migration-seeded ``admin`` user in the test database.
    user = User.objects.create(email=email, username=email.split("@")[0])
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _org():
    slug = f"org-{uuid.uuid4().hex[:6]}"
    return Organization.objects.create(name=slug.upper(), slug=slug)


def _emit_app_down(org, user):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "app.down",
            {
                "app_slug": "demo",
                "app_name": "Demo",
                "target_url": "https://demo.example/healthz",
                "status_code": 504,
            },
            resource_kind="registered_app",
            resource_id="app-1",
        )


def test_email_sent_when_enabled_and_opted_in(subscribed):
    org = _org()
    user = _admin(org, email="ops@astrolift.dev")
    with (
        mock.patch("astrolift_operations.notification_email.notifications_email_enabled", return_value=True),
        mock.patch("astrolift_operations.notification_email.send_notice_email", return_value=1) as send,
    ):
        _emit_app_down(org, user)

    assert send.call_count == 1
    kwargs = send.call_args.kwargs
    assert kwargs["to"] == ["ops@astrolift.dev"]
    assert kwargs["tag"] == "app.down"
    # the real probe target is surfaced as a clickable link in the body
    assert "https://demo.example/healthz" in kwargs["text_body"]


def test_no_email_when_flag_off(subscribed):
    org = _org()
    user = _admin(org)
    with (
        mock.patch("astrolift_operations.notification_email.notifications_email_enabled", return_value=False),
        mock.patch("astrolift_operations.notification_email.send_notice_email") as send,
    ):
        _emit_app_down(org, user)

    send.assert_not_called()


def test_per_user_email_optout_suppresses(subscribed):
    org = _org()
    user = _admin(org)
    NotificationPreference.objects.create(user=user, channel="email", event_kind="app.down", enabled=False)
    with (
        mock.patch("astrolift_operations.notification_email.notifications_email_enabled", return_value=True),
        mock.patch("astrolift_operations.notification_email.send_notice_email") as send,
    ):
        _emit_app_down(org, user)

    send.assert_not_called()


def test_invalid_address_skipped(subscribed):
    org = _org()
    user = _admin(org, email="ops@astrolift.dev")
    User.objects.filter(pk=user.pk).update(email="not-an-email")
    with (
        mock.patch("astrolift_operations.notification_email.notifications_email_enabled", return_value=True),
        mock.patch("astrolift_operations.notification_email.send_notice_email") as send,
    ):
        _emit_app_down(org, user)

    send.assert_not_called()
