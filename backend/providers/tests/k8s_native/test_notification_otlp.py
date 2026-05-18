"""Tests for the k8s_native webhook + SMTP NotificationDriver (#490)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from email.message import EmailMessage

from _sdk.notification import (
    DevicePlatform,
    EmailTarget,
    NotificationPayload,
    PushTarget,
    RegisterDeviceRequest,
    SmsTarget,
    WebhookTarget,
)
from k8s_native.notification_otlp import (
    WebhookSMTPConfig,
    WebhookSMTPNotificationDriver,
    _WebhookHttpError,
)


@dataclass
class FakeWebhookHttp:
    canned_error: _WebhookHttpError | None = None
    calls: list[tuple[str, dict[str, str], dict[str, Any]]] = field(
        default_factory=list,
    )

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        self.calls.append((url, dict(headers), json.loads(body)))
        if self.canned_error is not None:
            raise self.canned_error
        return {}


@dataclass
class FakeSmtp:
    """Stub matching the relevant subset of ``smtplib.SMTP``."""

    host: str
    port: int
    timeout: int
    sent: list[EmailMessage] = field(default_factory=list)
    starttls_called: bool = False
    login_credentials: tuple[str, str] | None = None
    raise_on_send: Exception | None = None

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, user: str, password: str) -> None:
        self.login_credentials = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(msg)


_smtp_instances: list[FakeSmtp] = []


def _smtp_factory(host: str, port: int, timeout: int) -> FakeSmtp:
    instance = FakeSmtp(host=host, port=port, timeout=timeout)
    _smtp_instances.append(instance)
    return instance


@pytest.fixture(autouse=True)
def _clear_smtp_instances() -> None:
    _smtp_instances.clear()


@pytest.fixture
def fake_http() -> FakeWebhookHttp:
    return FakeWebhookHttp()


@pytest.fixture
def driver(fake_http: FakeWebhookHttp) -> WebhookSMTPNotificationDriver:
    return WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(
            default_webhook_url="https://default.webhook/in",
            default_webhook_bearer="bearer-token",
            push_webhook_url="https://push.bridge/in",
            smtp_host="smtp.example.test",
            smtp_port=587,
            smtp_username="ops",
            smtp_password="hunter2",
            smtp_from="notifications@astrolift.test",
            http_client=fake_http,
            smtp_factory=_smtp_factory,
        ),
    )


# ---- registration ---------------------------------------------------


def test_register_persists_token_as_registration_id(driver) -> None:
    reg = driver.register_device(
        RegisterDeviceRequest(
            user_id="u",
            device_token="tok",
            platform=DevicePlatform.IOS,
        ),
    )
    assert reg.registration_id == "tok"
    assert reg.user_id == "u"


def test_revoke_calls_push_bridge_when_configured(driver, fake_http) -> None:
    driver.revoke_device(registration_id="abc")
    assert fake_http.calls
    url, _, body = fake_http.calls[0]
    assert url == "https://push.bridge/in"
    assert body == {"op": "revoke", "registration_id": "abc"}


def test_revoke_without_bridge_is_noop(fake_http) -> None:
    drv = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(http_client=fake_http),
    )
    drv.revoke_device(registration_id="abc")
    assert fake_http.calls == []


# ---- webhook send ---------------------------------------------------


def test_send_webhook_uses_target_url(driver, fake_http) -> None:
    result = driver.send(
        target=WebhookTarget(url="https://specific.test/in"),
        payload=NotificationPayload(
            title="t",
            body="b",
            data={"k": "v"},
            action_url="https://x.test/y",
        ),
    )
    assert result.status == "delivered"
    url, headers, body = fake_http.calls[0]
    assert url == "https://specific.test/in"
    assert headers["Authorization"] == "Bearer bearer-token"
    assert body["title"] == "t"
    assert body["data"]["k"] == "v"
    assert body["action_url"] == "https://x.test/y"


def test_send_webhook_falls_back_to_default_url(driver, fake_http) -> None:
    driver.send(
        target=WebhookTarget(url=""),
        payload=NotificationPayload(title="t", body="b"),
    )
    url, _, _ = fake_http.calls[0]
    assert url == "https://default.webhook/in"


def test_send_webhook_no_url_fails(fake_http) -> None:
    drv = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(http_client=fake_http),
    )
    result = drv.send(
        target=WebhookTarget(url=""),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert "empty" in result.error


def test_send_webhook_429_is_rate_limited(driver, fake_http) -> None:
    fake_http.canned_error = _WebhookHttpError(
        message="throttled",
        status_code=429,
    )
    result = driver.send(
        target=WebhookTarget(url="https://x.test/y"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "rate_limited"
    assert result.retriable is True


def test_send_webhook_500_is_retriable_failure(driver, fake_http) -> None:
    fake_http.canned_error = _WebhookHttpError(
        message="bad gateway",
        status_code=502,
    )
    result = driver.send(
        target=WebhookTarget(url="https://x.test/y"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert result.retriable is True


def test_send_webhook_400_is_non_retriable_failure(
    driver,
    fake_http,
) -> None:
    fake_http.canned_error = _WebhookHttpError(
        message="bad request",
        status_code=400,
    )
    result = driver.send(
        target=WebhookTarget(url="https://x.test/y"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert result.retriable is False


# ---- email (smtp) ---------------------------------------------------


def test_send_email_routes_through_smtp(driver) -> None:
    result = driver.send(
        target=EmailTarget(
            to="user@example.com",
            subject="Test",
            body_text="hello there",
            body_html="<p>hello there</p>",
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "delivered"
    assert _smtp_instances
    smtp = _smtp_instances[0]
    assert smtp.starttls_called is True
    assert smtp.login_credentials == ("ops", "hunter2")
    sent = smtp.sent[0]
    assert sent["To"] == "user@example.com"
    assert sent["Subject"] == "Test"


def test_send_email_without_smtp_returns_unsupported(fake_http) -> None:
    drv = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(http_client=fake_http),
    )
    result = drv.send(
        target=EmailTarget(to="x@y.test"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_send_email_failure_returns_failed(driver) -> None:
    def factory(host: str, port: int, timeout: int) -> FakeSmtp:
        instance = FakeSmtp(host=host, port=port, timeout=timeout)
        instance.raise_on_send = RuntimeError("smtp down")
        _smtp_instances.append(instance)
        return instance

    driver._config = WebhookSMTPConfig(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_from="x@y.test",
        smtp_factory=factory,
        http_client=driver._http,
    )
    result = driver.send(
        target=EmailTarget(to="user@example.com"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "failed"
    assert "smtp down" in result.error


# ---- push via bridge ------------------------------------------------


def test_send_push_routes_through_bridge(driver, fake_http) -> None:
    result = driver.send(
        target=PushTarget(
            registration_id="tok-1",
            platform=DevicePlatform.ANDROID,
        ),
        payload=NotificationPayload(
            title="t",
            body="b",
            data={"k": "v"},
        ),
    )
    assert result.status == "queued"
    url, _, body = fake_http.calls[0]
    assert url == "https://push.bridge/in"
    assert body["op"] == "send_push"
    assert body["registration_id"] == "tok-1"
    assert body["payload"]["data"]["k"] == "v"


def test_send_push_without_bridge_returns_unsupported(fake_http) -> None:
    drv = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(http_client=fake_http),
    )
    result = drv.send(
        target=PushTarget(
            registration_id="t",
            platform=DevicePlatform.IOS,
        ),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


# ---- sms / health ---------------------------------------------------


def test_send_sms_returns_unsupported(driver) -> None:
    result = driver.send(
        target=SmsTarget(to="+15555550100", message="hi"),
        payload=NotificationPayload(title="t", body="b"),
    )
    assert result.status == "unsupported"


def test_healthcheck_ok_when_any_channel_configured(driver) -> None:
    health = driver.healthcheck()
    assert health.ok is True
    assert "webhook" in health.message
    assert "smtp" in health.message


def test_healthcheck_fails_when_no_channel_configured(fake_http) -> None:
    drv = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(http_client=fake_http),
    )
    health = drv.healthcheck()
    assert health.ok is False
