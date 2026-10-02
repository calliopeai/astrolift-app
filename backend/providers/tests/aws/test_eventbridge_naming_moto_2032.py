"""Real boto3/Moto bus, rule and account-wide archive identity boundaries."""

from __future__ import annotations

import dataclasses

import boto3
import pytest
from moto import mock_aws

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.event_bus_eventbridge import EventBridgeConfig, EventBridgeDriver, _name
from tests.aws._eventbridge_native_2032 import exact_native_tags
from tests.aws.test_managed_eventbridge import _spec


@pytest.fixture
def cloud(monkeypatch):
    exact_native_tags(monkeypatch)
    with mock_aws():
        api = boto3.client("events", region_name="us-west-2")
        config = EventBridgeConfig(region="us-west-2", account_id="123456789012")
        yield EventBridgeDriver(config=config, client=api), api, config


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_collisions_create_distinct_buses_and_default_archives(cloud, collision):
    driver, api, config = cloud
    declared = {"archive": {}, "rules": [{"name": "Exact_Rule", "event_pattern": {"source": ["owned"]}}]}
    first = dataclasses.replace(_spec(), organization_slug="alpha-beta", app_slug="gamma", config=declared)
    second = dataclasses.replace(
        _spec(managed_service_id="22222222-2222-4222-8222-222222222222"),
        organization_slug="alpha",
        app_slug="beta-gamma",
        config=declared,
    )
    if collision == "truncated":
        config = dataclasses.replace(config, event_bus_name_prefix="p" * 300)
        driver = EventBridgeDriver(config=config, client=api)
    old = [
        _name(
            "-".join(
                [
                    config.event_bus_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint,
                ]
            ),
            limit=256,
        )
        for spec in (first, second)
    ]
    assert old[0] == old[1]
    results = [driver.provision(spec) for spec in (first, second)]
    assert all(result.ok for result in results), results
    assert results[0].handle != results[1].handle
    for spec, result in zip((first, second), results, strict=True):
        arn = result.handle.partition("/")[2]
        bus = api.describe_event_bus(Name=arn)
        assert bus["Name"].endswith(spec.managed_service_id.replace("-", ""))
        archives = api.list_archives(EventSourceArn=arn)["Archives"]
        assert len(archives) == 1
        archive = api.describe_archive(ArchiveName=archives[0]["ArchiveName"])
        assert archive["EventSourceArn"] == arn
        assert len(archive["ArchiveName"]) <= 48
        assert spec.managed_service_id.replace("-", "") in archive["ArchiveName"]
        rules = [rule for rule in api.list_rules(EventBusName=arn)["Rules"] if not rule.get("ManagedBy")]
        assert len(rules) == 1 and rules[0]["Name"] == "Exact_Rule"
        assert rules[0]["Arn"].endswith("/" + bus["Name"] + "/Exact_Rule")
        assert driver.provision(dataclasses.replace(spec, recorded_handle=result.handle)).handle == result.handle


def test_recorded_legacy_bus_and_archive_remain_exact_after_renames(cloud):
    driver, api, config = cloud
    spec = _spec(config={"archive": {}})
    bus = api.create_event_bus(
        Name="Legacy_Bus",
        Tags=[
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": spec.managed_service_id},
        ],
    )
    api.create_archive(ArchiveName="Legacy_Bus-archive", EventSourceArn=bus["EventBusArn"])
    locator = "event_bus/" + bus["EventBusArn"]
    driver = EventBridgeDriver(config=dataclasses.replace(config, event_bus_name_prefix="changed"), client=api)
    result = driver.provision(
        dataclasses.replace(spec, recorded_handle=locator, organization_slug="changed", app_slug="changed")
    )
    assert result.ok and result.handle == locator
    names = [row["Name"] for row in api.list_event_buses()["EventBuses"]]
    assert sorted(names) == ["Legacy_Bus", "default"]
    assert api.describe_archive(ArchiveName="Legacy_Bus-archive")["EventSourceArn"] == bus["EventBusArn"]


def test_foreign_recorded_bus_cannot_be_reconfigured_retagged_or_force_deleted(cloud):
    driver, api, _ = cloud
    first = _spec()
    created = driver.provision(first)
    assert created.ok
    arn = created.handle.partition("/")[2]
    second = dataclasses.replace(
        _spec(managed_service_id="22222222-2222-4222-8222-222222222222"),
        recorded_handle=created.handle,
        config={"description": "foreign change", "archive": {}},
    )
    before = api.describe_event_bus(Name=arn)
    before.pop("ResponseMetadata", None)
    tags = api.list_tags_for_resource(ResourceARN=arn)["Tags"]
    assert not driver.provision(second).ok
    assert not driver.update(
        UpdateSpec(handle=created.handle, managed_service_id=second.managed_service_id, config=second.config)
    ).ok
    result = driver.deprovision(
        DeprovisionSpec(handle=created.handle, managed_service_id=second.managed_service_id),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok and result.retryable is False
    after = api.describe_event_bus(Name=arn)
    after.pop("ResponseMetadata", None)
    assert after == before and api.list_tags_for_resource(ResourceARN=arn)["Tags"] == tags
    assert api.list_archives(EventSourceArn=arn)["Archives"] == []


@pytest.mark.parametrize("disabled", [False, True])
def test_account_wide_archive_collision_cannot_update_or_delete_another_bus_archive(cloud, disabled):
    driver, api, _ = cloud
    first, second = _spec(), _spec(managed_service_id="22222222-2222-4222-8222-222222222222")
    owner = driver.provision(first)
    contender = driver.provision(second)
    assert owner.ok and contender.ok
    api.create_archive(ArchiveName="Shared_Archive", EventSourceArn=owner.handle.partition("/")[2], RetentionDays=30)
    cfg = {"archive": {"name": "Shared_Archive", "enabled": not disabled, "delete_data": True, "retention_days": 1}}
    result = driver.update(
        UpdateSpec(handle=contender.handle, managed_service_id=second.managed_service_id, config=cfg)
    )
    assert not result.ok and "exact bus" in result.message
    archive = api.describe_archive(ArchiveName="Shared_Archive")
    assert archive["RetentionDays"] == 30 and archive["EventSourceArn"] == owner.handle.partition("/")[2]


def test_update_tags_new_rules_with_exact_owner_and_does_not_prune_other_owner(cloud):
    driver, api, _ = cloud
    spec = _spec()
    created = driver.provision(spec)
    assert created.ok
    arn = created.handle.partition("/")[2]
    other = api.put_rule(
        Name="Other",
        EventBusName=arn,
        EventPattern='{"source":["other"]}',
        Tags=[
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"},
        ],
    )
    result = driver.update(
        UpdateSpec(
            handle=created.handle,
            managed_service_id=spec.managed_service_id,
            config={"rules": [{"name": "Mine", "event_pattern": {"source": ["mine"]}}]},
        )
    )
    assert result.ok
    rules = {row["Name"]: row for row in api.list_rules(EventBusName=arn)["Rules"]}
    assert set(rules) == {"Mine", "Other"} and rules["Other"]["Arn"] == other["RuleArn"]
    tags = {row["Key"]: row["Value"] for row in api.list_tags_for_resource(ResourceARN=rules["Mine"]["Arn"])["Tags"]}
    assert tags["astrolift.io/managed_service_id"] == spec.managed_service_id


def test_missing_recorded_bus_never_falls_back_to_new_name(cloud):
    driver, api, _ = cloud
    locator = "event_bus/arn:aws:events:us-west-2:123456789012:event-bus/Missing_Legacy"
    result = driver.provision(dataclasses.replace(_spec(), recorded_handle=locator))
    assert not result.ok and "refusing a replacement" in result.message
    assert [row["Name"] for row in api.list_event_buses()["EventBuses"]] == ["default"]


@pytest.mark.parametrize(
    "locator",
    [
        "queue/arn:aws:events:us-west-2:123456789012:event-bus/Legacy",
        "event_bus/arn:aws:events:us-west-1:123456789012:event-bus/Legacy",
        "event_bus/arn:aws:events:us-west-2:222222222222:event-bus/Legacy",
        "event_bus/arn:aws-cn:events:us-west-2:123456789012:event-bus/Legacy",
        "event_bus/arn:aws:events:us-west-2:123456789012:rule/Legacy",
    ],
)
def test_recorded_target_scope_is_refused_before_create(cloud, locator):
    driver, api, _ = cloud
    with pytest.raises(ManagedServiceError, match="configured driver target"):
        driver.provision(dataclasses.replace(_spec(), recorded_handle=locator))
    assert [row["Name"] for row in api.list_event_buses()["EventBuses"]] == ["default"]


@pytest.mark.parametrize("contamination", ["listed", "returned"])
def test_rule_metadata_cannot_retarget_tagging_or_targets_to_another_bus(cloud, contamination):
    driver, api, _ = cloud
    spec = _spec()
    owned = driver.provision(spec)
    other = driver.provision(_spec(managed_service_id="22222222-2222-4222-8222-222222222222"))
    assert owned.ok and other.ok
    other_arn = other.handle.partition("/")[2]
    foreign = api.put_rule(Name="Shared", EventBusName=other_arn, EventPattern='{"source":["foreign"]}')
    child_writes = []

    class ContaminatedMetadata:
        def __getattr__(self, name):
            return getattr(api, name)

        def list_rules(self, **params):
            return (
                {"Rules": [{"Name": "Shared", "Arn": foreign["RuleArn"]}]}
                if contamination == "listed"
                else api.list_rules(**params)
            )

        def put_rule(self, **params):
            api.put_rule(**params)
            return {"RuleArn": foreign["RuleArn"]}

        def tag_resource(self, **params):
            child_writes.append(params)
            return api.tag_resource(**params)

        def put_targets(self, **params):
            child_writes.append(params)
            return api.put_targets(**params)

    driver._events = ContaminatedMetadata()
    result = driver.update(
        UpdateSpec(
            handle=owned.handle,
            managed_service_id=spec.managed_service_id,
            config={
                "rules": [
                    {
                        "name": "Shared",
                        "event_pattern": {"source": ["owned"]},
                        "targets": [{"id": "Target", "arn": "arn:aws:sqs:us-west-2:123456789012:fixture"}],
                    }
                ]
            },
        )
    )
    assert not result.ok and "exact bus" in result.message
    assert child_writes == []
    assert api.describe_rule(Name="Shared", EventBusName=other_arn)["EventPattern"] == '{"source":["foreign"]}'
    assert api.list_targets_by_rule(Rule="Shared", EventBusName=other_arn)["Targets"] == []


def test_force_delete_preflights_every_archive_source_before_any_delete(cloud):
    driver, api, _ = cloud
    spec = _spec(config={"archive": {}})
    owned = driver.provision(spec)
    other = driver.provision(_spec(managed_service_id="22222222-2222-4222-8222-222222222222"))
    assert owned.ok and other.ok
    own_arn, other_arn = owned.handle.partition("/")[2], other.handle.partition("/")[2]
    own_archives = api.list_archives(EventSourceArn=own_arn)["Archives"]
    api.create_archive(ArchiveName="Foreign_Archive", EventSourceArn=other_arn)
    writes = []

    class ContaminatedArchives:
        def __getattr__(self, name):
            return getattr(api, name)

        def list_archives(self, **params):
            return {"Archives": [*own_archives, {"ArchiveName": "Foreign_Archive"}]}

        def delete_archive(self, **params):
            writes.append(params)
            return api.delete_archive(**params)

    driver._events = ContaminatedArchives()
    result = driver.deprovision(
        DeprovisionSpec(handle=owned.handle, managed_service_id=spec.managed_service_id),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok and "exact bus" in result.message and result.retryable is False
    assert writes == []
    assert api.describe_archive(ArchiveName=own_archives[0]["ArchiveName"])["EventSourceArn"] == own_arn
    assert api.describe_archive(ArchiveName="Foreign_Archive")["EventSourceArn"] == other_arn


@pytest.mark.parametrize("names", [("a b", "a-b"), ("r" * 64 + "a", "r" * 64 + "b")])
def test_colliding_explicit_child_declarations_refuse_before_bus_creation(cloud, names):
    driver, api, _ = cloud
    result = driver.provision(
        _spec(config={"rules": [{"name": name, "event_pattern": {"source": [name]}} for name in names]})
    )
    assert not result.ok and "duplicate rule name" in result.message
    assert [bus["Name"] for bus in api.list_event_buses()["EventBuses"]] == ["default"]
