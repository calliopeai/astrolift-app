"""Saved SNS targets reach real boto3/Moto lifecycle calls through the registry."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import boto3
import pytest
from aws.managed._base import ManagedServiceError
from aws.managed.topic_sns import SNSConfig, SNSFifoTopicDriver, SNSStandardTopicDriver
from moto import mock_aws

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["sns", "sns_fifo"])
def cloud(request, monkeypatch):
    with mock_aws():
        client = boto3.client("sns", region_name="us-west-2")
        state = SimpleNamespace(
            api=client, cfg=SNSConfig(region="us-west-2", account_id="123456789012"), variant=request.param
        )
        base = SNSFifoTopicDriver if request.param == "sns_fifo" else SNSStandardTopicDriver

        class Driver(base):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={f"managed:topic:{request.param}": Driver},
            )
        )
        state.cls = Driver
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        yield state


def new_service(cloud, org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    svc.kind, svc.name = "topic", "events"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_saved_orgs_with_colliding_previous_names_receive_distinct_owned_topics(cloud, collision):
    if collision == "joined":
        first, second = new_service(cloud, "alpha-beta", "gamma"), new_service(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="p" * 300)
        first, second = new_service(cloud, "alpha"), new_service(cloud, "beta")
    suffix = ".fifo" if cloud.variant == "sns_fifo" else ""
    old = [
        "-".join(
            (
                cloud.cfg.topic_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[: 256 - len(suffix)]
        + suffix
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        name = arn.rsplit(":", 1)[1]
        assert name.endswith(row.guid.hex + suffix) and len(name) <= 256
        tags = cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
        assert {tag["Key"]: tag["Value"] for tag in tags}["astrolift.io/managed_service_id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_topics()["Topics"]) == 2


def test_recorded_case_sensitive_legacy_topic_survives_slug_and_prefix_changes(cloud):
    row = new_service(cloud, "legacy")
    suffix = ".fifo" if cloud.variant == "sns_fifo" else ""
    locator = f"topic/arn:aws:sns:us-west-2:123456789012:Old_Topic{suffix}"
    spec = dataclasses.replace(
        build_provision_spec(row, cluster=row.app_environment.tenant_cluster), recorded_handle=locator
    )
    initial = cloud.cls(config=cloud.cfg).provision(spec)
    assert initial.ok and initial.handle == locator
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="renamed-prefix")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == locator
    assert cloud.api.list_topics()["Topics"] == [{"TopicArn": locator.partition("/")[2]}]
    row.refresh_from_db()
    assert row.backend_ref == locator


def test_foreign_recorded_topic_cannot_be_retagged_reconfigured_or_subscribed(cloud):
    first, second = new_service(cloud, "owner"), new_service(cloud, "contender")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    arn = initial["handle"].partition("/")[2]
    second.backend_ref = initial["handle"]
    second.config = {
        "managed_service_id": str(first.guid),
        "display_name": "foreign-change",
        "policy": {"Statement": []},
        "subscriptions": [{"protocol": "sqs", "endpoint": "arn:aws:sqs:us-west-2:123456789012:unwanted"}],
    }
    second.save(update_fields=["backend_ref", "config"])
    before_tags = cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
    before_attrs = cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"]
    before_subs = cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
    result = _provision_sync(second.pk)
    assert not result["ok"] and "ownership does not match" in result["message"]
    assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"] == before_tags
    assert cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"] == before_attrs
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] == before_subs
    assert len(cloud.api.list_topics()["Topics"]) == 1


def test_foreign_topic_created_after_missing_read_cannot_be_adopted_by_create_retry(cloud):
    row = new_service(cloud, "contender")
    row.config = {"display_name": "must-not-change"}
    row.save(update_fields=["config"])
    actual = cloud.api
    foreign = "22222222-2222-4222-8222-222222222222"
    race = []

    class RacingSNS:
        def __getattr__(self, name):
            return getattr(actual, name)

        def create_topic(self, **params):
            foreign_tags = [
                {"Key": "astrolift.io/managed-by", "Value": "platform"},
                {"Key": "astrolift.io/managed_service_id", "Value": foreign},
            ]
            arn = actual.create_topic(
                Name=params["Name"], Attributes=params["Attributes"], Tags=foreign_tags
            )["TopicArn"]
            race.append(arn)
            return actual.create_topic(**params)

    cloud.api = RacingSNS()
    result = _provision_sync(row.pk)
    assert not result["ok"] and "ownership does not match" in result["message"]
    assert len(race) == 1
    tags = {tag["Key"]: tag["Value"] for tag in actual.list_tags_for_resource(ResourceArn=race[0])["Tags"]}
    assert tags["astrolift.io/managed_service_id"] == foreign
    assert actual.get_topic_attributes(TopicArn=race[0])["Attributes"].get("DisplayName") != "must-not-change"
    assert len(actual.list_topics()["Topics"]) == 1


@pytest.mark.parametrize("target", ["kind", "account", "region", "partition", "mode", "name"])
def test_recorded_invalid_target_never_creates_a_fallback(cloud, target):
    row = new_service(cloud, "wrong-target")
    suffix = ".fifo" if cloud.variant == "sns_fifo" else ""
    locator = f"topic/arn:aws:sns:us-west-2:123456789012:Original{suffix}"
    if target == "kind":
        locator = locator.replace("topic/", "stream/", 1)
    elif target == "account":
        locator = locator.replace("123456789012", "999999999999")
    elif target == "region":
        locator = locator.replace("us-west-2", "us-east-1")
    elif target == "partition":
        locator = locator.replace("arn:aws:", "arn:aws-cn:")
    elif target == "mode":
        locator = locator.removesuffix(".fifo") if suffix else locator + ".fifo"
    else:
        locator = locator.replace("Original", "invalid/name")
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError):
        _provision_sync(row.pk)
    assert cloud.api.list_topics()["Topics"] == []


def test_foreign_live_target_never_changes_or_deletes_through_actual_lifecycle_services(cloud):
    first, second = new_service(cloud, "owner"), new_service(cloud, "contender")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    first.backend_ref = initial["handle"]
    first.save(update_fields=["backend_ref"])
    second.backend_ref = initial["handle"]
    second.config = {
        "display_name": "refused",
        "subscriptions": [{"protocol": "sqs", "endpoint": "arn:aws:sqs:us-west-2:123456789012:unwanted"}],
    }
    second.save(update_fields=["backend_ref", "config"])
    arn = initial["handle"].partition("/")[2]
    attrs = cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"]
    tags = cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
    updated = _update_sync(second.pk)
    deleted = _deprovision_sync(second.pk, delete_data=True, force_destroy=True)
    assert not updated["ok"] and not updated["retryable"]
    assert not deleted["ok"] and not deleted["retryable"]
    assert cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"] == attrs
    assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"] == tags
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] == []
    assert cloud.api.list_topics()["Topics"] == [{"TopicArn": arn}]
    first.config = {"display_name": "accepted"}
    first.save(update_fields=["config"])
    assert _update_sync(first.pk)["ok"]
    assert cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"]["DisplayName"] == "accepted"
    assert _deprovision_sync(first.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_topics()["Topics"] == []
