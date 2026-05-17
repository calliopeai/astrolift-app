"""Tests for NotificationProfile policy (#490)."""

from __future__ import annotations

import pytest

from astrolift_operations.notification_profile import (
    RETENTION_DELIVERY,
    NotificationDriverKind,
    NotificationProfileError,
    NotificationProfileSpec,
    RetentionConfig,
    collect_profile_issues,
    validate_notification_driver_config,
    validate_profile,
)

# ---- retention -----------------------------------------------------


def test_retention_defaults_locked() -> None:
    assert RETENTION_DELIVERY.default_days == 30
    assert RETENTION_DELIVERY.max_days == 365


def test_retention_config_default_value() -> None:
    cfg = RetentionConfig()
    assert cfg.delivery_days == 30


def test_retention_rejects_negative() -> None:
    with pytest.raises(NotificationProfileError, match="delivery_days"):
        RetentionConfig(delivery_days=0)
    with pytest.raises(NotificationProfileError, match="delivery_days"):
        RetentionConfig(delivery_days=-1)


def test_retention_rejects_over_max() -> None:
    with pytest.raises(NotificationProfileError, match="exceeds max"):
        RetentionConfig(delivery_days=10000)


# ---- per-driver validation ----------------------------------------


def test_unknown_driver_rejected() -> None:
    with pytest.raises(NotificationProfileError, match="unknown notification driver"):
        validate_notification_driver_config(
            driver="nope",
            config={"foo": "bar"},
        )


def test_non_mapping_config_rejected() -> None:
    with pytest.raises(NotificationProfileError, match="must be a mapping"):
        validate_notification_driver_config(
            driver="aws_sns",
            config=["not", "a", "mapping"],
        )


# ---- AWS SNS specifics --------------------------------------------


def test_aws_sns_requires_platform_applications_map() -> None:
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="aws_sns",
            config={"region": "us-east-1"},
        )


def test_aws_sns_requires_non_empty_platform_map() -> None:
    with pytest.raises(NotificationProfileError, match="non-empty"):
        validate_notification_driver_config(
            driver="aws_sns",
            config={
                "region": "us-east-1",
                "platform_applications": {},
            },
        )


def test_aws_sns_rejects_unknown_platform() -> None:
    with pytest.raises(NotificationProfileError, match="unknown platform"):
        validate_notification_driver_config(
            driver="aws_sns",
            config={
                "region": "us-east-1",
                "platform_applications": {
                    "blackberry": "arn:aws:sns:us-east-1:1:app/foo",
                },
            },
        )


def test_aws_sns_rejects_bad_arn() -> None:
    with pytest.raises(NotificationProfileError, match="PlatformApplicationArn"):
        validate_notification_driver_config(
            driver="aws_sns",
            config={
                "region": "us-east-1",
                "platform_applications": {"ios": "garbage"},
            },
        )


def test_aws_sns_accepts_valid_config() -> None:
    kind = validate_notification_driver_config(
        driver="aws_sns",
        config={
            "region": "us-east-1",
            "platform_applications": {
                "ios": "arn:aws:sns:us-east-1:1:app/APNS/astrolift-ios",
                "android": "arn:aws:sns:us-east-1:1:app/GCM/astrolift-android",
            },
        },
    )
    assert kind == NotificationDriverKind.AWS_SNS


# ---- GCP FCM ------------------------------------------------------


def test_gcp_fcm_requires_project_id() -> None:
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="gcp_fcm",
            config={},
        )


def test_gcp_fcm_accepts_project_id() -> None:
    kind = validate_notification_driver_config(
        driver="gcp_fcm",
        config={"project_id": "my-project"},
    )
    assert kind == NotificationDriverKind.GCP_FCM


# ---- Azure ANH ----------------------------------------------------


def test_azure_anh_requires_all_namespace_fields() -> None:
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="azure_anh",
            config={"namespace": "ns"},
        )


def test_azure_anh_accepts_full_config() -> None:
    kind = validate_notification_driver_config(
        driver="azure_anh",
        config={
            "namespace": "ns",
            "hub_name": "hub",
            "shared_access_key_name": "DefaultFull",
            "shared_access_key_secret_ref": "secrets://anh/key",
        },
    )
    assert kind == NotificationDriverKind.AZURE_ANH


# ---- OTLP webhook -------------------------------------------------


def test_otlp_webhook_requires_channels() -> None:
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="otlp_webhook",
            config={},
        )


def test_otlp_webhook_rejects_empty_channels() -> None:
    with pytest.raises(NotificationProfileError, match="at least one channel"):
        validate_notification_driver_config(
            driver="otlp_webhook",
            config={"channels": []},
        )


def test_otlp_webhook_rejects_unknown_channel() -> None:
    with pytest.raises(NotificationProfileError, match="unknown channel"):
        validate_notification_driver_config(
            driver="otlp_webhook",
            config={"channels": ["carrier_pigeon"]},
        )


def test_otlp_webhook_accepts_valid_channels() -> None:
    kind = validate_notification_driver_config(
        driver="otlp_webhook",
        config={"channels": ["webhook", "smtp"]},
    )
    assert kind == NotificationDriverKind.OTLP_WEBHOOK


# ---- multiplexer --------------------------------------------------


def test_multiplexer_requires_primary_and_secondaries() -> None:
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="multiplexer",
            config={"primary": {}},
        )


def test_multiplexer_rejects_nested_multiplexer() -> None:
    with pytest.raises(NotificationProfileError, match="cannot itself be a multiplexer"):
        validate_notification_driver_config(
            driver="multiplexer",
            config={
                "primary": {
                    "driver": "multiplexer",
                    "config": {"primary": {}, "secondaries": []},
                },
                "secondaries": [
                    {
                        "driver": "gcp_fcm",
                        "config": {"project_id": "p"},
                    },
                ],
            },
        )


def test_multiplexer_rejects_empty_secondaries() -> None:
    with pytest.raises(NotificationProfileError, match="non-empty"):
        validate_notification_driver_config(
            driver="multiplexer",
            config={
                "primary": {
                    "driver": "gcp_fcm",
                    "config": {"project_id": "p"},
                },
                "secondaries": [],
            },
        )


def test_multiplexer_validates_each_child() -> None:
    # Secondary with bad config should bubble the child's error.
    with pytest.raises(NotificationProfileError, match="missing required keys"):
        validate_notification_driver_config(
            driver="multiplexer",
            config={
                "primary": {
                    "driver": "gcp_fcm",
                    "config": {"project_id": "p"},
                },
                "secondaries": [
                    {"driver": "otlp_webhook", "config": {}},
                ],
            },
        )


def test_multiplexer_accepts_well_formed_config() -> None:
    kind = validate_notification_driver_config(
        driver="multiplexer",
        config={
            "primary": {
                "driver": "gcp_fcm",
                "config": {"project_id": "p"},
            },
            "secondaries": [
                {
                    "driver": "otlp_webhook",
                    "config": {"channels": ["webhook"]},
                },
            ],
        },
    )
    assert kind == NotificationDriverKind.MULTIPLEXER


# ---- profile-level ------------------------------------------------


def test_validate_profile_full_happy_path() -> None:
    validate_profile(
        profile=NotificationProfileSpec(
            driver="gcp_fcm",
            config={"project_id": "p"},
        ),
    )


def test_collect_profile_issues_aggregates_errors() -> None:
    issues = collect_profile_issues(
        profile=NotificationProfileSpec(
            driver="aws_sns",
            config={"region": "us-east-1"},  # missing platform_applications
        ),
    )
    assert len(issues) == 1
    assert "platform_applications" in issues[0]


def test_collect_profile_issues_empty_when_valid() -> None:
    issues = collect_profile_issues(
        profile=NotificationProfileSpec(
            driver="gcp_fcm",
            config={"project_id": "p"},
        ),
    )
    assert issues == ()
