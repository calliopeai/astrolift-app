"""Real lifecycle UUIDs authorize live SDK resources and refuse old incarnations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.dynamodb import DynamoDBConfig, DynamoDBDriver
from aws.managed.object_store_s3 import S3Config, S3Driver
from aws.managed.queue_sqs import SQSConfig, SQSDriver
from moto import mock_aws
from tests.aws.test_managed_dynamodb import FakeDDB

from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["s3", "sqs", "dynamodb"])
def world(request):
    with mock_aws():
        calls = []
        if request.param == "s3":
            kind, variant, driver_class = "object_store", "s3", S3Driver
            cfg = S3Config(region="us-east-1")
            api = boto3.client("s3", region_name="us-east-1")
            api.meta.events.register("before-call.s3", lambda model, **_: calls.append(model.name))
        elif request.param == "sqs":
            kind, variant, driver_class = "queue", "sqs", SQSDriver
            cfg = SQSConfig(region="us-east-1")
            api = boto3.client("sqs", region_name="us-east-1")
            api.meta.events.register("before-call.sqs", lambda model, **_: calls.append(model.name))
        else:
            kind, variant, driver_class = "kv_store", "dynamodb", DynamoDBDriver
            cfg = DynamoDBConfig(region="us-east-1")
            api = FakeDDB()
            calls = api.calls

        class Recorded(driver_class):
            def __init__(self, *, config):
                super().__init__(config=config, **{"ddb_client" if variant == "dynamodb" else "client": api})

        svc = _service(
            org_slug=f"aws-live-{variant}-2098", plugin_slug="aws", variant=variant, backend_ref=""
        )
        svc.kind = kind
        svc.save(update_fields=["kind"])
        with (
            patch("astrolift_drivers.registry.plugins.get", return_value=Recorded),
            patch("core.cluster_observability.managed_config_for", return_value=cfg),
        ):
            result = _provision_sync(svc.pk)
            assert result["ok"]
            svc.backend_ref = result["handle"]
            svc.save(update_fields=["backend_ref"])
            name = parse_handle(svc.backend_ref)[1]
            if variant == "s3":
                tags = api.get_bucket_tagging(Bucket=name)["TagSet"]
            elif variant == "sqs":
                url = api.get_queue_url(QueueName=name)["QueueUrl"]
                tags = [{"Key": k, "Value": v} for k, v in api.list_queue_tags(QueueUrl=url)["Tags"].items()]
            else:
                tags = api.tables[name]["Tags"]
            assert next(
                tag["Value"] for tag in tags if tag["Key"] == "astrolift.io/managed_service_id"
            ) == str(svc.guid)
            calls.clear()
            yield SimpleNamespace(svc=svc, api=api, calls=calls, name=name, variant=variant)


def test_actual_uuid_flows_from_provision_to_live_binding_without_static_credentials(world):
    binding = _managed_binding_for(world.svc)
    assert binding.iam_grants and all(world.name in grant.resource for grant in binding.iam_grants)
    assert all(value.secret_ref is None for value in binding.env_vars.values())
    if world.variant == "s3":
        world.svc.config = {"mount_path": "/data"}
        world.svc.save(update_fields=["config"])
        assert _update_sync(world.svc.pk)["ok"]
        assert _deprovision_sync(world.svc.pk, True, True)["ok"]
        assert world.api.list_buckets()["Buckets"] == []


def test_persisted_old_handle_and_caller_config_cannot_bind_a_replaced_live_resource(world):
    foreign_id = "22222222-2222-4222-8222-222222222222"
    tags = [
        {"Key": "astrolift.io/managed-by", "Value": "platform"},
        {"Key": "astrolift.io/managed_service_id", "Value": foreign_id},
    ]
    if world.variant == "s3":
        world.api.put_bucket_tagging(Bucket=world.name, Tagging={"TagSet": tags})
    elif world.variant == "sqs":
        url = world.api.get_queue_url(QueueName=world.name)["QueueUrl"]
        world.api.tag_queue(QueueUrl=url, Tags={tag["Key"]: tag["Value"] for tag in tags})
    else:
        world.api.tables[world.name]["Tags"] = tags
    world.svc.config = {"managed_service_id": foreign_id, "recorded_handle_exclusive": True}
    world.svc.save(update_fields=["config"])
    world.calls.clear()
    with pytest.raises(ManagedServiceError, match="ownership"):
        _managed_binding_for(world.svc)
    if world.variant == "s3":
        world.svc.config = {"mount_path": "/data"}
        world.svc.save(update_fields=["config"])
        assert not _update_sync(world.svc.pk)["ok"]
        assert not _deprovision_sync(world.svc.pk, True, True)["ok"]
        assert not _deprovision_sync(world.svc.pk, False, False)["ok"]
    allowed = {"HeadBucket", "GetBucketTagging", "GetQueueUrl", "ListQueueTags", "describe_table"}
    assert all((call[0] if isinstance(call, tuple) else call) in allowed for call in world.calls)
    world.svc.refresh_from_db()
    assert world.svc.backend_ref.endswith(world.name)
