from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.redshift import RedshiftConfig, RedshiftProvisionedDriver


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "analytics",
        "size": "medium",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config() -> RedshiftConfig:
    return RedshiftConfig(
        region="us-west-2",
        account_id="123456789012",
        cluster_subnet_group="redshift-private",
        security_group_ids=["sg-redshift"],
        cluster_name_prefix="platform",
    )


def _client(cluster=None) -> MagicMock:
    client = MagicMock()
    client.describe_clusters.return_value = {"Clusters": [] if cluster is None else [cluster]}
    return client


def _cluster(**overrides):
    values = {
        "ClusterIdentifier": "platform-steadymd-triage-prod-analytics",
        "ClusterNamespaceArn": "arn:aws:redshift:us-west-2:123456789012:namespace:namespace-id",
        "ClusterStatus": "available",
        "DBName": "analytics",
        "MasterUsername": "astrolift",
        "MasterPasswordSecretArn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:redshift",
        "Endpoint": {
            "Address": "analytics.abc.us-west-2.redshift.amazonaws.com",
            "Port": 5439,
        },
        "NodeType": "ra3.xlplus",
        "NumberOfNodes": 2,
        "IamRoles": [
            {"IamRoleArn": "arn:aws:iam::123456789012:role/old", "ApplyStatus": "in-sync"},
        ],
        "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
    }
    values.update(overrides)
    return values


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("redshift")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_create_cluster_is_private_encrypted_managed_secret_and_ra3():
    client = _client()
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    result = driver.provision(
        _spec(
            config={
                "database": "analytics",
                "number_of_nodes": 2,
                "node_type": "ra3.4xlarge",
                "multi_az": True,
                "kms_key_id": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                "automated_snapshot_retention_days": 14,
                "manual_snapshot_retention_days": 90,
                "iam_roles": ["arn:aws:iam::123456789012:role/redshift-copy"],
                "default_iam_role_arn": "arn:aws:iam::123456789012:role/redshift-copy",
                "ip_address_type": "dualstack",
                "extra_compute_for_automatic_optimization": True,
            },
        ),
    )

    assert result.ok
    assert result.handle == "warehouse/platform-steadymd-triage-prod-analytics"
    request = client.create_cluster.call_args.kwargs
    assert request["ClusterType"] == "multi-node"
    assert request["NodeType"] == "ra3.4xlarge"
    assert request["NumberOfNodes"] == 2
    assert request["ManageMasterPassword"] is True
    assert request["Encrypted"] is True
    assert request["PubliclyAccessible"] is False
    assert request["EnhancedVpcRouting"] is True
    assert request["ClusterSubnetGroupName"] == "redshift-private"
    assert request["VpcSecurityGroupIds"] == ["sg-redshift"]
    assert request["MultiAZ"] is True
    assert request["ExtraComputeForAutomaticOptimization"] is True
    assert {tag["Key"] for tag in request["Tags"]} >= {
        "astrolift.io/binding",
        "astrolift.io/managed_service_id",
    }
    _validate("CreateCluster", request)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"manage_admin_password": False}, "managed admin passwords"),
        ({"number_of_nodes": 0}, "1 through 100"),
        ({"number_of_nodes": 1, "multi_az": True}, "at least two nodes"),
        ({"port": 6000}, "5431-5455"),
        ({"auth_mode": "password"}, "iam or admin_secret"),
        ({"automated_snapshot_retention_days": 36}, "0 through 35"),
        ({"manual_snapshot_retention_days": 0}, "1 through 3653"),
    ],
)
def test_invalid_or_unsafe_configuration_is_rejected(config, message):
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=_client())

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


def test_update_resizes_changes_network_and_reconciles_iam_roles():
    client = _client(_cluster())
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    result = driver.update(
        UpdateSpec(
            handle="warehouse/platform-steadymd-triage-prod-analytics",
            size="large",
            config={
                "number_of_nodes": 4,
                "enhanced_vpc_routing": True,
                "encrypted": True,
                "kms_key_id": "arn:aws:kms:us-west-2:123456789012:key/key-2",
                "iam_roles": ["arn:aws:iam::123456789012:role/new"],
                "default_iam_role_arn": "arn:aws:iam::123456789012:role/new",
            },
        ),
    )

    assert result.ok
    modify = client.modify_cluster.call_args.kwargs
    assert modify["NodeType"] == "ra3.4xlarge"
    assert modify["NumberOfNodes"] == 4
    assert modify["ClusterType"] == "multi-node"
    assert modify["Encrypted"] is True
    _validate("ModifyCluster", modify)
    roles = client.modify_cluster_iam_roles.call_args.kwargs
    assert roles["AddIamRoles"] == ["arn:aws:iam::123456789012:role/new"]
    assert roles["RemoveIamRoles"] == ["arn:aws:iam::123456789012:role/old"]
    _validate("ModifyClusterIamRoles", roles)


def test_binding_defaults_to_iam_temporary_credentials_and_scoped_data_api():
    client = _client(_cluster())
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    binding = driver.binding(
        ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
        {"data_api_access": True},
    )

    assert binding.env_vars["WAREHOUSE_ENDPOINT"].literal == ("analytics.abc.us-west-2.redshift.amazonaws.com")
    assert binding.env_vars["WAREHOUSE_URL"].literal == (
        "postgresql://analytics.abc.us-west-2.redshift.amazonaws.com:5439/analytics"
    )
    assert binding.env_vars["WAREHOUSE_AUTH_MODE"].literal == "iam"
    assert binding.env_vars["WAREHOUSE_TLS"].literal == "1"
    assert "WAREHOUSE_PASSWORD" not in binding.env_vars
    assert binding.iam_grants[0].resource == (
        "arn:aws:redshift:us-west-2:123456789012:dbname:platform-steadymd-triage-prod-analytics/analytics"
    )
    assert binding.iam_grants[0].actions == ["redshift:GetClusterCredentialsWithIAM"]
    assert any(
        "redshift-data:ExecuteStatement" in grant.actions
        and grant.resource.endswith(":cluster:platform-steadymd-triage-prod-analytics")
        for grant in binding.iam_grants
    )


def test_binding_can_explicitly_expose_managed_admin_secret_reference():
    client = _client(_cluster())
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    binding = driver.binding(
        ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
        {"auth_mode": "admin_secret"},
    )

    secret = "arn:aws:secretsmanager:us-west-2:123456789012:secret:redshift"
    assert binding.env_vars["WAREHOUSE_AUTH_MODE"].literal == "admin_secret"
    assert binding.env_vars["WAREHOUSE_CREDENTIALS_REF"].literal == secret
    assert binding.env_vars["WAREHOUSE_USER"].literal == "astrolift"
    assert any(grant.resource == secret for grant in binding.iam_grants)


def test_admin_secret_binding_refuses_cluster_without_managed_secret():
    driver = RedshiftProvisionedDriver(
        config=_config(),
        redshift_client=_client(_cluster(MasterPasswordSecretArn="")),
    )

    with pytest.raises(ManagedServiceError, match="managed admin secret"):
        driver.binding(
            ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
            {"auth_mode": "admin_secret"},
        )


def test_status_maps_cluster_lifecycle():
    handle = ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics")
    driver = RedshiftProvisionedDriver(
        config=_config(),
        redshift_client=_client(_cluster(ClusterStatus="resizing")),
    )

    assert driver.status(handle).state == "updating"

    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=_client())
    assert driver.status(handle).state == "deprovisioned"


def test_deprovision_requires_force_when_astrolift_protection_is_enabled():
    client = _client(_cluster())
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)
    handle = "warehouse/platform-steadymd-triage-prod-analytics"

    refused = driver.deprovision(DeprovisionSpec(handle))

    assert not refused.ok
    assert not refused.retryable
    assert refused.errors == ["deletion_protection_enabled"]
    client.delete_cluster.assert_not_called()

    accepted = driver.deprovision(
        DeprovisionSpec(handle, config={"manual_snapshot_retention_days": 60}),
        force_destroy=True,
    )

    assert not accepted.ok
    delete = client.delete_cluster.call_args.kwargs
    assert delete["SkipFinalClusterSnapshot"] is False
    assert delete["FinalClusterSnapshotRetentionPeriod"] == 60
    _validate("DeleteCluster", delete)


def test_destructive_deprovision_skips_final_snapshot():
    client = _client(_cluster())
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    result = driver.deprovision(
        DeprovisionSpec(
            "warehouse/platform-steadymd-triage-prod-analytics",
            config={"deletion_protection": False},
        ),
        delete_data=True,
    )

    assert not result.ok
    assert client.delete_cluster.call_args.kwargs == {
        "ClusterIdentifier": "platform-steadymd-triage-prod-analytics",
        "SkipFinalClusterSnapshot": True,
    }


def test_snapshot_copies_tags_and_restore_retags_target():
    client = _client(_cluster())
    client.create_cluster_snapshot.return_value = {
        "Snapshot": {
            "SnapshotArn": "arn:aws:redshift:us-west-2:123456789012:snapshot:cluster/snap-1",
        },
    }
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)
    handle = ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics")

    snapshot = driver.snapshot(handle)

    assert snapshot.snapshot_id.endswith("/snap-1")
    snapshot_request = client.create_cluster_snapshot.call_args.kwargs
    assert snapshot_request["Tags"] == [
        {"Key": "astrolift.io/managed-by", "Value": "platform"},
    ]
    _validate("CreateClusterSnapshot", snapshot_request)

    client.describe_clusters.return_value = {"Clusters": []}
    client.restore_from_cluster_snapshot.return_value = {
        "Cluster": {
            "ClusterNamespaceArn": "arn:aws:redshift:us-west-2:123456789012:namespace:new-id",
        },
    }
    result = driver.restore(
        SnapshotHandle(handle.handle, snapshot.snapshot_id, snapshot.created_at),
        _spec(service_handle_hint="restored"),
    )

    assert result.ok
    restore = client.restore_from_cluster_snapshot.call_args.kwargs
    assert restore["SnapshotArn"] == snapshot.snapshot_id
    assert restore["ManageMasterPassword"] is True
    _validate("RestoreFromClusterSnapshot", restore)
    tagged = client.create_tags.call_args.kwargs
    assert tagged["ResourceName"].endswith(":namespace:new-id")
    assert any(tag["Key"] == "astrolift.io/managed_service_id" for tag in tagged["Tags"])
    _validate("CreateTags", tagged)


def test_schemas_expose_capacity_security_data_api_and_snapshot_controls():
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=_client())

    properties = driver.config_schema()["properties"]
    assert {
        "node_type",
        "number_of_nodes",
        "multi_az",
        "manage_admin_password",
        "auth_mode",
        "data_api_access",
        "enhanced_vpc_routing",
        "manual_snapshot_retention_days",
    } <= properties.keys()
    assert properties["manage_admin_password"]["const"] is True
    assert {
        "WAREHOUSE_URL",
        "WAREHOUSE_AUTH_MODE",
        "WAREHOUSE_RESOURCE_ARN",
        "WAREHOUSE_CREDENTIALS_REF",
    } <= driver.binding_schema().env_vars.keys()


def test_an_existing_cluster_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client(
        _cluster(
            Tags=[
                {"Key": "astrolift.io/managed-by", "Value": "platform"},
                {"Key": "astrolift.io/organization", "Value": "globex"},
            ]
        )
    )
    driver = RedshiftProvisionedDriver(config=_config(), redshift_client=client)

    result = driver.provision(_spec())

    assert not result.ok and "refusing to adopt" in result.message
    client.modify_cluster.assert_not_called()
