"""Tests for SQS Queue managed-service driver (#35)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.queue_sqs import KIND, SQSConfig, SQSDriver

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def sqs_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("sqs", region_name="us-east-1")


@pytest.fixture
def driver(sqs_client) -> SQSDriver:
    return SQSDriver(
        config=SQSConfig(region="us-east-1"),
        client=sqs_client,
    )


MSID = "11111111-1111-4111-8111-111111111111"


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        managed_service_id=MSID,
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="work",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_queue(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, queue_name = parse_handle(result.handle)
    assert kind == KIND
    response = sqs_client.list_queues()
    urls = response.get("QueueUrls", [])
    assert any(queue_name in url for url in urls)


def test_provision_idempotent(driver: SQSDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle


def test_provision_fifo_when_configured(
    driver: SQSDriver,
    sqs_client,
) -> None:
    result = driver.provision(_spec(config={"fifo": True}))
    _, queue_name = parse_handle(result.handle)
    assert queue_name.endswith(".fifo")


def test_provision_default_size_small(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec(size="small"))
    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout", "MessageRetentionPeriod"],
    )
    assert attrs["Attributes"]["VisibilityTimeout"] == "30"


def test_provision_large_size_higher_visibility(
    driver: SQSDriver,
    sqs_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout", "MessageRetentionPeriod"],
    )
    assert int(attrs["Attributes"]["VisibilityTimeout"]) >= 60


def test_provision_explicit_visibility_in_config(
    driver: SQSDriver,
    sqs_client,
) -> None:
    result = driver.provision(
        _spec(
            config={"visibility_timeout_seconds": 120},
        )
    )
    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout"],
    )
    assert attrs["Attributes"]["VisibilityTimeout"] == "120"


def test_provision_exposes_full_fifo_encryption_redrive_and_long_poll_surface() -> None:
    client = MagicMock()
    client.get_queue_url.side_effect = ClientError(
        {"Error": {"Code": "QueueDoesNotExist", "Message": "queue absent"}}, "GetQueueUrl"
    )
    client.create_queue.return_value = {
        "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/platform.fifo",
    }
    subject = SQSDriver(
        config=SQSConfig(region="us-east-1", account_id="123456789012"),
        client=client,
    )

    result = subject.provision(
        _spec(
            config={
                "fifo": True,
                "content_based_deduplication": True,
                "deduplication_scope": "messageGroup",
                "fifo_throughput_limit": "perMessageGroupId",
                "kms_master_key_id": "arn:aws:kms:us-east-1:123456789012:key/key-1",
                "kms_data_key_reuse_period_seconds": 300,
                "receive_message_wait_time_seconds": 20,
                "maximum_message_size": 1048576,
                "dead_letter_queue_arn": "arn:aws:sqs:us-east-1:123456789012:work-dlq.fifo",
                "max_receive_count": 7,
                "policy": {"Version": "2012-10-17", "Statement": []},
            },
        ),
    )

    assert result.ok and result.ready
    request = client.create_queue.call_args.kwargs
    attrs = request["Attributes"]
    assert attrs["FifoQueue"] == "true"
    assert attrs["DeduplicationScope"] == "messageGroup"
    assert attrs["FifoThroughputLimit"] == "perMessageGroupId"
    assert attrs["KmsMasterKeyId"].endswith("key/key-1")
    assert attrs["ReceiveMessageWaitTimeSeconds"] == "20"
    assert '"maxReceiveCount":"7"' in attrs["RedrivePolicy"]
    service = Session().get_service_model("sqs")
    validate_parameters(request, service.operation_model("CreateQueue").input_shape)


# ---- status ----------------------------------------------------


def test_status_available(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=result.handle, managed_service_id=MSID))
    assert status.state == "available"


def test_status_deprovisioned_when_missing(driver: SQSDriver) -> None:
    status = driver.status(
        ServiceHandle(handle="queue/never-existed-queue"),
    )
    assert status.state == "deprovisioned"


# ---- binding ---------------------------------------------------


def test_binding_emits_env_vars(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle, managed_service_id=MSID))
    assert "SQS_QUEUE_NAME" in binding.env_vars
    assert "SQS_QUEUE_URL" in binding.env_vars
    assert "SQS_QUEUE_ARN" in binding.env_vars
    assert binding.env_vars["QUEUE_URL"].literal == binding.env_vars["SQS_QUEUE_URL"].literal
    assert binding.env_vars["QUEUE_ARN_OR_ID"].literal == binding.env_vars["SQS_QUEUE_ARN"].literal


def test_binding_emits_iam_grants(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle, managed_service_id=MSID))
    assert len(binding.iam_grants) == 1
    grant = binding.iam_grants[0]
    assert "sqs:SendMessage" in grant.actions
    assert "sqs:ReceiveMessage" in grant.actions


def test_binding_lookup_failure_is_not_replaced_with_fabricated_credentials(driver: SQSDriver) -> None:
    with pytest.raises(ManagedServiceError, match="cannot bind SQS queue"):
        driver.binding(ServiceHandle(handle="queue/never-existed"))


# ---- update ---------------------------------------------------


def test_update_changes_visibility(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec(size="small"))
    update = driver.update(UpdateSpec(handle=result.handle, managed_service_id=MSID, size="large"))
    assert update.ok is True

    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout"],
    )
    assert int(attrs["Attributes"]["VisibilityTimeout"]) >= 60


def test_update_accepts_documented_snake_case_attribute_names(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec())

    update = driver.update(
        UpdateSpec(
            handle=result.handle,
            managed_service_id=MSID,
            config={
                "visibility_timeout_seconds": 121,
                "receive_message_wait_time_seconds": 20,
            },
        ),
    )

    assert update.ok
    _, queue_name = parse_handle(result.handle)
    queue_url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["VisibilityTimeout", "ReceiveMessageWaitTimeSeconds"],
    )["Attributes"]
    assert attrs["VisibilityTimeout"] == "121"
    assert attrs["ReceiveMessageWaitTimeSeconds"] == "20"


def test_update_no_op_when_no_changes(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    update = driver.update(UpdateSpec(handle=result.handle, managed_service_id=MSID))
    assert update.ok is True


def test_update_missing_queue(driver: SQSDriver) -> None:
    update = driver.update(
        UpdateSpec(handle="queue/never-existed", size="medium"),
    )
    assert update.ok is False


# ---- deprovision ----------------------------------------------


def test_deprovision_deletes_queue(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec())
    _, queue_name = parse_handle(result.handle)
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle, managed_service_id=MSID),
        delete_data=True,
    )
    assert deprov.ok is True
    response = sqs_client.list_queues()
    urls = response.get("QueueUrls", [])
    assert not any(queue_name in url for url in urls)


def test_deprovision_already_gone_idempotent(driver: SQSDriver) -> None:
    deprov = driver.deprovision(
        DeprovisionSpec(handle="queue/never-existed"),
        delete_data=True,
    )
    assert deprov.ok is True


def test_safe_deprovision_refuses_to_discard_messages(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec())
    _, queue_name = parse_handle(result.handle)
    queue_url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
    sqs_client.send_message(QueueUrl=queue_url, MessageBody="important")

    safe = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=MSID))
    destructive = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=MSID), delete_data=True)

    assert not safe.ok and not safe.retryable
    assert "drain it" in safe.message
    assert destructive.ok


def test_deletion_protection_requires_force_destroy(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    spec = DeprovisionSpec(result.handle, managed_service_id=MSID, config={"deletion_protection": True})

    protected = driver.deprovision(spec, delete_data=True)
    forced = driver.deprovision(spec, delete_data=True, force_destroy=True)

    assert not protected.ok and not protected.retryable
    assert forced.ok


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"deduplication_scope": "messageGroup"}, "fifo=true"),
        (
            {"fifo": True, "deduplication_scope": "queue", "fifo_throughput_limit": "perMessageGroupId"},
            "requires deduplication_scope=messageGroup",
        ),
        ({"fifo": True, "dead_letter_queue_arn": "arn:aws:sqs:us-east-1:123:standard"}, "both be FIFO"),
        ({"visibility_timeout_seconds": 50000}, "0 through 43200"),
        ({"kms_master_key_id": "arn:key", "sqs_managed_sse_enabled": True}, "mutually exclusive"),
        ({"redrive_policy": [], "fifo": False}, "JSON object"),
    ],
)
def test_invalid_queue_configuration_is_rejected(driver: SQSDriver, config, message) -> None:
    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


# ---- snapshot rejection ---------------------------------------


def test_snapshot_raises(driver: SQSDriver) -> None:
    """SQS doesn't snapshot — in-flight messages have no recovery
    value (matches platform-side #87/#127 invariant)."""
    result = driver.provision(_spec())
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle=result.handle, managed_service_id=MSID))


# ---- schemas ---------------------------------------------------


def test_binding_schema(driver: SQSDriver) -> None:
    schema = driver.binding_schema()
    assert "SQS_QUEUE_URL" in schema.env_vars
    assert "SQS_QUEUE_ARN" in schema.env_vars


def test_config_schema_includes_fifo(driver: SQSDriver) -> None:
    schema = driver.config_schema()
    assert "fifo" in schema["properties"]


def test_provision_does_not_adopt_or_retag_another_services_resource(driver) -> None:
    """The platform account "owns" every org's resources; only this service's is adopted (#1961)."""
    first = driver.provision(_spec(managed_service_id=MSID))
    second = driver.provision(
        _spec(
            managed_service_id="22222222-2222-4222-8222-222222222222",
            recorded_handle=first.handle,
            recorded_handle_exclusive=True,
        )
    )

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "ownership" in second.message
