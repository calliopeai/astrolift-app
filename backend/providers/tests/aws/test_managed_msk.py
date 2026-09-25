from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed._networking import ensure_msk_networking
from aws.managed.event_stream_msk import (
    MSKConfig,
    MSKProvisionedDriver,
    MSKServerlessDriver,
)

CLUSTER_NAME = "platform-steadymd-triage-prod-kafka"
CLUSTER_ARN = f"arn:aws:kafka:us-west-2:123456789012:cluster/{CLUSTER_NAME}/cluster-uuid"
KEY_ARN = "arn:aws:kms:us-west-2:123456789012:key/key-1"
SCRAM_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:AmazonMSK_user"


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "kafka",
        "size": "small",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config(**overrides) -> MSKConfig:
    values = {
        "region": "us-west-2",
        "account_id": "123456789012",
        "cluster_name_prefix": "platform",
        "subnet_ids": ("subnet-a", "subnet-b", "subnet-c"),
        "security_group_ids": ("sg-kafka",),
        "kms_key_arn": KEY_ARN,
        "kafka_version_default": "3.9.x",
        "deletion_protection_default": True,
        "poll_delay_seconds": 0,
        "max_poll_attempts": 3,
    }
    values.update(overrides)
    return MSKConfig(**values)


def _cluster(cluster_type="PROVISIONED", **overrides) -> dict:
    values = {
        "ClusterArn": CLUSTER_ARN,
        "ClusterName": CLUSTER_NAME,
        "ClusterType": cluster_type,
        "CurrentVersion": "K1",
        "State": "ACTIVE",
    }
    if cluster_type == "PROVISIONED":
        values["Provisioned"] = {
            "BrokerNodeGroupInfo": {
                "ClientSubnets": ["subnet-a", "subnet-b", "subnet-c"],
                "InstanceType": "kafka.m5.large",
            },
            "NumberOfBrokerNodes": 3,
            "ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}},
            "EncryptionInfo": {"EncryptionAtRest": {"DataVolumeKMSKeyId": KEY_ARN}},
        }
    else:
        values["Serverless"] = {
            "VpcConfigs": [{"SubnetIds": ["subnet-a", "subnet-b", "subnet-c"]}],
            "ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}},
        }
    values.update(overrides)
    return values


def _client(cluster_type="PROVISIONED", **cluster_overrides) -> MagicMock:
    client = MagicMock()
    client.create_cluster_v2.return_value = {
        "ClusterArn": CLUSTER_ARN,
        "ClusterName": CLUSTER_NAME,
        "ClusterType": cluster_type,
        "State": "CREATING",
    }
    client.describe_cluster_v2.return_value = {
        "ClusterInfo": _cluster(cluster_type, **cluster_overrides),
    }
    client.list_tags_for_resource.return_value = {
        "Tags": {"astrolift.io/managed-by": "platform"},
    }
    client.list_scram_secrets.return_value = {"SecretArnList": []}
    client.get_bootstrap_brokers.return_value = {
        "BootstrapBrokerStringSaslIam": "b-1:9098,b-2:9098",
    }
    return client


def _error(code: str, operation: str, message: str | None = None) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("kafka")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_provisioned_create_exposes_native_controls_and_platform_ownership():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    native = {
        "BrokerNodeGroupInfo": {
            "ClientSubnets": ["subnet-a", "subnet-b", "subnet-c"],
            "InstanceType": "kafka.m7g.large",
            "SecurityGroups": ["sg-kafka"],
            "StorageInfo": {
                "EbsStorageInfo": {
                    "VolumeSize": 100,
                    "ProvisionedThroughput": {"Enabled": True, "VolumeThroughput": 250},
                },
            },
            "ConnectivityInfo": {"PublicAccess": {"Type": "DISABLED"}},
        },
        "Rebalancing": {"Status": "ACTIVE"},
        "ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}},
        "EncryptionInfo": {
            "EncryptionAtRest": {"DataVolumeKMSKeyId": KEY_ARN},
            "EncryptionInTransit": {"ClientBroker": "TLS", "InCluster": True},
        },
        "EnhancedMonitoring": "PER_BROKER",
        "OpenMonitoring": {
            "Prometheus": {
                "JmxExporter": {"EnabledInBroker": True},
                "NodeExporter": {"EnabledInBroker": True},
            },
        },
        "KafkaVersion": "3.9.x",
        "LoggingInfo": {"BrokerLogs": {"CloudWatchLogs": {"Enabled": False}}},
        "NumberOfBrokerNodes": 3,
        "StorageMode": "TIERED",
    }

    result = driver.provision(_spec(config={"provisioned": native}))

    assert result.ok and result.ready and result.handle == f"event_stream/{CLUSTER_ARN}"
    request = client.create_cluster_v2.call_args.kwargs
    assert request["Provisioned"] == native
    assert request["Tags"]["astrolift.io/managed-by"] == "platform"
    assert request["Tags"]["astrolift.io/binding"] == "binding-1"
    _validate("CreateClusterV2", request)
    client.tag_resource.assert_called_once()


def test_convenience_provisioned_shape_is_complete_and_valid():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(
        _spec(
            size="custom",
            config={
                "kafka_version": "3.9.x",
                "instance_type": "kafka.m7g.xlarge",
                "broker_count": 6,
                "volume_size_gib": 500,
                "enhanced_monitoring": "PER_TOPIC_PER_BROKER",
                "storage_mode": "LOCAL",
            },
        ),
    )

    assert result.ok
    native = client.create_cluster_v2.call_args.kwargs["Provisioned"]
    assert native["NumberOfBrokerNodes"] == 6
    assert native["BrokerNodeGroupInfo"]["StorageInfo"]["EbsStorageInfo"]["VolumeSize"] == 500
    assert native["EncryptionInfo"]["EncryptionAtRest"]["DataVolumeKMSKeyId"] == KEY_ARN
    _validate("CreateClusterV2", client.create_cluster_v2.call_args.kwargs)


def test_two_az_default_chooses_a_valid_broker_multiple():
    client = _client()
    driver = MSKProvisionedDriver(
        config=_config(subnet_ids=("subnet-a", "subnet-b")),
        client=client,
        sleep=lambda _seconds: None,
    )

    result = driver.provision(_spec())

    assert result.ok
    assert client.create_cluster_v2.call_args.kwargs["Provisioned"]["NumberOfBrokerNodes"] == 4


def test_serverless_create_uses_iam_and_private_vpc_shape():
    client = _client("SERVERLESS")
    driver = MSKServerlessDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec())

    assert result.ok
    request = client.create_cluster_v2.call_args.kwargs
    assert "Provisioned" not in request
    assert request["Serverless"] == {
        "VpcConfigs": [
            {
                "SubnetIds": ["subnet-a", "subnet-b", "subnet-c"],
                "SecurityGroupIds": ["sg-kafka"],
            },
        ],
        "ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}},
    }
    _validate("CreateClusterV2", request)


def test_serverless_rejects_provisioned_and_non_iam_configuration():
    driver = MSKServerlessDriver(config=_config(), client=_client("SERVERLESS"))

    wrong_shape = driver.provision(_spec(config={"provisioned": {}}))
    wrong_auth = driver.provision(_spec(config={"auth_mode": "scram"}))

    assert not wrong_shape.ok and "cannot use provisioned" in wrong_shape.message
    assert not wrong_auth.ok and "IAM authentication only" in wrong_auth.message


def test_existing_external_cluster_is_never_silently_adopted():
    client = _client()
    client.create_cluster_v2.side_effect = _error(
        "ConflictException",
        "CreateClusterV2",
        "cluster already exists",
    )
    client.list_clusters_v2.return_value = {"ClusterInfoList": [_cluster()]}
    client.list_tags_for_resource.return_value = {"Tags": {}}
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec())

    assert not result.ok and "outside this resource declaration" in result.message
    client.tag_resource.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "parameters", "method"),
    [
        ("broker_count", {"TargetNumberOfBrokerNodes": 6}, "update_broker_count"),
        (
            "broker_storage",
            {"TargetBrokerEBSVolumeInfo": [{"KafkaBrokerNodeId": "1", "VolumeSizeGB": 500}]},
            "update_broker_storage",
        ),
        ("broker_type", {"TargetInstanceType": "kafka.m7g.large"}, "update_broker_type"),
        (
            "cluster_configuration",
            {"ConfigurationInfo": {"Arn": "arn:aws:kafka:us-west-2:123:configuration/cfg/id", "Revision": 2}},
            "update_cluster_configuration",
        ),
        ("kafka_version", {"TargetKafkaVersion": "3.9.x"}, "update_cluster_kafka_version"),
        ("connectivity", {"ConnectivityInfo": {"PublicAccess": {"Type": "DISABLED"}}}, "update_connectivity"),
        ("monitoring", {"EnhancedMonitoring": "PER_BROKER"}, "update_monitoring"),
        ("rebalancing", {"Rebalancing": {"Status": "ACTIVE"}}, "update_rebalancing"),
        ("security", {"ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}}}, "update_security"),
        ("storage", {"VolumeSizeGB": 500, "StorageMode": "LOCAL"}, "update_storage"),
        ("topic", {"TopicName": "events", "PartitionCount": 12}, "update_topic"),
    ],
)
def test_update_operations_use_current_version_and_exact_aws_shapes(operation, parameters, method):
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        UpdateSpec(
            f"event_stream/{CLUSTER_ARN}",
            config={"update_operations": [{"operation": operation, "parameters": parameters}]},
        ),
    )

    assert result.ok
    request = getattr(client, method).call_args.kwargs
    assert request["ClusterArn"] == CLUSTER_ARN
    if operation != "topic":
        assert request["CurrentVersion"] == "K1"
    _validate(
        {
            "broker_count": "UpdateBrokerCount",
            "broker_storage": "UpdateBrokerStorage",
            "broker_type": "UpdateBrokerType",
            "cluster_configuration": "UpdateClusterConfiguration",
            "kafka_version": "UpdateClusterKafkaVersion",
            "connectivity": "UpdateConnectivity",
            "monitoring": "UpdateMonitoring",
            "rebalancing": "UpdateRebalancing",
            "security": "UpdateSecurity",
            "storage": "UpdateStorage",
            "topic": "UpdateTopic",
        }[operation],
        request,
    )


def test_identical_update_plan_is_applied_once_across_retries():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    update = UpdateSpec(
        f"event_stream/{CLUSTER_ARN}",
        config={
            "update_operations": [
                {"operation": "broker_count", "parameters": {"TargetNumberOfBrokerNodes": 6}},
            ],
        },
    )

    first = driver.update(update)
    digest = client.tag_resource.call_args.kwargs["Tags"]["astrolift.io/update-plan-sha256"]
    client.list_tags_for_resource.return_value = {
        "Tags": {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/update-plan-sha256": digest,
        },
    }
    second = driver.update(update)

    assert first.ok and second.ok
    client.update_broker_count.assert_called_once()


def test_scram_associations_and_cluster_policy_reconcile_exact_delta():
    old_secret = SCRAM_ARN + "-old"
    client = _client()
    client.list_scram_secrets.return_value = {"SecretArnList": [old_secret]}
    client.get_cluster_policy.return_value = {"CurrentVersion": "P1", "Policy": "{}"}
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    policy = {"Version": "2012-10-17", "Statement": []}

    result = driver.update(
        UpdateSpec(
            f"event_stream/{CLUSTER_ARN}",
            config={
                "scram_secret_arns": [SCRAM_ARN],
                "prune_scram_secrets": True,
                "resource_policy": policy,
            },
        ),
    )

    assert result.ok
    client.batch_associate_scram_secret.assert_called_once_with(
        ClusterArn=CLUSTER_ARN,
        SecretArnList=[SCRAM_ARN],
    )
    client.batch_disassociate_scram_secret.assert_called_once_with(
        ClusterArn=CLUSTER_ARN,
        SecretArnList=[old_secret],
    )
    policy_request = client.put_cluster_policy.call_args.kwargs
    assert policy_request["CurrentVersion"] == "P1"
    assert policy_request["Policy"] == '{"Statement":[],"Version":"2012-10-17"}'
    _validate("PutClusterPolicy", policy_request)


def test_scram_association_chunks_requests_and_surfaces_partial_failures():
    secrets = [f"{SCRAM_ARN}-{index}" for index in range(11)]
    client = _client()
    client.batch_associate_scram_secret.side_effect = [
        {},
        {
            "UnprocessedScramSecrets": [
                {
                    "SecretArn": secrets[-1],
                    "ErrorCode": "BAD_SECRET",
                    "ErrorMessage": "invalid JSON",
                },
            ],
        },
    ]
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        UpdateSpec(
            f"event_stream/{CLUSTER_ARN}",
            config={"scram_secret_arns": secrets},
        ),
    )

    assert not result.ok and "BAD_SECRET" in result.message
    assert [len(call.kwargs["SecretArnList"]) for call in client.batch_associate_scram_secret.call_args_list] == [10, 1]


def test_policy_can_be_removed_idempotently():
    client = _client()
    client.delete_cluster_policy.side_effect = _error("NotFoundException", "DeleteClusterPolicy")
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.update(
        UpdateSpec(f"event_stream/{CLUSTER_ARN}", config={"resource_policy": None}),
    )

    assert result.ok


def test_iam_binding_is_portable_scoped_and_supports_secret_refs():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    binding = driver.binding(
        ServiceHandle(f"event_stream/{CLUSTER_ARN}"),
        {
            "access_mode": "read_write",
            "username_secret_ref": "bundle/kafka#username",
            "password_secret_ref": "bundle/kafka#password",
        },
    )

    assert binding.env_vars["EVENT_STREAM_BROKERS"].literal == "b-1:9098,b-2:9098"
    assert binding.env_vars["EVENT_STREAM_TLS"].literal == "true"
    assert binding.env_vars["EVENT_STREAM_USERNAME"].secret_ref == "bundle/kafka#username"
    assert binding.env_vars["EVENT_STREAM_PASSWORD"].secret_ref == "bundle/kafka#password"
    resources = {grant.resource: set(grant.actions) for grant in binding.iam_grants}
    assert "kafka:GetBootstrapBrokers" in resources[CLUSTER_ARN]
    assert "kafka-cluster:WriteDataIdempotently" in resources[CLUSTER_ARN]
    topic = next(resource for resource in resources if ":topic/" in resource)
    group = next(resource for resource in resources if ":group/" in resource)
    assert {"kafka-cluster:ReadData", "kafka-cluster:WriteData"} <= resources[topic]
    assert "kafka-cluster:AlterGroup" in resources[group]
    # MSK brokers, not Kafka clients, use the data-volume KMS key.
    assert KEY_ARN not in resources


def test_mtls_binding_can_carry_portable_certificate_secret_refs():
    client = _client(
        Provisioned={
            "BrokerNodeGroupInfo": {},
            "NumberOfBrokerNodes": 3,
            "ClientAuthentication": {"Tls": {"Enabled": True}},
        },
    )
    client.get_bootstrap_brokers.return_value = {
        "BootstrapBrokerStringTls": "b-1:9094,b-2:9094",
    }
    driver = MSKProvisionedDriver(config=_config(), client=client)

    binding = driver.binding(
        ServiceHandle(f"event_stream/{CLUSTER_ARN}"),
        {
            "auth_mode": "tls",
            "client_certificate_secret_ref": "bundle/kafka#client.crt",
            "client_key_secret_ref": "bundle/kafka#client.key",
            "ca_certificate_secret_ref": "bundle/kafka#ca.crt",
        },
    )

    assert binding.env_vars["EVENT_STREAM_CLIENT_CERT"].secret_ref == "bundle/kafka#client.crt"
    assert binding.env_vars["EVENT_STREAM_CLIENT_KEY"].secret_ref == "bundle/kafka#client.key"
    assert binding.env_vars["EVENT_STREAM_CA_CERT"].secret_ref == "bundle/kafka#ca.crt"


def test_binding_fails_honestly_when_requested_endpoint_is_unavailable():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client)

    with pytest.raises(ManagedServiceError, match="BootstrapBrokerStringPublicSaslIam"):
        driver.binding(
            ServiceHandle(f"event_stream/{CLUSTER_ARN}"),
            {"auth_mode": "iam", "bootstrap_scope": "public"},
        )


def test_deprovision_requires_guard_override_and_explicit_data_loss_ack():
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client)
    handle = f"event_stream/{CLUSTER_ARN}"

    protected = driver.deprovision(DeprovisionSpec(handle))
    retained = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=True,
        force_destroy=True,
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not retained.ok and retained.errors == ["retained_cluster_data_requires_delete_data"]
    assert deleted.ok
    request = client.delete_cluster.call_args.kwargs
    assert request == {"ClusterArn": CLUSTER_ARN, "CurrentVersion": "K1"}
    _validate("DeleteCluster", request)


@pytest.mark.parametrize(
    ("provider_state", "state"),
    [
        ("ACTIVE", "available"),
        ("CREATING", "provisioning"),
        ("UPDATING", "updating"),
        ("HEALING", "updating"),
        ("DELETING", "deprovisioning"),
        ("FAILED", "error"),
    ],
)
def test_status_maps_provider_lifecycle(provider_state, state):
    client = _client(State=provider_state)
    driver = MSKProvisionedDriver(config=_config(), client=client)

    assert driver.status(ServiceHandle(f"event_stream/{CLUSTER_ARN}")).state == state


def test_missing_paths_and_snapshot_contract_are_honest():
    client = _client()
    client.describe_cluster_v2.side_effect = _error("NotFoundException", "DescribeClusterV2")
    driver = MSKProvisionedDriver(config=_config(), client=client)
    handle = f"event_stream/{CLUSTER_ARN}"

    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert driver.deprovision(DeprovisionSpec(handle)).ok
    assert not driver.update(UpdateSpec(handle, config={})).ok
    with pytest.raises(ManagedServiceError, match="no cluster snapshot API"):
        driver.snapshot(ServiceHandle(handle))


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": True}, "unsupported MSK config fields"),
        ({"access_mode": "consume"}, "access_mode"),
        ({"bootstrap_scope": "internet"}, "bootstrap_scope"),
        ({"bootstrap_scope": "public", "bootstrap_ipv6": True}, "combination is unavailable"),
        ({"deletion_protection": "yes"}, "boolean"),
        ({"update_operations": {}}, "array"),
        ({"scram_secret_arns": "secret"}, "array of non-empty strings"),
        ({"resource_policy": "{}"}, "object or null"),
        ({"broker_count": 4}, "multiple of the number of client_subnets"),
        ({"update_operations": [{"operation": "reboot", "parameters": {}}]}, "must be one of"),
        ({"provisioned": {"KafkaVersion": "3.9.x"}}, "invalid AWS MSK request structure"),
    ],
)
def test_invalid_configuration_is_rejected_before_aws(config, message):
    client = _client()
    driver = MSKProvisionedDriver(config=_config(), client=client)

    result = driver.provision(_spec(config=config))

    assert not result.ok and message in result.message
    client.create_cluster_v2.assert_not_called()


def test_managed_config_for_resolves_both_msk_variants_without_cloud_discovery():
    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        auth_config={},
        provider_config={
            "account_id": "123456789012",
            "msk_subnet_ids": ["subnet-a", "subnet-b", "subnet-c"],
            "msk_security_group_ids": ["sg-kafka"],
            "msk_kafka_version": "3.9.x",
            "msk_cluster_name_prefix": "platform",
        },
    )

    provisioned = managed_config_for("aws", cluster, kind="event_stream", variant="msk")
    serverless = managed_config_for("aws", cluster, kind="event_stream", variant="msk_serverless")

    assert provisioned == serverless
    assert provisioned.subnet_ids == ("subnet-a", "subnet-b", "subnet-c")
    assert provisioned.security_group_ids == ("sg-kafka",)
    assert provisioned.kafka_version_default == "3.9.x"


def test_msk_networking_discovers_private_subnets_and_all_auth_ports():
    ec2 = MagicMock()
    eks = MagicMock()
    cluster = SimpleNamespace(
        slug="aws-prod",
        provider_config={},
        auth_config={},
    )
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": "subnet-a", "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-b", "AvailabilityZone": "us-west-2b", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-c", "AvailabilityZone": "us-west-2c", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-d", "AvailabilityZone": "us-west-2d", "MapPublicIpOnLaunch": False},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-msk"}

    subnets, groups = ensure_msk_networking(
        cluster,
        region="us-west-2",
        clients=(ec2, eks),
    )

    assert subnets == ["subnet-a", "subnet-b", "subnet-c"]
    assert groups == ["sg-msk"]
    ingress_ports = [
        call.kwargs["IpPermissions"][0]["FromPort"] for call in ec2.authorize_security_group_ingress.call_args_list
    ]
    assert ingress_ports == [9092, 9094, 9096, 9098]


def test_msk_networking_honors_pinned_resources_without_cloud_calls():
    cluster = SimpleNamespace(
        slug="aws-prod",
        provider_config={
            "msk_subnet_ids": ["subnet-a", "subnet-b"],
            "msk_security_group_ids": ["sg-pinned"],
        },
        auth_config={},
    )

    assert ensure_msk_networking(cluster, region="us-west-2", clients=(None, None)) == (
        ["subnet-a", "subnet-b"],
        ["sg-pinned"],
    )


def test_plugin_cost_and_schema_surfaces_are_wired():
    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    assert PLUGIN.managed_service_drivers[("event_stream", "msk")] is MSKProvisionedDriver
    assert PLUGIN.managed_service_drivers[("event_stream", "msk_serverless")] is MSKServerlessDriver
    assert SERVICE_CODE_BY_VARIANT[("event_stream", "msk")] == "AmazonMSK"
    for variant in ("msk", "msk_serverless"):
        entry = next(
            item
            for item in MATRIX.managed_services
            if item.plugin_id == "aws" and item.kind == "event_stream" and item.variant == variant
        )
        assert entry.status == "preview"
        assert "EVENT_STREAM_BROKERS" in entry.binding_envs
    assert (
        "EVENT_STREAM_BROKERS"
        in MSKProvisionedDriver(
            config=_config(),
            client=_client(),
        )
        .binding_schema()
        .env_vars
    )


def test_a_platform_cluster_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client()
    client.create_cluster_v2.side_effect = _error("ConflictException", "CreateClusterV2", "cluster already exists")
    client.list_clusters_v2.return_value = {"ClusterInfoList": [_cluster()]}
    client.list_tags_for_resource.return_value = {
        "Tags": {"astrolift.io/managed-by": "platform", "astrolift.io/organization": "globex"}
    }
    driver = MSKProvisionedDriver(config=_config(), client=client, sleep=lambda _seconds: None)

    result = driver.provision(_spec())

    assert not result.ok and "outside this resource declaration" in result.message
    client.tag_resource.assert_not_called()
