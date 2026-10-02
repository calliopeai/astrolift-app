"""Saved SMS identities exercise the real registry, PostgreSQL and boto3 SNS."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from aws.managed._base import ManagedServiceError
from aws.managed.sms_sns import SNSSmsConfig, SNSSmsDriver
from botocore.exceptions import ClientError
from moto import mock_aws

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    with mock_aws():
        api = boto3.client("sns", region_name="us-west-2")

        def unavailable_sandbox(**_):
            raise ClientError(
                {"Error": {"Code": "AuthorizationError", "Message": "fixture"}}, "GetSMSSandboxAccountStatus"
            )

        # Moto lacks this optional account read; all resource calls remain native.
        monkeypatch.setattr(api, "get_sms_sandbox_account_status", unavailable_sandbox)
        state = SimpleNamespace(api=api, cfg=SNSSmsConfig(region="us-west-2", account_id="123456789012"))

        class Driver(SNSSmsDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:sms:sns_sms": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        yield state


def service(org_slug, app_slug="api"):
    row = _service(org_slug=org_slug, plugin_slug="aws", variant="sns_sms", backend_ref="")
    row.kind, row.name = "sms", "alerts"
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name"])
    return row


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_saved_organizations_with_previous_collision_receive_distinct_owned_sms_topics(cloud, collision):
    if collision == "joined":
        first, second = service("alpha-beta", "gamma"), service("alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="p" * 300)
        first, second = service("alpha"), service("beta")
    old = [
        "-".join(
            (
                cloud.cfg.topic_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                "prod",
                row.name,
            )
        )[:256]
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        name = arn.rsplit(":", 1)[1]
        assert name.endswith(row.guid.hex) and len(name) <= 256
        tags = {tag["Key"]: tag["Value"] for tag in cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]}
        assert tags["astrolift.io/managed_service_id"] == str(row.guid)
        assert tags["astrolift.io/sms-binding"] == name
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_topics()["Topics"]) == 2


def test_owned_recorded_legacy_sms_survives_label_prefix_changes_and_real_child_lifecycle(cloud):
    row = service("legacy")
    row.config = {"topic_name": "Old_SMS", "phone_numbers": ["+12025550101"]}
    row.save(update_fields=["config"])
    initial = _provision_sync(row.pk)
    assert initial["ok"]
    row.backend_ref = initial["handle"]
    row.config = {"phone_numbers": ["+12025550101"]}
    row.save(update_fields=["backend_ref", "config"])
    arn = initial["handle"].partition("/")[2]
    before_child = cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"][0]["SubscriptionArn"]
    row.registered_app.slug = "renamed"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="different-prefix")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == initial["handle"]
    assert (
        cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"][0]["SubscriptionArn"]
        == before_child
    )
    row.refresh_from_db()
    assert row.backend_ref == initial["handle"]
    row.config = {"phone_numbers": ["+12025550102"], "display_name": "Updated"}
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    children = cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
    assert len(children) == 1 and children[0]["Endpoint"] == "+12025550102"
    assert children[0]["SubscriptionArn"] != before_child and children[0]["TopicArn"] == arn
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_topics()["Topics"] == []


def test_second_saved_owner_cannot_mutate_or_force_delete_recorded_sms_topic(cloud):
    first, second = service("owner"), service("contender")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    second.backend_ref = initial["handle"]
    second.config = {"display_name": "refused", "phone_numbers": ["+12025550101"]}
    second.save(update_fields=["backend_ref", "config"])
    arn = initial["handle"].partition("/")[2]
    before_tags = cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
    before_attrs = cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"]
    assert not _provision_sync(second.pk)["ok"]
    updated = _update_sync(second.pk)
    deleted = _deprovision_sync(second.pk, delete_data=True, force_destroy=True)
    assert not updated["ok"] and not updated["retryable"] and not deleted["ok"] and not deleted["retryable"]
    assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"] == before_tags
    assert cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"] == before_attrs
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] == []


def test_missing_saved_sms_root_is_not_silently_replaced(cloud):
    row = service("missing")
    row.backend_ref = "sms/arn:aws:sns:us-west-2:123456789012:Recorded_Missing"
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"] and "refusing a replacement" in result["message"]
    assert cloud.api.list_topics()["Topics"] == []
    row.refresh_from_db()
    assert row.backend_ref.endswith(":Recorded_Missing")


@pytest.mark.parametrize("target", ["kind", "account", "region", "partition", "name", "fifo"])
def test_invalid_saved_sms_target_never_creates_a_guessed_replacement(cloud, target):
    row = service("wrong-target")
    locator = "sms/arn:aws:sns:us-west-2:123456789012:Original"
    old, new = {
        "kind": ("sms/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "name": ("Original", "bad/name"),
        "fifo": ("Original", "Original.fifo"),
    }[target]
    row.backend_ref = locator.replace(old, new)
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError):
        _provision_sync(row.pk)
    assert cloud.api.list_topics()["Topics"] == []


@pytest.mark.parametrize("operation", ["update", "reprovision"])
def test_saved_sms_child_parent_corruption_is_refused_before_any_resource_write(cloud, operation):
    first, second = service("owner"), service("other")
    initial, foreign = _provision_sync(first.pk), _provision_sync(second.pk)
    assert initial["ok"] and foreign["ok"]
    first.backend_ref = initial["handle"]
    first.config = {"display_name": "refused", "phone_numbers": ["+12025550102"]}
    first.save(update_fields=["backend_ref", "config"])
    arn, other = initial["handle"].partition("/")[2], foreign["handle"].partition("/")[2]
    cloud.api.subscribe(TopicArn=other, Protocol="sms", Endpoint="+12025550101", ReturnSubscriptionArn=True)
    rows = cloud.api.list_subscriptions_by_topic(TopicArn=other)["Subscriptions"]
    with (
        patch.object(cloud.api, "list_subscriptions_by_topic", return_value={"Subscriptions": rows}),
        patch.object(cloud.api, "set_topic_attributes", wraps=cloud.api.set_topic_attributes) as attributes,
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as tags,
        patch.object(cloud.api, "subscribe", wraps=cloud.api.subscribe) as add,
        patch.object(cloud.api, "unsubscribe", wraps=cloud.api.unsubscribe) as remove,
    ):
        result = _update_sync(first.pk) if operation == "update" else _provision_sync(first.pk)
        assert not result["ok"] and "exact topic" in result["message"]
        attributes.assert_not_called()
        tags.assert_not_called()
        add.assert_not_called()
        remove.assert_not_called()
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] == []
    assert cloud.api.list_subscriptions_by_topic(TopicArn=other)["Subscriptions"] == rows
