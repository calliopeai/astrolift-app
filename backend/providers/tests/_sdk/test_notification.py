"""Tests for the NotificationDriver SDK protocol + multiplexer (#490).

Covers:
* Dataclass shape + defaults (DeviceRegistration, NotificationPayload,
  SendResult, target variants).
* MultiplexerNotificationDriver: primary-then-fallback dispatch,
  registration delegated to primary, healthcheck aggregation,
  no-nesting + non-empty rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from _sdk.notification import (
    DeliveryTarget,
    DevicePlatform,
    DeviceRegistration,
    EmailTarget,
    MultiplexerNotificationDriver,
    NotificationDriver,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
    SmsTarget,
    WebhookTarget,
)

# ---- protocol / dataclass shape ------------------------------------


def test_payload_defaults() -> None:
    p = NotificationPayload(title="t", body="b")
    assert p.data == {}
    assert p.action_url == ""
    assert p.badge is None
    assert p.category == ""
    assert p.ttl_seconds is None
    assert p.extra == {}


def test_device_registration_shape() -> None:
    reg = DeviceRegistration(
        registration_id="endpoint-arn",
        user_id="user-1",
        device_token="token-abc",
        platform=DevicePlatform.IOS,
    )
    assert reg.label == ""
    assert reg.provider_metadata == {}
    assert reg.platform == DevicePlatform.IOS


def test_send_result_failure_carries_diagnostic() -> None:
    target: DeliveryTarget = PushTarget(
        registration_id="r",
        platform=DevicePlatform.IOS,
    )
    r = SendResult(
        target=target,
        status="failed",
        error="upstream 500",
        retriable=True,
    )
    assert r.retriable is True
    assert r.error == "upstream 500"
    assert r.provider_message_id == ""


def test_target_variants_construct() -> None:
    PushTarget(registration_id="r", platform=DevicePlatform.ANDROID)
    EmailTarget(to="user@example.com", subject="s", body_text="b")
    SmsTarget(to="+15555550100", message="hi")
    WebhookTarget(url="https://hooks.example.com/in", headers={"X-Foo": "1"})


def test_platform_enum_values_are_lowercase() -> None:
    """Wire-stable values that other layers (FE codegen) lean on."""
    assert DevicePlatform.IOS.value == "ios"
    assert DevicePlatform.ANDROID.value == "android"
    assert DevicePlatform.WEB.value == "web"


# ---- multiplexer test fakes ----------------------------------------


@dataclass
class FakeDriver:
    name: str
    speaks: set[type] = field(default_factory=set)
    health_ok: bool = True
    sent: list[tuple[DeliveryTarget, NotificationPayload]] = field(
        default_factory=list,
    )
    registered: list[RegisterDeviceRequest] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        self.registered.append(request)
        return DeviceRegistration(
            registration_id=f"{self.name}::{request.device_token}",
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
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
        if type(target) in self.speaks:
            return SendResult(
                target=target,
                status="delivered",
                provider_message_id=f"{self.name}-msg-id",
            )
        return SendResult(
            target=target,
            status="unsupported",
            error=f"{self.name} doesn't speak {type(target).__name__}",
        )

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            ok=self.health_ok,
            message=f"{self.name} {'ok' if self.health_ok else 'fail'}",
        )


# Sanity: FakeDriver structurally implements NotificationDriver.
def _typecheck_protocol() -> NotificationDriver:
    return FakeDriver(name="x")


# ---- multiplexer behaviour -----------------------------------------


def test_multiplexer_rejects_empty_secondaries() -> None:
    primary = FakeDriver(name="primary")
    with pytest.raises(ValueError, match="non-empty"):
        MultiplexerNotificationDriver(primary=primary, secondaries=[])


def test_multiplexer_rejects_nested_multiplexers() -> None:
    primary = FakeDriver(name="primary")
    secondary = FakeDriver(name="secondary")
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    with pytest.raises(ValueError, match="nested"):
        MultiplexerNotificationDriver(primary=mux, secondaries=[secondary])
    with pytest.raises(ValueError, match="nested"):
        MultiplexerNotificationDriver(primary=primary, secondaries=[mux])


def test_multiplexer_send_uses_primary_when_supported() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={PushTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    target = PushTarget(
        registration_id="r",
        platform=DevicePlatform.IOS,
    )
    result = mux.send(
        target=target,
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "delivered"
    assert result.provider_message_id == "primary-msg-id"
    # Secondary not consulted when primary handles the target.
    assert secondary.sent == []


def test_multiplexer_send_falls_back_when_primary_unsupported() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={EmailTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    target: DeliveryTarget = EmailTarget(to="a@example.com")
    result = mux.send(
        target=target,
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "delivered"
    assert result.provider_message_id == "secondary-msg-id"


def test_multiplexer_returns_unsupported_when_no_child_speaks_target() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={EmailTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    target = SmsTarget(to="+15555550100", message="hi")
    result = mux.send(
        target=target,
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"
    assert "no child driver" in result.error


def test_multiplexer_register_goes_to_primary_only() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={PushTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    reg = mux.register_device(
        RegisterDeviceRequest(
            user_id="u1",
            device_token="tok-1",
            platform=DevicePlatform.IOS,
        ),
    )
    assert len(primary.registered) == 1
    assert secondary.registered == []
    assert reg.registration_id.startswith("primary::")


def test_multiplexer_revoke_goes_to_primary_only() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={PushTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    mux.revoke_device(registration_id="some-id")
    assert primary.revoked == ["some-id"]
    assert secondary.revoked == []


def test_multiplexer_healthcheck_ok_only_if_all_children_ok() -> None:
    primary = FakeDriver(name="primary", health_ok=True)
    secondary = FakeDriver(name="secondary", health_ok=False)
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    health = mux.healthcheck()
    assert health.ok is False
    assert "primary:primary=ok" in health.message
    assert "secondary=fail" in health.message


def test_multiplexer_healthcheck_all_ok() -> None:
    primary = FakeDriver(name="primary", health_ok=True)
    secondary = FakeDriver(name="secondary", health_ok=True)
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    health = mux.healthcheck()
    assert health.ok is True


def test_multiplexer_send_bulk_routes_per_target() -> None:
    primary = FakeDriver(name="primary", speaks={PushTarget})
    secondary = FakeDriver(name="secondary", speaks={EmailTarget})
    mux = MultiplexerNotificationDriver(
        primary=primary,
        secondaries=[secondary],
    )
    targets: list[DeliveryTarget] = [
        PushTarget(registration_id="r1", platform=DevicePlatform.IOS),
        EmailTarget(to="a@example.com"),
    ]
    results = mux.send_bulk(
        targets=targets,
        payload=NotificationPayload(title="t", body="b"),
    )
    assert [r.status for r in results] == ["delivered", "delivered"]
    assert [r.provider_message_id for r in results] == [
        "primary-msg-id",
        "secondary-msg-id",
    ]
