"""GCP Firebase Cloud Messaging NotificationDriver (#490).

FCM HTTP v1 is the canonical mobile-push surface for GCP and
the primary delivery path for Android. iOS routes through FCM
too -- FCM dispatches to APNs on the operator's behalf via the
APNs Auth Key the operator uploads to the Firebase console.

The driver authenticates with a Google service-account access
token. Tokens are minted out-of-band by ``GCPCredentialProvider``
(the same chain GCP's other drivers use); the driver accepts a
pre-minted token or a token-callable so tests don't need real
Google auth.

FCM HTTP v1 endpoint shape:

    POST https://fcm.googleapis.com/v1/projects/{project}/messages:send
    Authorization: Bearer {access_token}
    Content-Type: application/json

    { "message": {
        "token": "<device_token>",
        "notification": { "title": ..., "body": ... },
        "data": { ... },
        "android": { ... },
        "apns": { ... },
        "webpush": { ... }
    } }

The driver returns ``invalid_token`` when FCM responds with
``UNREGISTERED`` / ``INVALID_ARGUMENT`` on the token field. The
dispatcher uses that to mark the device row revoked.

Channel coverage in this driver:

* **Push** -- full implementation (the primary purpose of FCM).
* **Email** -- ``unsupported``; the dispatcher should route to a
  separate email driver (e.g. SendGrid / Mailgun via a webhook
  driver), as GCP does not ship a first-party transactional
  email service.
* **SMS** -- ``unsupported``; route via Twilio through a webhook
  driver. GCP does not ship a first-party SMS service.
* **Webhook** -- ``unsupported``; same rationale -- this driver
  is the FCM channel.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from _sdk._telemetry import driver_op
from _sdk.notification import (
    DeliveryTarget,
    DevicePlatform,
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

_FCM_ENDPOINT_TMPL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


@dataclass(frozen=True)
class FCMConfig:
    project_id: str
    """GCP project ID hosting the Firebase project. Required.
    Used to assemble the HTTP v1 endpoint URL."""

    token_provider: Callable[[], str] | None = None
    """Callable that returns a valid Bearer access token. The
    driver invokes this on every send -- it must do its own caching
    to amortize the Google IAM token mint. When ``None`` the
    driver falls back to a literal in ``access_token``."""

    access_token: str = ""
    """Pre-minted Bearer token. Only used when ``token_provider``
    is ``None``. Tests use this path."""

    timeout_seconds: int = 10
    http_client: Any | None = None


class FCMNotificationDriver(NotificationDriver):
    """Firebase Cloud Messaging implementation."""

    name = "gcp_fcm"

    def __init__(self, *, config: FCMConfig) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultFCMHttp(timeout=config.timeout_seconds)

    # ---- registration ----------------------------------------------

    @driver_op(cloud="gcp", driver="notification")
    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        # FCM's registration model is client-side: the mobile SDK
        # mints a token by talking to FCM directly and ships that
        # token to our backend. The platform's job is just to
        # persist the (user, token) tuple -- there's no server-side
        # 'register endpoint' call.
        return DeviceRegistration(
            registration_id=request.device_token,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata={
                **request.provider_metadata,
                "fcm_project_id": self._config.project_id,
            },
        )

    @driver_op(cloud="gcp", driver="notification")
    def revoke_device(self, *, registration_id: str) -> None:
        # FCM has no server-side revoke -- the next send to a stale
        # token returns ``UNREGISTERED`` and the dispatcher marks
        # the row revoked from there. Nothing to do at this layer.
        return

    # ---- send ------------------------------------------------------

    @driver_op(cloud="gcp", driver="notification")
    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if isinstance(target, PushTarget):
            return self._send_push(target=target, payload=payload)
        if isinstance(target, (EmailTarget, SmsTarget, WebhookTarget)):
            return SendResult(
                target=target,
                status="unsupported",
                error=("FCM driver handles push only -- route email + sms + webhook to dedicated drivers"),
            )
        return SendResult(
            target=target,
            status="unsupported",
            error=f"unknown target type {type(target).__name__}",
        )

    @driver_op(cloud="gcp", driver="notification")
    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    @driver_op(cloud="gcp", driver="notification", heartbeat=False)
    def healthcheck(self) -> ProviderHealth:
        # FCM doesn't expose a dedicated ping; treat the ability to
        # mint a token as the proxy. Token-mint failures usually
        # mean creds are wrong / expired.
        try:
            token = self._access_token()
        except Exception as exc:
            return ProviderHealth(
                ok=False,
                message=f"fcm token mint failed: {exc}",
                checked_at=datetime.now(UTC).isoformat(),
            )
        return ProviderHealth(
            ok=bool(token),
            message="fcm token mint ok" if token else "fcm token mint returned empty",
            checked_at=datetime.now(UTC).isoformat(),
        )

    # ---- internals -------------------------------------------------

    def _access_token(self) -> str:
        if self._config.token_provider is not None:
            return self._config.token_provider()
        return self._config.access_token

    def _send_push(
        self,
        *,
        target: PushTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        url = _FCM_ENDPOINT_TMPL.format(
            project_id=self._config.project_id,
        )
        message = self._build_fcm_message(
            token=target.registration_id,
            platform=target.platform,
            payload=payload,
        )
        try:
            token = self._access_token()
        except Exception as exc:
            return SendResult(
                target=target,
                status="failed",
                error=f"token mint failed: {exc}",
                retriable=True,
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = self._http.post(
                url=url,
                headers=headers,
                body=json.dumps({"message": message}, separators=(",", ":")),
            )
        except _FCMHttpError as exc:
            return self._classify_http_error(target=target, exc=exc)
        return SendResult(
            target=target,
            status="delivered",
            provider_message_id=str(response.get("name", "")),
        )

    def _classify_http_error(
        self,
        *,
        target: PushTarget,
        exc: _FCMHttpError,
    ) -> SendResult:
        body = exc.body or {}
        status = "failed"
        retriable = True
        message = exc.message
        if isinstance(body, dict):
            err_obj = body.get("error") or {}
            err_status = (err_obj.get("status") or "").upper()
            details = err_obj.get("details") or []
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                if detail.get("errorCode") == "UNREGISTERED":
                    status = "invalid_token"
                    retriable = False
                    break
            else:
                if err_status in {"UNREGISTERED", "NOT_FOUND"}:
                    status = "invalid_token"
                    retriable = False
                elif err_status in {
                    "RESOURCE_EXHAUSTED",
                    "QUOTA_EXCEEDED",
                }:
                    status = "rate_limited"
                    retriable = True
                elif err_status in {
                    "INVALID_ARGUMENT",
                    "PERMISSION_DENIED",
                    "UNAUTHENTICATED",
                }:
                    retriable = False
        return SendResult(
            target=target,
            status=status,
            error=message,
            retriable=retriable,
        )

    def _build_fcm_message(
        self,
        *,
        token: str,
        platform: DevicePlatform,
        payload: NotificationPayload,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "token": token,
            "notification": {
                "title": payload.title,
                "body": payload.body,
            },
            "data": {**payload.data},
        }
        if payload.action_url:
            message["data"]["action_url"] = payload.action_url
        if platform == DevicePlatform.ANDROID:
            android: dict[str, Any] = {}
            if payload.ttl_seconds is not None:
                android["ttl"] = f"{payload.ttl_seconds}s"
            if payload.category:
                android.setdefault("notification", {})["channel_id"] = payload.category
            if android:
                message["android"] = android
        elif platform == DevicePlatform.IOS:
            aps: dict[str, Any] = {
                "alert": {
                    "title": payload.title,
                    "body": payload.body,
                },
                "sound": "default",
            }
            if payload.badge is not None:
                aps["badge"] = payload.badge
            if payload.category:
                aps["category"] = payload.category
            apns_headers: dict[str, str] = {}
            if payload.ttl_seconds is not None:
                # APNs expiration is an absolute UNIX timestamp.
                expiry = int(
                    datetime.now(UTC).timestamp(),
                ) + int(payload.ttl_seconds)
                apns_headers["apns-expiration"] = str(expiry)
            message["apns"] = {
                "payload": {"aps": aps},
                **({"headers": apns_headers} if apns_headers else {}),
            }
        elif platform == DevicePlatform.WEB:
            message["webpush"] = {
                "notification": {
                    "title": payload.title,
                    "body": payload.body,
                    **({"badge": str(payload.badge)} if payload.badge is not None else {}),
                },
                **({"fcm_options": {"link": payload.action_url}} if payload.action_url else {}),
            }
        return message


# ---- HTTP transport -------------------------------------------------


@dataclass(frozen=True)
class _FCMHttpError(Exception):
    """Diagnostic wrapper carrying FCM's structured error body so
    the driver can classify per-error retriability."""

    message: str
    status_code: int
    body: Any | None = None

    def __str__(self) -> str:
        return f"fcm http {self.status_code}: {self.message}"


class _DefaultFCMHttp:
    """Stdlib POST. Production wiring should swap in httpx for
    connection pooling + retry support."""

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
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_body = None
            raise _FCMHttpError(
                message=str(exc),
                status_code=exc.code,
                body=error_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise _FCMHttpError(
                message=str(exc),
                status_code=0,
                body=None,
            ) from exc
        return json.loads(response_body) if response_body else {}
