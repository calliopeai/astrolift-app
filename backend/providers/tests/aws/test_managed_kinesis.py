from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.stream_kinesis import KinesisConfig, KinesisDriver

STREAM_NAME = "platform-11111111111141118111111111111111"
STREAM_ARN = f"arn:aws:kinesis:us-west-2:123456789012:stream/{STREAM_NAME}"
CONSUMER_ARN = f"{STREAM_ARN}/consumer/triage:1234"
KEY_ARN = "arn:aws:kms:us-west-2:123456789012:key/key-1"


SERVICE_ID = "11111111-1111-4111-8111-111111111111"

_OWNED_TAGS = [
    {"Key": "astrolift.io/managed-by", "Value": "platform"},
    {"Key": "astrolift.io/organization", "Value": "example"},
    {"Key": "astrolift.io/app", "Value": "triage"},
    {"Key": "astrolift.io/managed_service_id", "Value": "11111111-1111-4111-8111-111111111111"},
]


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
        "managed_service_id": "11111111-1111-4111-8111-111111111111",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config() -> KinesisConfig:
    return KinesisConfig(
        region="us-west-2",
        account_id="123456789012",
        stream_name_prefix="platform",
        deletion_protection_default=True,
    )


def _summary(**overrides) -> dict:
    values = {
        "StreamName": STREAM_NAME,
        "StreamARN": STREAM_ARN,
        "StreamStatus": "ACTIVE",
        "StreamModeDetails": {"StreamMode": "PROVISIONED"},
        "OpenShardCount": 1,
        "RetentionPeriodHours": 24,
        "EncryptionType": "NONE",
        "KeyId": "",
        "EnhancedMonitoring": [],
        "MaxRecordSizeInKiB": 1024,
    }
    values.update(overrides)
    return values


def _client(**summary_overrides) -> MagicMock:
    client = MagicMock()
    client.describe_stream_summary.return_value = {
        "StreamDescriptionSummary": _summary(**summary_overrides),
    }
    client.list_stream_consumers.return_value = {"Consumers": []}
    client.list_tags_for_resource.return_value = {"Tags": _OWNED_TAGS}
    return client


def _error(code: str, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("kinesis")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_provision_reconciles_every_supported_stream_control():
    client = _client()
    driver = KinesisDriver(config=_config(), client=client)
    policy = {"Version": "2012-10-17", "Statement": []}

    result = driver.provision(
        _spec(
            size="custom",
            config={
                "stream_mode": "PROVISIONED",
                "shard_count": 2,
                "retention_hours": 72,
                "kms_key_id": KEY_ARN,
                "enhanced_monitoring": ["IncomingBytes"],
                "warm_throughput_mibps": 4,
                "max_record_size_kib": 2048,
                "resource_policy": policy,
                "consumers": ["triage"],
            },
        ),
    )

    assert result.ok and result.ready and result.handle == f"stream/{STREAM_ARN}"
    create = client.create_stream.call_args.kwargs
    assert create["StreamModeDetails"] == {"StreamMode": "PROVISIONED"}
    assert create["ShardCount"] == 2
    assert create["WarmThroughputMiBps"] == 4
    assert create["MaxRecordSizeInKiB"] == 2048
    assert create["Tags"]["astrolift.io/managed-by"] == "platform"
    _validate("CreateStream", create)
    _validate("AddTagsToStream", client.add_tags_to_stream.call_args.kwargs)
    _validate("UpdateShardCount", client.update_shard_count.call_args.kwargs)
    _validate(
        "IncreaseStreamRetentionPeriod",
        client.increase_stream_retention_period.call_args.kwargs,
    )
    client.update_stream_warm_throughput.assert_not_called()
    client.update_max_record_size.assert_not_called()
    encryption = client.start_stream_encryption.call_args.kwargs
    assert encryption["KeyId"] == KEY_ARN
    _validate("StartStreamEncryption", encryption)
    _validate("EnableEnhancedMonitoring", client.enable_enhanced_monitoring.call_args.kwargs)
    resource_policy = client.put_resource_policy.call_args.kwargs
    assert resource_policy["Policy"] == '{"Statement":[],"Version":"2012-10-17"}'
    _validate("PutResourcePolicy", resource_policy)
    consumer = client.register_stream_consumer.call_args.kwargs
    assert consumer["ConsumerName"] == "triage"
    assert consumer["Tags"]["astrolift.io/managed-by"] == "platform"
    _validate("RegisterStreamConsumer", consumer)


def test_existing_stream_is_idempotently_reconciled():
    client = _client(
        StreamModeDetails={"StreamMode": "ON_DEMAND"},
        EncryptionType="KMS",
        KeyId="alias/aws/kinesis",
    )
    client.create_stream.side_effect = _error("ResourceInUseException", "CreateStream")
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert result.ok and result.ready
    client.add_tags_to_stream.assert_called_once()
    client.start_stream_encryption.assert_not_called()


def test_existing_external_stream_is_not_silently_adopted():
    client = _client(StreamModeDetails={"StreamMode": "ON_DEMAND"})
    client.create_stream.side_effect = _error("ResourceInUseException", "CreateStream")
    client.list_tags_for_resource.return_value = {"Tags": []}
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert not result.ok
    assert "outside this resource declaration" in result.message
    client.add_tags_to_stream.assert_not_called()


def test_update_applies_warm_throughput_and_max_record_size():
    client = _client(WarmThroughput={"TargetMiBps": 2}, MaxRecordSizeInKiB=1024)
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            f"stream/{STREAM_ARN}",
            managed_service_id=SERVICE_ID,
            config={"warm_throughput_mibps": 4, "max_record_size_kib": 2048},
        ),
    )

    assert result.ok
    _validate("UpdateStreamWarmThroughput", client.update_stream_warm_throughput.call_args.kwargs)
    _validate("UpdateMaxRecordSize", client.update_max_record_size.call_args.kwargs)


def test_update_changes_mode_then_waits_and_resizes():
    client = _client(StreamModeDetails={"StreamMode": "ON_DEMAND"})
    client.describe_stream_summary.side_effect = [
        {"StreamDescriptionSummary": _summary(StreamModeDetails={"StreamMode": "ON_DEMAND"})},
        {"StreamDescriptionSummary": _summary(StreamModeDetails={"StreamMode": "PROVISIONED"})},
        {
            "StreamDescriptionSummary": _summary(
                StreamModeDetails={"StreamMode": "PROVISIONED"},
                OpenShardCount=3,
            ),
        },
    ]
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            f"stream/{STREAM_ARN}",
            managed_service_id=SERVICE_ID,
            config={"stream_mode": "PROVISIONED", "shard_count": 3},
        ),
    )

    assert result.ok
    _validate("UpdateStreamMode", client.update_stream_mode.call_args.kwargs)
    assert client.update_shard_count.call_args.kwargs["TargetShardCount"] == 3


@pytest.mark.parametrize(
    ("config", "size", "message"),
    [
        ({"stream_mode": "BURST"}, "small", "ON_DEMAND or PROVISIONED"),
        ({"shard_count": 0, "stream_mode": "PROVISIONED"}, "small", "positive integer"),
        ({"shard_count": 2}, "small", "only valid for PROVISIONED"),
        ({"stream_mode": "PROVISIONED"}, "custom", "require shard_count"),
        ({"retention_hours": 23}, "small", "24 through 8760"),
        ({"max_record_size_kib": 1000}, "small", "1024 through 10240"),
        ({"enhanced_monitoring": ["Nope"]}, "small", "unsupported"),
        ({"enhanced_monitoring": ["ALL", "IncomingBytes"]}, "small", "cannot be combined"),
        ({"consumers": ["same", "same"]}, "small", "duplicate"),
        ({"encryption_enabled": False, "kms_key_id": KEY_ARN}, "small", "cannot be set"),
        ({"resource_policy": "[]"}, "small", "must contain a JSON object"),
    ],
)
def test_invalid_configuration_is_rejected(config, size, message):
    driver = KinesisDriver(config=_config(), client=_client())

    result = driver.provision(_spec(size=size, config=config))

    assert not result.ok and message in result.message


def test_invalid_operator_retention_default_is_rejected_before_aws():
    cfg = KinesisConfig(
        region="us-west-2",
        account_id="123456789012",
        retention_hours_default=12,
    )
    client = _client(StreamModeDetails={"StreamMode": "ON_DEMAND"})
    driver = KinesisDriver(config=cfg, client=client)

    result = driver.provision(_spec())

    assert not result.ok and "24 through 8760" in result.message
    client.create_stream.assert_not_called()


def test_monitoring_reconciliation_enables_and_disables_exact_delta():
    client = _client(
        EnhancedMonitoring=[{"ShardLevelMetrics": ["IncomingBytes", "OutgoingBytes"]}],
        EncryptionType="KMS",
        KeyId="alias/aws/kinesis",
    )
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            f"stream/{STREAM_ARN}",
            managed_service_id=SERVICE_ID,
            config={"enhanced_monitoring": ["IncomingBytes", "IncomingRecords"]},
        ),
    )

    assert result.ok
    assert client.disable_enhanced_monitoring.call_args.kwargs["ShardLevelMetrics"] == ["OutgoingBytes"]
    assert client.enable_enhanced_monitoring.call_args.kwargs["ShardLevelMetrics"] == ["IncomingRecords"]


def test_retention_decrease_requires_explicit_data_loss_acknowledgement():
    client = _client(RetentionPeriodHours=72, EncryptionType="KMS", KeyId="alias/aws/kinesis")
    driver = KinesisDriver(config=_config(), client=client)

    refused = driver.update(
        UpdateSpec(f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"retention_hours": 24}),
    )
    allowed = driver.update(
        UpdateSpec(
            f"stream/{STREAM_ARN}",
            managed_service_id=SERVICE_ID,
            config={"retention_hours": 24, "allow_retention_decrease": True},
        ),
    )

    assert not refused.ok and "allow_retention_decrease=true" in refused.message
    assert allowed.ok
    _validate(
        "DecreaseStreamRetentionPeriod",
        client.decrease_stream_retention_period.call_args.kwargs,
    )


def test_policy_can_be_explicitly_removed_and_missing_policy_is_idempotent():
    client = _client()
    client.delete_resource_policy.side_effect = _error("ResourceNotFoundException", "DeleteResourcePolicy")
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"resource_policy": None})
    )

    assert result.ok
    client.delete_resource_policy.assert_called_once_with(ResourceARN=STREAM_ARN)


def test_consumer_pruning_never_deletes_external_consumers():
    managed_arn = f"{STREAM_ARN}/consumer/old-managed:1"
    external_arn = f"{STREAM_ARN}/consumer/old-external:2"
    client = _client()
    client.list_stream_consumers.return_value = {
        "Consumers": [
            {"ConsumerName": "old-managed", "ConsumerARN": managed_arn},
            {"ConsumerName": "old-external", "ConsumerARN": external_arn},
        ],
    }

    def tags(**request):
        if request["ResourceARN"] in {managed_arn, STREAM_ARN}:
            return {"Tags": _OWNED_TAGS}
        return {"Tags": []}

    client.list_tags_for_resource.side_effect = tags
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"consumers": [], "prune_consumers": True}
        ),
    )

    assert result.ok
    client.deregister_stream_consumer.assert_called_once_with(ConsumerARN=managed_arn)


def test_deprovision_requires_protection_override_and_explicit_data_loss():
    client = _client()
    driver = KinesisDriver(config=_config(), client=client)
    handle = f"stream/{STREAM_ARN}"

    protected = driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID))
    retained = driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=SERVICE_ID, config={"deletion_protection": False}),
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not retained.ok and retained.errors == ["retained_stream_data_requires_delete_data"]
    client.delete_stream.assert_not_called()


def test_deprovision_refuses_external_consumer_without_force():
    client = _client()
    client.list_stream_consumers.return_value = {
        "Consumers": [{"ConsumerName": "external", "ConsumerARN": CONSUMER_ARN}],
    }
    client.list_tags_for_resource.side_effect = lambda **params: {
        "Tags": _OWNED_TAGS if params["ResourceARN"] == STREAM_ARN else []
    }
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.deprovision(
        DeprovisionSpec(
            f"stream/{STREAM_ARN}",
            managed_service_id=SERVICE_ID,
            config={"deletion_protection": False},
        ),
        delete_data=True,
    )

    assert not result.ok and result.errors == ["external_consumers_present"]
    client.delete_stream.assert_not_called()


def test_force_destroy_deletes_consumers_and_stream_data():
    client = _client()
    client.list_stream_consumers.return_value = {
        "Consumers": [{"ConsumerName": "external", "ConsumerARN": CONSUMER_ARN}],
    }
    client.list_tags_for_resource.side_effect = lambda **params: {
        "Tags": _OWNED_TAGS if params["ResourceARN"] == STREAM_ARN else []
    }
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.deprovision(
        DeprovisionSpec(f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID),
        delete_data=True,
        force_destroy=True,
    )

    assert result.ok
    client.deregister_stream_consumer.assert_called_once_with(ConsumerARN=CONSUMER_ARN)
    delete = client.delete_stream.call_args.kwargs
    assert delete["EnforceConsumerDeletion"] is True
    _validate("DeleteStream", delete)


def test_binding_is_portable_and_access_scoped_with_consumer_and_kms_grants():
    client = _client(EncryptionType="KMS", KeyId=KEY_ARN)
    client.list_stream_consumers.return_value = {
        "Consumers": [{"ConsumerName": "triage", "ConsumerARN": CONSUMER_ARN}],
    }
    driver = KinesisDriver(config=_config(), client=client)

    binding = driver.binding(ServiceHandle(f"stream/{STREAM_ARN}"), {"access_mode": "read_write"})

    assert binding.env_vars["STREAM_NAME"].literal == STREAM_NAME
    assert binding.env_vars["STREAM_ARN"].literal == STREAM_ARN
    grants = {(grant.resource, tuple(grant.actions)) for grant in binding.iam_grants}
    stream_actions = next(actions for resource, actions in grants if resource == STREAM_ARN)
    assert "kinesis:PutRecords" in stream_actions and "kinesis:GetRecords" in stream_actions
    assert (
        CONSUMER_ARN,
        ("kinesis:DescribeStreamConsumer", "kinesis:SubscribeToShard"),
    ) in grants
    assert (KEY_ARN, ("kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey")) in grants
    assert "STREAM_NAME" in driver.binding_schema().env_vars


@pytest.mark.parametrize(
    ("provider_state", "state"),
    [
        ("ACTIVE", "available"),
        ("CREATING", "provisioning"),
        ("UPDATING", "updating"),
        ("DELETING", "deprovisioning"),
    ],
)
def test_status_maps_provider_lifecycle(provider_state, state):
    driver = KinesisDriver(config=_config(), client=_client(StreamStatus=provider_state))

    result = driver.status(ServiceHandle(f"stream/{STREAM_ARN}"))

    assert result.state == state


def test_missing_stream_paths_are_honest_and_snapshot_is_unsupported():
    client = _client()
    client.describe_stream_summary.side_effect = _error("ResourceNotFoundException", "DescribeStreamSummary")
    driver = KinesisDriver(config=_config(), client=client)
    handle = f"stream/{STREAM_ARN}"

    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID)).ok
    assert not driver.update(UpdateSpec(handle, config={})).ok
    with pytest.raises(ManagedServiceError, match="no snapshot API"):
        driver.snapshot(ServiceHandle(handle))


def test_a_platform_stream_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client()
    client.create_stream.side_effect = _error("ResourceInUseException", "CreateStream")
    client.list_tags_for_resource.return_value = {
        "Tags": [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/organization", "Value": "globex"},
        ],
    }
    driver = KinesisDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert not result.ok and "outside this resource declaration" in result.message
    client.add_tags_to_stream.assert_not_called()


@pytest.mark.parametrize("mismatch", ["name", "arn", "owner", "duplicate_owner"])
def test_creation_response_and_live_owner_must_prove_the_exact_target_before_reconcile(mismatch):
    client = _client()
    if mismatch == "name":
        client.describe_stream_summary.return_value["StreamDescriptionSummary"]["StreamName"] = "foreign-name"
    elif mismatch == "arn":
        client.describe_stream_summary.return_value["StreamDescriptionSummary"]["StreamARN"] = (
            "arn:aws:kinesis:us-west-2:123456789012:stream/foreign-name"
        )
    else:
        tags = [dict(row) for row in _OWNED_TAGS]
        if mismatch == "owner":
            tags[-1]["Value"] = "22222222-2222-4222-8222-222222222222"
        else:
            tags.append({"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"})
        client.list_tags_for_resource.return_value = {"Tags": tags}
    result = KinesisDriver(config=_config(), client=client).provision(_spec())
    assert not result.ok
    client.add_tags_to_stream.assert_not_called()
    client.start_stream_encryption.assert_not_called()
    client.register_stream_consumer.assert_not_called()


@pytest.mark.parametrize("region,partition", [("cn-north-1", "aws-cn"), ("us-gov-west-1", "aws-us-gov")])
def test_recorded_partition_and_legacy_name_are_preserved_during_real_driver_reconcile(region, partition):
    from dataclasses import replace

    name = "Legacy.Stream_Name"
    arn = f"arn:{partition}:kinesis:{region}:123456789012:stream/{name}"
    client = _client(StreamName=name, StreamARN=arn, StreamModeDetails={"StreamMode": "ON_DEMAND"})
    client.create_stream.side_effect = _error("ResourceInUseException", "CreateStream")
    driver = KinesisDriver(config=replace(_config(), region=region), client=client)
    result = driver.provision(_spec(recorded_handle=f"stream/{arn}"))
    assert result.ok and result.handle == f"stream/{arn}"
    assert client.create_stream.call_args.kwargs["StreamName"] == name
    assert client.add_tags_to_stream.call_args.kwargs["StreamARN"] == arn


@pytest.mark.parametrize("operation", ["update", "deprovision"])
@pytest.mark.parametrize("mismatch", ["foreign_owner", "unknown_owner", "duplicate_owner", "live_name", "live_arn"])
def test_foreign_or_mismatched_stream_never_changes_policy_consumers_or_data(operation, mismatch):
    client = _client()
    if mismatch == "foreign_owner":
        client.list_tags_for_resource.return_value = {
            "Tags": [
                {"Key": "astrolift.io/managed-by", "Value": "platform"},
                {"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"},
            ]
        }
    elif mismatch == "unknown_owner":
        client.list_tags_for_resource.return_value = {"Tags": None}
    elif mismatch == "duplicate_owner":
        client.list_tags_for_resource.return_value = {
            "Tags": [*_OWNED_TAGS, {"Key": "astrolift.io/managed_service_id", "Value": SERVICE_ID}]
        }
    elif mismatch == "live_name":
        client.describe_stream_summary.return_value["StreamDescriptionSummary"]["StreamName"] = "different"
    else:
        client.describe_stream_summary.return_value["StreamDescriptionSummary"]["StreamARN"] = STREAM_ARN + "different"
    driver = KinesisDriver(config=_config(), client=client)
    if operation == "update":
        result = driver.update(
            UpdateSpec(
                f"stream/{STREAM_ARN}",
                managed_service_id=SERVICE_ID,
                config={"retention_hours": 72, "resource_policy": {}, "consumers": [], "prune_consumers": True},
            )
        )
    else:
        result = driver.deprovision(
            DeprovisionSpec(f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
    assert not result.ok and not result.retryable
    for method in [
        "increase_stream_retention_period",
        "put_resource_policy",
        "delete_resource_policy",
        "register_stream_consumer",
        "deregister_stream_consumer",
        "delete_stream",
        "add_tags_to_stream",
    ]:
        getattr(client, method).assert_not_called()


def test_update_consumer_creation_keeps_the_canonical_stream_owner_marker():
    client = _client()
    result = KinesisDriver(config=_config(), client=client).update(
        UpdateSpec(f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"consumers": ["new-consumer"]})
    )
    assert result.ok
    request = client.register_stream_consumer.call_args.kwargs
    assert request["Tags"] == {"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": SERVICE_ID}
    _validate("RegisterStreamConsumer", request)


@pytest.mark.parametrize("operation", ["update", "deprovision"])
def test_foreign_platform_marked_consumers_are_not_owned_by_the_stream_service(operation):
    client = _client()
    client.list_stream_consumers.return_value = {
        "Consumers": [{"ConsumerName": "foreign", "ConsumerARN": CONSUMER_ARN}]
    }
    foreign_tags = [
        {"Key": "astrolift.io/managed-by", "Value": "platform"},
        {"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"},
    ]
    client.list_tags_for_resource.side_effect = lambda **params: {
        "Tags": _OWNED_TAGS if params["ResourceARN"] == STREAM_ARN else foreign_tags
    }
    driver = KinesisDriver(config=_config(), client=client)
    if operation == "update":
        result = driver.update(
            UpdateSpec(
                f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"consumers": [], "prune_consumers": True}
            )
        )
        assert result.ok
    else:
        result = driver.deprovision(
            DeprovisionSpec(
                f"stream/{STREAM_ARN}", managed_service_id=SERVICE_ID, config={"deletion_protection": False}
            ),
            delete_data=True,
        )
        assert not result.ok and not result.retryable
        assert result.errors == ["external_consumers_present"]
    client.deregister_stream_consumer.assert_not_called()
    client.delete_stream.assert_not_called()
