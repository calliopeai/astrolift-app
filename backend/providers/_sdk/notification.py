"""NotificationDriver protocol (#490) -- fan out platform events to
delivery channels.

Where notifications fit in:

* The platform's internal **event bus** (see ``_sdk/event.py``)
  carries kube/app events. A separate dispatcher layer in
  ``astrolift_operations.notification_dispatch`` decides which
  events go to which users, then calls into a NotificationDriver
  to actually deliver to a device / inbox / channel.
* A notification driver is the per-install outbound channel.
  Every install picks its driver via the ``NotificationProfile``
  (same shape as ``ObservabilityProfile``) -- which keeps the
  platform portable across BYOC clouds.

Backends:

* ``aws_sns``         - SNS platform endpoints + topics
                        (APNs / FCM hand-off natively)
* ``gcp_fcm``         - Firebase Cloud Messaging HTTP v1
                        (iOS routed through APNs by FCM)
* ``azure_anh``       - Azure Notification Hubs (REST API)
* ``otlp_webhook``    - generic outbound webhook fan-out + SMTP
                        for users bringing their own delivery
                        path (Pushover, ntfy.sh, opsgenie etc.)
* ``multiplexer``     - fan-out across multiple drivers; mirrors
                        the multiplexer pattern from the
                        observability profile

Devices (push) versus channels (email / sms / webhook):

The protocol unifies *push* delivery (which requires a previously
registered device token) and *channel* delivery (which targets a
direct address: email, phone, webhook URL) into a single
``send`` call. The dispatcher hands the driver a
``DeliveryTarget`` discriminated union; drivers that don't speak
a channel return ``SendResult(status="unsupported")`` rather than
raising. This keeps the multiplexer logic uniform: every child
driver gets every send + a uniform ``SendResult`` row goes into
the per-send audit log.

Identity ownership:

Drivers DO NOT mint or store user identity. ``user_id`` flows in
from the caller (the dispatcher). Drivers persist provider-side
registration handles (SNS endpoint ARNs, ANH installation IDs,
FCM token caches) only as needed to translate
``DeviceRegistration.token`` to the cloud-specific identifier on
each send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol


class DevicePlatform(StrEnum):
    """Platforms a push token can target.

    ``ios`` and ``android`` are the canonical mobile platforms.
    ``web`` covers the Web Push protocol (VAPID-signed) which AWS
    SNS and ANH both support; FCM v1 supports it natively.

    The set is closed -- drivers that can't deliver to a given
    platform return ``SendResult(status='unsupported', ...)``."""

    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


@dataclass(frozen=True)
class DeviceRegistration:
    """A device token previously registered with a notification
    driver. The platform persists one row per (user, token) pair
    in ``astrolift_operations.DeviceRegistration``; this dataclass
    is the in-flight transport.

    ``registration_id`` is the driver-side handle (SNS endpoint
    ARN, FCM token-as-ID, ANH installation ID). It can match
    ``token`` exactly (FCM does; SNS does not). Dispatchers must
    not assume equality."""

    registration_id: str
    user_id: str
    device_token: str
    platform: DevicePlatform
    label: str = ""
    """Operator-visible label, e.g. 'iPhone 15 -- Leo's work phone'.
    Drivers MUST NOT use this for routing; it is UI-only."""

    provider_metadata: dict[str, str] = field(default_factory=dict)
    """Driver-side opaque blob. SNS uses it for the
    ``PlatformApplicationArn``; ANH uses it for tags + templates.
    Read-only at the driver boundary -- the dispatcher round-trips
    it back on every send without inspecting."""


@dataclass(frozen=True)
class NotificationPayload:
    """The platform-neutral notification envelope.

    Drivers translate this into the cloud's wire shape (APNs
    aps dict, FCM message proto, ANH template). Fields shared by
    all backends live here; provider-specific extras travel via
    ``extra`` (e.g. ``{'apns.collapse_id': 'deploy-42'}``)."""

    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)
    """Key/value blob delivered as the message's data payload.
    Mobile apps receive this in the foreground handler. Keep
    values stringy -- APNs and FCM both clamp data values to
    strings on the wire."""

    action_url: str = ""
    """Deep-link the app opens on tap. Optional."""

    badge: int | None = None
    """iOS badge count. Ignored on non-iOS targets."""

    category: str = ""
    """APNs ``category`` / FCM ``channel_id``. Lets the OS pick
    an icon + sound class. Optional."""

    ttl_seconds: int | None = None
    """Provider-side TTL (drop the notification if undeliverable
    after this many seconds). When unset the provider's default
    applies."""

    extra: dict[str, str] = field(default_factory=dict)
    """Provider-specific extras. Drivers SHOULD recognize their
    own keys; unknown keys are ignored, not rejected."""


@dataclass(frozen=True)
class EmailTarget:
    """Direct email channel. The dispatcher uses this when a user
    has opted into email delivery (separate from device push)."""

    to: str
    subject: str = ""
    body_html: str = ""
    body_text: str = ""


@dataclass(frozen=True)
class SmsTarget:
    """SMS / text-message channel. Phone number must be E.164."""

    to: str
    message: str = ""


@dataclass(frozen=True)
class WebhookTarget:
    """Generic outbound HTTP. The webhook driver POSTs the
    notification payload + headers; useful for Pushover / ntfy.sh
    / opsgenie integrations the user already operates."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PushTarget:
    """Push to a previously-registered device. The driver looks
    the device up by ``registration_id`` and routes via the
    cloud's native push fan-out."""

    registration_id: str
    platform: DevicePlatform


# Discriminated union -- every driver call carries exactly one
# target. The driver inspects the type at the top of send() and
# either handles it or returns ``unsupported``.
DeliveryTarget = PushTarget | EmailTarget | SmsTarget | WebhookTarget


SendStatus = Literal[
    "delivered",
    "queued",
    "failed",
    "unsupported",
    "rate_limited",
    "invalid_token",
]


@dataclass(frozen=True)
class SendResult:
    """Per-send outcome. The dispatcher records one row in
    ``NotificationDelivery`` per element of the returned list.

    ``status``:
      * ``delivered`` - provider acknowledged synchronously
      * ``queued``    - provider accepted, async delivery underway
      * ``failed``    - provider rejected; ``error`` carries the
                        diagnostic
      * ``unsupported`` - this driver doesn't speak the target's
                        channel; pick a different driver
      * ``rate_limited`` - provider returned 429-equivalent; the
                        dispatcher MAY retry
      * ``invalid_token`` - device token is stale / unregistered;
                        the dispatcher MUST mark the corresponding
                        ``DeviceRegistration`` revoked
    """

    target: DeliveryTarget
    status: SendStatus
    provider_message_id: str = ""
    """Provider-side ID for tracking (SNS MessageId, FCM name).
    Empty when the provider didn't return one."""

    error: str = ""
    """Human-readable diagnostic. Required when status='failed'."""

    retriable: bool = False
    """When ``status='failed'`` or ``status='rate_limited'``, hints
    whether the dispatcher should re-enqueue. ``invalid_token`` is
    NEVER retriable; the device must be re-registered."""


@dataclass(frozen=True)
class RegisterDeviceRequest:
    """Inputs to ``register_device``.

    Drivers translate this into the cloud's registration call
    (SNS ``CreatePlatformEndpoint``, ANH ``createOrUpdateInstallation``,
    FCM no-op + token caching)."""

    user_id: str
    device_token: str
    platform: DevicePlatform
    label: str = ""
    provider_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHealth:
    """Result of ``healthcheck``. ``ok=False`` doesn't tear down
    the driver -- it surfaces a banner in the operator UI so the
    operator can investigate.

    ``checked_at`` lets the platform short-circuit repeated
    checks within a small window (driver caches; see the
    multiplexer)."""

    ok: bool
    message: str = ""
    checked_at: str = ""


class NotificationDriver(Protocol):
    """Driver for fanning out platform events to delivery channels.

    Implementations:

    * ``aws.notification_sns.SNSNotificationDriver``
    * ``gcp.notification_fcm.FCMNotificationDriver``
    * ``azure.notification_anh.AzureNotificationHubsDriver``
    * ``k8s_native.notification_otlp.WebhookSMTPNotificationDriver``
    * ``_sdk.notification.MultiplexerNotificationDriver`` (fan-out)
    """

    name: str
    """Stable identifier matching the registry entry, e.g.
    ``'aws_sns'``, ``'gcp_fcm'``. The dispatcher uses this for
    delivery-row attribution + per-driver health reporting."""

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration: ...

    def revoke_device(self, *, registration_id: str) -> None: ...

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult: ...

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]: ...

    def healthcheck(self) -> ProviderHealth: ...


# ---- multiplexer -----------------------------------------------------


class MultiplexerNotificationDriver(NotificationDriver):
    """Fan-out across multiple child drivers. Same role as the
    observability profile's multiplexer: send to all, surface
    the per-child result, register against the *primary* driver.

    ``primary`` is the driver used for registration (one device
    token can only belong to one cloud's identifier). Sends fan
    out to every child including the primary."""

    name = "multiplexer"

    def __init__(
        self,
        *,
        primary: NotificationDriver,
        secondaries: list[NotificationDriver],
    ) -> None:
        if not secondaries:
            # Empty secondaries reduces to a passthrough -- caller
            # should use the primary directly. Refuse so a misconfig
            # surfaces immediately instead of silently no-op'ing.
            raise ValueError(
                "MultiplexerNotificationDriver: secondaries must be "
                "non-empty; use the primary driver directly when "
                "there is no fan-out",
            )
        # Guard against nesting -- mirrors the
        # ObservabilityProfile.MULTIPLEXER rule.
        for child in [primary, *secondaries]:
            if isinstance(child, MultiplexerNotificationDriver):
                raise ValueError(
                    "MultiplexerNotificationDriver: nested multiplexers are not supported (cycle risk)",
                )
        self._primary = primary
        self._secondaries = list(secondaries)

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        return self._primary.register_device(request)

    def revoke_device(self, *, registration_id: str) -> None:
        self._primary.revoke_device(registration_id=registration_id)

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        # Fan-out send: try primary first, fall back through
        # secondaries until one returns non-``unsupported``. This
        # mirrors the multiplexer's read-side rule -- writes go to
        # all, reads pick the first that can speak the channel.
        result = self._primary.send(target=target, payload=payload)
        if result.status != "unsupported":
            return result
        for child in self._secondaries:
            r = child.send(target=target, payload=payload)
            if r.status != "unsupported":
                return r
        return SendResult(
            target=target,
            status="unsupported",
            error="no child driver speaks this target channel",
        )

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        primary_health = self._primary.healthcheck()
        secondary_healths = [c.healthcheck() for c in self._secondaries]
        all_ok = primary_health.ok and all(h.ok for h in secondary_healths)
        summary_bits: list[str] = []
        summary_bits.append(
            f"primary:{self._primary.name}={'ok' if primary_health.ok else 'fail'}",
        )
        for child, h in zip(
            self._secondaries,
            secondary_healths,
            strict=True,
        ):
            summary_bits.append(
                f"{child.name}={'ok' if h.ok else 'fail'}",
            )
        return ProviderHealth(ok=all_ok, message=" | ".join(summary_bits))
