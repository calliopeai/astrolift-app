"""The direct APNs NotificationDriver (#2256), against a recording transport.

Every request is captured whole (URL, headers, body) and every answer is one
Apple documents, so the tests pin the wire shape and how each answer is
treated: only ``410`` prunes, environment and topic mismatches never do, and
retries stay bounded.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from _sdk.notification import (
    DevicePlatform,
    EmailTarget,
    NotificationPayload,
    PushTarget,
    RegisterDeviceRequest,
    SmsTarget,
    WebhookTarget,
)
from k8s_native import notification_apns
from k8s_native.notification_apns import (
    APNsConfig,
    APNsConfigError,
    APNsNotificationDriver,
    APNsResponse,
    APNsTransportError,
)

TOKEN = "a1" * 32
NOW = 1_792_670_400.0
DATA = {
    "payload_version": "1",
    "install_id": "install-0123456789abcdef01234567",
    "organization_id": "0190a0b0-0000-7000-8000-00000000000a",
    "event_kind": "alert.fired",
    "target_kind": "alert_event",
    "target_id": "0190a0b0-0000-7000-8000-0000000000e1",
    "action_url": "astrolift://alerts/0190a0b0-0000-7000-8000-0000000000e1",
}
PAYLOAD = NotificationPayload(
    title="CRITICAL · 5xx surge",
    body="web is firing",
    data=dict(DATA),
    action_url=DATA["action_url"],
    category="alert.fired",
)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _p8(key: ec.EllipticCurvePrivateKey | None = None) -> str:
    key = key or ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


@dataclass
class FakeTransport:
    """Records each request; answers from ``answers`` in order (the last one repeats)."""

    answers: list[APNsResponse | Exception] = field(default_factory=lambda: [APNsResponse(200, "apple-id-1")])
    requests: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)

    def post(self, *, url: str, headers: dict[str, str], body: bytes) -> APNsResponse:
        self.requests.append((url, dict(headers), json.loads(body)))
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


@dataclass
class Clock:
    now: float = NOW
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture(autouse=True)
def _fresh_caches():
    notification_apns._TOKENS.clear()
    notification_apns._TRANSPORTS.clear()
    yield
    notification_apns._TOKENS.clear()
    notification_apns._TRANSPORTS.clear()


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _driver(transport: FakeTransport, clock: Clock, key: ec.EllipticCurvePrivateKey, **overrides: Any):
    config: dict[str, Any] = {
        "team_id": "TEAM123456",
        "key_id": "KEY1234567",
        "topic": "com.example.astrolift",
        "environment": "production",
        "auth_key": _p8(key),
        "transport": transport,
        "clock": clock,
        "sleep": clock.sleep,
        **overrides,
    }
    return APNsNotificationDriver(config=APNsConfig(**config))


def _send(driver: APNsNotificationDriver, payload: NotificationPayload = PAYLOAD, token: str = TOKEN):
    return driver.send(target=PushTarget(registration_id=token, platform=DevicePlatform.IOS), payload=payload)


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _verified_claims(authorization: str, public_key: ec.EllipticCurvePublicKey) -> tuple[dict, dict]:
    scheme, _, jwt = authorization.partition(" ")
    assert scheme == "bearer"
    header, claims, signature = jwt.split(".")
    raw = _b64decode(signature)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public_key.verify(der, f"{header}.{claims}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return json.loads(_b64decode(header)), json.loads(_b64decode(claims))


# ---- wire shape -------------------------------------------------------


def test_ios_send_posts_the_contract_alert_to_the_production_host(clock, key) -> None:
    transport = FakeTransport()
    result = _send(_driver(transport, clock, key))

    assert result.status == "delivered"
    assert result.provider_message_id == "apple-id-1"
    [(url, headers, body)] = transport.requests
    assert url == f"https://api.push.apple.com/3/device/{TOKEN}"
    authorization = headers.pop("authorization")
    notification_id = headers.pop("apns-id")
    assert UUID.fullmatch(notification_id)
    assert headers == {
        "apns-topic": "com.example.astrolift",
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(int(NOW) + 86400),
        # No collapse id was asked for: the notification's own id, so only a
        # retried copy of it can collapse into it.
        "apns-collapse-id": notification_id,
    }
    assert body == {
        **DATA,
        "aps": {
            "alert": {"title": "CRITICAL · 5xx surge", "body": "web is firing"},
            "category": "alert.fired",
            "sound": "default",
        },
    }
    jwt_header, claims = _verified_claims(authorization, key.public_key())
    assert jwt_header == {"alg": "ES256", "kid": "KEY1234567"}
    assert claims == {"iss": "TEAM123456", "iat": int(NOW)}


def test_sandbox_environment_sends_to_the_sandbox_host(clock, key) -> None:
    transport = FakeTransport()
    assert _send(_driver(transport, clock, key, environment="sandbox")).status == "delivered"
    assert transport.requests[0][0] == f"https://api.sandbox.push.apple.com/3/device/{TOKEN}"


def test_badge_and_data_never_replace_aps(clock, key) -> None:
    transport = FakeTransport()
    payload = NotificationPayload(title="t", body="b", data={"aps": "spoofed", "k": "v"}, badge=3)
    _send(_driver(transport, clock, key), payload)
    body = transport.requests[0][2]
    assert body == {"k": "v", "aps": {"alert": {"title": "t", "body": "b"}, "sound": "default", "badge": 3}}


def test_ttl_sets_the_expiration_and_zero_means_one_attempt(clock, key) -> None:
    transport = FakeTransport()
    driver = _driver(transport, clock, key, default_ttl_seconds=600)
    _send(driver)
    _send(driver, NotificationPayload(title="t", body="b", ttl_seconds=60))
    _send(driver, NotificationPayload(title="t", body="b", ttl_seconds=0))
    expirations = [headers["apns-expiration"] for _url, headers, _body in transport.requests]
    assert expirations == [str(int(NOW) + 600), str(int(NOW) + 60), "0"]


def test_collapse_id_comes_from_the_payload_and_long_ids_are_hashed(clock, key) -> None:
    transport = FakeTransport()
    driver = _driver(transport, clock, key)
    _send(driver, NotificationPayload(title="t", body="b", extra={"apns.collapse_id": "deploy-42"}))
    long_id = "x" * 65
    _send(driver, NotificationPayload(title="t", body="b", extra={"apns.collapse_id": long_id}))
    first, second = (headers["apns-collapse-id"] for _url, headers, _body in transport.requests)
    assert first == "deploy-42"
    assert re.fullmatch(r"[0-9a-f]{64}", second)
    assert second != long_id[:64]


def test_payload_over_4096_bytes_is_refused_before_any_request(clock, key) -> None:
    transport = FakeTransport()
    result = _send(_driver(transport, clock, key), NotificationPayload(title="t", body="b", data={"k": "v" * 4100}))
    assert result.status == "failed"
    assert result.retriable is False
    assert "APNs accepts 4096" in result.error
    assert transport.requests == []


def test_a_registration_that_is_not_a_hex_token_is_refused_before_any_request(clock, key) -> None:
    transport = FakeTransport()
    result = _send(_driver(transport, clock, key), token="a1b2/../../3/device/c3?x=1")
    assert result.status == "failed"
    assert result.retriable is False
    assert transport.requests == []


# ---- provider token -----------------------------------------------------


def test_provider_token_is_shared_by_drivers_of_one_key_for_50_minutes(clock, key) -> None:
    transport = FakeTransport()
    pem = _p8(key)
    first = _driver(transport, clock, key, auth_key=pem)
    second = _driver(transport, clock, key, auth_key=pem)
    _send(first)
    clock.now = NOW + 49 * 60
    _send(second)
    clock.now = NOW + 50 * 60
    _send(second)
    tokens = [headers["authorization"] for _url, headers, _body in transport.requests]
    assert tokens[0] == tokens[1] != tokens[2]
    assert _verified_claims(tokens[2], key.public_key())[1]["iat"] == int(NOW + 50 * 60)


def test_a_rotated_key_signs_its_own_token_at_once(clock, key) -> None:
    transport = FakeTransport()
    _send(_driver(transport, clock, key))
    rotated = ec.generate_private_key(ec.SECP256R1())
    _send(_driver(transport, clock, rotated, key_id="KEY7654321"))
    old, new = (headers["authorization"] for _url, headers, _body in transport.requests)
    assert _verified_claims(new, rotated.public_key())[0]["kid"] == "KEY7654321"
    assert old != new


def test_expired_provider_token_is_replaced_once_and_the_send_retried(clock, key) -> None:
    transport = FakeTransport()
    driver = _driver(transport, clock, key)
    _send(driver)  # caches the token at NOW
    transport.requests.clear()
    transport.answers = [APNsResponse(403, reason="ExpiredProviderToken"), APNsResponse(200, "a-2")]
    clock.now = NOW + 30 * 60
    result = _send(driver)

    assert result.status == "delivered"
    first, second = (headers for _url, headers, _body in transport.requests)
    assert first["authorization"] != second["authorization"]
    assert first["apns-id"] == second["apns-id"]
    assert clock.sleeps == []


# ---- answers --------------------------------------------------------------


@pytest.mark.parametrize("reason", ["Unregistered", "ExpiredToken"])
def test_410_is_an_invalid_token_and_is_not_retried(clock, key, reason) -> None:
    transport = FakeTransport(answers=[APNsResponse(410, reason=reason)])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert result.status == "invalid_token"
    assert result.retriable is False
    assert result.error == f"APNs 410 {reason}"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("status", "reason", "hint"),
    [
        (400, "BadDeviceToken", "other APNs environment"),
        (400, "DeviceTokenNotForTopic", "another app"),
        (400, "TopicDisallowed", "may not push"),
        (403, "InvalidProviderToken", "not revoked"),
        (403, "BadEnvironmentKeyIdInToken", "other APNs environment"),
        (403, "Forbidden", ""),
        (400, "BadCollapseId", ""),
    ],
)
def test_configuration_and_environment_mismatches_fail_without_pruning_or_retry(
    clock, key, status, reason, hint
) -> None:
    transport = FakeTransport(answers=[APNsResponse(status, reason=reason)])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert result.status == "failed"
    assert result.retriable is False
    assert result.error.startswith(f"APNs {status} {reason}")
    assert hint in result.error
    assert len(transport.requests) == 1


def test_too_many_requests_is_rate_limited(clock, key) -> None:
    transport = FakeTransport(answers=[APNsResponse(429, reason="TooManyRequests")])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert (result.status, result.retriable, len(transport.requests)) == ("rate_limited", True, 1)


def test_too_many_provider_token_updates_fails_retriable_without_a_new_token(clock, key) -> None:
    transport = FakeTransport(answers=[APNsResponse(429, reason="TooManyProviderTokenUpdates")])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert (result.status, result.retriable, len(transport.requests)) == ("failed", True, 1)


@pytest.mark.parametrize(
    ("status", "reason"), [(500, "InternalServerError"), (503, "ServiceUnavailable"), (503, "Shutdown")]
)
def test_server_errors_are_retriable_but_not_sent_again_at_once(clock, key, status, reason) -> None:
    # Apple: retry a 5xx after 15 minutes. That is the caller's schedule.
    transport = FakeTransport(answers=[APNsResponse(status, reason=reason), APNsResponse(200, "a-9")])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert (result.status, result.retriable, result.error) == ("failed", True, f"APNs {status} {reason}")
    assert len(transport.requests) == 1
    assert clock.sleeps == []


def test_a_send_that_got_no_answer_is_sent_again_with_the_same_notification_id(clock, key) -> None:
    transport = FakeTransport(answers=[APNsTransportError("ReadTimeout"), APNsResponse(200, "a-9")])
    result = _send(_driver(transport, clock, key))
    assert (result.status, result.provider_message_id) == ("delivered", "a-9")
    first, second = (headers for _url, headers, _body in transport.requests)
    # If Apple did take the first, the second replaces it on the device.
    assert first["apns-id"] == second["apns-id"] == second["apns-collapse-id"]
    assert clock.sleeps == [0.5]


def test_resends_stop_at_max_attempts(clock, key) -> None:
    transport = FakeTransport(answers=[APNsTransportError("ConnectError")])
    result = _send(_driver(transport, clock, key, max_attempts=3))
    assert (result.status, result.retriable) == ("failed", True)
    assert len(transport.requests) == 3
    assert clock.sleeps == [0.5, 1.0]


def test_transport_errors_are_retried_and_reported_without_the_device_token(clock, key) -> None:
    transport = FakeTransport(answers=[APNsTransportError("ConnectTimeout")])
    result = _send(_driver(transport, clock, key))
    assert (result.status, result.retriable) == ("failed", True)
    assert result.error == "APNs unreachable: ConnectTimeout"
    assert TOKEN not in result.error
    assert len(transport.requests) == 2


def test_an_unexpected_reason_is_not_echoed(clock, key) -> None:
    transport = FakeTransport(answers=[APNsResponse(400, reason=f"<{TOKEN}>")])
    result = _send(_driver(transport, clock, key))
    assert result.error == "APNs 400 unknown"


# ---- channels and platforms -----------------------------------------------


@pytest.mark.parametrize("platform", [DevicePlatform.ANDROID, DevicePlatform.WEB])
def test_non_apple_platforms_are_unsupported(clock, key, platform) -> None:
    transport = FakeTransport()
    result = _driver(transport, clock, key).send(
        target=PushTarget(registration_id=TOKEN, platform=platform), payload=PAYLOAD
    )
    assert result.status == "unsupported"
    assert transport.requests == []


@pytest.mark.parametrize(
    "target",
    [EmailTarget(to="a@example.test"), SmsTarget(to="+15550000000"), WebhookTarget(url="https://example.test")],
)
def test_email_sms_and_webhook_are_unsupported(clock, key, target) -> None:
    assert _driver(FakeTransport(), clock, key).send(target=target, payload=PAYLOAD).status == "unsupported"


def test_register_echoes_the_token_and_stamps_topic_and_environment(clock, key) -> None:
    registration = _driver(FakeTransport(), clock, key, environment="sandbox").register_device(
        RegisterDeviceRequest(user_id="7", device_token=TOKEN, platform=DevicePlatform.IOS)
    )
    assert registration.registration_id == registration.device_token == TOKEN
    assert registration.provider_metadata == {"apns_topic": "com.example.astrolift", "apns_environment": "sandbox"}


def test_healthcheck_signs_a_provider_token(clock, key) -> None:
    health = _driver(FakeTransport(), clock, key).healthcheck()
    assert health.ok is True
    assert health.message == "apns production com.example.astrolift: provider token signed with key KEY1234567"


# ---- configuration --------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"team_id": "team123456"}, "team_id"),
        ({"key_id": "KEY12345"}, "key_id"),
        ({"topic": "astrolift"}, "bundle ID"),
        ({"topic": "com.example/app"}, "bundle ID"),
        ({"environment": "development"}, "production or sandbox"),
        ({"max_attempts": 4}, "max_attempts"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"default_ttl_seconds": -1}, "default_ttl_seconds"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
    ],
)
def test_config_is_validated(clock, key, overrides, message) -> None:
    with pytest.raises(APNsConfigError, match=message):
        _driver(FakeTransport(), clock, key, **overrides)


def test_auth_key_must_be_a_p256_pkcs8_key_and_errors_never_echo_it(clock, key) -> None:
    rsa_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode("ascii")
    )
    p384 = _p8(ec.generate_private_key(ec.SECP384R1()))
    for pem, message in ((rsa_pem, "P-256"), (p384, "P-256"), ("not a key", "PKCS#8")):
        with pytest.raises(APNsConfigError, match=message) as raised:
            _driver(FakeTransport(), clock, key, auth_key=pem)
        assert pem not in str(raised.value)
    assert "auth_key" not in repr(APNsConfig("TEAM123456", "KEY1234567", "com.example.app", "sandbox", _p8(key)))


# ---- the HTTP/2 transport ---------------------------------------------------


def test_the_default_transport_is_one_http2_client_per_key(monkeypatch, clock, key) -> None:
    created: list[dict[str, Any]] = []

    class RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(httpx, "Client", RecordingClient)
    pem = _p8(key)
    config = {"team_id": "TEAM123456", "topic": "com.example.astrolift", "environment": "production"}
    first = APNsNotificationDriver(config=APNsConfig(key_id="KEY1234567", auth_key=pem, **config))
    again = APNsNotificationDriver(config=APNsConfig(key_id="KEY1234567", auth_key=pem, **config))
    other = APNsNotificationDriver(config=APNsConfig(key_id="KEY7654321", auth_key=pem, **config))
    assert first._transport is again._transport is not other._transport
    assert created == [{"http2": True, "timeout": 10.0}, {"http2": True, "timeout": 10.0}]


def test_the_default_transport_reads_status_apns_id_and_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gone"):
            return httpx.Response(410, json={"reason": "Unregistered", "timestamp": 1})
        if request.url.path.endswith("/down"):
            raise httpx.ConnectError("connection refused", request=request)
        assert request.headers["apns-topic"] == "com.example.astrolift"
        return httpx.Response(200, headers={"apns-id": "apple-id-7"})

    transport = notification_apns._HttpxTransport(timeout=1)
    transport._client.close()
    transport._client = httpx.Client(transport=httpx.MockTransport(handler))
    headers = {"apns-topic": "com.example.astrolift"}
    assert transport.post(url="https://api.push.apple.com/3/device/ok", headers=headers, body=b"{}") == APNsResponse(
        200, "apple-id-7", ""
    )
    assert transport.post(url="https://api.push.apple.com/3/device/gone", headers=headers, body=b"{}") == APNsResponse(
        410, "", "Unregistered"
    )
    with pytest.raises(APNsTransportError) as raised:
        transport.post(url="https://api.push.apple.com/3/device/down", headers=headers, body=b"{}")
    assert str(raised.value) == "ConnectError"


def test_the_device_token_stays_out_of_the_httpx_request_log(caplog) -> None:
    # httpx logs every request URL at INFO, and an APNs URL's path is the
    # device token. Other hosts' URLs are left as they are.
    transport = notification_apns._HttpxTransport(timeout=1)
    transport._client.close()
    transport._client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with caplog.at_level("INFO", logger="httpx"):
        transport.post(url=f"https://api.sandbox.push.apple.com/3/device/{TOKEN}", headers={}, body=b"{}")
        transport._client.get("https://example.test/3/device/kept")

    assert [record.getMessage() for record in caplog.records if record.name == "httpx"] == [
        'HTTP Request: POST https://api.sandbox.push.apple.com/3/device/[redacted] "HTTP/1.1 200 OK"',
        'HTTP Request: GET https://example.test/3/device/kept "HTTP/1.1 200 OK"',
    ]


def test_the_device_token_stays_out_of_the_driver_log(clock, key, caplog) -> None:
    transport = FakeTransport()
    with caplog.at_level("INFO", logger="astrolift.providers"):
        _driver(transport, clock, key).send_bulk(
            targets=[PushTarget(registration_id=TOKEN, platform=DevicePlatform.IOS)], payload=PAYLOAD
        )
    assert len(transport.requests) == 1
    assert any(record.getMessage() == "driver_op start" for record in caplog.records)
    assert all(TOKEN not in repr(record.__dict__) for record in caplog.records)
