"""Tests for the GCP FCM NotificationDriver (#490)."""

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
from gcp.notification_fcm import (
    FCMConfig,
    FCMNotificationDriver,
    _FCMHttpError,
)


@dataclass
class FakeFCMHttp:
    """In-memory FCM stub. Each call records the request and either
    returns a pre-canned response or raises a pre-canned error so
    tests can exercise the driver's response classification."""

    canned_response: dict[str, Any] = field(default_factory=dict)
    canned_error: _FCMHttpError | None = None
    last_url: str = ""
    last_headers: dict[str, str] = field(default_factory=dict)
    last_body: dict[str, Any] = field(default_factory=dict)

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        self.last_url = url
        self.last_headers = dict(headers)
        self.last_body = json.loads(body) if body else {}
        if self.canned_error is not None:
            raise self.canned_error
        return self.canned_response


@pytest.fixture
def fake_http() -> FakeFCMHttp:
    return FakeFCMHttp(
        canned_response={
            "name": "projects/proj-1/messages/0:abc",
        },
    )


@pytest.fixture
def driver(fake_http: FakeFCMHttp) -> FCMNotificationDriver:
    return FCMNotificationDriver(
        config=FCMConfig(
            project_id="proj-1",
            access_token="fake-bearer",
            http_client=fake_http,
        ),
    )


# ---- registration ---------------------------------------------------


def test_register_stamps_project_id_metadata(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u1",
            device_token="tok-1",
            platform=DevicePlatform.ANDROID,
        ),
    )
    assert reg.registration_id == "tok-1"
    assert reg.provider_metadata["fcm_project_id"] == "proj-1"


def test_revoke_is_noop(driver) -> None:
    # FCM has no server-side revoke -- exercise it for coverage.
    driver.revoke_device(registration_id="anything")


# ---- send -----------------------------------------------------------


def test_send_push_targets_v1_endpoint(driver, fake_http) -> None:
    result = driver.send(
        target=PushTarget(
            registration_id="tok-1",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(
            title="Hi",
            body="there",
            data={"deploy_id": "42"},
            ttl_seconds=600,
            category="deploy",
        ),
    )
    assert result.status == "delivered"
    assert result.provider_message_id.startswith("projects/")
    assert fake_http.last_url == ("https://fcm.googleapis.com/v1/projects/proj-1/messages:send")
    assert fake_http.last_headers["Authorization"] == "Bearer fake-bearer"
    msg = fake_http.last_body["message"]
    assert msg["token"] == "tok-1"
    assert msg["notification"]["title"] == "Hi"
    assert msg["data"]["deploy_id"] == "42"
    assert msg["android"]["ttl"] == "600s"
    assert msg["android"]["notification"]["channel_id"] == "deploy"


def test_send_push_ios_includes_apns_payload(driver, fake_http) -> None:
    driver.send(
        target=PushTarget(
            registration_id="tok-1",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(
            title="t",
            body="b",
            badge=5,
            category="alert",
            ttl_seconds=120,
            action_url="https://x.test/y",
        ),
    )
    msg = fake_http.last_body["message"]
    assert msg["apns"]["payload"]["aps"]["badge"] == 5
    assert msg["apns"]["payload"]["aps"]["category"] == "alert"
    assert msg["data"]["action_url"] == "https://x.test/y"
    assert msg["apns"]["headers"]["apns-expiration"]


def test_send_push_web_uses_webpush_block(driver, fake_http) -> None:
    driver.send(
        target=PushTarget(
            registration_id="tok-w",
            platform=DevicePlatform.WEB,
        ),
        payload=NotificationPayload(
            title="t",
            body="b",
            action_url="https://x.test/y",
        ),
    )
    msg = fake_http.last_body["message"]
    assert msg["webpush"]["notification"]["title"] == "t"
    assert msg["webpush"]["fcm_options"]["link"] == "https://x.test/y"


def test_send_classifies_unregistered_as_invalid_token(
    fake_http,
) -> None:
    fake_http.canned_error = _FCMHttpError(
        message="404 not found",
        status_code=404,
        body={
            "error": {
                "status": "NOT_FOUND",
                "details": [{"errorCode": "UNREGISTERED"}],
            },
        },
    )
    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            access_token="t",
            http_client=fake_http,
        ),
    )
    result = driver.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "invalid_token"
    assert result.retriable is False


def test_send_classifies_quota_as_rate_limited(fake_http) -> None:
    fake_http.canned_error = _FCMHttpError(
        message="429 quota",
        status_code=429,
        body={"error": {"status": "RESOURCE_EXHAUSTED"}},
    )
    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            access_token="t",
            http_client=fake_http,
        ),
    )
    result = driver.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "rate_limited"
    assert result.retriable is True


def test_send_classifies_perm_denied_as_failed_not_retriable(
    fake_http,
) -> None:
    fake_http.canned_error = _FCMHttpError(
        message="403",
        status_code=403,
        body={"error": {"status": "PERMISSION_DENIED"}},
    )
    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            access_token="t",
            http_client=fake_http,
        ),
    )
    result = driver.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert result.retriable is False


def test_send_email_returns_unsupported(driver) -> None:
    result = driver.send(
        target=EmailTarget(to="x@y.test"),
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


def test_send_uses_token_provider_when_set(fake_http) -> None:
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return "fresh-token"

    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            token_provider=provider,
            http_client=fake_http,
        ),
    )
    driver.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert calls == [1]
    assert fake_http.last_headers["Authorization"] == "Bearer fresh-token"


def test_send_token_mint_failure_returns_failed(fake_http) -> None:
    def boom() -> str:
        raise RuntimeError("metadata server unreachable")

    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            token_provider=boom,
            http_client=fake_http,
        ),
    )
    result = driver.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert "metadata server" in result.error
    assert result.retriable is True


def test_healthcheck_ok_when_token_mint_returns_value(driver) -> None:
    health = driver.healthcheck()
    assert health.ok is True


def test_healthcheck_fails_when_token_mint_raises(fake_http) -> None:
    def boom() -> str:
        raise RuntimeError("nope")

    driver = FCMNotificationDriver(
        config=FCMConfig(
            project_id="p",
            token_provider=boom,
            http_client=fake_http,
        ),
    )
    health = driver.healthcheck()
    assert health.ok is False
    assert "nope" in health.message
