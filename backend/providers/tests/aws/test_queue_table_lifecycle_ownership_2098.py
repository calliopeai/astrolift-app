"""Recorded queue/table handles never authorize a replacement resource's lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from tests.aws.test_managed_live_ownership_2098 import MSID, OTHER, OWNER, PLATFORM, _read_only
from tests.aws.test_managed_live_ownership_2098 import service as _service_fixture

service = _service_fixture


@pytest.mark.parametrize("service", ["sqs", "dynamodb"], indirect=True)
@pytest.mark.parametrize("operation", ["update", "noop", "deprovision", "status", "snapshot"])
@pytest.mark.parametrize("case", ["foreign", "no-marker", "legacy", "no-id", "unreadable"])
def test_every_lifecycle_path_refuses_unknown_or_replaced_owner_before_mutation(service, operation, case):
    tags = {PLATFORM: "platform", OWNER: OTHER if case == "foreign" else MSID}
    if case == "no-marker":
        tags.pop(PLATFORM)
    if case == "legacy":
        tags = {PLATFORM: "platform", "astrolift.io/organization": "acme", "astrolift.io/app": "api"}
    service.replace(tags)
    service.calls.clear()
    identity = "" if case == "no-id" else MSID
    with patch.object(service.api, service.reader, wraps=getattr(service.api, service.reader)) as read:
        if case == "unreadable":
            read.side_effect = ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "ResourceNotFoundException is a resource name"}},
                "ReadTags",
            )
        if operation in {"update", "noop"}:
            config = (
                {}
                if operation == "noop"
                else ({"visibility_timeout_seconds": 60} if service.kind == "sqs" else {"deletion_protection": False})
            )
            result = service.driver.update(UpdateSpec(service.handle, config=config, managed_service_id=identity))
            assert not result.ok and not result.retryable
            assert result.errors == ["ownership_unknown" if case == "unreadable" else "ownership_refused"]
        elif operation == "deprovision":
            result = service.driver.deprovision(
                DeprovisionSpec(service.handle, managed_service_id=identity, config={"managed_service_id": OTHER}),
                delete_data=True,
                force_destroy=True,
            )
            assert not result.ok and not result.retryable
            assert result.errors == ["ownership_unknown" if case == "unreadable" else "ownership_refused"]
        elif operation == "status":
            assert service.driver.status(ServiceHandle(service.handle, managed_service_id=identity)).state == "error"
        else:
            with pytest.raises(ManagedServiceError):
                service.driver.snapshot(ServiceHandle(service.handle, managed_service_id=identity))
    _read_only(service)


@pytest.mark.parametrize("service", ["sqs", "dynamodb"], indirect=True)
@pytest.mark.parametrize("operation", ["update", "deprovision", "status", "snapshot"])
def test_permission_error_containing_not_found_marker_never_means_resource_disappearance(service, operation):
    lookup = "get_queue_url" if service.kind == "sqs" else "describe_table"
    with patch.object(
        service.api,
        lookup,
        side_effect=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "ResourceNotFoundException NonExistentQueue"}}, "Lookup"
        ),
    ):
        if operation == "update":
            assert not service.driver.update(UpdateSpec(service.handle, managed_service_id=MSID)).ok
        elif operation == "deprovision":
            result = service.driver.deprovision(
                DeprovisionSpec(service.handle, managed_service_id=MSID), delete_data=True
            )
            assert not result.ok and result.errors == ["ownership_unknown"]
        elif operation == "status":
            assert service.driver.status(ServiceHandle(service.handle, managed_service_id=MSID)).state == "error"
        else:
            with pytest.raises(ManagedServiceError):
                service.driver.snapshot(ServiceHandle(service.handle, managed_service_id=MSID))
    assert service.calls == []


@pytest.mark.parametrize("service", ["sqs", "dynamodb"], indirect=True)
def test_wrong_handle_kind_is_refused_without_looking_up_a_physical_resource(service):
    wrong = "foreign_kind/" + service.name
    for action in (
        lambda: service.driver.update(UpdateSpec(wrong, managed_service_id=MSID)),
        lambda: service.driver.deprovision(DeprovisionSpec(wrong, managed_service_id=MSID), delete_data=True),
        lambda: service.driver.status(ServiceHandle(wrong, managed_service_id=MSID)),
        lambda: service.driver.binding(ServiceHandle(wrong, managed_service_id=MSID)),
        lambda: service.driver.snapshot(ServiceHandle(wrong, managed_service_id=MSID)),
    ):
        with pytest.raises(ManagedServiceError):
            action()
    assert service.calls == []


@pytest.mark.parametrize("service", ["dynamodb"], indirect=True)
@pytest.mark.parametrize("operation", ["update", "deprovision", "status", "snapshot"])
@pytest.mark.parametrize("case", ["wrong-name", "wrong-region", "missing-arn", "partial-tags", "malformed-description"])
def test_incomplete_or_misdirected_table_identity_never_allows_lifecycle(service, operation, case):
    table = service.api.tables[service.name]
    if case == "wrong-name":
        table["TableName"] = "some-other-table"
    elif case == "wrong-region":
        table["TableArn"] = table["TableArn"].replace("us-east-1", "eu-west-1")
    elif case == "missing-arn":
        table.pop("TableArn")
    with (
        patch.object(service.api, "list_tags_of_resource", wraps=service.api.list_tags_of_resource) as read,
        patch.object(service.api, "describe_table", wraps=service.api.describe_table) as describe,
    ):
        if case == "partial-tags":
            read.return_value = {"Tags": [{"Key": OWNER, "Value": MSID}], "NextToken": "same"}
        elif case == "malformed-description":
            describe.return_value = {}
        if operation == "update":
            assert not service.driver.update(UpdateSpec(service.handle, managed_service_id=MSID)).ok
        elif operation == "deprovision":
            assert not service.driver.deprovision(
                DeprovisionSpec(service.handle, managed_service_id=MSID), delete_data=True, force_destroy=True
            ).ok
        elif operation == "status":
            assert service.driver.status(ServiceHandle(service.handle, managed_service_id=MSID)).state == "error"
        else:
            with pytest.raises(ManagedServiceError):
                service.driver.snapshot(ServiceHandle(service.handle, managed_service_id=MSID))
    assert read.call_count <= 2
    _read_only(service)


@pytest.mark.parametrize("service", ["dynamodb"], indirect=True)
def test_owned_snapshot_requires_actual_source_matched_backup_identity(service):
    handle = ServiceHandle(service.handle, managed_service_id=MSID)
    assert service.driver.snapshot(handle).snapshot_id.startswith(
        service.api.tables[service.name]["TableArn"] + "/backup/"
    )
    for response in (
        {"BackupDetails": {}},
        {"BackupDetails": {"BackupArn": "arn:aws:dynamodb:eu-west-1:123456789012:table/other/backup/1"}},
    ):
        with patch.object(service.api, "create_backup", return_value=response), pytest.raises(ManagedServiceError):
            service.driver.snapshot(handle)


@pytest.mark.parametrize("service", ["sqs"], indirect=True)
@pytest.mark.parametrize("case", ["wrong-name", "wrong-region", "wrong-account", "missing-arn", "unknown-counts"])
def test_sqs_exact_target_and_noninvented_inventory_before_actions(service, case):
    url = service.api.get_queue_url(QueueName=service.name)["QueueUrl"]
    attrs = service.api.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    if case == "wrong-name":
        attrs["QueueArn"] += "-foreign"
    elif case == "wrong-region":
        attrs["QueueArn"] = attrs["QueueArn"].replace("us-east-1", "eu-west-1")
    elif case == "wrong-account":
        service.driver._config = type(service.driver._config)(region="us-east-1", account_id="999999999999")
    elif case == "missing-arn":
        attrs.pop("QueueArn")
    else:
        attrs.pop("ApproximateNumberOfMessages")
    service.calls.clear()
    with patch.object(service.api, "get_queue_attributes", return_value={"Attributes": attrs}):
        result = service.driver.deprovision(DeprovisionSpec(service.handle, managed_service_id=MSID))
        assert not result.ok
        assert service.driver.status(ServiceHandle(service.handle, managed_service_id=MSID)).state == "error"
        if case != "unknown-counts":
            assert not service.driver.update(UpdateSpec(service.handle, managed_service_id=MSID)).ok
    _read_only(service)


@pytest.mark.parametrize("service", ["sqs"], indirect=True)
@pytest.mark.parametrize("response", [{}, None])
def test_unknown_queue_lookup_payload_is_not_already_gone(service, response):
    with patch.object(service.api, "get_queue_url", return_value=response):
        result = service.driver.deprovision(DeprovisionSpec(service.handle, managed_service_id=MSID), delete_data=True)
        assert not result.ok and result.errors == ["ownership_unknown"]
        assert service.driver.status(ServiceHandle(service.handle, managed_service_id=MSID)).state == "error"
    assert service.calls == []


def test_dynamodb_actual_sdk_metadata_and_tags_gate_snapshot_and_force_delete():
    import boto3
    from moto import mock_aws

    from aws.managed.dynamodb import DynamoDBConfig, DynamoDBDriver
    from tests.aws.test_managed_dynamodb import _spec

    with mock_aws():
        api = boto3.client("dynamodb", region_name="us-east-1")
        driver = DynamoDBDriver(config=DynamoDBConfig(region="us-east-1"), ddb_client=api)
        created = driver.provision(_spec())
        assert created.ok
        handle = ServiceHandle(created.handle, managed_service_id=MSID)
        assert driver.status(handle).state == "available"
        snapshot = driver.snapshot(handle)
        table_name = created.handle.split("/", 1)[1]
        arn = api.describe_table(TableName=table_name)["Table"]["TableArn"]
        assert snapshot.snapshot_id.startswith(arn + "/backup/")
        # Moto's TagResource appends duplicate keys; use the real removal API
        # before replacement so the refusal proves foreign identity specifically.
        api.untag_resource(ResourceArn=arn, TagKeys=[OWNER])
        api.tag_resource(ResourceArn=arn, Tags=[{"Key": OWNER, "Value": OTHER}])
        calls = []
        api.meta.events.register("before-call.dynamodb", lambda model, **_: calls.append(model.name))
        result = driver.deprovision(
            DeprovisionSpec(created.handle, managed_service_id=MSID), delete_data=True, force_destroy=True
        )
        assert not result.ok and result.errors == ["ownership_refused"]
        assert calls == ["DescribeTable", "ListTagsOfResource"]
        assert driver.status(handle).state == "error"
        with pytest.raises(ManagedServiceError):
            driver.snapshot(handle)
        assert set(calls) <= {"DescribeTable", "ListTagsOfResource"}
        api.untag_resource(ResourceArn=arn, TagKeys=[OWNER])
        api.tag_resource(ResourceArn=arn, Tags=[{"Key": OWNER, "Value": MSID}])
        assert driver.deprovision(
            DeprovisionSpec(created.handle, managed_service_id=MSID), delete_data=True, force_destroy=True
        ).ok
