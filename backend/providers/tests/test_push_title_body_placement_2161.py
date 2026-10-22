"""Where each push driver puts the title, body and data map (#2161).

The mobile app shows the platform's alert title and body and routes by the
flat string ``data`` map (``docs/mobile-push-contract.md``), so each driver
must put them where its provider documents them:

* FCM HTTP v1 (``gcp_fcm``): ``message.notification``, for iOS also
  ``message.apns.payload.aps.alert``; data in ``message.data``.
* SNS (``aws_sns``): the ``APNS`` document's ``aps.alert`` with the data keys
  beside ``aps``; the legacy ``GCM`` document's ``notification`` and
  ``data``, which SNS maps onto an FCM v1 message (its channel key is
  ``android_channel_id``).
* Notification Hubs (``azure_anh``): APNs JSON for the ``apple`` format; for
  ``fcmv1`` the FCM v1 message under ``message``.
* The webhook push bridge (``otlp_webhook``): the relay envelope's
  ``payload``.
* Direct APNs (``apns``, #2256): ``aps.alert`` with the data keys beside
  ``aps``, as SNS and Notification Hubs send it.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from _sdk.notification import DevicePlatform, NotificationPayload, PushTarget
from aws.notification_sns import SNSNotificationConfig, SNSNotificationDriver
from azure.notification_anh import AzureNotificationHubsConfig, AzureNotificationHubsDriver
from gcp.notification_fcm import FCMConfig, FCMNotificationDriver
from k8s_native.notification_apns import APNsConfig, APNsNotificationDriver, APNsResponse
from k8s_native.notification_otlp import WebhookSMTPConfig, WebhookSMTPNotificationDriver

ALERT_URL = "astrolift://alerts/0190a0b0-0000-7000-8000-0000000000e1"
DATA = {
    "payload_version": "1",
    "install_id": "install-0123456789abcdef01234567",
    "organization_id": "0190a0b0-0000-7000-8000-00000000000a",
    "event_kind": "alert.fired",
    "target_kind": "alert_event",
    "target_id": "0190a0b0-0000-7000-8000-0000000000e1",
    "action_url": ALERT_URL,
}
PAYLOAD = NotificationPayload(
    title="CRITICAL · 5xx surge",
    body="web is firing",
    data=dict(DATA),
    action_url=ALERT_URL,
    category="alert.fired",
)
ALERT = {"title": "CRITICAL · 5xx surge", "body": "web is firing"}


class _RecordingHttp:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.posts: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self._response = response or {}

    def post(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.posts.append((url, dict(headers), json.loads(body)))
        return self._response


class _RecordingSns:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, str]:
        self.published.append(kwargs)
        return {"MessageId": "m-1"}


def _send(driver: Any, platform: DevicePlatform, registration_id: str = "reg-1") -> str:
    target = PushTarget(registration_id=registration_id, platform=platform)
    return str(driver.send(target=target, payload=PAYLOAD).status)


def _fcm(platform: DevicePlatform) -> dict[str, Any]:
    http = _RecordingHttp({"name": "projects/p/messages/1"})
    driver = FCMNotificationDriver(config=FCMConfig(project_id="p", access_token="t", http_client=http))
    assert _send(driver, platform) == "delivered"
    [(_url, _headers, body)] = http.posts
    return dict(body["message"])


def _sns(platform: DevicePlatform) -> dict[str, Any]:
    client = _RecordingSns()
    driver = SNSNotificationDriver(config=SNSNotificationConfig(region="us-east-1"), sns_client=client)
    arn = "arn:aws:sns:us-east-1:000000000000:endpoint/APNS/astrolift/1"
    assert _send(driver, platform, arn) == "delivered"
    [published] = client.published
    assert published["MessageStructure"] == "json"
    return dict(json.loads(published["Message"]))


def _anh(platform: DevicePlatform) -> tuple[dict[str, str], dict[str, Any]]:
    http = _RecordingHttp({"notificationId": "n-1"})
    driver = AzureNotificationHubsDriver(
        config=AzureNotificationHubsConfig(
            namespace="ns",
            hub_name="hub",
            shared_access_key_name="DefaultFullSharedAccessSignature",
            shared_access_key="c2VjcmV0",
            http_client=http,
        )
    )
    assert _send(driver, platform) == "queued"
    [(_url, headers, body)] = http.posts
    return headers, body


def test_fcm_android_puts_title_and_body_in_the_notification_and_routes_by_data() -> None:
    message = _fcm(DevicePlatform.ANDROID)
    assert message["token"] == "reg-1"
    assert message["notification"] == ALERT
    assert message["data"] == DATA
    assert message["android"] == {"notification": {"channel_id": "alert.fired"}}
    assert "apns" not in message


def test_fcm_ios_puts_title_and_body_in_the_aps_alert_too() -> None:
    message = _fcm(DevicePlatform.IOS)
    assert message["notification"] == ALERT
    assert message["apns"]["payload"]["aps"]["alert"] == ALERT
    assert message["apns"]["payload"]["aps"]["category"] == "alert.fired"
    # FCM hands ``data`` to APNs as custom keys beside ``aps``.
    assert message["data"] == DATA


def test_fcm_web_puts_title_and_body_in_the_webpush_notification() -> None:
    message = _fcm(DevicePlatform.WEB)
    assert message["notification"] == ALERT
    assert {key: message["webpush"]["notification"][key] for key in ALERT} == ALERT
    assert message["data"] == DATA


def test_sns_ios_puts_title_and_body_in_the_aps_alert_with_data_beside_it() -> None:
    envelope = _sns(DevicePlatform.IOS)
    assert envelope["default"] == "web is firing"
    assert envelope["APNS_SANDBOX"] == envelope["APNS"]
    apns = json.loads(envelope["APNS"])
    assert apns["aps"]["alert"] == ALERT
    assert apns["aps"]["category"] == "alert.fired"
    assert {key: value for key, value in apns.items() if key != "aps"} == DATA


def test_sns_android_uses_the_legacy_fcm_keys_sns_maps_to_fcm_v1() -> None:
    envelope = _sns(DevicePlatform.ANDROID)
    gcm = json.loads(envelope["GCM"])
    assert gcm == {
        "notification": {**ALERT, "android_channel_id": "alert.fired"},
        "data": DATA,
    }


def test_anh_apple_puts_title_and_body_in_the_aps_alert_with_data_beside_it() -> None:
    headers, body = _anh(DevicePlatform.IOS)
    assert headers["ServiceBusNotification-Format"] == "apple"
    assert headers["ServiceBusNotification-Tags"] == "installationId:{reg-1}"
    assert body["aps"]["alert"] == ALERT
    assert body["aps"]["category"] == "alert.fired"
    assert {key: value for key, value in body.items() if key != "aps"} == DATA


def test_anh_fcmv1_sends_the_fcm_v1_message_envelope() -> None:
    headers, body = _anh(DevicePlatform.ANDROID)
    assert headers["ServiceBusNotification-Format"] == "fcmv1"
    assert body == {
        "message": {
            "notification": ALERT,
            "data": DATA,
            "android": {"notification": {"channel_id": "alert.fired"}},
        }
    }


def test_apns_puts_title_and_body_in_the_aps_alert_with_data_beside_it() -> None:
    requests: list[dict[str, Any]] = []

    class Transport:
        def post(self, *, url: str, headers: dict[str, str], body: bytes) -> APNsResponse:
            requests.append(json.loads(body))
            return APNsResponse(200, "apple-id-1")

    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    driver = APNsNotificationDriver(
        config=APNsConfig(
            team_id="TEAM123456",
            key_id="KEY1234567",
            topic="com.example.astrolift",
            environment="production",
            auth_key=pem.decode("ascii"),
            transport=Transport(),
        )
    )
    assert _send(driver, DevicePlatform.IOS, "ab" * 32) == "delivered"
    [body] = requests
    assert body["aps"] == {"alert": ALERT, "category": "alert.fired", "sound": "default"}
    assert {key: value for key, value in body.items() if key != "aps"} == DATA


def test_webhook_bridge_relays_title_body_and_data_unchanged() -> None:
    http = _RecordingHttp()
    driver = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(push_webhook_url="https://bridge.example.test/push", http_client=http)
    )
    assert _send(driver, DevicePlatform.ANDROID) == "queued"
    [(_url, _headers, envelope)] = http.posts
    assert envelope["op"] == "send_push"
    assert envelope["registration_id"] == "reg-1"
    assert envelope["payload"] == {**ALERT, "data": DATA, "action_url": ALERT_URL}
