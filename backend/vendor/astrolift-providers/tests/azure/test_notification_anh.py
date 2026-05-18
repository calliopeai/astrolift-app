"""Tests for the Azure Notification Hubs NotificationDriver (#490)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.notification import (
    DevicePlatform,
    EmailTarget,
    NotificationPayload,
    PushTarget,
    RegisterDeviceRequest,
    SmsTarget,
    WebhookTarget,
)
from azure.notification_anh import (
    AzureNotificationHubsConfig,
    AzureNotificationHubsDriver,
    AzureNotificationHubsError,
    _AnhHttpError,
    _make_sas_token,
)


@dataclass
class FakeAnhHttp:
    """Records each request + replays a canned response. Driver swaps
    moto-equivalent in because ANH doesn't have one."""

    canned_post_response: dict[str, Any] = field(default_factory=dict)
    canned_post_error: _AnhHttpError | None = None
    canned_put_error: _AnhHttpError | None = None
    canned_delete_error: _AnhHttpError | None = None
    canned_get_error: _AnhHttpError | None = None
    calls: list[tuple[str, str, dict[str, str], str]] = field(
        default_factory=list,
    )

    def put(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        self.calls.append(("PUT", url, dict(headers), body))
        if self.canned_put_error is not None:
            raise self.canned_put_error
        return {}

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        self.calls.append(("POST", url, dict(headers), body))
        if self.canned_post_error is not None:
            raise self.canned_post_error
        return self.canned_post_response

    def delete(
        self,
        *,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append(("DELETE", url, dict(headers), ""))
        if self.canned_delete_error is not None:
            raise self.canned_delete_error
        return {}

    def get(
        self,
        *,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append(("GET", url, dict(headers), ""))
        if self.canned_get_error is not None:
            raise self.canned_get_error
        return {}


@pytest.fixture
def fake_http() -> FakeAnhHttp:
    return FakeAnhHttp(
        canned_post_response={"notificationId": "anh-123"},
    )


@pytest.fixture
def driver(fake_http: FakeAnhHttp) -> AzureNotificationHubsDriver:
    return AzureNotificationHubsDriver(
        config=AzureNotificationHubsConfig(
            namespace="my-ns",
            hub_name="my-hub",
            shared_access_key_name="DefaultFullSharedAccessSignature",
            shared_access_key="c2VjcmV0LWtleQ==",
            http_client=fake_http,
        ),
    )


# ---- sas + helpers --------------------------------------------------


def test_sas_token_includes_signature_and_expiry() -> None:
    token = _make_sas_token(
        uri="https://my-ns.servicebus.windows.net/my-hub",
        key_name="kn",
        key="abc",
        ttl_seconds=60,
    )
    assert token.startswith("SharedAccessSignature ")
    assert "sig=" in token
    assert "se=" in token
    assert "skn=kn" in token


# ---- registration ---------------------------------------------------


def test_register_puts_installation_with_tags(driver, fake_http) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="user-1",
            device_token="ios-token",
            platform=DevicePlatform.IOS,
        ),
    )
    assert reg.registration_id.startswith("inst-")
    assert reg.provider_metadata["installation_id"] == reg.registration_id
    assert fake_http.calls
    method, url, _, body = fake_http.calls[0]
    assert method == "PUT"
    assert "/installations/" in url
    parsed = json.loads(body)
    assert parsed["platform"] == "apns"
    assert parsed["pushChannel"] == "ios-token"
    assert "user:user-1" in parsed["tags"]


def test_register_uses_supplied_installation_id(driver, fake_http) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.ANDROID,
            provider_metadata={"installation_id": "fixed-id-42"},
        ),
    )
    assert reg.registration_id == "fixed-id-42"
    _, url, _, _ = fake_http.calls[0]
    assert url.endswith("/installations/fixed-id-42?api-version=2020-06")


def test_register_surface_failure_raises(driver, fake_http) -> None:
    fake_http.canned_put_error = _AnhHttpError(
        message="500",
        status_code=500,
    )
    with pytest.raises(AzureNotificationHubsError):
        driver.register_device(
            RegisterDeviceRequest(
                user_id="u",
                device_token="t",
                platform=DevicePlatform.IOS,
            ),
        )


def test_revoke_swallows_errors(driver, fake_http) -> None:
    fake_http.canned_delete_error = _AnhHttpError(
        message="404",
        status_code=404,
    )
    driver.revoke_device(registration_id="some-id")
    method, _, _, _ = fake_http.calls[0]
    assert method == "DELETE"


def test_revoke_empty_id_is_noop(driver, fake_http) -> None:
    driver.revoke_device(registration_id="")
    assert fake_http.calls == []


# ---- send -----------------------------------------------------------


def test_send_push_ios_includes_apns_wire_body(driver, fake_http) -> None:
    result = driver.send(
        target=PushTarget(
            registration_id="inst-1",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(
            title="t",
            body="b",
            badge=2,
            category="alert",
        ),
    )
    assert result.status == "queued"
    assert result.provider_message_id == "anh-123"
    method, url, headers, body = fake_http.calls[0]
    assert method == "POST"
    assert "/messages?api-version=2020-06" in url
    assert headers["ServiceBusNotification-Format"] == "apple"
    assert headers["ServiceBusNotification-Tags"] == "installationId:{inst-1}"
    parsed = json.loads(body)
    assert parsed["aps"]["alert"]["title"] == "t"
    assert parsed["aps"]["badge"] == 2


def test_send_push_android_uses_fcmv1_format(driver, fake_http) -> None:
    driver.send(
        target=PushTarget(
            registration_id="inst-2",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(
            title="t",
            body="b",
            data={"x": "1"},
            action_url="https://x.test/y",
        ),
    )
    _, _, headers, body = fake_http.calls[0]
    assert headers["ServiceBusNotification-Format"] == "fcmv1"
    parsed = json.loads(body)
    assert parsed["notification"]["title"] == "t"
    assert parsed["data"]["x"] == "1"
    assert parsed["data"]["action_url"] == "https://x.test/y"


def test_send_classifies_404_as_invalid_token(driver, fake_http) -> None:
    fake_http.canned_post_error = _AnhHttpError(
        message="not found",
        status_code=404,
    )
    result = driver.send(
        target=PushTarget(
            registration_id="inst-1",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "invalid_token"
    assert result.retriable is False


def test_send_classifies_429_as_rate_limited(driver, fake_http) -> None:
    fake_http.canned_post_error = _AnhHttpError(
        message="throttled",
        status_code=429,
    )
    result = driver.send(
        target=PushTarget(
            registration_id="inst-1",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "rate_limited"
    assert result.retriable is True


def test_send_classifies_401_as_failed_non_retriable(
    driver,
    fake_http,
) -> None:
    fake_http.canned_post_error = _AnhHttpError(
        message="unauthorized",
        status_code=401,
    )
    result = driver.send(
        target=PushTarget(
            registration_id="inst-1",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert result.retriable is False


def test_send_email_returns_unsupported(driver) -> None:
    result = driver.send(
        target=EmailTarget(to="a@example.com"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_send_sms_returns_unsupported(driver) -> None:
    result = driver.send(
        target=SmsTarget(to="+15555550100", message="hi"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_send_webhook_returns_unsupported(driver) -> None:
    result = driver.send(
        target=WebhookTarget(url="https://x.test/y"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_healthcheck_treats_401_as_failure(driver, fake_http) -> None:
    fake_http.canned_get_error = _AnhHttpError(
        message="unauthorized",
        status_code=401,
    )
    health = driver.healthcheck()
    assert health.ok is False
    assert "sas auth failed" in health.message


def test_healthcheck_treats_404_as_ok(driver, fake_http) -> None:
    fake_http.canned_get_error = _AnhHttpError(
        message="not found",
        status_code=404,
    )
    health = driver.healthcheck()
    assert health.ok is True
