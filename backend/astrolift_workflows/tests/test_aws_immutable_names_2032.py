"""Real saved UUIDs separate slug collisions and preserve recorded physical data."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import boto3
import pytest
from aws.managed.dynamodb import DynamoDBConfig, DynamoDBDriver
from aws.managed.object_store_s3 import S3Config, S3Driver
from aws.managed.queue_sqs import SQSConfig, SQSDriver
from moto import mock_aws

from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["s3", "sqs", "dynamodb"])
def cloud(request):
    variant = request.param
    kind, cls, cfg, prefix_field, max_len = {
        "s3": ("object_store", S3Driver, S3Config(region="us-east-1"), "bucket_name_prefix", 63),
        "sqs": ("queue", SQSDriver, SQSConfig(region="us-east-1"), "queue_name_prefix", 75),
        "dynamodb": (
            "kv_store",
            DynamoDBDriver,
            DynamoDBConfig(region="us-east-1"),
            "table_name_prefix",
            255,
        ),
    }[variant]
    with mock_aws():
        api = boto3.client(variant, region_name="us-east-1")
        calls = []
        api.meta.events.register(f"before-call.{variant}", lambda model, **_: calls.append(model.name))
        state = SimpleNamespace(
            variant=variant,
            kind=kind,
            cfg=cfg,
            prefix_field=prefix_field,
            max_len=max_len,
            api=api,
            calls=calls,
        )

        class Recorded(cls):
            def __init__(self, *, config):
                super().__init__(config=config, **{"ddb_client" if variant == "dynamodb" else "client": api})

        with (
            patch("astrolift_drivers.registry.plugins.get", return_value=Recorded),
            patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
        ):
            yield state


def _new(cloud, org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    svc.kind = cloud.kind
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind"])
    assert UUID(str(svc.guid)).int and str(UUID(str(svc.guid))) == str(svc.guid)
    return svc


def _tags(cloud, name):
    if cloud.variant == "s3":
        return {row["Key"]: row["Value"] for row in cloud.api.get_bucket_tagging(Bucket=name)["TagSet"]}
    if cloud.variant == "sqs":
        url = cloud.api.get_queue_url(QueueName=name)["QueueUrl"]
        return cloud.api.list_queue_tags(QueueUrl=url)["Tags"]
    arn = cloud.api.describe_table(TableName=name)["Table"]["TableArn"]
    return {row["Key"]: row["Value"] for row in cloud.api.list_tags_of_resource(ResourceArn=arn)["Tags"]}


def _put_data(cloud, name):
    if cloud.variant == "s3":
        cloud.api.put_object(Bucket=name, Key="preserved", Body=b"actual recorded data")
    elif cloud.variant == "sqs":
        url = cloud.api.get_queue_url(QueueName=name)["QueueUrl"]
        cloud.api.send_message(QueueUrl=url, MessageBody="actual recorded data")
    else:
        cloud.api.put_item(
            TableName=name, Item={"pk": {"S": "preserved"}, "value": {"S": "actual recorded data"}}
        )


def _assert_data(cloud, name):
    if cloud.variant == "s3":
        assert cloud.api.get_object(Bucket=name, Key="preserved")["Body"].read() == b"actual recorded data"
    elif cloud.variant == "sqs":
        url = cloud.api.get_queue_url(QueueName=name)["QueueUrl"]
        assert (
            cloud.api.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])[
                "Attributes"
            ]["ApproximateNumberOfMessages"]
            == "1"
        )
    else:
        assert (
            cloud.api.get_item(TableName=name, Key={"pk": {"S": "preserved"}})["Item"]["value"]["S"]
            == "actual recorded data"
        )


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_collision_reaches_actual_sdk_as_two_full_immutable_uuid_names(cloud, collision):
    if collision == "joined":
        first = _new(cloud, "alpha-beta", "gamma")
        second = _new(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix_field: "p" * 300})
        first = _new(cloud, "collision-alpha")
        second = _new(cloud, "collision-beta")
    # Establish the concrete old failure: the complete join, or the provider's
    # prefix truncation, mapped two distinct orgs to one physical name.
    old = []
    for svc in (first, second):
        old.append(
            "-".join(
                (
                    getattr(cloud.cfg, cloud.prefix_field),
                    svc.registered_app.organization.slug,
                    svc.registered_app.slug,
                    svc.app_environment.name,
                    svc.name,
                )
            )[: cloud.max_len]
        )
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results)
    assert results[0]["handle"] != results[1]["handle"]
    for svc, result in zip((first, second), results, strict=True):
        name = result["handle"].split("/", 1)[1]
        assert str(svc.guid).replace("-", "") in name and len(name) <= cloud.max_len
        assert _tags(cloud, name)["astrolift.io/managed_service_id"] == str(svc.guid)
        _put_data(cloud, name)
        # A retry before the returned handle was finalized cannot invent an ID.
        assert _provision_sync(svc.pk)["handle"] == result["handle"]
        _assert_data(cloud, name)
    creates = {"s3": "CreateBucket", "sqs": "CreateQueue", "dynamodb": "CreateTable"}[cloud.variant]
    assert creates in cloud.calls


def test_recorded_legacy_name_survives_actual_reprovision_after_owner_display_and_prefix_changes(cloud):
    svc = _new(cloud, "legacy-owner-2032")
    name = "astrolift-legacy-api-prod-data"
    tags = [
        {"Key": "astrolift.io/managed-by", "Value": "platform"},
        {"Key": "astrolift.io/managed_service_id", "Value": str(svc.guid)},
    ]
    if cloud.variant == "s3":
        cloud.api.create_bucket(Bucket=name)
        cloud.api.put_bucket_tagging(Bucket=name, Tagging={"TagSet": tags})
    elif cloud.variant == "sqs":
        cloud.api.create_queue(QueueName=name, tags={row["Key"]: row["Value"] for row in tags})
    else:
        cloud.api.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            Tags=tags,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        )
    _put_data(cloud, name)
    svc.backend_ref = f"{cloud.kind}/{name}"
    svc.name = "renamed-service"
    svc.save(update_fields=["backend_ref", "name"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    svc.app_environment.name = "renamed-environment"
    svc.app_environment.save(update_fields=["name"])
    cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix_field: "changed-operator-default"})
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["handle"] == svc.backend_ref
    _assert_data(cloud, name)
    assert _tags(cloud, name)["astrolift.io/managed_service_id"] == str(svc.guid)
    svc.refresh_from_db()
    assert svc.backend_ref == f"{cloud.kind}/{name}"


def test_recorded_foreign_handle_and_forged_config_never_mutate_existing_resource(cloud):
    owner = _new(cloud, "actual-owner-2032")
    existing = _provision_sync(owner.pk)
    assert existing["ok"]
    name = existing["handle"].split("/", 1)[1]
    _put_data(cloud, name)
    contender = _new(cloud, "foreign-owner-2032")
    contender.backend_ref = existing["handle"]
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    cloud.calls.clear()
    result = _provision_sync(contender.pk)
    assert not result["ok"] and result["handle"] == ""
    assert set(cloud.calls) <= {
        "HeadBucket",
        "GetBucketTagging",
        "GetQueueUrl",
        "ListQueueTags",
        "GetQueueAttributes",
        "DescribeTable",
        "ListTagsOfResource",
    }
    _assert_data(cloud, name)
    assert _tags(cloud, name)["astrolift.io/managed_service_id"] == str(owner.guid)
