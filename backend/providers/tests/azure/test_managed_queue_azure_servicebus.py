"""Sibling topic/default-subscription contract with real SDK recording transport."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, SnapshotHandle
from azure.managed.queue_servicebus import AzureServiceBusDriver, AzureServiceBusError

from . import test_servicebus_topic_wire_2032 as wire
from .test_servicebus_queue_wire_2032 import OWNER, spec
from .test_servicebus_topic_wire_2032 import owned


@pytest.fixture(params=["queue", "topic"])
def runtime(request):
    api = wire.RecordingTopics()
    cfg = wire.recording_config(api, kind=request.param)
    driver = AzureServiceBusDriver(config=cfg)
    yield api, driver
    cfg.client.close()
    assert api.closed


def test_exact_saved_long_names_child_and_data_survive_slug_prefix_changes(runtime):
    api, driver, _, target = owned(runtime)
    topic = "L" * 260
    child = "ExactHistoricalChild" + "c" * 30
    assert len(child) == 50
    saved = driver._coordinates(topic, child)
    old_topic, old_child = api.rows.pop(target.topic_id), api.rows.pop(target.child_id)
    for row, path in [(old_topic, saved.topic_id), (old_child, saved.child_id)]:
        row["id"] = path
        api.rows[path] = row
    api.payloads[saved.child_id] = [b"controlled retained messages"]
    handle = saved.handle(driver._config.handle_kind)
    assert len(handle) <= 512
    changed = AzureServiceBusDriver(config=dataclasses.replace(driver._config, topic_name_prefix="different"))
    result = changed.provision(
        spec(recorded_handle=handle, organization_slug="renamed", app_slug="renamed", environment_name="renamed")
    )
    assert result.ok and result.handle == handle
    assert set(api.rows) == {saved.topic_id, saved.child_id}
    assert api.payloads[saved.child_id] == [b"controlled retained messages"]
    binding = changed.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert binding.env_vars["SERVICEBUS_TOPIC"].literal == topic
    assert binding.env_vars["SERVICEBUS_SUBSCRIPTION"].literal == child
    assert all(verb != "DELETE" for verb, *_ in api.calls)


def test_child_missing_but_parent_owned_cannot_recreate_recorded_pair(runtime):
    api, driver, handle, target = owned(runtime)
    del api.rows[target.child_id]
    before = copy.deepcopy(api.rows)
    result = driver.provision(spec(recorded_handle=handle))
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)


def test_parent_missing_but_remaining_owned_child_never_claims_absent_cleanup(runtime):
    api, driver, handle, target = owned(runtime)
    del api.rows[target.topic_id]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert target.child_id in api.rows and all(verb == "GET" for verb, *_ in api.calls)


def test_both_flags_still_require_owned_pair_and_preserve_unsupported_backup_contract(runtime):
    api, driver, handle, _ = owned(runtime)
    with pytest.raises(AzureServiceBusError, match="snapshot"):
        driver.snapshot(ServiceHandle(handle, managed_service_id=OWNER))
    with pytest.raises(AzureServiceBusError, match="restore"):
        driver.restore(SnapshotHandle(handle, "snapshot", "2026-01-01T00:00:00Z"), spec())
    assert api.calls == []
    assert driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    ).ok


def test_schema_and_binding_schema_preserve_both_aliases(runtime):
    _, driver = runtime
    assert set(driver.config_schema()["properties"]) == {
        "max_size_in_megabytes",
        "enable_partitioning",
        "default_message_ttl",
        "lock_duration",
        "max_delivery_count",
        "dead_lettering_on_message_expiration",
    }
    assert set(driver.binding_schema().env_vars) >= {
        "SERVICEBUS_NAMESPACE",
        "SERVICEBUS_TOPIC",
        "SERVICEBUS_SUBSCRIPTION",
        "SERVICEBUS_ENDPOINT",
    }
    if driver._config.handle_kind == "topic":
        assert set(driver.binding_schema().env_vars) >= {"TOPIC_ARN_OR_ID", "TOPIC_NAME", "TOPIC_REGION"}


@pytest.mark.parametrize(
    "config",
    [
        {"max_delivery_count": True},
        {"enable_partitioning": "false"},
        {"lock_duration": "PT6M"},
        {"default_message_ttl": "P0D"},
        {"default_message_ttl": "bad"},
        {"max_size_in_megabytes": 0},
    ],
)
def test_invalid_config_is_not_false_applied_or_partial_cloud_write(runtime, config):
    api, driver = runtime
    result = driver.provision(spec(config=config))
    assert not result.ok and "ownership_refused" in result.errors
    assert not api.rows and all(verb == "GET" for verb, *_ in api.calls)
