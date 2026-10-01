"""Live source identity, not old handles/custom tags, authorizes AWS bindings."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.dynamodb import DynamoDBConfig, DynamoDBDriver
from aws.managed.object_store_s3 import S3Config, S3Driver
from aws.managed.queue_sqs import SQSConfig, SQSDriver
from tests.aws.test_managed_dynamodb import FakeDDB
from tests.aws.test_managed_dynamodb import _spec as ddb_spec
from tests.aws.test_managed_object_store_s3 import MSID
from tests.aws.test_managed_object_store_s3 import _spec as s3_spec
from tests.aws.test_managed_queue_sqs import _spec as sqs_spec

OTHER = "22222222-2222-4222-8222-222222222222"
PLATFORM = "astrolift.io/managed-by"
OWNER = "astrolift.io/managed_service_id"


@pytest.fixture(params=["s3", "sqs", "dynamodb"])
def service(request):
    with mock_aws():
        calls = []
        if request.param == "s3":
            api = boto3.client("s3", region_name="us-east-1")
            api.meta.events.register("before-call.s3", lambda model, **_: calls.append(model.name))
            driver = S3Driver(config=S3Config(region="us-east-1"), client=api)
            result = driver.provision(s3_spec())
            name = parse_handle(result.handle)[1]

            def replace(tags):
                api.put_bucket_tagging(
                    Bucket=name, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}
                )

            reader = "get_bucket_tagging"
        elif request.param == "sqs":
            api = boto3.client("sqs", region_name="us-east-1")
            api.meta.events.register("before-call.sqs", lambda model, **_: calls.append(model.name))
            driver = SQSDriver(config=SQSConfig(region="us-east-1"), client=api)
            result = driver.provision(sqs_spec())
            name = parse_handle(result.handle)[1]
            url = api.get_queue_url(QueueName=name)["QueueUrl"]

            def replace(tags):
                current = api.list_queue_tags(QueueUrl=url)["Tags"]
                api.untag_queue(QueueUrl=url, TagKeys=list(current))
                api.tag_queue(QueueUrl=url, Tags=tags)

            reader = "list_queue_tags"
        else:
            api = FakeDDB()
            calls = api.calls
            driver = DynamoDBDriver(config=DynamoDBConfig(region="us-east-1"), ddb_client=api)
            result = driver.provision(ddb_spec())
            name = parse_handle(result.handle)[1]

            def replace(tags):
                api.tables[name]["Tags"] = [{"Key": k, "Value": v} for k, v in tags.items()]

            reader = "list_tags_of_resource"
        assert result.ok
        calls.clear()
        yield SimpleNamespace(
            kind=request.param,
            driver=driver,
            api=api,
            calls=calls,
            reader=reader,
            handle=result.handle,
            name=name,
            replace=replace,
        )


def _read_only(service):
    allowed = {
        "HeadBucket",
        "GetBucketTagging",
        "GetQueueUrl",
        "ListQueueTags",
        "GetQueueAttributes",
        "describe_table",
        "list_tags_of_resource",
    }
    assert all((call[0] if isinstance(call, tuple) else call) in allowed for call in service.calls)


@pytest.mark.parametrize("case", ["foreign", "missing-identity", "missing-marker", "legacy-slugs", "unreadable"])
def test_binding_refuses_unknown_or_replaced_live_resource_without_any_mutating_call(service, case):
    identity = "" if case == "missing-identity" else MSID
    tags = {PLATFORM: "platform", OWNER: OTHER if case == "foreign" else MSID}
    if case == "missing-marker":
        del tags[PLATFORM]
    elif case == "legacy-slugs":
        tags = {PLATFORM: "platform", "astrolift.io/organization": "acme", "astrolift.io/app": "api"}
    service.replace(tags)
    service.calls.clear()
    authority = ServiceHandle(service.handle, managed_service_id=identity, recorded_handle_exclusive=True)
    if case == "unreadable":
        with (
            patch.object(
                service.api,
                service.reader,
                side_effect=ClientError({"Error": {"Code": "AccessDenied", "Message": "tag read refused"}}, "ReadTags"),
            ),
            pytest.raises(ManagedServiceError),
        ):
            service.driver.binding(authority)
    else:
        with pytest.raises(ManagedServiceError, match=r"ownership|identity"):
            service.driver.binding(authority)
    _read_only(service)


def test_owned_binding_uses_actual_resource_with_no_static_credentials(service):
    binding = service.driver.binding(ServiceHandle(service.handle, managed_service_id=MSID))
    assert binding.iam_grants
    assert all(service.name in grant.resource for grant in binding.iam_grants)
    assert all(value.secret_ref is None for value in binding.env_vars.values())
    _read_only(service)


@pytest.mark.parametrize("service", ["s3"], indirect=True)
@pytest.mark.parametrize("delete_data", [False, True])
@pytest.mark.parametrize("force_destroy", [False, True])
def test_s3_same_physical_name_new_incarnation_never_updates_or_tears_down(service, delete_data, force_destroy):
    service.replace({PLATFORM: "platform", OWNER: OTHER})
    service.calls.clear()
    update = service.driver.update(UpdateSpec(service.handle, managed_service_id=MSID, config={"mount_path": "/data"}))
    assert not update.ok and not update.retryable
    result = service.driver.deprovision(
        DeprovisionSpec(
            service.handle, managed_service_id=MSID, config={"force_destroy": True, "managed_service_id": OTHER}
        ),
        delete_data=delete_data,
        force_destroy=force_destroy,
    )
    assert not result.ok and not result.retryable
    assert service.api.get_bucket_tagging(Bucket=service.name)["TagSet"] == [
        {"Key": PLATFORM, "Value": "platform"},
        {"Key": OWNER, "Value": OTHER},
    ]
    assert set(service.calls) <= {"HeadBucket", "GetBucketTagging"}


def test_dynamodb_tags_are_complete_before_binding_even_if_owner_is_on_a_later_page():
    fake = FakeDDB()
    driver = DynamoDBDriver(config=DynamoDBConfig(region="us-east-1"), ddb_client=fake)
    result = driver.provision(ddb_spec())
    with patch.object(
        fake,
        "list_tags_of_resource",
        side_effect=[
            {"Tags": [{"Key": PLATFORM, "Value": "platform"}], "NextToken": "next"},
            {"Tags": [{"Key": OWNER, "Value": MSID}]},
        ],
    ) as read:
        assert driver.binding(ServiceHandle(result.handle, managed_service_id=MSID)).iam_grants
    assert len(read.call_args_list) == 2
    assert read.call_args_list[1].kwargs["NextToken"] == "next"


@pytest.mark.parametrize("failure", ["repeated", "invalid-token", "invalid-tags", "truncated", "duplicate-owner"])
def test_dynamodb_partial_or_unknown_tag_pages_never_grant_access(failure):
    fake = FakeDDB()
    driver = DynamoDBDriver(config=DynamoDBConfig(region="us-east-1"), ddb_client=fake)
    result = driver.provision(ddb_spec())
    owned = [{"Key": PLATFORM, "Value": "platform"}, {"Key": OWNER, "Value": MSID}]
    if failure == "repeated":
        pages = [{"Tags": owned, "NextToken": "same"}, {"Tags": [], "NextToken": "same"}]
    elif failure == "invalid-token":
        pages = [{"Tags": owned, "NextToken": 7}]
    elif failure == "invalid-tags":
        pages = [{"Tags": {OWNER: MSID}}]
    elif failure == "truncated":
        pages = [{"Tags": owned if i == 0 else [], "NextToken": str(i)} for i in range(10)]
    else:
        pages = [{"Tags": owned, "NextToken": "next"}, {"Tags": [{"Key": OWNER, "Value": OTHER}]}]
    fake.calls.clear()
    with patch.object(fake, "list_tags_of_resource", side_effect=pages), pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(result.handle, managed_service_id=MSID))
    assert [name for name, _ in fake.calls] == ["describe_table"]


@pytest.mark.parametrize("bad", ["missing", "foreign-name", "foreign-region", "unknown-account"])
def test_dynamodb_cannot_fabricate_or_redirect_grants_from_wrong_table_metadata(bad):
    fake = FakeDDB()
    driver = DynamoDBDriver(config=DynamoDBConfig(region="us-east-1"), ddb_client=fake)
    result = driver.provision(ddb_spec())
    table = fake.tables[parse_handle(result.handle)[1]]
    if bad == "missing":
        table.pop("TableArn")
    elif bad == "foreign-name":
        table["TableName"] = "other-table"
    elif bad == "foreign-region":
        table["TableArn"] = table["TableArn"].replace("us-east-1", "eu-west-1")
    else:
        parts = table["TableArn"].split(":", 5)
        parts[4] = "UNKNOWN"
        table["TableArn"] = ":".join(parts)
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(result.handle, managed_service_id=MSID))
