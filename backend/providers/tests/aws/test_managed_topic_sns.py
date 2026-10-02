from __future__ import annotations

import dataclasses
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

SERVICE_ID = "11111111-1111-4111-8111-111111111111"
TOPIC_NAME = "platform-11111111111141118111111111111111"
TOPIC_ARN = f"arn:aws:sns:us-west-2:123456789012:{TOPIC_NAME}"
FIFO_ARN = f"{TOPIC_ARN}.fifo"


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "example",
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
        "managed_service_id": SERVICE_ID,
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config() -> SNSConfig:
    return SNSConfig(
        region="us-west-2",
        account_id="123456789012",
        topic_name_prefix="platform",
    )


def _client(*, arn: str = TOPIC_ARN, attributes=None, subscriptions=None, creating=False) -> MagicMock:
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
    client.list_tags_for_resource.return_value = {
        "Tags": [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": SERVICE_ID},
        ]
    }
    if creating:
        client.get_topic_attributes.side_effect = [
            _not_found("GetTopicAttributes"),
            client.get_topic_attributes.return_value,
        ]
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
    client = _client(creating=True)
    driver = SNSStandardTopicDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(
            config={
                "kms_master_key_id": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                "display_name": "Application events",
                "policy": {"Version": "2012-10-17", "Statement": []},
                "tracing_config": "Active",
                "signature_version": "2",
                "data_protection_policy": {
                    "Name": "audit-events",
                    "Version": "2021-06-01",
                    "Statement": [],
                },
                "subscriptions": [
                    {
                        "protocol": "sqs",
                        "endpoint": "arn:aws:sqs:us-west-2:123456789012:triage",
                        "filter_policy": {"kind": ["change"]},
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
    assert request["Name"] == TOPIC_NAME
    assert request["Attributes"] == {}
    client.set_topic_attributes.assert_any_call(
        TopicArn=TOPIC_ARN,
        AttributeName="KmsMasterKeyId",
        AttributeValue="arn:aws:kms:us-west-2:123456789012:key/key-1",
    )
    client.set_topic_attributes.assert_any_call(
        TopicArn=TOPIC_ARN, AttributeName="TracingConfig", AttributeValue="Active"
    )
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
    client = _client(arn=FIFO_ARN, creating=True)
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
    assert request["Attributes"] == {"FifoTopic": "true"}
    for key, value in [
        ("ContentBasedDeduplication", "true"),
        ("FifoThroughputScope", "MessageGroup"),
        ("ArchivePolicy", '{"MessageRetentionPeriod":"30"}'),
    ]:
        client.set_topic_attributes.assert_any_call(TopicArn=FIFO_ARN, AttributeName=key, AttributeValue=value)
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
            managed_service_id=SERVICE_ID,
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
            managed_service_id=SERVICE_ID,
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
    assert binding.env_vars["TOPIC_NAME"].literal == TOPIC_NAME
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

    safe = driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID))
    destructive = driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID), delete_data=True)

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
    deleted = driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID))

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


@pytest.mark.parametrize("operation", ["provision", "update", "deprovision"])
@pytest.mark.parametrize(
    "tags",
    [
        [],
        None,
        [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"},
        ],
    ],
)
def test_foreign_or_unknown_owner_never_reconfigures_subscribes_retags_or_deletes(operation, tags):
    client = _client()
    client.list_tags_for_resource.return_value = {"Tags": tags}
    driver = SNSStandardTopicDriver(config=_config(), client=client)
    config = {
        "display_name": "refused",
        "subscriptions": [{"protocol": "sqs", "endpoint": "arn:aws:sqs:us-west-2:123456789012:unwanted"}],
        "prune_subscriptions": True,
    }
    if operation == "provision":
        result = driver.provision(_spec(config=config))
    elif operation == "update":
        result = driver.update(UpdateSpec(handle=f"topic/{TOPIC_ARN}", managed_service_id=SERVICE_ID, config=config))
    else:
        result = driver.deprovision(
            DeprovisionSpec(handle=f"topic/{TOPIC_ARN}", managed_service_id=SERVICE_ID),
            delete_data=True,
            force_destroy=True,
        )
    assert not result.ok
    if operation != "provision":
        assert not result.retryable
    for method in [
        "create_topic",
        "tag_resource",
        "set_topic_attributes",
        "put_data_protection_policy",
        "subscribe",
        "set_subscription_attributes",
        "unsubscribe",
        "delete_topic",
    ]:
        getattr(client, method).assert_not_called()


@pytest.mark.parametrize("mismatch", ["response_arn", "live_arn", "mode", "duplicate_owner"])
def test_new_topic_still_needs_exact_response_live_identity_and_owner_before_reconciliation(mismatch):
    client = _client(creating=True)
    if mismatch == "response_arn":
        client.create_topic.return_value = {"TopicArn": TOPIC_ARN + "wrong"}
    elif mismatch == "live_arn":
        client.get_topic_attributes.return_value["Attributes"]["TopicArn"] = TOPIC_ARN + "wrong"
    elif mismatch == "mode":
        client.get_topic_attributes.return_value["Attributes"]["FifoTopic"] = "true"
    else:
        client.list_tags_for_resource.return_value["Tags"].append(
            {"Key": "astrolift.io/managed_service_id", "Value": SERVICE_ID}
        )
    result = SNSStandardTopicDriver(config=_config(), client=client).provision(
        _spec(config={"display_name": "refused"})
    )
    assert not result.ok
    client.set_topic_attributes.assert_not_called()
    client.tag_resource.assert_not_called()
    client.subscribe.assert_not_called()


@pytest.mark.parametrize(("region", "partition"), [("cn-north-1", "aws-cn"), ("us-gov-west-1", "aws-us-gov")])
def test_recorded_partition_and_case_sensitive_name_remain_the_exact_topic_target(region, partition):
    arn = f"arn:{partition}:sns:{region}:123456789012:Legacy_Topic"
    client = _client(arn=arn)
    driver = SNSStandardTopicDriver(
        config=dataclasses.replace(_config(), region=region, topic_name_prefix="renamed"), client=client
    )
    result = driver.provision(_spec(recorded_handle=f"topic/{arn}", app_slug="renamed"))
    assert result.ok and result.handle == f"topic/{arn}"
    client.create_topic.assert_not_called()
    client.list_tags_for_resource.assert_called_once_with(ResourceArn=arn)
    assert all(call.kwargs["TopicArn"] == arn for call in client.set_topic_attributes.call_args_list)
