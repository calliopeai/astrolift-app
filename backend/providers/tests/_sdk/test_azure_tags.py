"""Azure ARM tag serialization contract."""

from __future__ import annotations

import pytest

from _sdk.azure_tags import AzureTagError, serialize_azure_arm_tags


def test_platform_and_custom_keys_are_valid_and_collision_resistant() -> None:
    tags = serialize_azure_arm_tags(
        {
            "astrolift.io/managed_service_id": "service-id",
            "astrolift.io/binding": "binding-id",
        },
        custom_tags={"Cost Center": "engineering", "cost/center": "operations"},
    )
    assert tags["astrolift-managed-service-id"] == "service-id"
    assert tags["astrolift-binding"] == "binding-id"
    custom = {key: value for key, value in tags.items() if key.startswith("astrolift-extra-cost-center-")}
    assert len(custom) == 2
    assert set(custom.values()) == {"engineering", "operations"}
    assert all(not set("<>%&\\?/") & set(key) for key in tags)


def test_case_insensitive_platform_collisions_fail_closed() -> None:
    with pytest.raises(AzureTagError, match="normalize to the same"):
        serialize_azure_arm_tags(
            {
                "astrolift.io/App": "one",
                "astrolift.io/app": "two",
            },
        )


def test_limits_and_non_platform_keys_fail_closed() -> None:
    with pytest.raises(AzureTagError, match="namespace"):
        serialize_azure_arm_tags({"owner": "platform"})
    with pytest.raises(AzureTagError, match="256"):
        serialize_azure_arm_tags({"astrolift.io/app": "x" * 257})
    with pytest.raises(AzureTagError, match="at most 50"):
        serialize_azure_arm_tags(
            {"astrolift.io/app": "api"},
            custom_tags={f"tag-{index}": "value" for index in range(50)},
        )
