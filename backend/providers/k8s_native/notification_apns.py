"""Direct APNs NotificationDriver (#2256): token-based HTTP/2 delivery to iOS and iPadOS.

The operator-hosted path for Apple devices. The control plane talks to Apple
itself, with the provider key of the Apple Developer team that owns the app's
bundle ID; no SNS, Notification Hubs, FCM or Expo relay sits in between.
Apple still carries the notification to the device: hosting the sender does
not replace the Apple Push Notification service.

Each send is ``POST /3/device/<device token>`` over HTTP/2:

* ``authorization: bearer <provider token>``, an ES256 JWT (``kid`` the key
  id, ``iss`` the team id, ``iat``) signed with the ``.p8`` key. Apple takes a
  token for an hour and answers ``TooManyProviderTokenUpdates`` when one is
  replaced more often than every 20 minutes, so every driver instance in the
  process reuses one token per key for 50 minutes.
* ``apns-topic`` (the bundle ID), ``apns-push-type: alert``, ``apns-priority:
  10``, ``apns-expiration`` (now plus the TTL; ``0`` means one attempt that
  Apple never stores), ``apns-id`` and ``apns-collapse-id``.
* A body of ``aps.alert`` (title and body), the category and a sound, with the
  push contract's ``data`` keys beside ``aps`` (``docs/mobile-push-contract.md``),
  as the SNS ``APNS`` document and the Notification Hubs ``apple`` format carry
  them. Apple refuses bodies over 4096 bytes.

``production`` (``api.push.apple.com``) serves App Store and TestFlight
builds, ``sandbox`` (``api.sandbox.push.apple.com``) development builds; a
token from one environment gets ``BadDeviceToken`` in the other.

Outcomes, following Apple's "Handling notification responses from APNs":

* ``200``: ``delivered``, with Apple's ``apns-id``.
* ``410`` (``Unregistered``, ``ExpiredToken``): ``invalid_token``, the one
  answer that lets the dispatcher prune the registration.
* Other ``400`` and ``403`` answers (``BadDeviceToken``,
  ``DeviceTokenNotForTopic``, ``TopicDisallowed``, a rejected provider token)
  say the configuration or the environment does not match the token. They
  fail, are never sent again and never prune.
* ``429 TooManyRequests``: ``rate_limited``. ``5xx``: ``failed``,
  retriable. Apple asks providers to wait (15 minutes for a ``5xx``), so
  neither is sent again here.
* A request that got no answer (connect, TLS, timeout, reset) is sent again
  after a short backoff, and one answered ``403 ExpiredProviderToken`` with a
  new provider token, up to ``max_attempts`` sends in all. Each send of a
  notification carries the same ``apns-id``, which is also its collapse id
  unless the payload names one (``extra["apns.collapse_id"]``), so a copy
  Apple accepted twice replaces the first on the device.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

APNS_ENDPOINTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
APNS_PAYLOAD_MAX_BYTES = 4096
APNS_COLLAPSE_ID_MAX_BYTES = 64
# FCM's ceiling; Apple documents none, and a notice older than this is stale.
APNS_TTL_MAX_SECONDS = 28 * 24 * 3600
PROVIDER_TOKEN_REUSE_SECONDS = 50 * 60
MAX_ATTEMPTS_CEILING = 3
RETRY_BACKOFF_SECONDS = 0.5

_APPLE_ID = re.compile(r"[A-Z0-9]{10}")
_BUNDLE_ID = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Hex only: the token becomes a path segment of the request URL.
_DEVICE_TOKEN = re.compile(r"[0-9A-Fa-f]{1,200}")
_REASON = re.compile(r"[A-Za-z]{1,64}")

_HINTS = {
    "BadDeviceToken": "; the token is malformed or comes from the other APNs environment (production or sandbox)",
    "DeviceTokenNotForTopic": "; the token belongs to another app than the configured topic",
    "TopicDisallowed": "; this key's team may not push to the configured topic",
    "BadTopic": "; the configured topic is not a bundle ID",
    "InvalidProviderToken": "; check team_id and key_id, and that the key is not revoked",
    "BadEnvironmentKeyIdInToken": "; the key is limited to the other APNs environment",
    "MissingProviderToken": "; the request carried no provider token",
}


class APNsConfigError(ValueError):
    """An APNs configuration that cannot send. Messages never carry key material."""


@dataclass(frozen=True)
class APNsResponse:
    status: int
    apns_id: str = ""
    reason: str = ""
    """Apple's ``reason`` from the JSON body of a non-200 answer."""


class APNsTransportError(Exception):
    """The request got no HTTP answer: DNS, connect, TLS, a timeout or a reset."""


class APNsTransport(Protocol):
    def post(self, *, url: str, headers: Mapping[str, str], body: bytes) -> APNsResponse: ...


@dataclass(frozen=True)
class APNsConfig:
    team_id: str
    """The 10-character id of the Apple Developer team that owns the app."""

    key_id: str
    """The 10-character id of the APNs auth key."""

    topic: str
    """The app's bundle ID. Every token this driver sends to belongs to it."""

    environment: str
    """``production`` or ``sandbox``."""

    auth_key: str = field(repr=False)
    """The ``.p8`` file's PEM text: an unencrypted PKCS#8 P-256 key."""

    default_ttl_seconds: int = 86400
    """``apns-expiration`` when the payload names no ``ttl_seconds``."""

    timeout_seconds: float = 10.0
    max_attempts: int = 2
    """Sends per notification, the first included; at most 3."""

    transport: APNsTransport | None = None
    """``None`` shares one HTTP/2 client per endpoint, team and key."""

    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not _APPLE_ID.fullmatch(self.team_id):
            raise APNsConfigError("team_id must be the 10-character Apple Developer team id")
        if not _APPLE_ID.fullmatch(self.key_id):
            raise APNsConfigError("key_id must be the 10-character APNs key id")
        if not _BUNDLE_ID.fullmatch(self.topic):
            raise APNsConfigError("topic must be the app's bundle ID")
        if self.environment not in APNS_ENDPOINTS:
            raise APNsConfigError("environment must be production or sandbox")
        if not 0 <= self.default_ttl_seconds <= APNS_TTL_MAX_SECONDS:
            raise APNsConfigError(f"default_ttl_seconds must be between 0 and {APNS_TTL_MAX_SECONDS}")
        if not 0 < self.timeout_seconds <= 60:
            raise APNsConfigError("timeout_seconds must be more than 0 and at most 60")
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_CEILING:
            raise APNsConfigError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CEILING}")


@dataclass(frozen=True)
class _ProviderToken:
    value: str
    issued_at: float


# Keyed by (team id, key id, the key's fingerprint): a rotated key gets its
# own. The fingerprint is the key's, not its file's, so one key copied into
# two organizations' Secrets with other line endings still signs one token.
_TOKENS: dict[tuple[str, str, str], _ProviderToken] = {}
_TOKENS_LOCK = threading.Lock()

# Keyed by (endpoint, team id, key id, the key's fingerprint, timeout). Apple
# asks providers to keep connections open, refuses a connection whose provider
# tokens change key (``UnrelatedKeyIdInToken``), and errors when a connection
# sees a new token more often than every 20 minutes. So each key gets its own
# client, and a profile naming another key's ids never shares its connection.
_TRANSPORTS: dict[tuple[str, str, str, str, float], APNsTransport] = {}
_TRANSPORTS_LOCK = threading.Lock()

_RETRY_NONE = "none"
_RETRY_BACKOFF = "backoff"
_RETRY_NEW_TOKEN = "new_token"


class APNsNotificationDriver(NotificationDriver):
    """Apple Push Notification service, spoken directly."""

    name = "apns"

    def __init__(self, *, config: APNsConfig) -> None:
        self._config = config
        self._key = _load_auth_key(config.auth_key)
        self._endpoint = APNS_ENDPOINTS[config.environment]
        fingerprint = _key_fingerprint(self._key)
        self._token_key = (config.team_id, config.key_id, fingerprint)
        self._transport = config.transport or _shared_transport(
            endpoint=self._endpoint,
            team_id=config.team_id,
            key_id=config.key_id,
            key_fingerprint=fingerprint,
            timeout=config.timeout_seconds,
        )

    # ---- registration ----------------------------------------------

    @driver_op(cloud="k8s_native", driver="notification")
    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        # APNs has no server-side registration: the app hands its device
        # token to the control plane, which sends to it as it is.
        return DeviceRegistration(
            registration_id=request.device_token,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata={
                **request.provider_metadata,
                "apns_topic": self._config.topic,
                "apns_environment": self._config.environment,
            },
        )

    @driver_op(cloud="k8s_native", driver="notification")
    def revoke_device(self, *, registration_id: str) -> None:
        # Nothing to revoke at Apple: a dead token answers 410 and the
        # dispatcher prunes its registration.
        return

    # ---- send ------------------------------------------------------

    @driver_op(cloud="k8s_native", driver="notification", redact_args=("target",))
    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if isinstance(target, PushTarget):
            if target.platform != DevicePlatform.IOS:
                return SendResult(
                    target=target,
                    status="unsupported",
                    error="APNs delivers to iOS and iPadOS devices; send Android through gcp_fcm",
                )
            return self._send_push(target=target, payload=payload)
        if isinstance(target, (EmailTarget, SmsTarget, WebhookTarget)):
            return SendResult(
                target=target,
                status="unsupported",
                error="APNs driver handles push only -- route email + sms + webhook to dedicated drivers",
            )
        return SendResult(
            target=target,
            status="unsupported",
            error=f"unknown target type {type(target).__name__}",
        )

    @driver_op(cloud="k8s_native", driver="notification", redact_args=("targets",))
    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    @driver_op(cloud="k8s_native", driver="notification", heartbeat=False)
    def healthcheck(self) -> ProviderHealth:
        # Apple has no ping: the check proves the key loads and signs a
        # provider token. Whether Apple accepts it shows on the first send.
        try:
            self._provider_token()
        except Exception as exc:
            return ProviderHealth(
                ok=False,
                message=f"apns provider token signing failed: {type(exc).__name__}",
                checked_at=datetime.now(UTC).isoformat(),
            )
        return ProviderHealth(
            ok=True,
            message=(
                f"apns {self._config.environment} {self._config.topic}: "
                f"provider token signed with key {self._config.key_id}"
            ),
            checked_at=datetime.now(UTC).isoformat(),
        )

    # ---- internals -------------------------------------------------

    def _provider_token(self, *, rejected: str = "") -> str:
        """The key's shared provider token, replaced at 50 minutes or when
        Apple rejected it as expired (``rejected``). Another thread may have
        replaced a rejected token already; that one is returned."""
        now = self._config.clock()
        with _TOKENS_LOCK:
            cached = _TOKENS.get(self._token_key)
            if (
                cached is not None
                and cached.value != rejected
                and 0 <= now - cached.issued_at < PROVIDER_TOKEN_REUSE_SECONDS
            ):
                return cached.value
            value = _sign_provider_token(
                key=self._key,
                team_id=self._config.team_id,
                key_id=self._config.key_id,
                issued_at=int(now),
            )
            _TOKENS[self._token_key] = _ProviderToken(value=value, issued_at=now)
            return value

    def _send_push(
        self,
        *,
        target: PushTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        device_token = target.registration_id
        if not _DEVICE_TOKEN.fullmatch(device_token):
            return SendResult(
                target=target,
                status="failed",
                error="registration is not an APNs device token (hex)",
                retriable=False,
            )
        body = json.dumps(_apns_body(payload), separators=(",", ":")).encode("utf-8")
        if len(body) > APNS_PAYLOAD_MAX_BYTES:
            return SendResult(
                target=target,
                status="failed",
                error=f"payload is {len(body)} bytes; APNs accepts {APNS_PAYLOAD_MAX_BYTES}",
                retriable=False,
            )
        notification_id = str(uuid.uuid4())
        headers = {
            "apns-topic": self._config.topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
            "apns-expiration": self._expiration(payload),
            "apns-id": notification_id,
            "apns-collapse-id": _collapse_id(payload, notification_id),
        }
        url = f"{self._endpoint}/3/device/{device_token}"
        provider_token = self._provider_token()
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._transport.post(
                    url=url,
                    headers={**headers, "authorization": f"bearer {provider_token}"},
                    body=body,
                )
            except APNsTransportError as exc:
                result = SendResult(
                    target=target,
                    status="failed",
                    error=f"APNs unreachable: {exc}",
                    retriable=True,
                )
                retry = _RETRY_BACKOFF
            else:
                result, retry = _classify(target=target, response=response, notification_id=notification_id)
            if retry == _RETRY_NONE or attempt >= self._config.max_attempts:
                return result
            if retry == _RETRY_NEW_TOKEN:
                provider_token = self._provider_token(rejected=provider_token)
            else:
                self._config.sleep(RETRY_BACKOFF_SECONDS * attempt)

    def _expiration(self, payload: NotificationPayload) -> str:
        ttl = payload.ttl_seconds if payload.ttl_seconds is not None else self._config.default_ttl_seconds
        if ttl <= 0:
            return "0"
        return str(int(self._config.clock()) + min(ttl, APNS_TTL_MAX_SECONDS))


def _apns_body(payload: NotificationPayload) -> dict[str, Any]:
    aps: dict[str, Any] = {
        "alert": {"title": payload.title, "body": payload.body},
        "sound": "default",
    }
    if payload.badge is not None:
        aps["badge"] = payload.badge
    if payload.category:
        aps["category"] = payload.category
    # ``aps`` last, so no data key can replace it.
    return {
        **payload.data,
        **({"action_url": payload.action_url} if payload.action_url else {}),
        "aps": aps,
    }


def _collapse_id(payload: NotificationPayload, notification_id: str) -> str:
    requested = payload.extra.get("apns.collapse_id", "")
    if not requested:
        return notification_id
    if len(requested.encode("utf-8")) > APNS_COLLAPSE_ID_MAX_BYTES:
        # Apple refuses longer ids (BadCollapseId); a digest keeps them distinct.
        return hashlib.sha256(requested.encode("utf-8")).hexdigest()
    return requested


def _classify(
    *,
    target: PushTarget,
    response: APNsResponse,
    notification_id: str,
) -> tuple[SendResult, str]:
    if response.status == 200:
        return (
            SendResult(
                target=target,
                status="delivered",
                provider_message_id=response.apns_id or notification_id,
            ),
            _RETRY_NONE,
        )
    reason = response.reason if _REASON.fullmatch(response.reason) else "unknown"
    error = f"APNs {response.status} {reason}"
    if response.status == 410:
        return SendResult(target=target, status="invalid_token", error=error), _RETRY_NONE
    if response.status == 429:
        if reason == "TooManyProviderTokenUpdates":
            return SendResult(target=target, status="failed", error=error, retriable=True), _RETRY_NONE
        return SendResult(target=target, status="rate_limited", error=error, retriable=True), _RETRY_NONE
    if response.status == 403 and reason == "ExpiredProviderToken":
        return SendResult(target=target, status="failed", error=error, retriable=True), _RETRY_NEW_TOKEN
    if response.status >= 500:
        return SendResult(target=target, status="failed", error=error, retriable=True), _RETRY_NONE
    return (
        SendResult(target=target, status="failed", error=error + _HINTS.get(reason, ""), retriable=False),
        _RETRY_NONE,
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_provider_token(*, key: EllipticCurvePrivateKey, team_id: str, key_id: str, issued_at: int) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    header = _b64url(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode("utf-8"))
    claims = _b64url(json.dumps({"iss": team_id, "iat": issued_at}, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{claims}"
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    # JWS carries ES256 as the raw 32-byte r and s, not DER.
    return f"{signing_input}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _load_auth_key(pem: str) -> EllipticCurvePrivateKey:
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        key = load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise APNsConfigError("auth key is not an unencrypted PKCS#8 PEM private key (the .p8 file)") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise APNsConfigError("auth key must be a P-256 key, as Apple issues APNs .p8 keys")
    return key


def _key_fingerprint(key: EllipticCurvePrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return hashlib.sha256(key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).hexdigest()


class _DeviceTokenLogFilter(logging.Filter):
    """httpx logs every request's URL at INFO, and an APNs request's path is the
    device token, so the token is replaced before the record is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 1:
            url = str(args[1])
            for endpoint in APNS_ENDPOINTS.values():
                if url.startswith(f"{endpoint}/3/device/"):
                    record.args = (args[0], f"{endpoint}/3/device/[redacted]", *args[2:])
                    break
        return True


logging.getLogger("httpx").addFilter(_DeviceTokenLogFilter())


class _HttpxTransport:
    """HTTP/2 through httpx; APNs refuses HTTP/1.1."""

    def __init__(self, *, timeout: float) -> None:
        import httpx

        self._client = httpx.Client(http2=True, timeout=timeout)

    def post(self, *, url: str, headers: Mapping[str, str], body: bytes) -> APNsResponse:
        import httpx

        try:
            response = self._client.post(url, headers=dict(headers), content=body)
        except httpx.HTTPError as exc:
            # The type only: an exception's text can carry the URL, and the
            # URL carries the device token.
            raise APNsTransportError(type(exc).__name__) from exc
        reason = ""
        if response.status_code != 200:
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                reason = str(parsed.get("reason", ""))
        return APNsResponse(
            status=response.status_code,
            apns_id=response.headers.get("apns-id", ""),
            reason=reason,
        )


def _shared_transport(
    *, endpoint: str, team_id: str, key_id: str, key_fingerprint: str, timeout: float
) -> APNsTransport:
    key = (endpoint, team_id, key_id, key_fingerprint, timeout)
    with _TRANSPORTS_LOCK:
        transport = _TRANSPORTS.get(key)
        if transport is None:
            transport = _HttpxTransport(timeout=timeout)
            _TRANSPORTS[key] = transport
        return transport
