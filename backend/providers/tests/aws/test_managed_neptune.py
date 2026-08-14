from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.neptune import (
    NeptuneConfig,
    NeptuneProvisionedDriver,
    NeptuneServerlessDriver,
)


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "knowledge-graph",
        "size": "medium",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config(*, serverless: bool = False) -> NeptuneConfig:
    return NeptuneConfig(
        region="us-west-2",
        account_id="123456789012",
        db_subnet_group="neptune-private",
        security_group_ids=["sg-neptune"],
        cluster_name_prefix="platform",
        serverless_v2=serverless,
    )


def _client(*, cluster=None, instances=None) -> MagicMock:
    client = MagicMock()
    client.describe_db_clusters.return_value = {"DBClusters": [] if cluster is None else [cluster]}
    client.describe_db_instances.return_value = {"DBInstances": instances or []}
    client.list_tags_for_resource.return_value = {
        "TagList": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
    }
    return client


def _cluster(**overrides):
    values = {
        "DBClusterIdentifier": "platform-steadymd-triage-prod-knowledge-graph",
        "DBClusterArn": ("arn:aws:rds:us-west-2:123456789012:cluster:platform-steadymd-triage-prod-knowledge-graph"),
        "DbClusterResourceId": "cluster-ABCDEFGHIJKL",
        "Status": "available",
        "Endpoint": "writer.cluster-xyz.us-west-2.neptune.amazonaws.com",
        "ReaderEndpoint": "reader.cluster-xyz.us-west-2.neptune.amazonaws.com",
        "Port": 8182,
        "IAMDatabaseAuthenticationEnabled": True,
        "DeletionProtection": False,
        "EnabledCloudwatchLogsExports": ["audit"],
    }
    values.update(overrides)
    return values


def _instance(index: int = 1, **overrides):
    values = {
        "DBInstanceIdentifier": f"platform-steadymd-triage-prod-knowledge-graph-i{index}",
        "DBClusterIdentifier": "platform-steadymd-triage-prod-knowledge-graph",
        "DBInstanceClass": "db.r6g.large",
        "DBInstanceStatus": "available",
    }
    values.update(overrides)
    return values


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("neptune")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_provisioned_cluster_request_is_private_encrypted_and_iam_authenticated():
    client = _client()
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.provision(
        _spec(
            config={
                "engine_version": "1.4.6.0",
                "num_instances": 2,
                "backup_retention_days": 14,
                "storage_type": "iopt1",
                "cloudwatch_log_exports": ["audit", "slowquery"],
                "availability_zones": ["us-west-2a", "us-west-2b"],
                "instance_parameter_group": "neptune-app",
                "monitoring_interval": 60,
                "monitoring_role_arn": "arn:aws:iam::123456789012:role/neptune-monitoring",
                "enable_performance_insights": True,
            },
        ),
    )

    assert result.ok
    assert result.handle == "graph_db/platform-steadymd-triage-prod-knowledge-graph"
    create_cluster = client.create_db_cluster.call_args.kwargs
    assert create_cluster["Engine"] == "neptune"
    assert create_cluster["DBSubnetGroupName"] == "neptune-private"
    assert create_cluster["VpcSecurityGroupIds"] == ["sg-neptune"]
    assert create_cluster["StorageEncrypted"] is True
    assert create_cluster["EnableIAMDatabaseAuthentication"] is True
    assert create_cluster["DeletionProtection"] is True
    assert create_cluster["StorageType"] == "iopt1"
    assert create_cluster["EnableCloudwatchLogsExports"] == ["audit", "slowquery"]
    assert {tag["Key"] for tag in create_cluster["Tags"]} >= {
        "astrolift.io/binding",
        "astrolift.io/managed_service_id",
    }
    _validate("CreateDBCluster", create_cluster)
    creates = [call.kwargs for call in client.create_db_instance.call_args_list]
    assert len(creates) == 2
    assert creates[0]["DBInstanceClass"] == "db.r6g.large"
    assert creates[0]["PubliclyAccessible"] is False
    assert creates[0]["AvailabilityZone"] == "us-west-2a"
    assert creates[0]["EnablePerformanceInsights"] is True
    for request in creates:
        _validate("CreateDBInstance", request)


def test_serverless_supports_capacity_and_mixed_instance_classes():
    client = _client()
    driver = NeptuneServerlessDriver(config=_config(serverless=True), neptune_client=client)

    result = driver.provision(
        _spec(
            config={
                "min_capacity": 1.5,
                "max_capacity": 64.0,
                "num_instances": 2,
                "instance_classes": ["db.serverless", "db.r6g.large"],
                "promotion_tiers": [0, 2],
            },
        ),
    )

    assert result.ok
    create_cluster = client.create_db_cluster.call_args.kwargs
    assert create_cluster["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 1.5,
        "MaxCapacity": 64.0,
    }
    _validate("CreateDBCluster", create_cluster)
    instances = [call.kwargs for call in client.create_db_instance.call_args_list]
    assert [row["DBInstanceClass"] for row in instances] == ["db.serverless", "db.r6g.large"]
    assert [row["PromotionTier"] for row in instances] == [0, 2]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"min_capacity": 0.5}, "min_capacity must be"),
        ({"max_capacity": 2.0}, "max_capacity must be"),
        ({"min_capacity": 3.0, "max_capacity": 2.5}, "min_capacity cannot exceed"),
        ({"min_capacity": 1.25}, "0.5 NCU increments"),
        ({"publicly_accessible": True, "iam_database_authentication": False}, "require IAM"),
        ({"cloudwatch_log_exports": ["profiler"]}, "audit and slowquery"),
        ({"instance_classes": ["db.r6g.large"] * 17}, "1 through 16"),
        ({"num_instances": 1, "instance_classes": ["db.r6g.large", "db.r6g.xlarge"]}, "num_instances"),
        ({"promotion_tiers": [16]}, "0 through 15"),
        ({"monitoring_interval": 12}, "must be one of"),
        ({"monitoring_interval": 60}, "monitoring_role_arn is required"),
    ],
)
def test_serverless_rejects_unsafe_or_invalid_configuration(config, message):
    driver = NeptuneServerlessDriver(config=_config(serverless=True), neptune_client=_client())

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


def test_provisioned_rejects_serverless_capacity_controls():
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=_client())

    result = driver.provision(_spec(config={"min_capacity": 1, "max_capacity": 16}))

    assert not result.ok
    assert "neptune_serverless" in result.message


def test_partial_serverless_capacity_update_uses_current_other_bound():
    client = _client(
        cluster=_cluster(
            ServerlessV2ScalingConfiguration={"MinCapacity": 1.0, "MaxCapacity": 64.0},
        ),
        instances=[_instance(DBInstanceClass="db.serverless")],
    )
    driver = NeptuneServerlessDriver(config=_config(serverless=True), neptune_client=client)

    result = driver.update(
        UpdateSpec(
            handle="graph_db/platform-steadymd-triage-prod-knowledge-graph",
            config={"min_capacity": 32.0},
        ),
    )

    assert result.ok
    assert client.modify_db_cluster.call_args.kwargs["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 32.0,
        "MaxCapacity": 64.0,
    }


def test_update_reconciles_cluster_logs_instances_and_scale_down():
    client = _client(cluster=_cluster(), instances=[_instance(1), _instance(2)])
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.update(
        UpdateSpec(
            handle="graph_db/platform-steadymd-triage-prod-knowledge-graph",
            size="large",
            config={
                "num_instances": 1,
                "backup_retention_days": 21,
                "cloudwatch_log_exports": ["slowquery"],
                "publicly_accessible": False,
                "apply_immediately": True,
            },
        ),
    )

    assert result.ok
    cluster_request = client.modify_db_cluster.call_args.kwargs
    assert cluster_request["BackupRetentionPeriod"] == 21
    assert cluster_request["CloudwatchLogsExportConfiguration"] == {
        "EnableLogTypes": ["slowquery"],
        "DisableLogTypes": ["audit"],
    }
    _validate("ModifyDBCluster", cluster_request)
    instance_request = client.modify_db_instance.call_args.kwargs
    assert instance_request["DBInstanceClass"] == "db.r6g.xlarge"
    assert instance_request["ApplyImmediately"] is True
    _validate("ModifyDBInstance", instance_request)
    assert client.delete_db_instance.call_args.kwargs == {
        "DBInstanceIdentifier": "platform-steadymd-triage-prod-knowledge-graph-i2",
        "SkipFinalSnapshot": True,
    }


def test_non_instance_update_does_not_remove_existing_readers():
    client = _client(cluster=_cluster(), instances=[_instance(1), _instance(2)])
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.update(
        UpdateSpec(
            handle="graph_db/platform-steadymd-triage-prod-knowledge-graph",
            config={"backup_retention_days": 10},
        ),
    )

    assert result.ok
    client.delete_db_instance.assert_not_called()
    client.modify_db_instance.assert_not_called()


def test_binding_emits_protocol_urls_and_least_privilege_database_grant():
    client = _client(cluster=_cluster())
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    binding = driver.binding(
        ServiceHandle("graph_db/platform-steadymd-triage-prod-knowledge-graph"),
        {"protocol": "opencypher"},
    )

    assert binding.env_vars["GRAPH_DB_URL"].literal == (
        "https://writer.cluster-xyz.us-west-2.neptune.amazonaws.com:8182/openCypher"
    )
    assert binding.env_vars["GRAPH_DB_READER_URL"].literal == (
        "https://reader.cluster-xyz.us-west-2.neptune.amazonaws.com:8182/openCypher"
    )
    assert binding.env_vars["GRAPH_DB_AUTH_MODE"].literal == "iam_sigv4"
    assert binding.env_vars["GRAPH_DB_TLS"].literal == "1"
    assert len(binding.iam_grants) == 1
    assert binding.iam_grants[0].resource == ("arn:aws:neptune-db:us-west-2:123456789012:cluster-ABCDEFGHIJKL/*")
    assert binding.iam_grants[0].actions == ["neptune-db:connect"]


def test_binding_without_iam_is_explicitly_network_only():
    client = _client(cluster=_cluster(IAMDatabaseAuthenticationEnabled=False))
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    binding = driver.binding(ServiceHandle("graph_db/platform-steadymd-triage-prod-knowledge-graph"))

    assert binding.env_vars["GRAPH_DB_AUTH_MODE"].literal == "network_only"
    assert binding.iam_grants == []


def test_binding_rejects_unknown_protocol():
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=_client(cluster=_cluster()))

    with pytest.raises(ManagedServiceError, match="protocol"):
        driver.binding(
            ServiceHandle("graph_db/platform-steadymd-triage-prod-knowledge-graph"),
            {"protocol": "bolt"},
        )


def test_status_requires_both_cluster_and_instance_readiness():
    driver = NeptuneProvisionedDriver(
        config=_config(),
        neptune_client=_client(cluster=_cluster(), instances=[]),
    )
    handle = ServiceHandle("graph_db/platform-steadymd-triage-prod-knowledge-graph")

    assert driver.status(handle).state == "provisioning"

    driver = NeptuneProvisionedDriver(
        config=_config(),
        neptune_client=_client(cluster=_cluster(), instances=[_instance()]),
    )
    assert driver.status(handle).state == "available"


def test_deprovision_refuses_deletion_protection_without_force():
    client = _client(cluster=_cluster(DeletionProtection=True))
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.deprovision(
        DeprovisionSpec("graph_db/platform-steadymd-triage-prod-knowledge-graph"),
    )

    assert not result.ok
    assert not result.retryable
    assert result.errors == ["deletion_protection_enabled"]
    client.delete_db_cluster.assert_not_called()


def test_force_deprovision_disables_protection_before_any_delete():
    client = _client(cluster=_cluster(DeletionProtection=True), instances=[_instance()])
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.deprovision(
        DeprovisionSpec("graph_db/platform-steadymd-triage-prod-knowledge-graph"),
        force_destroy=True,
    )

    assert not result.ok
    assert result.retryable
    assert client.modify_db_cluster.call_args.kwargs == {
        "DBClusterIdentifier": "platform-steadymd-triage-prod-knowledge-graph",
        "DeletionProtection": False,
        "ApplyImmediately": True,
    }
    client.delete_db_instance.assert_not_called()


def test_deprovision_deletes_instances_before_safe_final_snapshot():
    handle = "graph_db/platform-steadymd-triage-prod-knowledge-graph"
    client = _client(cluster=_cluster(), instances=[_instance()])
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    first = driver.deprovision(DeprovisionSpec(handle))

    assert not first.ok
    assert first.retryable
    client.delete_db_instance.assert_called_once()
    client.delete_db_cluster.assert_not_called()

    client.describe_db_instances.return_value = {"DBInstances": []}
    second = driver.deprovision(DeprovisionSpec(handle))

    assert not second.ok
    delete = client.delete_db_cluster.call_args.kwargs
    assert delete["SkipFinalSnapshot"] is False
    assert delete["FinalDBSnapshotIdentifier"].startswith("platform-steadymd-triage")
    _validate("DeleteDBCluster", delete)


def test_destructive_deprovision_skips_final_snapshot():
    client = _client(cluster=_cluster(), instances=[])
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)

    result = driver.deprovision(
        DeprovisionSpec("graph_db/platform-steadymd-triage-prod-knowledge-graph"),
        delete_data=True,
    )

    assert not result.ok
    assert client.delete_db_cluster.call_args.kwargs == {
        "DBClusterIdentifier": "platform-steadymd-triage-prod-knowledge-graph",
        "SkipFinalSnapshot": True,
    }


def test_snapshot_and_restore_use_cluster_snapshot_lifecycle():
    client = _client(cluster=_cluster())
    client.create_db_cluster_snapshot.return_value = {
        "DBClusterSnapshot": {
            "DBClusterSnapshotArn": "arn:aws:rds:us-west-2:123456789012:cluster-snapshot:snap-1",
        },
    }
    driver = NeptuneProvisionedDriver(config=_config(), neptune_client=client)
    handle = ServiceHandle("graph_db/platform-steadymd-triage-prod-knowledge-graph")

    snapshot = driver.snapshot(handle)

    assert snapshot.snapshot_id.endswith(":snap-1")
    _validate("CreateDBClusterSnapshot", client.create_db_cluster_snapshot.call_args.kwargs)

    client.describe_db_clusters.return_value = {"DBClusters": []}
    client.describe_db_instances.return_value = {"DBInstances": []}
    result = driver.restore(
        SnapshotHandle(handle.handle, snapshot.snapshot_id, snapshot.created_at),
        _spec(service_handle_hint="restored-graph"),
    )

    assert result.ok
    restore = client.restore_db_cluster_from_snapshot.call_args.kwargs
    assert restore["SnapshotIdentifier"] == snapshot.snapshot_id
    assert restore["EnableIAMDatabaseAuthentication"] is True
    _validate("RestoreDBClusterFromSnapshot", restore)


def test_schemas_expose_protocol_identity_and_operational_controls():
    driver = NeptuneServerlessDriver(config=_config(serverless=True), neptune_client=_client())

    properties = driver.config_schema()["properties"]
    assert {
        "protocol",
        "instance_classes",
        "min_capacity",
        "max_capacity",
        "global_cluster_identifier",
        "replication_source_identifier",
        "cloudwatch_log_exports",
        "publicly_accessible",
    } <= properties.keys()
    assert {
        "GRAPH_DB_URL",
        "GRAPH_DB_READER_URL",
        "GRAPH_DB_AUTH_MODE",
        "GRAPH_DB_RESOURCE_ARN",
    } <= driver.binding_schema().env_vars.keys()
