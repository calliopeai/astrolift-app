"""Tests for AWS SNS NotificationDriver (#490)."""

from __future__ import annotations

import json
from typing import Any

import boto3
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
from aws.notification_sns import (
    SNSNotificationConfig,
    SNSNotificationDriver,
    SNSNotificationError,
)


@pytest.fixture
def sns_client() -> Any:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("sns", region_name="us-east-1")


@pytest.fixture
def ios_platform_arn(sns_client) -> str:
    # moto's SNS implements CreatePlatformApplication; we create a
    # fake APNs platform so register_device has a real target.
    response = sns_client.create_platform_application(
        Name="astrolift-ios",
        Platform="APNS",
        Attributes={"PlatformCredential": "fake-pem"},
    )
    return response["PlatformApplicationArn"]


@pytest.fixture
def android_platform_arn(sns_client) -> str:
    response = sns_client.create_platform_application(
        Name="astrolift-android",
        Platform="GCM",
        Attributes={"PlatformCredential": "fake-key"},
    )
    return response["PlatformApplicationArn"]


@pytest.fixture
def driver(sns_client, ios_platform_arn, android_platform_arn) -> SNSNotificationDriver:
    return SNSNotificationDriver(
        config=SNSNotificationConfig(
            region="us-east-1",
            platform_applications={
                DevicePlatform.IOS: ios_platform_arn,
                DevicePlatform.ANDROID: android_platform_arn,
            },
            sms_sender_id="ASTROLIFT",
        ),
        sns_client=sns_client,
    )


# ---- registration ---------------------------------------------------


def test_register_ios_creates_platform_endpoint(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="user-1",
            device_token="ios-token-abc",
            platform=DevicePlatform.IOS,
            label="Leo's iPhone",
        ),
    )
    assert reg.registration_id.startswith("arn:aws:sns:")
    assert reg.user_id == "user-1"
    assert reg.platform == DevicePlatform.IOS
    assert reg.label == "Leo's iPhone"
    assert reg.provider_metadata["endpoint_arn"] == reg.registration_id


def test_register_unmapped_platform_yields_sentinel_id(sns_client) -> None:
    # WEB has no PlatformApplicationArn configured -- register should
    # still return cleanly so the row persists.
    driver = SNSNotificationDriver(
        config=SNSNotificationConfig(region="us-east-1"),
        sns_client=sns_client,
    )
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="web-token",
            platform=DevicePlatform.WEB,
        ),
    )
    assert reg.registration_id.startswith("unmapped:")
    assert reg.provider_metadata["endpoint_arn"] == ""


def test_revoke_device_deletes_endpoint(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="ios-token-1",
            platform=DevicePlatform.IOS,
        ),
    )
    driver.revoke_device(registration_id=reg.registration_id)
    # Subsequent revoke is idempotent (already-gone).
    driver.revoke_device(registration_id=reg.registration_id)


def test_revoke_unmapped_id_is_noop(driver) -> None:
    driver.revoke_device(registration_id="unmapped:xyz")
    driver.revoke_device(registration_id="")


# ---- send -----------------------------------------------------------


def test_send_push_to_ios_uses_json_envelope_with_apns_keys(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.IOS,
        ),
    )
    captured: dict[str, Any] = {}
    original_publish = driver._sns.publish

    def spy_publish(**kwargs):
        captured.update(kwargs)
        return original_publish(**kwargs)

    driver._sns.publish = spy_publish
    result = driver.send(
        target=PushTarget(
            registration_id=reg.registration_id,
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(
            title="Deploy ready",
            body="api-v2 ready to promote",
            badge=3,
            category="deploy",
            action_url="https://astrolift.test/deploys/42",
            ttl_seconds=600,
        ),
    )
    assert result.status == "delivered"
    assert result.provider_message_id
    assert captured["TargetArn"] == reg.registration_id
    assert captured["MessageStructure"] == "json"
    envelope = json.loads(captured["Message"])
    assert "APNS" in envelope
    apns = json.loads(envelope["APNS"])
    assert apns["aps"]["alert"]["title"] == "Deploy ready"
    assert apns["aps"]["badge"] == 3
    assert apns["aps"]["category"] == "deploy"
    assert apns["action_url"] == "https://astrolift.test/deploys/42"
    # APNS_SANDBOX mirrors APNS so sandbox-bound apps still receive.
    assert envelope["APNS_SANDBOX"] == envelope["APNS"]
    # TTL surfaces as APNS message attribute.
    ttl_attr = captured["MessageAttributes"]["AWS.SNS.MOBILE.APNS.TTL"]
    assert ttl_attr["StringValue"] == "600"


def test_send_push_to_android_uses_gcm_envelope(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.ANDROID,
        ),
    )
    captured: dict[str, Any] = {}
    original_publish = driver._sns.publish

    def spy_publish(**kwargs):
        captured.update(kwargs)
        return original_publish(**kwargs)

    driver._sns.publish = spy_publish
    result = driver.send(
        target=PushTarget(
            registration_id=reg.registration_id,
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(
            title="Hi",
            body="there",
            data={"x": "1"},
        ),
    )
    assert result.status == "delivered"
    envelope = json.loads(captured["Message"])
    assert "GCM" in envelope
    gcm = json.loads(envelope["GCM"])
    assert gcm["notification"]["title"] == "Hi"
    assert gcm["data"]["x"] == "1"


def test_send_push_with_unmapped_returns_unsupported(sns_client) -> None:
    driver = SNSNotificationDriver(
        config=SNSNotificationConfig(region="us-east-1"),
        sns_client=sns_client,
    )
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.WEB,
        ),
    )
    result = driver.send(
        target=PushTarget(
            registration_id=reg.registration_id,
            platform=DevicePlatform.WEB,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_send_email_returns_unsupported(driver) -> None:
    result = driver.send(
        target=EmailTarget(to="a@example.com"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"
    assert "email_ses" in result.error


def test_send_webhook_returns_unsupported(driver) -> None:
    result = driver.send(
        target=WebhookTarget(url="https://example.com/hook"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_send_sms_returns_delivered(driver) -> None:
    result = driver.send(
        target=SmsTarget(to="+15555550100", message="hi"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "delivered"


def test_send_push_classifies_endpoint_disabled_as_invalid_token(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.IOS,
        ),
    )

    def bad_publish(**_kwargs):
        raise RuntimeError("EndpointDisabled: token unregistered")

    driver._sns.publish = bad_publish
    result = driver.send(
        target=PushTarget(
            registration_id=reg.registration_id,
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "invalid_token"
    assert result.retriable is False


def test_send_push_classifies_throttling_as_rate_limited(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.IOS,
        ),
    )

    def bad_publish(**_kwargs):
        raise RuntimeError("Throttling: too many requests")

    driver._sns.publish = bad_publish
    result = driver.send(
        target=PushTarget(
            registration_id=reg.registration_id,
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "rate_limited"
    assert result.retriable is True


def test_send_bulk_routes_each_target(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="t",
            platform=DevicePlatform.IOS,
        ),
    )
    results = driver.send_bulk(
        targets=[
            PushTarget(
                registration_id=reg.registration_id,
                platform=DevicePlatform.IOS,
            ),
            EmailTarget(to="x@y.test"),
        ],
        payload=NotificationPayload(title="t", body="b"),
    )
    assert [r.status for r in results] == ["delivered", "unsupported"]


def test_healthcheck_succeeds_against_moto(driver) -> None:
    h = driver.healthcheck()
    assert h.ok is True
    assert "reachable" in h.message


def test_healthcheck_surfaces_failures(driver) -> None:
    def boom(**_kwargs):
        raise RuntimeError("network down")

    driver._sns.list_topics = boom
    h = driver.healthcheck()
    assert h.ok is False
    assert "network down" in h.message


def test_register_failure_raises_sns_error(driver) -> None:
    def boom(**_kwargs):
        raise RuntimeError("auth denied")

    driver._sns.create_platform_endpoint = boom
    with pytest.raises(SNSNotificationError, match="create_platform_endpoint"):
        driver.register_device(
            RegisterDeviceRequest(
                user_id="u",
                device_token="t",
                platform=DevicePlatform.IOS,
            ),
        )
