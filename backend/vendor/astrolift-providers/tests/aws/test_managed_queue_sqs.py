"""Tests for SQS Queue managed-service driver (#35)."""

from __future__ import annotations

from collections.abc import Generator

import boto3
import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.queue_sqs import KIND, SQSConfig, SQSDriver


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


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
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
    driver: SQSDriver, sqs_client,
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
    driver: SQSDriver, sqs_client,
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
    driver: SQSDriver, sqs_client,
) -> None:
    result = driver.provision(_spec(
        config={"visibility_timeout_seconds": 120},
    ))
    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout"],
    )
    assert attrs["Attributes"]["VisibilityTimeout"] == "120"


# ---- status ----------------------------------------------------


def test_status_available(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=result.handle))
    assert status.state == "available"


def test_status_deprovisioned_when_missing(driver: SQSDriver) -> None:
    status = driver.status(
        ServiceHandle(handle="queue/never-existed-queue"),
    )
    assert status.state == "deprovisioned"


# ---- binding ---------------------------------------------------


def test_binding_emits_env_vars(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle))
    assert "SQS_QUEUE_NAME" in binding.env_vars
    assert "SQS_QUEUE_URL" in binding.env_vars
    assert "SQS_QUEUE_ARN" in binding.env_vars


def test_binding_emits_iam_grants(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle))
    assert len(binding.iam_grants) == 1
    grant = binding.iam_grants[0]
    assert "sqs:SendMessage" in grant.actions
    assert "sqs:ReceiveMessage" in grant.actions


# ---- update ---------------------------------------------------


def test_update_changes_visibility(driver: SQSDriver, sqs_client) -> None:
    result = driver.provision(_spec(size="small"))
    update = driver.update(UpdateSpec(handle=result.handle, size="large"))
    assert update.ok is True

    _, queue_name = parse_handle(result.handle)
    response = sqs_client.get_queue_url(QueueName=queue_name)
    attrs = sqs_client.get_queue_attributes(
        QueueUrl=response["QueueUrl"],
        AttributeNames=["VisibilityTimeout"],
    )
    assert int(attrs["Attributes"]["VisibilityTimeout"]) >= 60


def test_update_no_op_when_no_changes(driver: SQSDriver) -> None:
    result = driver.provision(_spec())
    update = driver.update(UpdateSpec(handle=result.handle))
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
        DeprovisionSpec(handle=result.handle), delete_data=True,
    )
    assert deprov.ok is True
    response = sqs_client.list_queues()
    urls = response.get("QueueUrls", [])
    assert not any(queue_name in url for url in urls)


def test_deprovision_already_gone_idempotent(driver: SQSDriver) -> None:
    deprov = driver.deprovision(
        DeprovisionSpec(handle="queue/never-existed"), delete_data=True,
    )
    assert deprov.ok is True


# ---- snapshot rejection ---------------------------------------


def test_snapshot_raises(driver: SQSDriver) -> None:
    """SQS doesn't snapshot — in-flight messages have no recovery
    value (matches platform-side #87/#127 invariant)."""
    result = driver.provision(_spec())
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle=result.handle))


# ---- schemas ---------------------------------------------------


def test_binding_schema(driver: SQSDriver) -> None:
    schema = driver.binding_schema()
    assert "SQS_QUEUE_URL" in schema.env_vars
    assert "SQS_QUEUE_ARN" in schema.env_vars


def test_config_schema_includes_fifo(driver: SQSDriver) -> None:
    schema = driver.config_schema()
    assert "fifo" in schema["properties"]
