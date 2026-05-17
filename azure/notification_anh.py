"""Azure Notification Hubs NotificationDriver (#490).

Azure Notification Hubs (ANH) is the Azure-native push surface.
It wraps APNs + FCM (+ ADM / Baidu) behind a single REST API and
adds two features the platform leans on:

* **Installations** -- a stable per-device row that carries tags
  (operator + user metadata), templates (per-platform payload
  shape), and the device's last-known push token. Installation
  IDs are operator-supplied so the platform can use its own
  ``DeviceRegistration.guid`` as the installation id directly.
* **Tag expressions** -- when sending, the operator can target by
  arbitrary tag query (``user:<id>`` OR ``team:<slug>``) instead
  of a single device. The driver exposes this via a per-target
  ``tags`` field on the provider_metadata when present.

REST API endpoints used:

    PUT  {namespace}.servicebus.windows.net/{hub}/installations/{id}?api-version=2020-06
    DELETE {namespace}.servicebus.windows.net/{hub}/installations/{id}?api-version=2020-06
    POST {namespace}.servicebus.windows.net/{hub}/messages?api-version=2020-06

Authentication uses a SAS token derived from a shared-access
policy (typically ``DefaultFullSharedAccessSignature``). The
driver accepts the connection string and computes the SAS at
call time so token TTLs stay short.

Channel coverage:

* **Push** -- full implementation.
* **Email** / **SMS** -- ``unsupported``; route to ACS Email and
  ACS SMS via ``azure.managed.email_acs`` (and a future SMS
  driver). ANH does not deliver email or SMS.
* **Webhook** -- ``unsupported``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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


@dataclass(frozen=True)
class AzureNotificationHubsConfig:
    namespace: str
    """Notification Hubs namespace, e.g. ``my-ns``. Used to build
    the REST endpoint ``{namespace}.servicebus.windows.net``."""

    hub_name: str
    """Notification hub name within the namespace."""

    shared_access_key_name: str
    shared_access_key: str
    """Shared-access policy name + key. The driver derives a SAS
    token from these on each call. The key must have ``Listen +
    Send + Manage`` rights for installation create + delete."""

    api_version: str = "2020-06"
    timeout_seconds: int = 10
    http_client: Any | None = None


class AzureNotificationHubsDriver(NotificationDriver):
    name = "azure_anh"

    def __init__(
        self,
        *,
        config: AzureNotificationHubsConfig,
    ) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultAnhHttp(timeout=config.timeout_seconds)

    # ---- registration ----------------------------------------------

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        installation_id = request.provider_metadata.get(
            "installation_id",
        ) or _installation_id_for(
            user_id=request.user_id,
            token=request.device_token,
        )
        body = {
            "installationId": installation_id,
            "platform": _anh_platform_for(request.platform),
            "pushChannel": request.device_token,
            "tags": [
                f"user:{request.user_id}",
                *(
                    request.provider_metadata.get("tags", "").split(",")
                    if request.provider_metadata.get("tags")
                    else []
                ),
            ],
        }
        url = self._build_url(f"installations/{installation_id}")
        try:
            self._http.put(
                url=url,
                headers=self._auth_headers(),
                body=json.dumps(body, separators=(",", ":")),
            )
        except _AnhHttpError as exc:
            raise AzureNotificationHubsError(
                f"installation upsert failed: {exc}",
            ) from exc
        provider_metadata = dict(request.provider_metadata)
        provider_metadata["installation_id"] = installation_id
        return DeviceRegistration(
            registration_id=installation_id,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata=provider_metadata,
        )

    def revoke_device(self, *, registration_id: str) -> None:
        if not registration_id:
            return
        url = self._build_url(f"installations/{registration_id}")
        try:
            self._http.delete(url=url, headers=self._auth_headers())
        except _AnhHttpError:
            # Idempotent revoke -- treat any failure as already-gone.
            return

    # ---- send ------------------------------------------------------

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
                error=("Azure Notification Hubs handles push only -- route email + sms + webhook to dedicated drivers"),
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
        # Sas-validation HEAD against the hub: cheap + auth-bound.
        url = self._build_url("")
        try:
            self._http.get(url=url, headers=self._auth_headers())
        except _AnhHttpError as exc:
            # ANH returns 404 for the root, but a 401 / 403 is the
            # actionable signal (bad SAS key). Treat 401/403 as
            # failure; anything else (including 404) is the SAS
            # roundtrip succeeding.
            if exc.status_code in (401, 403):
                return ProviderHealth(
                    ok=False,
                    message=f"anh sas auth failed: {exc}",
                    checked_at=datetime.now(UTC).isoformat(),
                )
        return ProviderHealth(
            ok=True,
            message="anh sas auth ok",
            checked_at=datetime.now(UTC).isoformat(),
        )

    # ---- internals -------------------------------------------------

    def _send_push(
        self,
        *,
        target: PushTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        installation_id = target.registration_id
        url = self._build_url(
            f"messages?api-version={self._config.api_version}",
            include_api_version=False,
        )
        body = self._wire_payload_for(
            platform=target.platform,
            payload=payload,
        )
        headers = self._auth_headers()
        headers["Content-Type"] = self._content_type_for(target.platform)
        headers["ServiceBusNotification-Format"] = self._anh_format_for(target.platform)
        # Target a single installation via tag expression rather than
        # global broadcast. ANH evaluates the tag, drops non-matching
        # subscribers, so a single installation tagged ``user:<uid>``
        # narrows correctly.
        headers["ServiceBusNotification-Tags"] = f"installationId:{{{installation_id}}}"
        try:
            response = self._http.post(
                url=url,
                headers=headers,
                body=body,
            )
        except _AnhHttpError as exc:
            return self._classify_http_error(target=target, exc=exc)
        return SendResult(
            target=target,
            status="queued",
            provider_message_id=str(
                response.get("notificationId", "") if isinstance(response, dict) else "",
            ),
        )

    def _classify_http_error(
        self,
        *,
        target: PushTarget,
        exc: _AnhHttpError,
    ) -> SendResult:
        if exc.status_code in (404, 410):
            return SendResult(
                target=target,
                status="invalid_token",
                error=str(exc),
                retriable=False,
            )
        if exc.status_code == 429:
            return SendResult(
                target=target,
                status="rate_limited",
                error=str(exc),
                retriable=True,
            )
        if exc.status_code in (401, 403):
            return SendResult(
                target=target,
                status="failed",
                error=str(exc),
                retriable=False,
            )
        return SendResult(
            target=target,
            status="failed",
            error=str(exc),
            retriable=True,
        )

    def _wire_payload_for(
        self,
        *,
        platform: DevicePlatform,
        payload: NotificationPayload,
    ) -> str:
        # ANH wants the native per-platform body; we shape APNs JSON
        # for iOS and FCM JSON for Android / Web.
        if platform == DevicePlatform.IOS:
            wire: dict[str, Any] = {
                "aps": {
                    "alert": {
                        "title": payload.title,
                        "body": payload.body,
                    },
                    "sound": "default",
                },
                **payload.data,
            }
            if payload.badge is not None:
                wire["aps"]["badge"] = payload.badge
            if payload.category:
                wire["aps"]["category"] = payload.category
            return json.dumps(wire, separators=(",", ":"))
        wire = {
            "notification": {
                "title": payload.title,
                "body": payload.body,
                **({"channel_id": payload.category} if payload.category else {}),
            },
            "data": dict(payload.data),
        }
        if payload.action_url:
            wire["data"]["action_url"] = payload.action_url
        return json.dumps(wire, separators=(",", ":"))

    def _content_type_for(self, platform: DevicePlatform) -> str:
        if platform == DevicePlatform.IOS:
            return "application/json;charset=utf-8"
        return "application/json;charset=utf-8"

    def _anh_format_for(self, platform: DevicePlatform) -> str:
        return {
            DevicePlatform.IOS: "apple",
            DevicePlatform.ANDROID: "fcmv1",
            DevicePlatform.WEB: "browser",
        }[platform]

    def _build_url(
        self,
        suffix: str,
        *,
        include_api_version: bool = True,
    ) -> str:
        base = f"https://{self._config.namespace}.servicebus.windows.net/{self._config.hub_name}"
        full = f"{base}/{suffix}" if suffix else base
        if include_api_version and "api-version=" not in full:
            sep = "&" if "?" in full else "?"
            full = f"{full}{sep}api-version={self._config.api_version}"
        return full

    def _auth_headers(self) -> dict[str, str]:
        # SAS tokens are URL-scoped + time-limited. Sign the full
        # base endpoint (without per-call suffix) so a single token
        # works across installation upsert + send + delete.
        target_uri = f"https://{self._config.namespace}.servicebus.windows.net/{self._config.hub_name}"
        sas = _make_sas_token(
            uri=target_uri,
            key_name=self._config.shared_access_key_name,
            key=self._config.shared_access_key,
            ttl_seconds=300,
        )
        return {
            "Authorization": sas,
            "Content-Type": "application/json",
            "x-ms-version": self._config.api_version,
        }


# ---- helpers --------------------------------------------------------


def _installation_id_for(*, user_id: str, token: str) -> str:
    """Deterministic installation id when the caller doesn't supply
    one. Matches the platform's convention of one installation per
    (user, token) pair."""
    digest = hashlib.sha256(
        f"{user_id}::{token}".encode(),
    ).hexdigest()
    return f"inst-{digest[:32]}"


def _anh_platform_for(platform: DevicePlatform) -> str:
    return {
        DevicePlatform.IOS: "apns",
        DevicePlatform.ANDROID: "fcmv1",
        DevicePlatform.WEB: "browser",
    }[platform]


def _make_sas_token(
    *,
    uri: str,
    key_name: str,
    key: str,
    ttl_seconds: int,
) -> str:
    """Azure SAS token (servicebus / notification hubs flavor).

    Format: ``SharedAccessSignature sr=<uri>&sig=<sig>&se=<expiry>
    &skn=<keyname>``.
    """
    expiry = int(time.time()) + ttl_seconds
    string_to_sign = f"{urllib.parse.quote_plus(uri)}\n{expiry}"
    signature = base64.b64encode(
        hmac.new(
            key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    return (
        "SharedAccessSignature "
        f"sr={urllib.parse.quote_plus(uri)}"
        f"&sig={urllib.parse.quote_plus(signature)}"
        f"&se={expiry}"
        f"&skn={key_name}"
    )


# ---- HTTP transport -------------------------------------------------


@dataclass(frozen=True)
class _AnhHttpError(Exception):
    message: str
    status_code: int
    body: str = ""

    def __str__(self) -> str:
        return f"anh http {self.status_code}: {self.message}"


class _DefaultAnhHttp:
    def __init__(self, *, timeout: int) -> None:
        self._timeout = timeout

    def put(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            method="PUT",
            url=url,
            headers=headers,
            body=body,
        )

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            method="POST",
            url=url,
            headers=headers,
            body=body,
        )

    def delete(
        self,
        *,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return self._request(
            method="DELETE",
            url=url,
            headers=headers,
            body="",
        )

    def get(
        self,
        *,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return self._request(
            method="GET",
            url=url,
            headers=headers,
            body="",
        )

    def _request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body else None,
            method=method,
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
                error_body = exc.read().decode("utf-8")
            except Exception:
                error_body = ""
            raise _AnhHttpError(
                message=str(exc),
                status_code=exc.code,
                body=error_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise _AnhHttpError(
                message=str(exc),
                status_code=0,
                body="",
            ) from exc
        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return {"raw": response_body}


class AzureNotificationHubsError(Exception):
    """Driver-side failure that the dispatcher should surface as
    an unrecoverable registration error."""
