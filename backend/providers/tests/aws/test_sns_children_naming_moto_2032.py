"""Exact topic and child identities through real boto3 and Moto storage."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.sms_sns import SNSSmsConfig, SNSSmsDriver
from aws.managed.topic_sns import SNSConfig, SNSFifoTopicDriver, SNSStandardTopicDriver
from tests.aws.test_managed_topic_sns import _spec

FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(params=["sns_standard", "sns_fifo", "sns_sms"])
def sns_cloud(request, monkeypatch):
    with mock_aws():
        api = boto3.client("sns", region_name="us-west-2")
        if request.param == "sns_sms":
            # Moto has no sandbox API; exercise the documented unavailable-read path.
            def unavailable_sandbox(**_):
                raise ClientError(
                    {"Error": {"Code": "AuthorizationError", "Message": "fixture"}}, "GetSMSSandboxAccountStatus"
                )

            monkeypatch.setattr(api, "get_sms_sandbox_account_status", unavailable_sandbox)
            cls, cfg, kind = SNSSmsDriver, SNSSmsConfig(region="us-west-2", account_id="123456789012"), "sms"
        else:
            cls = SNSFifoTopicDriver if request.param == "sns_fifo" else SNSStandardTopicDriver
            cfg, kind = SNSConfig(region="us-west-2", account_id="123456789012"), "topic"
        yield SimpleNamespace(api=api, cls=cls, cfg=cfg, kind=kind, variant=request.param)


def config_for(cloud, *, keep=False):
    if cloud.kind == "sms":
        return {"phone_numbers": ["+12025550101" if keep else "+12025550102"], "prune_phone_numbers": True}
    return {
        "subscriptions": [
            {
                "protocol": "sqs",
                "endpoint": "arn:aws:sqs:us-west-2:123456789012:" + ("keep" if keep else "replacement"),
                "raw_message_delivery": True,
            }
        ],
        "prune_subscriptions": True,
    }


def provision(cloud, *, identity=FIRST, config=None, recorded=""):
    return cloud.cls(config=cloud.cfg, client=cloud.api).provision(
        _spec(managed_service_id=identity, config=config or {}, recorded_handle=recorded)
    )


def subscribe(cloud, topic, *, endpoint=None):
    return cloud.api.subscribe(
        TopicArn=topic,
        Protocol="sms" if cloud.kind == "sms" else "sqs",
        Endpoint=endpoint or ("+12025550101" if cloud.kind == "sms" else "arn:aws:sqs:us-west-2:123456789012:keep"),
        ReturnSubscriptionArn=True,
    )["SubscriptionArn"]


@pytest.mark.parametrize("corruption", ["parent", "arn", "missing_parent", "malformed_arn", "later_page"])
@pytest.mark.parametrize("operation", ["update", "reprovision"])
def test_entire_subscription_list_is_preflighted_before_any_child_write(sns_cloud, corruption, operation):
    cloud = sns_cloud
    initial, foreign = provision(cloud), provision(cloud, identity=SECOND)
    assert initial.ok and foreign.ok
    arn, other = initial.handle.partition("/")[2], foreign.handle.partition("/")[2]
    child, foreign_child = subscribe(cloud, arn), subscribe(cloud, other)
    rows = cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
    foreign_rows = cloud.api.list_subscriptions_by_topic(TopicArn=other)["Subscriptions"]
    bad = dict(rows[0])
    if corruption in {"parent", "later_page"}:
        bad["TopicArn"] = other
    elif corruption == "missing_parent":
        bad.pop("TopicArn")
    elif corruption == "arn":
        bad["SubscriptionArn"] = foreign_child
    else:
        bad["SubscriptionArn"] = arn + ":bad:child"
    responses = (
        [{"Subscriptions": [rows[0]], "NextToken": "page-2"}, {"Subscriptions": [bad]}]
        if corruption == "later_page"
        else [{"Subscriptions": [rows[0], bad]}]
    )
    original_list = cloud.api.list_subscriptions_by_topic
    with (
        patch.object(cloud.api, "list_subscriptions_by_topic", side_effect=responses),
        patch.object(cloud.api, "subscribe", wraps=cloud.api.subscribe) as add,
        patch.object(cloud.api, "unsubscribe", wraps=cloud.api.unsubscribe) as remove,
        patch.object(
            cloud.api, "set_subscription_attributes", wraps=cloud.api.set_subscription_attributes
        ) as attributes,
        patch.object(cloud.api, "set_topic_attributes", wraps=cloud.api.set_topic_attributes) as topic_attributes,
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as tags,
        patch.object(cloud.api, "put_data_protection_policy", wraps=cloud.api.put_data_protection_policy) as policy,
    ):
        cfg = {**config_for(cloud), "display_name": "must-not-change"}
        if operation == "reprovision":
            result = provision(cloud, config=cfg, recorded=initial.handle)
        else:
            result = cloud.cls(config=cloud.cfg, client=cloud.api).update(
                UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=cfg)
            )
        assert not result.ok and "exact topic" in result.message
        add.assert_not_called()
        remove.assert_not_called()
        attributes.assert_not_called()
        topic_attributes.assert_not_called()
        tags.assert_not_called()
        policy.assert_not_called()
    assert original_list(TopicArn=arn)["Subscriptions"] == rows
    assert original_list(TopicArn=other)["Subscriptions"] == foreign_rows
    assert cloud.api.get_subscription_attributes(SubscriptionArn=child)["Attributes"]["TopicArn"] == arn


def test_subscribe_returned_foreign_arn_is_not_forwarded_to_child_effects(sns_cloud):
    cloud = sns_cloud
    initial, foreign = provision(cloud), provision(cloud, identity=SECOND)
    assert initial.ok and foreign.ok
    other = foreign.handle.partition("/")[2]
    child = subscribe(cloud, other)
    before = cloud.api.get_subscription_attributes(SubscriptionArn=child)["Attributes"]
    with (
        patch.object(cloud.api, "subscribe", return_value={"SubscriptionArn": child}),
        patch.object(cloud.api, "unsubscribe", wraps=cloud.api.unsubscribe) as remove,
        patch.object(
            cloud.api, "set_subscription_attributes", wraps=cloud.api.set_subscription_attributes
        ) as attributes,
    ):
        result = cloud.cls(config=cloud.cfg, client=cloud.api).update(
            UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud))
        )
        assert not result.ok and "exact topic" in result.message
        remove.assert_not_called()
        attributes.assert_not_called()
    assert cloud.api.get_subscription_attributes(SubscriptionArn=child)["Attributes"] == before


@pytest.mark.parametrize("sentinel", ["PendingConfirmation", "Deleted"])
def test_subscribe_returned_pending_marker_is_not_used_as_child_identity(sns_cloud, sentinel):
    cloud = sns_cloud
    initial = provision(cloud)
    assert initial.ok
    with (
        patch.object(cloud.api, "subscribe", return_value={"SubscriptionArn": sentinel}) as add,
        patch.object(cloud.api, "unsubscribe", wraps=cloud.api.unsubscribe) as remove,
        patch.object(
            cloud.api, "set_subscription_attributes", wraps=cloud.api.set_subscription_attributes
        ) as attributes,
    ):
        result = cloud.cls(config=cloud.cfg, client=cloud.api).update(
            UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud))
        )
        assert result.ok, result
        assert add.call_args.kwargs["TopicArn"] == initial.handle.partition("/")[2]
        remove.assert_not_called()
        attributes.assert_not_called()


def test_native_owned_children_are_reused_then_pruned(sns_cloud):
    cloud = sns_cloud
    initial = provision(cloud)
    assert initial.ok
    arn = initial.handle.partition("/")[2]
    child = subscribe(cloud, arn)
    driver = cloud.cls(config=cloud.cfg, client=cloud.api)
    assert driver.update(
        UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud, keep=True))
    ).ok
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"][0]["SubscriptionArn"] == child
    assert driver.update(UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud))).ok
    children = cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
    assert len(children) == 1 and children[0]["SubscriptionArn"] != child
    assert children[0]["TopicArn"] == arn


@pytest.mark.parametrize("sentinel", ["PendingConfirmation", "Deleted"])
def test_valid_parent_pending_children_are_never_unsubscribed(sns_cloud, sentinel):
    cloud = sns_cloud
    initial = provision(cloud)
    assert initial.ok
    arn = initial.handle.partition("/")[2]
    row = {
        "TopicArn": arn,
        "SubscriptionArn": sentinel,
        "Protocol": "sms" if cloud.kind == "sms" else "sqs",
        "Endpoint": "+12025550101" if cloud.kind == "sms" else "arn:aws:sqs:us-west-2:123456789012:keep",
    }
    with (
        patch.object(cloud.api, "list_subscriptions_by_topic", return_value={"Subscriptions": [row]}),
        patch.object(cloud.api, "unsubscribe", wraps=cloud.api.unsubscribe) as remove,
    ):
        result = cloud.cls(config=cloud.cfg, client=cloud.api).update(
            UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud))
        )
        assert result.ok, result
        remove.assert_not_called()


@pytest.mark.parametrize("sns_cloud", ["sns_sms"], indirect=True)
def test_sms_recorded_owned_legacy_topic_survives_labels_and_prefix_changes(sns_cloud):
    cloud = sns_cloud
    initial = provision(cloud, config={"topic_name": "Old_SMS"})
    assert initial.ok
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="changed-prefix")
    result = cloud.cls(config=cloud.cfg, client=cloud.api).provision(
        _spec(managed_service_id=FIRST, recorded_handle=initial.handle, app_slug="new-app", organization_slug="new-org")
    )
    assert result.ok and result.handle == initial.handle
    assert cloud.api.list_topics()["Topics"] == [{"TopicArn": initial.handle.partition("/")[2]}]
    conflict = provision(cloud, recorded=initial.handle, config={"topic_name": "different"})
    assert not conflict.ok and conflict.errors == ["immutable_topic_name"]


@pytest.mark.parametrize("corruption", ["identity", "marker", "platform", "missing_identity"])
@pytest.mark.parametrize("sns_cloud", ["sns_sms"], indirect=True)
def test_sms_foreign_recorded_topic_is_untouched_even_with_force(sns_cloud, corruption):
    cloud = sns_cloud
    initial = provision(cloud, config={"topic_name": "Old_SMS"})
    assert initial.ok
    arn = initial.handle.partition("/")[2]
    key, value = {
        "identity": ("astrolift.io/managed_service_id", SECOND),
        "marker": ("astrolift.io/sms-binding", "other"),
        "platform": ("astrolift.io/managed-by", "foreign"),
        "missing_identity": ("astrolift.io/managed_service_id", None),
    }[corruption]
    if value is None:
        cloud.api.untag_resource(ResourceArn=arn, TagKeys=[key])
    else:
        cloud.api.tag_resource(ResourceArn=arn, Tags=[{"Key": key, "Value": value}])
    before_tags = cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
    before_attrs = cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"]
    assert not provision(cloud, recorded=initial.handle, config=config_for(cloud)).ok
    driver = cloud.cls(config=cloud.cfg, client=cloud.api)
    updated = driver.update(UpdateSpec(handle=initial.handle, managed_service_id=FIRST, config=config_for(cloud)))
    assert not updated.ok and not updated.retryable
    deleted = driver.deprovision(DeprovisionSpec(handle=initial.handle, managed_service_id=FIRST), force_destroy=True)
    assert not deleted.ok and not deleted.retryable
    assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"] == before_tags
    assert cloud.api.get_topic_attributes(TopicArn=arn)["Attributes"] == before_attrs
    assert cloud.api.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] == []


@pytest.mark.parametrize("corruption", ["kind", "account", "region", "partition", "name", "fifo"])
@pytest.mark.parametrize("sns_cloud", ["sns_sms"], indirect=True)
def test_sms_invalid_recorded_locator_never_creates_a_replacement(sns_cloud, corruption):
    cloud = sns_cloud
    locator = "sms/arn:aws:sns:us-west-2:123456789012:Old_SMS"
    old, new = {
        "kind": ("sms/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "name": ("Old_SMS", "bad/name"),
        "fifo": ("Old_SMS", "Old_SMS.fifo"),
    }[corruption]
    with pytest.raises(ManagedServiceError):
        provision(cloud, recorded=locator.replace(old, new))
    assert cloud.api.list_topics()["Topics"] == []


@pytest.mark.parametrize("sns_cloud", ["sns_sms"], indirect=True)
def test_sms_missing_recorded_topic_never_creates_a_replacement(sns_cloud):
    cloud = sns_cloud
    result = provision(cloud, recorded="sms/arn:aws:sns:us-west-2:123456789012:Missing")
    assert not result.ok and "refusing a replacement" in result.message
    assert cloud.api.list_topics()["Topics"] == []


@pytest.mark.parametrize("sns_cloud", ["sns_sms"], indirect=True)
def test_sms_idempotent_create_race_does_not_configure_or_retag_foreign_topic(sns_cloud):
    cloud = sns_cloud
    original = cloud.api.create_topic
    raced = []

    def racing_create(**params):
        assert set(params) == {"Name", "Tags"}
        arn = original(
            Name=params["Name"],
            Tags=[
                {"Key": "astrolift.io/managed-by", "Value": "platform"},
                {"Key": "astrolift.io/managed_service_id", "Value": SECOND},
                {"Key": "astrolift.io/sms-binding", "Value": params["Name"]},
            ],
        )["TopicArn"]
        raced.append(arn)
        return original(**params)

    with patch.object(cloud.api, "create_topic", side_effect=racing_create):
        result = provision(cloud, config={"display_name": "refused", "phone_numbers": ["+12025550101"]})
    assert not result.ok and "foreign SNS SMS managed-service identity" in result.message
    assert len(raced) == 1
    assert cloud.api.get_topic_attributes(TopicArn=raced[0])["Attributes"].get("DisplayName") != "refused"
    tags = {row["Key"]: row["Value"] for row in cloud.api.list_tags_for_resource(ResourceArn=raced[0])["Tags"]}
    assert tags["astrolift.io/managed_service_id"] == SECOND
    assert cloud.api.list_subscriptions_by_topic(TopicArn=raced[0])["Subscriptions"] == []
