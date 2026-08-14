from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.topic_sns import (
    SNSConfig,
    SNSFifoTopicDriver,
    SNSStandardTopicDriver,
)

TOPIC_ARN = "arn:aws:sns:us-west-2:123456789012:platform-steadymd-triage-prod-events"
FIFO_ARN = f"{TOPIC_ARN}.fifo"


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "events",
        "size": "small",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config() -> SNSConfig:
    return SNSConfig(
        region="us-west-2",
        account_id="123456789012",
        topic_name_prefix="platform",
    )


def _client(*, arn: str = TOPIC_ARN, attributes=None, subscriptions=None) -> MagicMock:
    client = MagicMock()
    client.create_topic.return_value = {"TopicArn": arn}
    client.get_topic_attributes.return_value = {
        "Attributes": attributes
        or {
            "TopicArn": arn,
            "FifoTopic": "true" if arn.endswith(".fifo") else "false",
            "KmsMasterKeyId": "arn:aws:kms:us-west-2:123456789012:key/key-1",
        },
    }
    client.list_subscriptions_by_topic.return_value = {
        "Subscriptions": subscriptions or [],
    }
    client.subscribe.return_value = {"SubscriptionArn": f"{arn}:subscription-id"}
    return client


def _not_found(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "NotFound", "Message": "not found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("sns")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_standard_topic_provisions_encryption_policy_data_protection_and_subscription():
    client = _client()
    driver = SNSStandardTopicDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(
            config={
                "kms_master_key_id": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                "display_name": "EMR triage events",
                "policy": {"Version": "2012-10-17", "Statement": []},
                "tracing_config": "Active",
                "signature_version": "2",
                "data_protection_policy": {
                    "Name": "audit-phi",
                    "Version": "2021-06-01",
                    "Statement": [],
                },
                "subscriptions": [
                    {
                        "protocol": "sqs",
                        "endpoint": "arn:aws:sqs:us-west-2:123456789012:triage",
                        "filter_policy": {"kind": ["emr-bug"]},
                        "filter_policy_scope": "MessageAttributes",
                        "raw_message_delivery": True,
                        "dead_letter_queue_arn": "arn:aws:sqs:us-west-2:123456789012:triage-dlq",
                    },
                ],
            },
        ),
    )

    assert result.ok and result.ready
    assert result.handle == f"topic/{TOPIC_ARN}"
    request = client.create_topic.call_args.kwargs
    assert request["Name"] == "platform-steadymd-triage-prod-events"
    assert request["Attributes"]["KmsMasterKeyId"].endswith("key/key-1")
    assert request["Attributes"]["TracingConfig"] == "Active"
    assert {tag["Key"] for tag in request["Tags"]} >= {
        "astrolift.io/binding",
        "astrolift.io/managed_service_id",
    }
    _validate("CreateTopic", request)
    subscribe = client.subscribe.call_args.kwargs
    assert subscribe["Protocol"] == "sqs"
    assert subscribe["Attributes"]["RawMessageDelivery"] == "true"
    assert "deadLetterTargetArn" in subscribe["Attributes"]["RedrivePolicy"]
    _validate("Subscribe", subscribe)
    data_policy = client.put_data_protection_policy.call_args.kwargs
    assert data_policy["ResourceArn"] == TOPIC_ARN
    _validate("PutDataProtectionPolicy", data_policy)


def test_fifo_topic_exposes_high_throughput_archive_and_content_deduplication():
    client = _client(arn=FIFO_ARN)
    driver = SNSFifoTopicDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(
            config={
                "content_based_deduplication": True,
                "fifo_throughput_scope": "MessageGroup",
                "archive_retention_days": 30,
                "subscriptions": [
                    {
                        "protocol": "sqs",
                        "endpoint": "arn:aws:sqs:us-west-2:123456789012:triage.fifo",
                    },
                ],
            },
        ),
    )

    assert result.ok
    request = client.create_topic.call_args.kwargs
    assert request["Name"].endswith(".fifo")
    assert request["Attributes"]["FifoTopic"] == "true"
    assert request["Attributes"]["ContentBasedDeduplication"] == "true"
    assert request["Attributes"]["FifoThroughputScope"] == "MessageGroup"
    assert request["Attributes"]["ArchivePolicy"] == '{"MessageRetentionPeriod":"30"}'
    _validate("CreateTopic", request)


@pytest.mark.parametrize(
    ("driver_type", "config", "message"),
    [
        (SNSStandardTopicDriver, {"archive_retention_days": 30}, "sns_fifo"),
        (SNSFifoTopicDriver, {"archive_retention_days": 0}, "1 through 365"),
        (SNSFifoTopicDriver, {"fifo_throughput_scope": "Queue"}, "Topic or MessageGroup"),
        (
            SNSFifoTopicDriver,
            {"subscriptions": [{"protocol": "https", "endpoint": "https://example.com/hook"}]},
            "only SQS",
        ),
        (
            SNSStandardTopicDriver,
            {
                "subscriptions": [
                    {
                        "protocol": "sqs",
                        "endpoint": "arn:queue",
                        "dead_letter_queue_arn": "arn:dlq",
                        "redrive_policy": {},
                    },
                ],
            },
            "either dead_letter_queue_arn",
        ),
    ],
)
def test_invalid_topic_configuration_is_rejected(driver_type, config, message):
    driver = driver_type(config=_config(), client=_client(arn=FIFO_ARN if driver_type.FIFO else TOPIC_ARN))

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


def test_update_reconciles_existing_subscription_and_prunes_only_when_requested():
    existing = [
        {
            "Protocol": "sqs",
            "Endpoint": "arn:aws:sqs:us-west-2:123456789012:keep",
            "SubscriptionArn": f"{TOPIC_ARN}:keep-id",
        },
        {
            "Protocol": "https",
            "Endpoint": "https://old.example.com/hook",
            "SubscriptionArn": f"{TOPIC_ARN}:old-id",
        },
    ]
    client = _client(subscriptions=existing)
    driver = SNSStandardTopicDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            handle=f"topic/{TOPIC_ARN}",
            config={
                "display_name": "Updated",
                "subscriptions": [
                    {
                        "protocol": "sqs",
                        "endpoint": "arn:aws:sqs:us-west-2:123456789012:keep",
                        "filter_policy": {"severity": ["high"]},
                    },
                ],
                "prune_subscriptions": True,
            },
        ),
    )

    assert result.ok
    client.subscribe.assert_not_called()
    client.set_subscription_attributes.assert_called_once()
    client.unsubscribe.assert_called_once_with(SubscriptionArn=f"{TOPIC_ARN}:old-id")
    client.set_topic_attributes.assert_any_call(
        TopicArn=TOPIC_ARN,
        AttributeName="DisplayName",
        AttributeValue="Updated",
    )


def test_update_missing_topic_is_not_reported_as_success():
    client = _client()
    client.get_topic_attributes.side_effect = _not_found("GetTopicAttributes")
    driver = SNSStandardTopicDriver(config=_config(), client=client)

    result = driver.update(UpdateSpec(handle=f"topic/{TOPIC_ARN}", config={"display_name": "x"}))

    assert not result.ok
    assert result.errors == ["not_found"]


def test_fifo_high_throughput_scope_cannot_be_silently_reverted():
    client = _client(
        arn=FIFO_ARN,
        attributes={
            "TopicArn": FIFO_ARN,
            "FifoTopic": "true",
            "FifoThroughputScope": "MessageGroup",
        },
    )
    driver = SNSFifoTopicDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            handle=f"topic/{FIFO_ARN}",
            config={"fifo_throughput_scope": "Topic"},
        ),
    )

    assert not result.ok
    assert result.errors == ["irreversible_fifo_throughput_scope"]
    client.set_topic_attributes.assert_not_called()


def test_binding_emits_portable_identity_publish_grant_and_customer_kms_grant():
    driver = SNSStandardTopicDriver(config=_config(), client=_client())

    binding = driver.binding(ServiceHandle(f"topic/{TOPIC_ARN}"), {"access_mode": "manage"})

    assert binding.env_vars["TOPIC_ARN_OR_ID"].literal == TOPIC_ARN
    assert binding.env_vars["TOPIC_NAME"].literal.endswith("events")
    assert binding.env_vars["TOPIC_REGION"].literal == "us-west-2"
    assert "sns:Publish" in binding.iam_grants[0].actions
    assert "sns:Subscribe" in binding.iam_grants[0].actions
    assert binding.iam_grants[1].actions == ["kms:Decrypt", "kms:GenerateDataKey"]


def test_archived_fifo_topic_requires_explicit_data_deletion():
    client = _client(
        arn=FIFO_ARN,
        attributes={
            "TopicArn": FIFO_ARN,
            "FifoTopic": "true",
            "ArchivePolicy": '{"MessageRetentionPeriod":"30"}',
        },
    )
    driver = SNSFifoTopicDriver(config=_config(), client=client)
    handle = f"topic/{FIFO_ARN}"

    safe = driver.deprovision(DeprovisionSpec(handle))
    destructive = driver.deprovision(DeprovisionSpec(handle), delete_data=True)

    assert not safe.ok and not safe.retryable
    assert "delete_data=true" in safe.message
    assert destructive.ok
    client.set_topic_attributes.assert_called_with(
        TopicArn=FIFO_ARN,
        AttributeName="ArchivePolicy",
        AttributeValue="{}",
    )
    client.delete_topic.assert_called_once_with(TopicArn=FIFO_ARN)


def test_deprovision_and_status_are_idempotent_when_topic_is_missing():
    client = _client()
    client.get_topic_attributes.side_effect = _not_found("GetTopicAttributes")
    driver = SNSStandardTopicDriver(config=_config(), client=client)
    handle = f"topic/{TOPIC_ARN}"

    status = driver.status(ServiceHandle(handle))
    deleted = driver.deprovision(DeprovisionSpec(handle))

    assert status.state == "deprovisioned"
    assert deleted.ok


def test_topic_snapshot_is_honestly_unsupported_and_schemas_are_portable():
    standard = SNSStandardTopicDriver(config=_config(), client=_client())
    fifo = SNSFifoTopicDriver(config=_config(), client=_client(arn=FIFO_ARN))

    with pytest.raises(ManagedServiceError, match="do not expose portable snapshots"):
        standard.snapshot(ServiceHandle(f"topic/{TOPIC_ARN}"))
    assert "archive_retention_days" not in standard.config_schema()["properties"]
    assert "archive_retention_days" in fifo.config_schema()["properties"]
    assert "TOPIC_ARN_OR_ID" in standard.binding_schema().env_vars
