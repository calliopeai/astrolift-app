"""Persisted event buses and archives reach native SDK calls through the registry."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import boto3
import pytest
from aws.managed.event_bus_eventbridge import EventBridgeConfig, EventBridgeDriver, _name
from moto import mock_aws

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.aws._eventbridge_native_2032 import exact_native_tags

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    exact_native_tags(monkeypatch)
    with mock_aws():
        state = SimpleNamespace(
            api=boto3.client("events", region_name="us-west-2"),
            cfg=EventBridgeConfig(region="us-west-2", account_id="123456789012"),
        )

        class Driver(EventBridgeDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:event_bus:eventbridge": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        yield state


def new_service(org, app="api", *, archive=False):
    row = _service(org_slug=org, plugin_slug="aws", variant="eventbridge", backend_ref="")
    row.kind, row.name = "event_bus", "events"
    row.config = {"archive": {}} if archive else {}
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    return row


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_actual_two_org_collision_gets_distinct_saved_buses_and_archives(cloud, collision):
    first, second = (
        new_service("alpha-beta", "gamma", archive=True),
        new_service("alpha", "beta-gamma", archive=True),
    )
    for row in (first, second):
        row.config = {**row.config, "rules": [{"name": "Exact_Rule", "event_pattern": {"source": ["owned"]}}]}
        row.save(update_fields=["config"])
    if collision == "truncated":
        cloud.cfg = dataclasses.replace(cloud.cfg, event_bus_name_prefix="p" * 300)
    old = [
        _name(
            "-".join(
                [
                    cloud.cfg.event_bus_name_prefix,
                    row.registered_app.organization.slug,
                    row.registered_app.slug,
                    row.app_environment.name,
                    row.name,
                ]
            ),
            limit=256,
        )
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        bus = cloud.api.describe_event_bus(Name=arn)
        assert bus["Name"].endswith(row.guid.hex)
        row.backend_ref = result["handle"]
        row.save(update_fields=["backend_ref"])
        assert _provision_sync(row.pk)["handle"] == row.backend_ref
        archives = cloud.api.list_archives(EventSourceArn=arn)["Archives"]
        assert len(archives) == 1 and row.guid.hex in archives[0]["ArchiveName"]
        rules = [rule for rule in cloud.api.list_rules(EventBusName=arn)["Rules"] if not rule.get("ManagedBy")]
        assert len(rules) == 1 and rules[0]["Name"] == "Exact_Rule"
        assert rules[0]["Arn"].endswith("/" + bus["Name"] + "/Exact_Rule")


def test_actual_legacy_bus_and_archive_preserve_recorded_names_after_rename(cloud):
    row = new_service("legacy", archive=True)
    created = cloud.api.create_event_bus(
        Name="Legacy_Bus",
        Tags=[
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": str(row.guid)},
        ],
    )
    arn = created["EventBusArn"]
    cloud.api.create_archive(ArchiveName="Legacy_Bus-archive", EventSourceArn=arn)
    row.backend_ref = "event_bus/" + arn
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, event_bus_name_prefix="changed")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == row.backend_ref
    assert sorted(bus["Name"] for bus in cloud.api.list_event_buses()["EventBuses"]) == [
        "Legacy_Bus",
        "default",
    ]
    assert cloud.api.describe_archive(ArchiveName="Legacy_Bus-archive")["EventSourceArn"] == arn


def test_actual_lifecycle_cannot_change_or_force_delete_foreign_recorded_bus(cloud):
    owner, contender = new_service("owner"), new_service("contender")
    created = _provision_sync(owner.pk)
    assert created["ok"]
    owner.backend_ref = contender.backend_ref = created["handle"]
    owner.save(update_fields=["backend_ref"])
    contender.save(update_fields=["backend_ref"])
    arn = created["handle"].partition("/")[2]
    tags = cloud.api.list_tags_for_resource(ResourceARN=arn)["Tags"]
    assert not _provision_sync(contender.pk)["ok"]
    assert not _update_sync(contender.pk)["ok"]
    assert not _deprovision_sync(contender.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_tags_for_resource(ResourceARN=arn)["Tags"] == tags
    assert _update_sync(owner.pk)["ok"]
    assert _deprovision_sync(owner.pk, delete_data=True, force_destroy=True)["ok"]
    assert [bus["Name"] for bus in cloud.api.list_event_buses()["EventBuses"]] == ["default"]


@pytest.mark.parametrize("disabled", [False, True])
def test_actual_update_cannot_reuse_or_delete_another_bus_account_wide_archive(cloud, disabled):
    owner, contender = new_service("archive-owner"), new_service("archive-contender")
    created, other = _provision_sync(owner.pk), _provision_sync(contender.pk)
    assert created["ok"] and other["ok"]
    arn = created["handle"].partition("/")[2]
    cloud.api.create_archive(ArchiveName="Shared_Archive", EventSourceArn=arn, RetentionDays=30)
    contender.backend_ref = other["handle"]
    contender.config = {
        "archive": {
            "name": "Shared_Archive",
            "enabled": not disabled,
            "delete_data": True,
            "retention_days": 1,
        }
    }
    contender.save(update_fields=["backend_ref", "config"])
    refused = _update_sync(contender.pk)
    assert not refused["ok"] and "exact bus" in refused["message"]
    archive = cloud.api.describe_archive(ArchiveName="Shared_Archive")
    assert archive["RetentionDays"] == 30 and archive["EventSourceArn"] == arn


def test_actual_missing_recorded_bus_does_not_create_replacement(cloud):
    row = new_service("missing")
    row.backend_ref = "event_bus/arn:aws:events:us-west-2:123456789012:event-bus/Missing_Legacy"
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"] and "refusing a replacement" in result["message"]
    assert [bus["Name"] for bus in cloud.api.list_event_buses()["EventBuses"]] == ["default"]
