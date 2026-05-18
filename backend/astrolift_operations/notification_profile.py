"""
NotificationProfile policy (#490).

Pure-Python policy mirroring ``observability_profile.py`` shape.
Validates an install's notification configuration: which driver
ships to which channel, how multiplexer fan-out is structured,
and the retention bounds for the per-send delivery audit rows.

The actual ``NotificationDriver`` implementations live under
``backend/providers/{aws,gcp,azure,k8s_native}/``;
this module is the shape contract orgs configure against.

A NotificationProfile carries:

* a primary ``driver`` (``aws_sns`` / ``gcp_fcm`` / ``azure_anh``
  / ``otlp_webhook`` / ``multiplexer``)
* a per-driver ``config`` blob (required keys validated against
  ``_REQUIRED_KEYS_BY_NOTIFICATION_DRIVER``)
* a ``retention`` window on the per-send delivery audit log
  (default 30 days; max 365)

When ``driver=multiplexer`` the ``config`` carries a ``children``
list -- each child is itself a {driver, config} pair, recursively
validated. No nesting (children may not themselves be
multiplexers; same cycle-defense as the observability profile).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum


class NotificationProfileError(ValueError):
    pass


# ---- driver registry -----------------------------------------------


class NotificationDriverKind(StrEnum):
    """Wire identifiers matching ``NotificationDriver.name`` on the
    SDK side. Frontend codegen narrows to these literals."""

    AWS_SNS = "aws_sns"
    GCP_FCM = "gcp_fcm"
    AZURE_ANH = "azure_anh"
    OTLP_WEBHOOK = "otlp_webhook"
    MULTIPLEXER = "multiplexer"


# ---- per-driver required-config keys -------------------------------


_REQUIRED_KEYS_BY_NOTIFICATION_DRIVER: dict[NotificationDriverKind, frozenset[str]] = {
    # SNS needs the platform-application map (per-platform ARN). The
    # driver accepts a partial map but the operator must have wired
    # at least one platform's ARN for the driver to deliver anything;
    # the policy layer enforces that here.
    NotificationDriverKind.AWS_SNS: frozenset({"region", "platform_applications"}),
    NotificationDriverKind.GCP_FCM: frozenset({"project_id"}),
    NotificationDriverKind.AZURE_ANH: frozenset(
        {
            "namespace",
            "hub_name",
            "shared_access_key_name",
            "shared_access_key_secret_ref",
        },
    ),
    # OTLP/webhook: needs at least one delivery channel configured.
    # We require ``channels`` to be non-empty rather than picking one
    # specific field -- that surfaces a clearer error to operators
    # who forgot to wire any sink.
    NotificationDriverKind.OTLP_WEBHOOK: frozenset({"channels"}),
    NotificationDriverKind.MULTIPLEXER: frozenset({"primary", "secondaries"}),
}


# ---- retention bounds ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionWindow:
    default_days: int
    max_days: int

    def __post_init__(self) -> None:
        if self.default_days <= 0:
            raise NotificationProfileError(
                f"default_days must be > 0, got {self.default_days}",
            )
        if self.max_days < self.default_days:
            raise NotificationProfileError(
                f"max_days {self.max_days} < default_days {self.default_days}",
            )


# Delivery audit rows carry one row per send + outcome. Default 30
# days keeps debug history available; max 365 covers compliance
# windows that require multi-quarter retention without burning
# storage on a write-heavy log.
RETENTION_DELIVERY = RetentionWindow(default_days=30, max_days=365)


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionConfig:
    delivery_days: int = RETENTION_DELIVERY.default_days

    def __post_init__(self) -> None:
        if self.delivery_days <= 0:
            raise NotificationProfileError(
                f"delivery_days must be > 0, got {self.delivery_days}",
            )
        if self.delivery_days > RETENTION_DELIVERY.max_days:
            raise NotificationProfileError(
                f"delivery_days {self.delivery_days} exceeds max {RETENTION_DELIVERY.max_days}",
            )


# ---- driver config validation --------------------------------------


def _validate_required_keys(
    *,
    driver_label: str,
    required: frozenset[str],
    config: Mapping,
) -> None:
    missing = required - set(config.keys())
    if missing:
        raise NotificationProfileError(
            f"{driver_label} config missing required keys: {sorted(missing)}",
        )


def _validate_aws_sns_extras(*, config: Mapping) -> None:
    platform_applications = config.get("platform_applications")
    if not isinstance(platform_applications, Mapping):
        raise NotificationProfileError(
            "aws_sns config.platform_applications must be a mapping of platform → PlatformApplicationArn",
        )
    if not platform_applications:
        raise NotificationProfileError(
            "aws_sns config.platform_applications must be non-empty "
            "(at least one platform's ARN must be configured)",
        )
    for platform, arn in platform_applications.items():
        if platform not in {"ios", "android", "web"}:
            raise NotificationProfileError(
                f"aws_sns platform_applications: unknown platform {platform!r}; valid: ios, android, web",
            )
        if not isinstance(arn, str) or not arn.startswith("arn:aws:sns:"):
            raise NotificationProfileError(
                f"aws_sns platform_applications[{platform!r}]: value "
                f"must be a non-empty SNS PlatformApplicationArn",
            )


def _validate_otlp_webhook_extras(*, config: Mapping) -> None:
    channels = config.get("channels")
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        raise NotificationProfileError(
            "otlp_webhook config.channels must be a list",
        )
    known = {"webhook", "smtp", "push_bridge"}
    if not channels:
        raise NotificationProfileError(
            "otlp_webhook config.channels must list at least one channel",
        )
    for channel in channels:
        if channel not in known:
            raise NotificationProfileError(
                f"otlp_webhook channels: unknown channel {channel!r}; valid: {sorted(known)}",
            )


def _validate_multiplexer_children(
    *,
    config: Mapping,
    parse_child: Callable[[str, Mapping], NotificationDriverKind],
) -> None:
    primary = config.get("primary")
    if not isinstance(primary, Mapping):
        raise NotificationProfileError(
            "multiplexer config.primary must be a {driver, config} mapping",
        )
    if "driver" not in primary or "config" not in primary:
        raise NotificationProfileError(
            "multiplexer config.primary missing 'driver' or 'config'",
        )
    if primary["driver"] == NotificationDriverKind.MULTIPLEXER.value:
        raise NotificationProfileError(
            "multiplexer config.primary cannot itself be a multiplexer",
        )
    parse_child(primary["driver"], primary["config"])

    secondaries = config.get("secondaries")
    if not isinstance(secondaries, Sequence) or isinstance(secondaries, (str, bytes)):
        raise NotificationProfileError(
            "multiplexer config.secondaries must be a list",
        )
    if not secondaries:
        raise NotificationProfileError(
            "multiplexer config.secondaries must be non-empty "
            "(use the primary driver directly when there is no fan-out)",
        )
    for i, child in enumerate(secondaries):
        if not isinstance(child, Mapping):
            raise NotificationProfileError(
                f"multiplexer secondaries[{i}] must be a mapping",
            )
        if "driver" not in child or "config" not in child:
            raise NotificationProfileError(
                f"multiplexer secondaries[{i}] missing 'driver' or 'config'",
            )
        if child["driver"] == NotificationDriverKind.MULTIPLEXER.value:
            raise NotificationProfileError(
                f"multiplexer secondaries[{i}] cannot itself be a multiplexer",
            )
        parse_child(child["driver"], child["config"])


def validate_notification_driver_config(
    *,
    driver: str,
    config: Mapping,
) -> NotificationDriverKind:
    try:
        kind = NotificationDriverKind(driver)
    except ValueError as exc:
        raise NotificationProfileError(
            f"unknown notification driver {driver!r}; known: {[d.value for d in NotificationDriverKind]}",
        ) from exc

    if not isinstance(config, Mapping):
        raise NotificationProfileError(
            f"notification driver {driver!r} config must be a mapping",
        )

    _validate_required_keys(
        driver_label=f"notification driver {kind.value}",
        required=_REQUIRED_KEYS_BY_NOTIFICATION_DRIVER[kind],
        config=config,
    )

    if kind == NotificationDriverKind.AWS_SNS:
        _validate_aws_sns_extras(config=config)
    elif kind == NotificationDriverKind.OTLP_WEBHOOK:
        _validate_otlp_webhook_extras(config=config)
    elif kind == NotificationDriverKind.MULTIPLEXER:
        _validate_multiplexer_children(
            config=config,
            parse_child=lambda d, c: validate_notification_driver_config(
                driver=d,
                config=c,
            ),
        )

    return kind


# ---- whole-profile validation --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class NotificationProfileSpec:
    driver: str
    config: Mapping
    retention: RetentionConfig = dataclasses.field(default_factory=RetentionConfig)


def validate_profile(*, profile: NotificationProfileSpec) -> None:
    validate_notification_driver_config(
        driver=profile.driver,
        config=profile.config,
    )
    # RetentionConfig validates in __post_init__.


def collect_profile_issues(
    *,
    profile: NotificationProfileSpec,
) -> tuple[str, ...]:
    issues: list[str] = []
    try:
        validate_notification_driver_config(
            driver=profile.driver,
            config=profile.config,
        )
    except NotificationProfileError as exc:
        issues.append(f"driver: {exc}")
    return tuple(issues)
