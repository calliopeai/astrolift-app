from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.stream_firehose import FirehoseConfig, FirehoseDriver

SERVICE_ID = "11111111-1111-4111-8111-111111111111"
STREAM_NAME = "platform-11111111111141118111111111111111"
STREAM_ARN = f"arn:aws:firehose:us-west-2:123456789012:deliverystream/{STREAM_NAME}"
ROLE_ARN = "arn:aws:iam::123456789012:role/firehose-delivery"
BUCKET_ARN = "arn:aws:s3:::triage-events"
KEY_ARN = "arn:aws:kms:us-west-2:123456789012:key/key-1"
DESTINATION_ID = "destinationId-000000000001"


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
        "config": {"destination": _destination("extended_s3")},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": SERVICE_ID,
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _update_spec(handle, **kwargs) -> UpdateSpec:
    return UpdateSpec(handle, managed_service_id=SERVICE_ID, **kwargs)


def _deprovision_spec(handle, **kwargs) -> DeprovisionSpec:
    return DeprovisionSpec(handle, managed_service_id=SERVICE_ID, **kwargs)


def _config(**overrides) -> FirehoseConfig:
    values = {
        "region": "us-west-2",
        "account_id": "123456789012",
        "delivery_stream_name_prefix": "platform",
        "deletion_protection_default": True,
        "poll_delay_seconds": 0,
        "max_poll_attempts": 3,
    }
    values.update(overrides)
    return FirehoseConfig(**values)


def _description(**overrides) -> dict:
    values = {
        "DeliveryStreamName": STREAM_NAME,
        "DeliveryStreamARN": STREAM_ARN,
        "DeliveryStreamStatus": "ACTIVE",
        "DeliveryStreamType": "DirectPut",
        "VersionId": "1",
        "Destinations": [
            {
                "DestinationId": DESTINATION_ID,
                "ExtendedS3DestinationDescription": {
                    "RoleARN": ROLE_ARN,
                    "BucketARN": BUCKET_ARN,
                },
            },
        ],
        "DeliveryStreamEncryptionConfiguration": {
            "Status": "ENABLED",
            "KeyType": "AWS_OWNED_CMK",
        },
    }
    values.update(overrides)
    return values


def _client(**description_overrides) -> MagicMock:
    client = MagicMock()
    client.create_delivery_stream.return_value = {"DeliveryStreamARN": STREAM_ARN}
    client.describe_delivery_stream.return_value = {
        "DeliveryStreamDescription": _description(**description_overrides),
    }
    client.list_tags_for_delivery_stream.return_value = {
        "Tags": [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": SERVICE_ID},
            {"Key": "astrolift.io/organization", "Value": "example"},
            {"Key": "astrolift.io/app", "Value": "triage"},
        ],
        "HasMoreTags": False,
    }
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
    service = Session().get_service_model("firehose")
    validate_parameters(params, service.operation_model(operation).input_shape)


def _s3() -> dict:
    return {"RoleARN": ROLE_ARN, "BucketARN": BUCKET_ARN}


def _destination(destination_type: str) -> dict:
    configs = {
        "s3": _s3(),
        "extended_s3": {**_s3(), "Prefix": "events/"},
        "redshift": {
            "RoleARN": ROLE_ARN,
            "ClusterJDBCURL": "jdbc:redshift://warehouse.example:5439/dev",
            "CopyCommand": {"DataTableName": "events"},
            "SecretsManagerConfiguration": {
                "Enabled": True,
                "SecretARN": "arn:aws:secretsmanager:us-west-2:123456789012:secret:firehose",
            },
            "S3Configuration": _s3(),
        },
        "elasticsearch": {
            "RoleARN": ROLE_ARN,
            "DomainARN": "arn:aws:es:us-west-2:123456789012:domain/triage",
            "IndexName": "events",
            "S3Configuration": _s3(),
        },
        "opensearch": {
            "RoleARN": ROLE_ARN,
            "DomainARN": "arn:aws:es:us-west-2:123456789012:domain/triage",
            "IndexName": "events",
            "S3Configuration": _s3(),
        },
        "opensearch_serverless": {
            "RoleARN": ROLE_ARN,
            "CollectionEndpoint": "https://collection.us-west-2.aoss.amazonaws.com",
            "IndexName": "events",
            "S3Configuration": _s3(),
        },
        "splunk": {
            "HECEndpoint": "https://splunk.example.com:8088",
            "HECEndpointType": "Raw",
            "SecretsManagerConfiguration": {
                "Enabled": True,
                "SecretARN": "arn:aws:secretsmanager:us-west-2:123456789012:secret:firehose",
            },
            "S3Configuration": _s3(),
        },
        "http_endpoint": {
            "EndpointConfiguration": {"Url": "https://events.example.com/ingest"},
            "RoleARN": ROLE_ARN,
            "S3Configuration": _s3(),
        },
        "snowflake": {
            "AccountUrl": "https://account.snowflakecomputing.com",
            "SecretsManagerConfiguration": {
                "Enabled": True,
                "SecretARN": "arn:aws:secretsmanager:us-west-2:123456789012:secret:firehose",
            },
            "Database": "EVENTS",
            "Schema": "PUBLIC",
            "Table": "TRIAGE",
            "RoleARN": ROLE_ARN,
            "S3Configuration": _s3(),
        },
        "iceberg": {
            "RoleARN": ROLE_ARN,
            "CatalogConfiguration": {},
            "S3Configuration": _s3(),
        },
    }
    return {"type": destination_type, "configuration": configs[destination_type]}


def test_provision_creates_direct_put_stream_with_encryption_and_tags():
    client = _client(
        DeliveryStreamEncryptionConfiguration={
            "Status": "ENABLED",
            "KeyType": "CUSTOMER_MANAGED_CMK",
            "KeyARN": KEY_ARN,
        },
    )
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(
        _spec(
            config={
                "source": {
                    "type": "direct_put",
                    "configuration": {"ThroughputHintInMBs": 5},
                },
                "destination": _destination("extended_s3"),
                "encryption": {
                    "enabled": True,
                    "key_type": "CUSTOMER_MANAGED_CMK",
                    "key_arn": KEY_ARN,
                },
            },
        ),
    )

    assert result.ok and result.ready and result.handle == f"stream/{STREAM_ARN}"
    create = client.create_delivery_stream.call_args.kwargs
    assert create["DeliveryStreamType"] == "DirectPut"
    assert create["DirectPutSourceConfiguration"] == {"ThroughputHintInMBs": 5}
    assert create["ExtendedS3DestinationConfiguration"]["BucketARN"] == BUCKET_ARN
    assert create["DeliveryStreamEncryptionConfigurationInput"]["KeyARN"] == KEY_ARN
    assert any(tag["Key"] == "astrolift.io/managed-by" for tag in create["Tags"])
    _validate("CreateDeliveryStream", create)
    _validate("TagDeliveryStream", client.tag_delivery_stream.call_args.kwargs)
    client.start_delivery_stream_encryption.assert_not_called()


@pytest.mark.parametrize(
    "destination_type",
    [
        "elasticsearch",
        "extended_s3",
        "http_endpoint",
        "iceberg",
        "opensearch",
        "opensearch_serverless",
        "redshift",
        "s3",
        "snowflake",
        "splunk",
    ],
)
def test_every_current_firehose_destination_shape_is_exposed(destination_type):
    client = _client()
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(
        _spec(config={"destination": _destination(destination_type)}),
    )

    assert result.ok, result.message
    create = client.create_delivery_stream.call_args.kwargs
    _validate("CreateDeliveryStream", create)
    assert any(key.endswith("DestinationConfiguration") for key in create)


def test_kinesis_source_is_supported_and_direct_put_access_is_rejected_for_it():
    client = _client(DeliveryStreamType="KinesisStreamAsSource")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    source = {
        "type": "kinesis",
        "configuration": {
            "KinesisStreamARN": "arn:aws:kinesis:us-west-2:123456789012:stream/input",
            "RoleARN": ROLE_ARN,
        },
    }

    accepted = driver.provision(
        _spec(config={"source": source, "destination": _destination("extended_s3")}),
    )
    rejected = driver.provision(
        _spec(
            config={
                "access_mode": "put",
                "source": source,
                "destination": _destination("extended_s3"),
            },
        ),
    )

    assert accepted.ok
    assert accepted.handle == f"stream/{STREAM_ARN}"
    assert not rejected.ok and "only valid for a direct_put" in rejected.message


def test_existing_managed_stream_is_reconciled_but_external_collision_is_refused():
    client = _client()
    client.create_delivery_stream.side_effect = _error("ResourceInUseException", "CreateDeliveryStream")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    managed = driver.provision(_spec())
    client.list_tags_for_delivery_stream.return_value = {"Tags": [], "HasMoreTags": False}
    external = driver.provision(_spec())

    assert managed.ok
    assert not external.ok and "ownership" in external.message


def test_existing_stream_source_or_destination_identity_cannot_drift_silently():
    client = _client(DeliveryStreamType="KinesisStreamAsSource")
    client.create_delivery_stream.side_effect = _error("ResourceInUseException", "CreateDeliveryStream")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    source_mismatch = driver.provision(_spec())

    assert not source_mismatch.ok and "reprovision is required" in source_mismatch.message
    client.tag_delivery_stream.assert_not_called()


def test_destination_update_uses_version_and_destination_identity():
    client = _client()
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        _update_spec(
            f"stream/{STREAM_ARN}",
            config={
                "destination_update": {
                    "type": "extended_s3",
                    "configuration": {"Prefix": "partitioned/"},
                },
            },
        ),
    )

    assert result.ok
    update = client.update_destination.call_args.kwargs
    assert update["CurrentDeliveryStreamVersionId"] == "1"
    assert update["DestinationId"] == DESTINATION_ID
    assert update["ExtendedS3DestinationUpdate"] == {"Prefix": "partitioned/"}
    _validate("UpdateDestination", update)


def test_destination_update_type_must_match_live_destination():
    client = _client()
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        _update_spec(
            f"stream/{STREAM_ARN}",
            config={
                "destination_update": {
                    "type": "splunk",
                    "configuration": {"HECAcknowledgmentTimeoutInSeconds": 300},
                },
            },
        ),
    )

    assert not result.ok and "does not match" in result.message
    client.update_destination.assert_not_called()


def test_encryption_key_rotation_stops_waits_and_restarts():
    old_key = "arn:aws:kms:us-west-2:123456789012:key/old"
    initial = _description(
        DeliveryStreamEncryptionConfiguration={
            "Status": "ENABLED",
            "KeyType": "CUSTOMER_MANAGED_CMK",
            "KeyARN": old_key,
        },
    )
    disabled = _description(
        DeliveryStreamEncryptionConfiguration={"Status": "DISABLED"},
    )
    enabled = _description(
        DeliveryStreamEncryptionConfiguration={
            "Status": "ENABLED",
            "KeyType": "CUSTOMER_MANAGED_CMK",
            "KeyARN": KEY_ARN,
        },
    )
    client = _client()
    client.describe_delivery_stream.side_effect = [
        {"DeliveryStreamDescription": initial},
        {"DeliveryStreamDescription": disabled},
        {"DeliveryStreamDescription": enabled},
    ]
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        _update_spec(
            f"stream/{STREAM_ARN}",
            config={
                "encryption": {
                    "enabled": True,
                    "key_type": "CUSTOMER_MANAGED_CMK",
                    "key_arn": KEY_ARN,
                },
            },
        ),
    )

    assert result.ok
    _validate(
        "StopDeliveryStreamEncryption",
        client.stop_delivery_stream_encryption.call_args.kwargs,
    )
    start = client.start_delivery_stream_encryption.call_args.kwargs
    assert start["DeliveryStreamEncryptionConfigurationInput"]["KeyARN"] == KEY_ARN
    _validate("StartDeliveryStreamEncryption", start)


def test_in_progress_encryption_is_awaited_instead_of_started_twice():
    enabling = _description(
        DeliveryStreamEncryptionConfiguration={
            "Status": "ENABLING",
            "KeyType": "CUSTOMER_MANAGED_CMK",
            "KeyARN": KEY_ARN,
        },
    )
    enabled = _description(
        DeliveryStreamEncryptionConfiguration={
            "Status": "ENABLED",
            "KeyType": "CUSTOMER_MANAGED_CMK",
            "KeyARN": KEY_ARN,
        },
    )
    client = _client()
    client.describe_delivery_stream.side_effect = [
        {"DeliveryStreamDescription": enabling},
        {"DeliveryStreamDescription": enabled},
    ]
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        _update_spec(
            f"stream/{STREAM_ARN}",
            config={
                "encryption": {
                    "enabled": True,
                    "key_type": "CUSTOMER_MANAGED_CMK",
                    "key_arn": KEY_ARN,
                },
            },
        ),
    )

    assert result.ok
    client.start_delivery_stream_encryption.assert_not_called()
    client.stop_delivery_stream_encryption.assert_not_called()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "destination is required"),
        ({"destination": {"type": "unknown", "configuration": {"x": 1}}}, "must be one of"),
        ({"destination": {"type": "s3", "configuration": {"BucketARN": BUCKET_ARN}}}, "RoleARN"),
        (
            {
                "destination": {
                    "type": "s3",
                    "configuration": {**_s3(), "NotAField": True},
                },
            },
            "NotAField",
        ),
        (
            {
                "destination": _destination("s3"),
                "encryption": {"key_type": "CUSTOMER_MANAGED_CMK"},
            },
            "requires key_arn",
        ),
    ],
)
def test_invalid_configuration_is_rejected_before_aws(config, message):
    client = _client()
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec(config=config))

    assert not result.ok and message in result.message
    client.create_delivery_stream.assert_not_called()


def test_binding_is_portable_and_manage_grants_are_resource_scoped():
    client = _client()
    driver = FirehoseDriver(config=_config(kms_key_id=KEY_ARN), client=client, sleep=lambda _seconds: None)
    cfg = {
        "access_mode": "manage",
        "destination": _destination("extended_s3"),
        "encryption": {"key_arn": KEY_ARN, "key_type": "CUSTOMER_MANAGED_CMK"},
    }

    binding = driver.binding(ServiceHandle(f"stream/{STREAM_ARN}"), cfg)

    assert binding.env_vars["STREAM_NAME"].literal == STREAM_NAME
    assert binding.env_vars["STREAM_ARN"].literal == STREAM_ARN
    grants = {(grant.resource, tuple(grant.actions)) for grant in binding.iam_grants}
    stream_actions = next(actions for resource, actions in grants if resource == STREAM_ARN)
    assert "firehose:PutRecordBatch" in stream_actions
    assert "firehose:UpdateDestination" in stream_actions
    assert (ROLE_ARN, ("iam:PassRole",)) in grants
    assert (
        KEY_ARN,
        ("kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"),
    ) in grants


def test_sourced_stream_defaults_to_observe_and_has_no_put_grant():
    client = _client(DeliveryStreamType="KinesisStreamAsSource")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    binding = driver.binding(ServiceHandle(f"stream/{STREAM_ARN}"))

    actions = binding.iam_grants[0].actions
    assert actions == ["firehose:DescribeDeliveryStream"]
    assert "observe" in binding.notes


def test_deprovision_requires_protection_override_and_buffer_loss_acknowledgement():
    client = _client()
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    handle = f"stream/{STREAM_ARN}"

    protected = driver.deprovision(_deprovision_spec(handle))
    buffered = driver.deprovision(
        _deprovision_spec(handle, config={"deletion_protection": False}),
    )
    deleted = driver.deprovision(
        _deprovision_spec(handle, config={"deletion_protection": False}),
        delete_data=True,
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not buffered.ok and buffered.errors == ["buffered_records_require_delete_data"]
    assert deleted.ok
    delete = client.delete_delivery_stream.call_args.kwargs
    assert delete["AllowForceDelete"] is False
    _validate("DeleteDeliveryStream", delete)


@pytest.mark.parametrize(
    ("provider_state", "state"),
    [
        ("ACTIVE", "available"),
        ("CREATING", "provisioning"),
        ("DELETING", "deprovisioning"),
        ("CREATING_FAILED", "error"),
        ("DELETING_FAILED", "error"),
    ],
)
def test_status_maps_firehose_lifecycle(provider_state, state):
    driver = FirehoseDriver(
        config=_config(),
        client=_client(DeliveryStreamStatus=provider_state),
        sleep=lambda _seconds: None,
    )

    result = driver.status(ServiceHandle(f"stream/{STREAM_ARN}"))

    assert result.state == state


def test_terminal_create_failure_is_not_reported_as_available():
    client = _client(DeliveryStreamStatus="CREATING_FAILED")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec())

    assert not result.ok and "terminal state CREATING_FAILED" in result.message


def test_missing_stream_paths_and_snapshot_contract_are_honest():
    client = _client()
    client.describe_delivery_stream.side_effect = _error("ResourceNotFoundException", "DescribeDeliveryStream")
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    handle = f"stream/{STREAM_ARN}"

    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert driver.deprovision(_deprovision_spec(handle)).ok
    assert not driver.update(_update_spec(handle, config={})).ok
    with pytest.raises(ManagedServiceError, match="no snapshot API"):
        driver.snapshot(ServiceHandle(handle))
    assert "destination" not in driver.editable_fields()
    assert "destination_update" in driver.editable_fields()


def test_a_platform_stream_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client()
    client.create_delivery_stream.side_effect = _error("ResourceInUseException", "CreateDeliveryStream")
    client.list_tags_for_delivery_stream.return_value = {
        "Tags": [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/organization", "Value": "globex"},
        ],
        "HasMoreTags": False,
    }
    driver = FirehoseDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec())

    assert not result.ok and "ownership" in result.message
    client.tag_delivery_stream.assert_not_called()


@pytest.mark.parametrize(
    ("destination_type", "field", "value"),
    [("redshift", "Password", "hunter2"), ("splunk", "HECToken", "t"), ("snowflake", "PrivateKey", "k" * 256)],
)
def test_plaintext_destination_credentials_are_refused_on_create_and_update(destination_type, field, value):
    """They would sit in the stored service config (#1953)."""
    destination = _destination(destination_type)
    destination["configuration"][field] = value
    driver = FirehoseDriver(config=_config(), client=_client(), sleep=lambda _seconds: None)

    created = driver.provision(_spec(config={"destination": destination}))
    updated = driver.update(_update_spec(f"stream/{STREAM_ARN}", config={"destination_update": destination}))

    assert not created.ok and field in created.message and "SecretsManagerConfiguration" in created.message
    assert not updated.ok and field in updated.message
