"""Device tokens stay out of the notification drivers' log (#2256).

``driver_op`` logs each call's keyword arguments at INFO on the
``astrolift.providers`` logger. A push target carries the device token (a
webhook target its URL and bearer header), and so do a registration request
and a registration id. Every notification driver redacts them. Each test also
finds the call's own ``driver_op start`` record with the argument redacted,
so a missing token is not a missing record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from _sdk.notification import (
    DevicePlatform,
    NotificationDriver,
    NotificationPayload,
    PushTarget,
    RegisterDeviceRequest,
    WebhookTarget,
)
from aws.notification_sns import SNSNotificationConfig, SNSNotificationDriver
from azure.notification_anh import AzureNotificationHubsConfig, AzureNotificationHubsDriver
from gcp.notification_fcm import FCMConfig, FCMNotificationDriver
from k8s_native import notification_apns
from k8s_native.notification_apns import APNsConfig, APNsNotificationDriver, APNsResponse
from k8s_native.notification_otlp import WebhookSMTPConfig, WebhookSMTPNotificationDriver

if TYPE_CHECKING:
    from collections.abc import Callable

# Hex, so the direct APNs driver sends to it as it would to a real token.
TOKEN = "2256" + "c3" * 30
PAYLOAD = NotificationPayload(title="Deploy failed", body="web on production")
PUSH = PushTarget(registration_id=TOKEN, platform=DevicePlatform.IOS)
CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "register_device": (
        "request",
        {"request": RegisterDeviceRequest(user_id="user-2256", device_token=TOKEN, platform=DevicePlatform.IOS)},
    ),
    "revoke_device": ("registration_id", {"registration_id": TOKEN}),
    "send": ("target", {"target": PUSH, "payload": PAYLOAD}),
    "send_bulk": ("targets", {"targets": [PUSH], "payload": PAYLOAD}),
}


class _Http:
    """Notification Hubs', FCM's and the webhook bridge's transport; every request succeeds."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def put(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.requests.append(("PUT", url, dict(headers)))
        return {}

    def post(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.requests.append(("POST", url, dict(headers)))
        return {"name": "projects/astrolift-mobile/messages/1"}

    def delete(self, *, url: str, headers: dict[str, str]) -> dict[str, Any]:
        self.requests.append(("DELETE", url, dict(headers)))
        return {}


class _Sns:
    def create_platform_endpoint(self, **kwargs: Any) -> dict[str, str]:
        return {"EndpointArn": "arn:aws:sns:us-east-1:000000000000:endpoint/APNS/astrolift/1"}

    def delete_endpoint(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def publish(self, **kwargs: Any) -> dict[str, str]:
        return {"MessageId": "m-1"}


class _Apple:
    def post(self, *, url: str, headers: Any, body: bytes) -> APNsResponse:
        return APNsResponse(200, "apple-1")


def _sns() -> NotificationDriver:
    apps = {DevicePlatform.IOS: "arn:aws:sns:us-east-1:000000000000:app/APNS/astrolift"}
    return SNSNotificationDriver(
        config=SNSNotificationConfig(region="us-east-1", platform_applications=apps), sns_client=_Sns()
    )


def _anh() -> NotificationDriver:
    return AzureNotificationHubsDriver(
        config=AzureNotificationHubsConfig(
            namespace="acme-ns",
            hub_name="acme-hub",
            shared_access_key_name="DefaultFullSharedAccessSignature",
            shared_access_key="c2VjcmV0",
            http_client=_Http(),
        )
    )


def _webhook() -> NotificationDriver:
    return WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(push_webhook_url="https://bridge.example.test/push", http_client=_Http())
    )


def _fcm() -> NotificationDriver:
    return FCMNotificationDriver(
        config=FCMConfig(project_id="astrolift-mobile", access_token="ya29.test", http_client=_Http())
    )


def _apns() -> NotificationDriver:
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return APNsNotificationDriver(
        config=APNsConfig(
            team_id="TEAM123456",
            key_id="KEY1234567",
            topic="com.example.astrolift",
            environment="production",
            auth_key=key.decode("ascii"),
            transport=_Apple(),
        )
    )


DRIVERS: dict[str, Callable[[], NotificationDriver]] = {
    "aws_sns": _sns,
    "azure_anh": _anh,
    "otlp_webhook": _webhook,
    "gcp_fcm": _fcm,
    "apns": _apns,
}


@pytest.fixture(autouse=True)
def _fresh_apns_tokens():
    notification_apns._TOKENS.clear()
    yield
    notification_apns._TOKENS.clear()


def _assert_redacted(caplog: pytest.LogCaptureFixture, method: str, argument: str, secret: str) -> None:
    starts = [
        record
        for record in caplog.records
        if record.getMessage() == "driver_op start" and getattr(record, "method", None) == method
    ]
    assert starts, f"no driver_op start record for {method}"
    assert starts[0].driver_args[argument] == "<redacted>"
    assert all(secret not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.parametrize("method", list(CALLS))
@pytest.mark.parametrize("driver_name", list(DRIVERS))
def test_the_device_token_stays_out_of_the_driver_log(caplog, driver_name: str, method: str) -> None:
    argument, kwargs = CALLS[method]
    driver = DRIVERS[driver_name]()
    with caplog.at_level("INFO", logger="astrolift.providers"):
        getattr(driver, method)(**kwargs)
    _assert_redacted(caplog, method, argument, TOKEN)


def test_a_webhook_targets_bearer_stays_out_of_the_driver_log(caplog) -> None:
    http = _Http()
    driver = WebhookSMTPNotificationDriver(config=WebhookSMTPConfig(http_client=http))
    target = WebhookTarget(url="https://hooks.example.test/notify", headers={"Authorization": f"Bearer {TOKEN}"})
    with caplog.at_level("INFO", logger="astrolift.providers"):
        result = driver.send(target=target, payload=PAYLOAD)
    assert result.status == "delivered"
    [(_method, _url, headers)] = http.requests
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    _assert_redacted(caplog, "send", "target", TOKEN)
