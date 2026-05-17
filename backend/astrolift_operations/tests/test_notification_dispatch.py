"""Tests for the notification dispatcher (#490)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from _sdk.notification import (
    DeliveryTarget,
    DevicePlatform,
    EmailTarget,
    NotificationDriver,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
)
from _sdk.notification import (
    DeviceRegistration as SdkDeviceRegistration,
)
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_operations.models import (
    DeviceRegistration,
    NotificationDelivery,
    NotificationProfile,
)
from astrolift_operations.notification_dispatch import (
    ChannelOptIn,
    UserTarget,
    dispatch_notification,
    register_device,
    unregister_device,
)

pytestmark = pytest.mark.django_db


# ---- test driver ---------------------------------------------------


@dataclass
class FakeNotificationDriver:
    name: str = "fake"
    canned_results: dict[type, SendResult] = field(default_factory=dict)
    """Map of target-type -> SendResult to return for that channel."""

    registered: list[RegisterDeviceRequest] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    sent: list[tuple[DeliveryTarget, NotificationPayload]] = field(
        default_factory=list,
    )

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> SdkDeviceRegistration:
        self.registered.append(request)
        return SdkDeviceRegistration(
            registration_id=f"{self.name}::{request.device_token}",
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata={"hint": "fake"},
        )

    def revoke_device(self, *, registration_id: str) -> None:
        self.revoked.append(registration_id)

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        self.sent.append((target, payload))
        canned = self.canned_results.get(type(target))
        if canned is not None:
            # Rebuild with the live target so callers can compare
            # identity if needed.
            return SendResult(
                target=target,
                status=canned.status,
                provider_message_id=canned.provider_message_id,
                error=canned.error,
                retriable=canned.retriable,
            )
        return SendResult(target=target, status="delivered")

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, message=f"{self.name} ok")


# ---- scaffolding ---------------------------------------------------


def _org(slug: str = "acme") -> Organization:
    return Organization.objects.create(name="Acme", slug=slug)


def _user(*, email: str = "u@test.invalid") -> object:
    User = get_user_model()
    return User.objects.create_user(
        email=email,
        username=email,
        password="x",
    )


def _profile(org: Organization, *, driver: str = "fake") -> NotificationProfile:
    return NotificationProfile.objects.create(
        organization=org,
        driver=driver,
        config={},
        is_active=True,
    )


def _payload() -> NotificationPayload:
    return NotificationPayload(title="t", body="b", data={"k": "v"})


def _factory(driver: FakeNotificationDriver):
    def make(profile: NotificationProfile) -> NotificationDriver:
        return driver

    return make


# ---- dispatch ------------------------------------------------------


def test_dispatch_skips_when_no_profile() -> None:
    org = _org()
    u = _user()
    report = dispatch_notification(
        organization_id=org.id,
        event_type="deploy.failed",
        target_users=[UserTarget(user_id=str(u.id))],
        payload=_payload(),
    )
    assert report.sent_count == 0
    assert report.skipped_count == 1
    assert NotificationDelivery.objects.count() == 0


def test_dispatch_skips_user_with_no_devices_or_channels() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver()
    report = dispatch_notification(
        organization_id=org.id,
        event_type="deploy.failed",
        target_users=[UserTarget(user_id=str(u.id))],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    assert report.skipped_count == 1
    assert report.sent_count == 0
    assert driver.sent == []


def test_dispatch_routes_email_channel_target() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver(name="fake-email")
    report = dispatch_notification(
        organization_id=org.id,
        event_type="deploy.failed",
        target_users=[
            UserTarget(
                user_id=str(u.id),
                channels=ChannelOptIn(email="x@y.test"),
            ),
        ],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    assert report.sent_count == 1
    assert NotificationDelivery.objects.count() == 1
    delivery = NotificationDelivery.objects.first()
    assert delivery.target_kind == "email"
    assert delivery.target_address == "x@y.test"
    assert delivery.driver_name == "fake-email"
    assert delivery.status == "delivered"


def test_dispatch_fans_out_to_multiple_channels_per_user() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver()
    DeviceRegistration.objects.create(
        user_id=u.id,
        organization=org,
        device_token="tok-ios",
        platform="ios",
        registration_id="reg-ios",
        driver_name="fake",
    )
    report = dispatch_notification(
        organization_id=org.id,
        event_type="t",
        target_users=[
            UserTarget(
                user_id=str(u.id),
                channels=ChannelOptIn(
                    email="e@x.test",
                    sms="+15555550100",
                    webhook_url="https://hook.test/in",
                ),
            ),
        ],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    # One push + 3 channels = 4 deliveries
    assert NotificationDelivery.objects.count() == 4
    target_kinds = set(
        NotificationDelivery.objects.values_list("target_kind", flat=True),
    )
    assert target_kinds == {"push", "email", "sms", "webhook"}
    assert report.sent_count == 4


def test_dispatch_marks_device_revoked_on_invalid_token() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver(
        canned_results={
            PushTarget: SendResult(
                target=PushTarget(
                    registration_id="x",
                    platform=DevicePlatform.IOS,
                ),
                status="invalid_token",
                error="endpoint unregistered",
            ),
        },
    )
    device = DeviceRegistration.objects.create(
        user_id=u.id,
        organization=org,
        device_token="tok-ios",
        platform="ios",
        registration_id="reg-ios",
        driver_name="fake",
    )
    dispatch_notification(
        organization_id=org.id,
        event_type="t",
        target_users=[UserTarget(user_id=str(u.id))],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    device.refresh_from_db()
    assert device.deleted_at is not None
    assert device.revoked_reason == "endpoint unregistered"


def test_dispatch_records_failed_count_on_send_failure() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver(
        canned_results={
            EmailTarget: SendResult(
                target=EmailTarget(to="x@y.test"),
                status="failed",
                error="upstream 500",
                retriable=True,
            ),
        },
    )
    report = dispatch_notification(
        organization_id=org.id,
        event_type="t",
        target_users=[
            UserTarget(
                user_id=str(u.id),
                channels=ChannelOptIn(email="x@y.test"),
            ),
        ],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    assert report.failed_count == 1
    assert report.sent_count == 0
    delivery = NotificationDelivery.objects.first()
    assert delivery.status == "failed"
    assert delivery.retriable is True


def test_dispatch_stamps_last_used_at_on_successful_push() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver()
    device = DeviceRegistration.objects.create(
        user_id=u.id,
        organization=org,
        device_token="tok-ios",
        platform="ios",
        registration_id="reg-ios",
        driver_name="fake",
    )
    dispatch_notification(
        organization_id=org.id,
        event_type="t",
        target_users=[UserTarget(user_id=str(u.id))],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    device.refresh_from_db()
    assert device.last_used_at is not None


def test_dispatch_uses_most_recent_active_profile() -> None:
    org = _org()
    u = _user()
    # Older inactive profile should NOT be picked.
    NotificationProfile.objects.create(
        organization=org,
        driver="old",
        config={},
        is_active=False,
    )
    NotificationProfile.objects.create(
        organization=org,
        driver="newer-active",
        config={},
        is_active=True,
    )
    driver = FakeNotificationDriver(name="newer-active")
    dispatch_notification(
        organization_id=org.id,
        event_type="t",
        target_users=[
            UserTarget(
                user_id=str(u.id),
                channels=ChannelOptIn(email="x@y.test"),
            ),
        ],
        payload=_payload(),
        driver_factory=_factory(driver),
    )
    assert NotificationDelivery.objects.first().driver_name == "newer-active"


# ---- register / unregister ----------------------------------------


def test_register_device_creates_row_and_calls_driver() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver(name="fake")
    result = register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
        label="phone",
        driver_factory=_factory(driver),
    )
    assert result.device.device_token == "tok-1"
    assert result.device.driver_name == "fake"
    assert result.device.registration_id == "fake::tok-1"
    assert driver.registered[0].device_token == "tok-1"


def test_register_device_idempotent_on_same_token() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver()
    register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
        label="phone",
        driver_factory=_factory(driver),
    )
    result = register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
        label="phone v2",
        driver_factory=_factory(driver),
    )
    # Only one row exists; label updated.
    assert DeviceRegistration.objects.filter(deleted_at__isnull=True).count() == 1
    assert result.device.label == "phone v2"
    # Driver was called only once (the second call is short-circuited).
    assert len(driver.registered) == 1


def test_register_device_with_no_profile_creates_row_without_driver() -> None:
    org = _org()
    u = _user()
    result = register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
    )
    assert result.device.driver_name == ""
    assert result.device.registration_id == ""
    assert result.driver_result is None


def test_register_device_rejects_unknown_platform() -> None:
    org = _org()
    u = _user()
    with pytest.raises(ValueError):
        register_device(
            organization_id=org.id,
            user_id=u.id,
            device_token="tok-1",
            platform="blackberry",
        )


def test_unregister_device_soft_deletes_and_revokes() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver(name="fake")
    result = register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
        driver_factory=_factory(driver),
    )
    ok = unregister_device(
        device_guid=str(result.device.guid),
        user_id=u.id,
        driver_factory=_factory(driver),
    )
    assert ok is True
    result.device.refresh_from_db()
    assert result.device.deleted_at is not None
    assert driver.revoked == ["fake::tok-1"]


def test_unregister_device_idempotent_on_already_revoked() -> None:
    org = _org()
    u = _user()
    _profile(org)
    driver = FakeNotificationDriver()
    result = register_device(
        organization_id=org.id,
        user_id=u.id,
        device_token="tok-1",
        platform="ios",
        driver_factory=_factory(driver),
    )
    unregister_device(
        device_guid=str(result.device.guid),
        user_id=u.id,
        driver_factory=_factory(driver),
    )
    # Second revoke returns False (row already soft-deleted).
    assert (
        unregister_device(
            device_guid=str(result.device.guid),
            user_id=u.id,
            driver_factory=_factory(driver),
        )
        is False
    )


def test_unregister_device_returns_false_when_owned_by_other_user() -> None:
    org = _org()
    u1 = _user(email="u1@test.invalid")
    u2 = _user(email="u2@test.invalid")
    _profile(org)
    driver = FakeNotificationDriver()
    result = register_device(
        organization_id=org.id,
        user_id=u1.id,
        device_token="tok-1",
        platform="ios",
        driver_factory=_factory(driver),
    )
    # u2 cannot revoke u1's device.
    assert (
        unregister_device(
            device_guid=str(result.device.guid),
            user_id=u2.id,
            driver_factory=_factory(driver),
        )
        is False
    )
    result.device.refresh_from_db()
    assert result.device.deleted_at is None
