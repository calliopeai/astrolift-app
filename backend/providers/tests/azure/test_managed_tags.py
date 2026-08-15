"""Cross-driver Azure managed-resource tag contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from _sdk.azure_tags import AzureTagError
from _sdk.managed_service import ProvisionSpec
from azure.managed import (
    cache_redis,
    cosmos,
    email_acs,
    model_endpoint_aoai,
    mysql_flexible,
    postgres_flexible,
    queue_servicebus,
    search_aisearch,
    timeseries_monitor,
    vector_search,
)
from azure.managed.tags import arm_tags_for


def _spec() -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="api",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="shared",
        size="small",
        binding_id="binding-id",
        managed_service_id="service-id",
        tags={"Cost Center": "engineering", "cost/center": "operations"},
    )


def test_every_arm_managed_driver_uses_the_shared_codec() -> None:
    for module in (
        cache_redis,
        cosmos,
        email_acs,
        model_endpoint_aoai,
        mysql_flexible,
        postgres_flexible,
        search_aisearch,
        timeseries_monitor,
        vector_search,
    ):
        assert module.tags_for is arm_tags_for
    assert queue_servicebus._tags_for is arm_tags_for


def test_managed_tag_envelope_is_arm_safe_and_cost_joinable() -> None:
    tags = arm_tags_for(
        _spec(),
        platform_tags={"retention-days": 30},
    )
    assert tags["astrolift-managed-by"] == "platform"
    assert tags["astrolift-org"] == "acme"
    assert tags["astrolift-env"] == "prod"
    assert tags["astrolift-binding"] == "binding-id"
    assert tags["astrolift-managed-service-id"] == "service-id"
    assert tags["astrolift-retention-days"] == "30"
    assert len({key.casefold() for key in tags}) == len(tags)
    assert all(not set("<>%&\\?/") & set(key) for key in tags)


def test_managed_tag_limits_fail_before_a_driver_can_build_a_request() -> None:
    with pytest.raises(AzureTagError, match="at most 50"):
        arm_tags_for(replace(_spec(), tags={f"tag-{index}": "value" for index in range(43)}))
    with pytest.raises(AzureTagError, match="256"):
        arm_tags_for(replace(_spec(), tags={"oversized": "x" * 257}))
