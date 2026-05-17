"""Notification dispatcher (#490).

Bridges the platform's event bus → ``NotificationDriver`` SDK
calls. The flow:

1. Caller hands the dispatcher an ``(event_type, target_users,
   payload)`` tuple.
2. Dispatcher resolves the org's active ``NotificationProfile``,
   instantiates the driver via the provider registry.
3. For each target user, the dispatcher loads
   ``DeviceRegistration`` rows + opted-in channels (email / sms
   / webhook) and constructs the per-user
   ``DeliveryTarget`` list.
4. Driver ``send_bulk`` (or fan-out) returns ``SendResult`` rows.
5. Dispatcher persists one ``NotificationDelivery`` audit row
   per result + soft-deletes any device whose token came back
   ``invalid_token``.

Resolver/worker layer wraps this call -- the dispatcher itself
is permission-agnostic. Per the platform rules, every callsite
into ``dispatch_notification`` MUST be inside a resolver that
has already checked the actor's permission.

Driver instantiation is per-call; the provider plugin's driver
class is stamped on the ``NotificationProfile`` and the resolver
maps it to a concrete plugin manifest entry. This keeps the
dispatcher decoupled from the cluster-binding code (the driver
factory is the only knob shared).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from astrolift_operations.models import (
    DeviceRegistration,
    NotificationDelivery,
    NotificationProfile,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# ---- SDK imports ---------------------------------------------------
#
# Vendored providers SDK. The notification driver protocol +
# discriminated DeliveryTarget shape live there; the dispatcher
# imports them for type annotations + the .value enum mapping.
from _sdk.notification import (
    DeliveryTarget,
    DevicePlatform,
    EmailTarget,
    NotificationDriver,
    NotificationPayload,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
    SmsTarget,
    WebhookTarget,
)


@dataclass(frozen=True)
class ChannelOptIn:
    """Per-user channel preferences. The dispatcher builds one
    ``DeliveryTarget`` per opted-in channel."""

    email: str = ""
    sms: str = ""
    webhook_url: str = ""


@dataclass(frozen=True)
class UserTarget:
    """One recipient of a dispatch -- mostly just the user_id, with
    optional channel addresses for direct (non-push) delivery."""

    user_id: str
    channels: ChannelOptIn = ChannelOptIn()


@dataclass(frozen=True)
class DispatchReport:
    """Aggregated outcome of a ``dispatch_notification`` call."""

    sent_count: int
    failed_count: int
    skipped_count: int
    deliveries: list[NotificationDelivery] = field(default_factory=list)


# Type alias for the driver factory the resolver injects. Maps a
# stored ``NotificationProfile`` row to a live ``NotificationDriver``
# instance. The default factory in this module reads the vendored
# plugin manifests; tests inject a fake.
DriverFactory = "Callable[[NotificationProfile], NotificationDriver]"


# ---- driver factory -------------------------------------------------


def default_driver_factory(
    profile: NotificationProfile,
) -> NotificationDriver:
    """Resolve a NotificationProfile row -> a live NotificationDriver.

    Wired to the vendored provider plugins. Each driver kind picks
    its config dataclass + instantiates with the row's `config`
    JSON blob. Multiplexer wraps recursively.
    """
    driver_kind = profile.driver
    config = profile.config or {}

    if driver_kind == "aws_sns":
        from aws.notification_sns import (
            SNSNotificationConfig,
            SNSNotificationDriver,
        )

        platform_map = config.get("platform_applications") or {}
        return SNSNotificationDriver(
            config=SNSNotificationConfig(
                region=config["region"],
                platform_applications={
                    DevicePlatform(platform): arn for platform, arn in platform_map.items()
                },
                sms_sender_id=config.get("sms_sender_id", ""),
                default_ttl_seconds=int(
                    config.get("default_ttl_seconds", 86400),
                ),
            ),
        )

    if driver_kind == "gcp_fcm":
        from gcp.notification_fcm import (
            FCMConfig,
            FCMNotificationDriver,
        )

        return FCMNotificationDriver(
            config=FCMConfig(
                project_id=config["project_id"],
                access_token=config.get("access_token", ""),
                timeout_seconds=int(config.get("timeout_seconds", 10)),
            ),
        )

    if driver_kind == "azure_anh":
        from azure.notification_anh import (
            AzureNotificationHubsConfig,
            AzureNotificationHubsDriver,
        )

        return AzureNotificationHubsDriver(
            config=AzureNotificationHubsConfig(
                namespace=config["namespace"],
                hub_name=config["hub_name"],
                shared_access_key_name=config["shared_access_key_name"],
                shared_access_key=config.get("shared_access_key", ""),
                api_version=config.get("api_version", "2020-06"),
            ),
        )

    if driver_kind == "otlp_webhook":
        from k8s_native.notification_otlp import (
            WebhookSMTPConfig,
            WebhookSMTPNotificationDriver,
        )

        return WebhookSMTPNotificationDriver(
            config=WebhookSMTPConfig(
                default_webhook_url=config.get("default_webhook_url", ""),
                default_webhook_bearer=config.get("default_webhook_bearer", ""),
                push_webhook_url=config.get("push_webhook_url", ""),
                smtp_host=config.get("smtp_host", ""),
                smtp_port=int(config.get("smtp_port", 587)),
                smtp_username=config.get("smtp_username", ""),
                smtp_password=config.get("smtp_password", ""),
                smtp_from=config.get("smtp_from", ""),
                smtp_use_tls=bool(config.get("smtp_use_tls", True)),
            ),
        )

    if driver_kind == "multiplexer":
        from _sdk.notification import MultiplexerNotificationDriver

        primary_config = config.get("primary") or {}
        secondaries_config = config.get("secondaries") or []
        primary_profile = _SyntheticProfile(
            driver=primary_config.get("driver", ""),
            config=primary_config.get("config", {}),
        )
        primary = default_driver_factory(primary_profile)
        secondaries = [
            default_driver_factory(
                _SyntheticProfile(
                    driver=c.get("driver", ""),
                    config=c.get("config", {}),
                ),
            )
            for c in secondaries_config
        ]
        return MultiplexerNotificationDriver(
            primary=primary,
            secondaries=secondaries,
        )

    raise DispatcherError(
        f"unknown notification driver: {driver_kind!r}",
    )


class _SyntheticProfile:
    """In-memory stand-in for a NotificationProfile row -- used when
    the multiplexer recursively instantiates child drivers from the
    config blob (no DB row exists for children)."""

    def __init__(self, *, driver: str, config: dict) -> None:
        self.driver = driver
        self.config = config


# ---- dispatcher entrypoint ------------------------------------------


def dispatch_notification(
    *,
    organization_id: int,
    event_type: str,
    target_users: list[UserTarget],
    payload: NotificationPayload,
    driver_factory: DriverFactory = None,
) -> DispatchReport:
    """Fan out a single notification payload to multiple users.

    Loads the org's active NotificationProfile, builds a driver,
    resolves each user's targets (registered push devices + opted-in
    channels), calls send_bulk, and persists one NotificationDelivery
    audit row per result.

    Returns aggregate counts; the per-row results are also returned
    so callers (typically a worker) can correlate.
    """
    profile = (
        NotificationProfile.objects.filter(
            organization_id=organization_id,
            is_active=True,
            deleted_at__isnull=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if profile is None:
        # No profile configured for this org -- the dispatcher
        # records the skip but doesn't fail. The org admin sees an
        # empty delivery log + a banner suggesting configuration.
        return DispatchReport(
            sent_count=0,
            failed_count=0,
            skipped_count=len(target_users),
        )

    factory = driver_factory or default_driver_factory
    driver = factory(profile)

    report = DispatchReport(
        sent_count=0,
        failed_count=0,
        skipped_count=0,
        deliveries=[],
    )
    payload_excerpt = _payload_excerpt(payload)

    for user in target_users:
        device_rows = list(
            DeviceRegistration.objects.filter(
                user_id=user.user_id,
                deleted_at__isnull=True,
            ),
        )
        targets, target_rows = _build_targets_for(
            user=user,
            device_rows=device_rows,
        )
        if not targets:
            report = _bump(report, skipped=1)
            continue

        results = driver.send_bulk(targets=targets, payload=payload)
        with transaction.atomic():
            for target, target_row, result in zip(
                targets,
                target_rows,
                results,
                strict=True,
            ):
                delivery = _persist_delivery_row(
                    organization_id=organization_id,
                    user_id=user.user_id,
                    device_row=target_row.get("device"),
                    driver_name=driver.name,
                    target=target,
                    target_address=target_row["address"],
                    target_kind=target_row["kind"],
                    event_type=event_type,
                    result=result,
                    payload_excerpt=payload_excerpt,
                )
                report.deliveries.append(delivery)
                if result.status in ("delivered", "queued"):
                    report = _bump(report, sent=1)
                else:
                    report = _bump(report, failed=1)
                # Stale tokens: soft-delete the device row + stamp
                # a reason so operators see why. ``soft_delete()``
                # narrows its update_fields to the tracking columns,
                # so persist the reason explicitly first.
                if result.status == "invalid_token" and target_row.get("device") is not None:
                    device = target_row["device"]
                    device.revoked_reason = result.error or "invalid_token"
                    device.save(
                        update_fields=[
                            "revoked_reason",
                            "updated_at",
                            "version",
                        ],
                    )
                    device.soft_delete()
                # Stamp last_used_at on the device row when push
                # successfully landed -- helps inventory aging.
                if (
                    result.status in ("delivered", "queued")
                    and isinstance(target, PushTarget)
                    and target_row.get("device") is not None
                ):
                    device = target_row["device"]
                    device.last_used_at = timezone.now()
                    device.save(
                        update_fields=[
                            "last_used_at",
                            "updated_at",
                            "version",
                        ],
                    )
    return report


# ---- device registration helpers -----------------------------------


@dataclass(frozen=True)
class RegisterResult:
    device: DeviceRegistration
    driver_result: object | None
    """Optional driver-side blob -- the NotificationDriver's
    return from ``register_device``. None when the dispatcher
    skipped the driver call (no profile)."""


def register_device(
    *,
    organization_id: int,
    user_id: int,
    device_token: str,
    platform: str,
    label: str = "",
    driver_factory: DriverFactory = None,
) -> RegisterResult:
    """Persist a new (user, token) row + register with the driver.

    Reuses an existing live row if one already matches the (user,
    device_token) pair (idempotent re-registration from a mobile
    client that re-mints its token without checking).
    """
    DevicePlatform(platform)  # raises if unknown -- input validation

    existing = (
        DeviceRegistration.objects.filter(
            user_id=user_id,
            device_token=device_token,
            deleted_at__isnull=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if existing is not None:
        # Refresh metadata on re-registration so a relabel sticks.
        if label and existing.label != label:
            existing.label = label
            existing.save(update_fields=["label", "updated_at", "version"])
        return RegisterResult(device=existing, driver_result=None)

    device = DeviceRegistration.objects.create(
        user_id=user_id,
        organization_id=organization_id,
        device_token=device_token,
        platform=platform,
        label=label,
    )

    profile = (
        NotificationProfile.objects.filter(
            organization_id=organization_id,
            is_active=True,
            deleted_at__isnull=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if profile is None:
        return RegisterResult(device=device, driver_result=None)

    factory = driver_factory or default_driver_factory
    driver = factory(profile)
    sdk_result = driver.register_device(
        RegisterDeviceRequest(
            user_id=str(user_id),
            device_token=device_token,
            platform=DevicePlatform(platform),
            label=label,
        ),
    )
    device.registration_id = sdk_result.registration_id
    device.driver_name = driver.name
    device.provider_metadata = dict(sdk_result.provider_metadata)
    device.save(
        update_fields=[
            "registration_id",
            "driver_name",
            "provider_metadata",
            "updated_at",
            "version",
        ],
    )
    return RegisterResult(device=device, driver_result=sdk_result)


def unregister_device(
    *,
    device_guid: str,
    user_id: int,
    driver_factory: DriverFactory = None,
) -> bool:
    """Revoke a device. Soft-deletes the row + best-effort calls
    the driver's revoke. Returns True when the row was found +
    flipped (idempotent: revoking an already-revoked row returns
    False)."""
    device = DeviceRegistration.objects.filter(
        guid=device_guid,
        user_id=user_id,
        deleted_at__isnull=True,
    ).first()
    if device is None:
        return False

    if device.driver_name and device.registration_id:
        try:
            # Use the device's stored driver_name to instantiate
            # the same driver that minted the registration even when
            # the org's profile has since changed. This avoids
            # leaking endpoints on profile swap.
            profile = NotificationProfile.objects.filter(
                organization_id=device.organization_id,
                driver=device.driver_name,
                deleted_at__isnull=True,
            ).first()
            if profile is not None:
                factory = driver_factory or default_driver_factory
                driver = factory(profile)
                driver.revoke_device(
                    registration_id=device.registration_id,
                )
        except Exception:
            # Best-effort: never block local soft-delete on
            # remote revoke failures. The row is the source of
            # truth for "this device gets sends"; the driver-side
            # endpoint will time out / disable on its own schedule.
            pass

    device.soft_delete()
    return True


# ---- internals -----------------------------------------------------


def _build_targets_for(
    *,
    user: UserTarget,
    device_rows: Iterable[DeviceRegistration],
) -> tuple[list[DeliveryTarget], list[dict]]:
    """Build the parallel (targets, metadata) pair for a user.

    The metadata side carries per-target rows the dispatcher uses
    when persisting delivery audit rows (which device, which kind,
    which addr).
    """
    targets: list[DeliveryTarget] = []
    rows: list[dict] = []
    for device in device_rows:
        if not device.registration_id:
            continue
        try:
            platform = DevicePlatform(device.platform)
        except ValueError:
            continue
        targets.append(
            PushTarget(
                registration_id=device.registration_id,
                platform=platform,
            ),
        )
        rows.append(
            {
                "kind": "push",
                "address": device.registration_id,
                "device": device,
            },
        )
    if user.channels.email:
        targets.append(EmailTarget(to=user.channels.email))
        rows.append(
            {"kind": "email", "address": user.channels.email, "device": None},
        )
    if user.channels.sms:
        targets.append(SmsTarget(to=user.channels.sms))
        rows.append(
            {"kind": "sms", "address": user.channels.sms, "device": None},
        )
    if user.channels.webhook_url:
        targets.append(WebhookTarget(url=user.channels.webhook_url))
        rows.append(
            {
                "kind": "webhook",
                "address": user.channels.webhook_url,
                "device": None,
            },
        )
    return targets, rows


def _persist_delivery_row(
    *,
    organization_id: int,
    user_id: str,
    device_row: DeviceRegistration | None,
    driver_name: str,
    target: DeliveryTarget,
    target_address: str,
    target_kind: str,
    event_type: str,
    result: SendResult,
    payload_excerpt: str,
) -> NotificationDelivery:
    return NotificationDelivery.objects.create(
        organization_id=organization_id,
        user_id=user_id,
        device=device_row,
        event_type=event_type,
        driver_name=driver_name,
        target_kind=target_kind,
        target_address=target_address[:512],
        status=result.status,
        provider_message_id=result.provider_message_id or "",
        error=result.error or "",
        retriable=bool(result.retriable),
        payload_excerpt=payload_excerpt,
    )


def _payload_excerpt(payload: NotificationPayload) -> str:
    text = f"{payload.title}: {payload.body}".strip(": ")
    return text[:512]


def _bump(
    report: DispatchReport,
    *,
    sent: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> DispatchReport:
    return DispatchReport(
        sent_count=report.sent_count + sent,
        failed_count=report.failed_count + failed,
        skipped_count=report.skipped_count + skipped,
        deliveries=report.deliveries,
    )


class DispatcherError(Exception):
    """Raised when configuration is fundamentally unusable -- e.g.
    a profile row carries a driver name the resolver doesn't know
    about. Resolver-side catches surface as a MutationResult
    error."""
