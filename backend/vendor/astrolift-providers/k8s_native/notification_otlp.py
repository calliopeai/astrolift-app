"""k8s_native NotificationDriver (#490) -- webhook fan-out + SMTP.

This driver is the BYO-delivery path for installs that don't sit
on a public cloud or want to bring their own delivery service
(Pushover, ntfy.sh, opsgenie, custom on-call paging). Two
channels:

* **Webhook** -- POST the notification envelope as JSON to a
  configurable URL with optional bearer token. Drops in front
  of any of the popular cross-platform push-bridge services.
* **Email (SMTP)** -- direct SMTP send via the stdlib
  ``smtplib`` module. Bring-your-own MTA (Postfix in the
  cluster, MailHog for dev, an external relay like SendGrid /
  Mailgun / Postmark SMTP, AWS SES SMTP).

For push to mobile this driver returns ``unsupported`` for the
``PushTarget`` shape -- users wanting native push on k8s_native
must add a webhook bridge (ntfy.sh + a mobile companion app, or
a custom relay that holds APNs/FCM keys) and route through the
WebhookTarget code path. That's by design: we don't bake APNs
/ FCM keys into the platform's k8s_native plugin because the
operator's identity owns those secrets.

Configuration:

* ``default_webhook_url`` is the fallback when a target doesn't
  carry its own URL -- useful for installs that pipe everything
  to one bridge.
* ``smtp_host`` + ``smtp_port`` + creds drive the email path.
  Empty disables email (sends return ``unsupported``).
"""

from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from _sdk.notification import (
    DeliveryTarget,
    DeviceRegistration,
    EmailTarget,
    NotificationDriver,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
    SmsTarget,
    WebhookTarget,
)


@dataclass(frozen=True)
class WebhookSMTPConfig:
    default_webhook_url: str = ""
    """Fallback URL used when a WebhookTarget doesn't carry its
    own. Empty disables the implicit fan-out -- targets must
    supply a URL explicitly."""

    default_webhook_bearer: str = ""
    """Optional bearer token for outbound webhook auth."""

    push_webhook_url: str = ""
    """Bridge URL for PushTarget delivery. When set, PushTarget
    sends POST to this URL with the registration metadata + the
    payload, letting the operator's bridge mint APNs/FCM tokens
    out-of-platform. Empty = unsupported for push."""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    timeout_seconds: int = 10

    http_client: Any | None = None
    smtp_factory: Any | None = None
    """Test injection point -- a callable returning an
    smtplib.SMTP-compatible object. When ``None`` the driver uses
    ``smtplib.SMTP`` directly."""

    extra_headers: dict[str, str] = field(default_factory=dict)


class WebhookSMTPNotificationDriver(NotificationDriver):
    name = "otlp_webhook"

    def __init__(self, *, config: WebhookSMTPConfig) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultWebhookHttp(
                timeout=config.timeout_seconds,
            )

    # ---- registration ----------------------------------------------

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        # Generic registration: persist the (user, token) pair and
        # echo it back. The operator-side bridge translates the
        # token into the upstream provider's identifier on send.
        return DeviceRegistration(
            registration_id=request.device_token,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata=dict(request.provider_metadata),
        )

    def revoke_device(self, *, registration_id: str) -> None:
        # Best-effort: ping the bridge so it can drop the device.
        if not self._config.push_webhook_url or not registration_id:
            return
        try:
            self._http.post(
                url=self._config.push_webhook_url,
                headers=self._headers(),
                body=json.dumps(
                    {
                        "op": "revoke",
                        "registration_id": registration_id,
                    },
                    separators=(",", ":"),
                ),
            )
        except _WebhookHttpError:
            return

    # ---- send ------------------------------------------------------

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if isinstance(target, WebhookTarget):
            return self._send_webhook(target=target, payload=payload)
        if isinstance(target, EmailTarget):
            return self._send_email(target=target, payload=payload)
        if isinstance(target, PushTarget):
            return self._send_push_via_bridge(
                target=target,
                payload=payload,
            )
        if isinstance(target, SmsTarget):
            # SMS over SMTP isn't a thing; operators wire a webhook
            # bridge instead.
            return SendResult(
                target=target,
                status="unsupported",
                error=("k8s_native webhook+smtp driver has no SMS path; route SmsTarget through a cloud-native driver"),
            )
        return SendResult(
            target=target,
            status="unsupported",
            error=f"unknown target type {type(target).__name__}",
        )

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        # No remote ping -- consider healthy when at least one
        # channel is configured.
        configured_channels: list[str] = []
        if self._config.default_webhook_url or self._config.push_webhook_url:
            configured_channels.append("webhook")
        if self._config.smtp_host:
            configured_channels.append("smtp")
        if not configured_channels:
            return ProviderHealth(
                ok=False,
                message="no channels configured (webhook + smtp empty)",
                checked_at=datetime.now(UTC).isoformat(),
            )
        return ProviderHealth(
            ok=True,
            message=f"channels: {','.join(configured_channels)}",
            checked_at=datetime.now(UTC).isoformat(),
        )

    # ---- internals -------------------------------------------------

    def _send_webhook(
        self,
        *,
        target: WebhookTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        url = target.url or self._config.default_webhook_url
        if not url:
            return SendResult(
                target=target,
                status="failed",
                error="webhook url empty and no default configured",
                retriable=False,
            )
        envelope = {
            "title": payload.title,
            "body": payload.body,
            "data": payload.data,
            **({"action_url": payload.action_url} if payload.action_url else {}),
        }
        headers = self._headers()
        headers.update(target.headers)
        try:
            self._http.post(
                url=url,
                headers=headers,
                body=json.dumps(envelope, separators=(",", ":")),
            )
        except _WebhookHttpError as exc:
            status = "failed"
            retriable = exc.status_code in (0, 429, 500, 502, 503, 504)
            if exc.status_code == 429:
                status = "rate_limited"
            return SendResult(
                target=target,
                status=status,
                error=str(exc),
                retriable=retriable,
            )
        return SendResult(target=target, status="delivered")

    def _send_email(
        self,
        *,
        target: EmailTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if not self._config.smtp_host:
            return SendResult(
                target=target,
                status="unsupported",
                error="smtp not configured on this driver",
            )
        msg = EmailMessage()
        msg["From"] = self._config.smtp_from or "notifications@example.invalid"
        msg["To"] = target.to
        msg["Subject"] = target.subject or payload.title or "(no subject)"
        if target.body_text:
            msg.set_content(target.body_text)
        else:
            msg.set_content(payload.body or payload.title)
        if target.body_html:
            msg.add_alternative(target.body_html, subtype="html")
        try:
            smtp_factory = self._config.smtp_factory or smtplib.SMTP
            with smtp_factory(
                self._config.smtp_host,
                self._config.smtp_port,
                timeout=self._config.timeout_seconds,
            ) as smtp:
                if self._config.smtp_use_tls:
                    smtp.starttls()
                if self._config.smtp_username:
                    smtp.login(
                        self._config.smtp_username,
                        self._config.smtp_password,
                    )
                smtp.send_message(msg)
        except Exception as exc:
            return SendResult(
                target=target,
                status="failed",
                error=str(exc),
                retriable=True,
            )
        return SendResult(target=target, status="delivered")

    def _send_push_via_bridge(
        self,
        *,
        target: PushTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if not self._config.push_webhook_url:
            return SendResult(
                target=target,
                status="unsupported",
                error=(
                    "k8s_native webhook driver has no push bridge "
                    "configured; set push_webhook_url to route push "
                    "through your bridge service"
                ),
            )
        envelope = {
            "op": "send_push",
            "registration_id": target.registration_id,
            "platform": target.platform.value,
            "payload": {
                "title": payload.title,
                "body": payload.body,
                "data": payload.data,
                **({"action_url": payload.action_url} if payload.action_url else {}),
                **({"badge": payload.badge} if payload.badge is not None else {}),
            },
        }
        try:
            self._http.post(
                url=self._config.push_webhook_url,
                headers=self._headers(),
                body=json.dumps(envelope, separators=(",", ":")),
            )
        except _WebhookHttpError as exc:
            return SendResult(
                target=target,
                status="failed",
                error=str(exc),
                retriable=exc.status_code in (0, 429, 500, 502, 503, 504),
            )
        return SendResult(target=target, status="queued")

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "astrolift-notifications/1.0",
        }
        headers.update(self._config.extra_headers)
        if self._config.default_webhook_bearer:
            headers["Authorization"] = f"Bearer {self._config.default_webhook_bearer}"
        return headers


@dataclass(frozen=True)
class _WebhookHttpError(Exception):
    message: str
    status_code: int

    def __str__(self) -> str:
        return f"webhook http {self.status_code}: {self.message}"


class _DefaultWebhookHttp:
    def __init__(self, *, timeout: int) -> None:
        self._timeout = timeout

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            method="POST",
        )
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout,
            ) as raw:
                response_body = raw.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _WebhookHttpError(
                message=str(exc),
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise _WebhookHttpError(
                message=str(exc),
                status_code=0,
            ) from exc
        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return {"raw": response_body}
