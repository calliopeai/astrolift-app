from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from astrolift_agents.tests import test_agent_messages_2434 as messages
from astrolift_operations.models import NotificationQuietHours
from astrolift_operations.notification_quiet_hours import active_at
from astrolift_operations.schema.notification_quiet_hours import (
    NotificationQuietHoursMutation,
    NotificationQuietHoursQuery,
    SetNotificationQuietHoursInput,
)
from config.schema import schema

pytestmark = pytest.mark.django_db
world = messages.world
other = messages.other
_env = messages._env


@pytest.mark.parametrize(
    ("zone", "start", "end", "instant", "wanted"),
    [
        ("UTC", 540, 1020, "2026-10-17T09:00:00", True),
        ("UTC", 540, 1020, "2026-10-17T17:00:00", False),
        ("America/Costa_Rica", 1320, 480, "2026-10-18T04:00:00", True),
        ("America/Costa_Rica", 1320, 480, "2026-10-18T13:59:59", True),
        ("America/Costa_Rica", 1320, 480, "2026-10-18T14:00:00", False),
        ("Asia/Tokyo", 1320, 480, "2026-10-17T14:00:00", True),
        ("America/New_York", 60, 120, "2026-11-01T05:30:00", True),
        ("America/New_York", 60, 120, "2026-11-01T06:30:00", True),
        ("America/New_York", 60, 120, "2026-11-01T07:00:00", False),
    ],
)
def test_quiet_window_uses_the_recipients_timezone_and_excludes_the_end(zone, start, end, instant, wanted):
    row = NotificationQuietHours(enabled=True, timezone=zone, start_minute=start, end_minute=end)
    assert active_at(row, now=datetime.fromisoformat(instant).replace(tzinfo=UTC)) is wanted


def test_quiet_hours_are_authenticated_self_only_across_organizations(world, other):
    with messages._as(world) as info:
        defaults = NotificationQuietHoursQuery().astrolift_my_notification_quiet_hours(info)
        assert not defaults.enabled and not defaults.active and defaults.timezone == "UTC"
        result = NotificationQuietHoursMutation().set_notification_quiet_hours(
            info,
            input=SetNotificationQuietHoursInput(
                enabled=True, timezone="America/Costa_Rica", start_minute=1320, end_minute=480
            ),
        )
        assert result.ok, result.errors
    with messages._as(other) as info:
        own = NotificationQuietHoursQuery().astrolift_my_notification_quiet_hours(info)
        assert not own.enabled and own.timezone == "UTC"
    assert NotificationQuietHours.objects.get().user_id == world.user.pk
    context = SimpleNamespace(request=SimpleNamespace(user=AnonymousUser()))
    refused = schema.execute_sync("{ astroliftMyNotificationQuietHours { enabled } }", context_value=context)
    assert refused.errors
    refused = NotificationQuietHoursMutation().set_notification_quiet_hours(
        SimpleNamespace(context=context),
        input=SetNotificationQuietHoursInput(enabled=True, timezone="UTC", start_minute=0, end_minute=1),
    )
    assert not refused.ok and refused.errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("zone", "start", "end", "field"),
    [
        ("../etc/passwd", 0, 1, "timezone"),
        ("No/Such_Zone", 0, 1, "timezone"),
        ("UTC", -1, 1, "startMinute"),
        ("UTC", 0, 1440, "endMinute"),
        ("UTC", 1, 1, "endMinute"),
    ],
)
def test_invalid_quiet_hours_never_persist(world, zone, start, end, field):
    with messages._as(world) as info:
        result = NotificationQuietHoursMutation().set_notification_quiet_hours(
            info,
            input=SetNotificationQuietHoursInput(
                enabled=True, timezone=zone, start_minute=start, end_minute=end
            ),
        )
    assert not result.ok and result.errors[0].field == field
    assert not NotificationQuietHours.objects.exists()


def test_quiet_hours_upsert_preserves_other_peoples_settings(world, other):
    NotificationQuietHours.objects.create(user=other.user, enabled=True, timezone="Asia/Tokyo")
    for enabled in (True, False):
        with messages._as(world) as info:
            assert (
                NotificationQuietHoursMutation()
                .set_notification_quiet_hours(
                    info,
                    input=SetNotificationQuietHoursInput(
                        enabled=enabled, timezone="UTC", start_minute=1320, end_minute=480
                    ),
                )
                .ok
            )
    assert NotificationQuietHours.objects.filter(user=world.user).count() == 1
    assert not NotificationQuietHours.objects.get(user=world.user).enabled
    assert NotificationQuietHours.objects.get(user=other.user).timezone == "Asia/Tokyo"


def test_quiet_hours_suppress_personal_external_delivery_but_keep_the_inbox_and_event(
    world, monkeypatch, django_capture_on_commit_callbacks
):
    from astrolift_agents.models import AgentMessageRecipient
    from astrolift_operations.models import AuditEvent, DeviceRegistration, Event
    from astrolift_operations.notification_dispatch import (
        set_driver_override_for_tests,
        unset_driver_override_for_tests,
    )
    from astrolift_operations.tests.fixtures.notification_driver import MemoryNotificationDriver

    instant = datetime(2026, 10, 18, 5, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: instant)
    NotificationQuietHours.objects.create(
        user=world.user, enabled=True, timezone="America/Costa_Rica", start_minute=1320, end_minute=480
    )
    for user in (world.user, world.reader):
        DeviceRegistration.objects.create(
            user=user,
            organization=world.org,
            device_token=f"quiet-{user.pk}",
            platform="ios",
            driver="memory",
        )
    driver = MemoryNotificationDriver()
    emails = []
    monkeypatch.setattr("astrolift_operations.notification_email.notifications_email_enabled", lambda: True)
    monkeypatch.setattr(
        "astrolift_operations.notification_email.send_notice_email",
        lambda **kwargs: emails.append(kwargs) or 1,
    )
    set_driver_override_for_tests(driver)
    try:
        task, key = messages._task(world)
        with django_capture_on_commit_callbacks(execute=True):
            response = messages._send(
                task, key, body="A private update", to=[world.user.email, world.reader.email]
            )
        assert response.status_code == 201
        assert len(driver.sent) == 1 and driver.sent[0][0] == f"quiet-{world.reader.pk}"
        assert [row["to"] for row in emails] == [[world.reader.email]]
        assert AgentMessageRecipient.objects.filter(message__guid=response.json()["id"]).count() == 2
        assert Event.objects.filter(event_type="agent.message.sent").count() == 1
        assert AuditEvent.objects.filter(action="notification.push", data__status="quiet_hours").exists()
        assert AuditEvent.objects.filter(action="notification.email", data__status="quiet_hours").exists()
        with messages._as(world) as info:
            result = schema.execute_sync(
                "{ astroliftMyNotificationQuietHours { active } astroliftAgentMessages { items { body } } }",
                context_value=info.context,
            )
        assert not result.errors
        assert result.data["astroliftMyNotificationQuietHours"]["active"] is True
        assert result.data["astroliftAgentMessages"]["items"][0]["body"] == "A private update"
    finally:
        unset_driver_override_for_tests()


def test_external_agent_message_delivery_rechecks_current_audience(world):
    from astrolift_operations.notification_dispatch import _template_agent_message
    from core.events import EventEnvelope

    task, key = messages._task(world)
    message_id = messages._send(task, key, body="private", to=[world.reader.email]).json()["id"]
    world.reader.role_bindings.filter(deleted_at__isnull=True).update(deleted_at=timezone.now())
    envelope = EventEnvelope(
        event_type="agent.message.sent",
        payload={"message_id": message_id, "recipient_user_ids": [world.reader.pk]},
        organization_id=world.org.pk,
        team_id=None,
        project_id=None,
        registered_app_id=world.agent.registered_app_id,
    )
    assert _template_agent_message(envelope) is None


def test_quiet_hours_capability_is_advertised():
    from config.schema_public import schema_public

    result = schema_public.execute_sync("{ astroliftServerInfo { capabilities } }")
    assert not result.errors
    assert "notifications.quiet_hours" in result.data["astroliftServerInfo"]["capabilities"]
