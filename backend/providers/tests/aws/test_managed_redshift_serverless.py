from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.redshift_serverless import RedshiftServerlessConfig, RedshiftServerlessDriver


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


def _config() -> RedshiftServerlessConfig:
    return RedshiftServerlessConfig(
        region="us-west-2",
        account_id="123456789012",
        subnet_ids=["subnet-a", "subnet-b", "subnet-c"],
        security_group_ids=["sg-redshift"],
        name_prefix="platform",
    )


def _not_found(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ResourceNotFoundException", "Message": "not found"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _client(*, namespace=None, workgroup=None) -> MagicMock:
    client = MagicMock()
    if namespace is None:
        client.get_namespace.side_effect = _not_found("GetNamespace")
    else:
        client.get_namespace.return_value = {"namespace": namespace}
    if workgroup is None:
        client.get_workgroup.side_effect = _not_found("GetWorkgroup")
    else:
        client.get_workgroup.return_value = {"workgroup": workgroup}
    client.list_tags_for_resource.return_value = {
        "tags": [{"key": "astrolift.io/managed-by", "value": "platform"}],
    }
    return client


def _namespace(**overrides):
    values = {
        "namespaceName": "platform-steadymd-triage-prod-analytics",
        "namespaceId": "namespace-id",
        "namespaceArn": "arn:aws:redshift-serverless:us-west-2:123456789012:namespace/namespace-id",
        "status": "AVAILABLE",
        "dbName": "analytics",
        "adminUsername": "astrolift",
        "adminPasswordSecretArn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:redshift",
    }
    values.update(overrides)
    return values


def _workgroup(**overrides):
    values = {
        "workgroupName": "platform-steadymd-triage-prod-analytics",
        "workgroupId": "workgroup-id",
        "workgroupArn": "arn:aws:redshift-serverless:us-west-2:123456789012:workgroup/workgroup-id",
        "namespaceName": "platform-steadymd-triage-prod-analytics",
        "status": "AVAILABLE",
        "baseCapacity": 32,
        "maxCapacity": 128,
        "port": 5439,
        "endpoint": {
            "address": "analytics.123.us-west-2.redshift-serverless.amazonaws.com",
            "port": 5439,
        },
    }
    values.update(overrides)
    return values


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("redshift-serverless")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_create_namespace_and_private_workgroup_with_full_controls():
    client = _client()
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    result = driver.provision(
        _spec(
            config={
                "database": "analytics",
                "base_capacity": 32,
                "max_capacity": 256,
                "kms_key_id": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                "log_exports": ["connectionlog", "useractivitylog"],
                "iam_roles": ["arn:aws:iam::123456789012:role/redshift-copy"],
                "default_iam_role_arn": "arn:aws:iam::123456789012:role/redshift-copy",
                "config_parameters": {
                    "enable_user_activity_logging": "true",
                    "require_ssl": "true",
                },
                "price_performance_target": {"status": "ENABLED", "level": 50},
                "extra_compute_for_automatic_optimization": True,
                "ip_address_type": "dualstack",
            },
        ),
    )

    assert result.ok
    assert result.handle == "warehouse/platform-steadymd-triage-prod-analytics"
    namespace = client.create_namespace.call_args.kwargs
    assert namespace["manageAdminPassword"] is True
    assert namespace["kmsKeyId"].endswith("key/key-1")
    assert namespace["logExports"] == ["connectionlog", "useractivitylog"]
    assert {tag["key"] for tag in namespace["tags"]} >= {
        "astrolift.io/binding",
        "astrolift.io/managed_service_id",
    }
    _validate("CreateNamespace", namespace)
    workgroup = client.create_workgroup.call_args.kwargs
    assert workgroup["baseCapacity"] == 32
    assert workgroup["maxCapacity"] == 256
    assert workgroup["enhancedVpcRouting"] is True
    assert workgroup["publiclyAccessible"] is False
    assert workgroup["subnetIds"] == ["subnet-a", "subnet-b", "subnet-c"]
    assert workgroup["securityGroupIds"] == ["sg-redshift"]
    assert workgroup["configParameters"] == [
        {"parameterKey": "enable_user_activity_logging", "parameterValue": "true"},
        {"parameterKey": "require_ssl", "parameterValue": "true"},
    ]
    _validate("CreateWorkgroup", workgroup)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"manage_admin_password": False}, "managed admin passwords"),
        ({"base_capacity": 5}, "8-RPU increment"),
        ({"base_capacity": 64, "max_capacity": 32}, "cannot exceed"),
        ({"max_capacity": 513}, "32-RPU increment"),
        ({"port": 7000}, "5431-5455"),
        ({"subnet_ids": ["subnet-a", "subnet-b"]}, "three distinct"),
        ({"log_exports": ["audit"]}, "useractivitylog"),
        ({"config_parameters": ["require_ssl=true"]}, "key/value object"),
    ],
)
def test_invalid_serverless_configuration_is_rejected(config, message):
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=_client())

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


def test_update_validates_partial_capacity_against_current_and_updates_both_halves():
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    bad = driver.update(
        UpdateSpec(
            handle="warehouse/platform-steadymd-triage-prod-analytics",
            config={"base_capacity": 256},
        ),
    )

    assert not bad.ok
    assert "cannot exceed" in bad.message

    result = driver.update(
        UpdateSpec(
            handle="warehouse/platform-steadymd-triage-prod-analytics",
            config={
                "base_capacity": 64,
                "max_capacity": 256,
                "log_exports": ["userlog"],
                "publicly_accessible": False,
                "config_parameters": {"require_ssl": "true"},
            },
        ),
    )

    assert result.ok
    namespace = client.update_namespace.call_args.kwargs
    assert namespace["logExports"] == ["userlog"]
    _validate("UpdateNamespace", namespace)
    workgroup = client.update_workgroup.call_args.kwargs
    assert workgroup["baseCapacity"] == 64
    assert workgroup["maxCapacity"] == 256
    assert workgroup["configParameters"] == [
        {"parameterKey": "require_ssl", "parameterValue": "true"},
    ]
    _validate("UpdateWorkgroup", workgroup)


def test_size_update_maps_to_base_capacity_without_dropping_max():
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    result = driver.update(
        UpdateSpec(
            handle="warehouse/platform-steadymd-triage-prod-analytics",
            size="large",
        ),
    )

    assert result.ok
    assert client.update_workgroup.call_args.kwargs["baseCapacity"] == 128


def test_binding_uses_workgroup_scoped_iam_and_optional_data_api():
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    binding = driver.binding(
        ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
        {"data_api_access": True},
    )

    workgroup_arn = "arn:aws:redshift-serverless:us-west-2:123456789012:workgroup/workgroup-id"
    assert binding.env_vars["WAREHOUSE_URL"].literal == (
        "postgresql://analytics.123.us-west-2.redshift-serverless.amazonaws.com:5439/analytics"
    )
    assert binding.env_vars["WAREHOUSE_AUTH_MODE"].literal == "iam"
    assert binding.env_vars["WAREHOUSE_WORKGROUP"].literal == ("platform-steadymd-triage-prod-analytics")
    assert binding.iam_grants[0].resource == workgroup_arn
    assert binding.iam_grants[0].actions == ["redshift-serverless:GetCredentials"]
    assert any(
        grant.resource == workgroup_arn and "redshift-data:ExecuteStatement" in grant.actions
        for grant in binding.iam_grants
    )


def test_binding_can_expose_managed_admin_secret_but_never_plaintext_password():
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    binding = driver.binding(
        ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
        {"auth_mode": "admin_secret"},
    )

    secret = "arn:aws:secretsmanager:us-west-2:123456789012:secret:redshift"
    assert binding.env_vars["WAREHOUSE_CREDENTIALS_REF"].literal == secret
    assert "WAREHOUSE_PASSWORD" not in binding.env_vars
    assert any(grant.resource == secret for grant in binding.iam_grants)


def test_admin_secret_binding_refuses_namespace_without_managed_secret():
    driver = RedshiftServerlessDriver(
        config=_config(),
        serverless_client=_client(
            namespace=_namespace(adminPasswordSecretArn=""),
            workgroup=_workgroup(),
        ),
    )

    with pytest.raises(ManagedServiceError, match="no managed admin secret"):
        driver.binding(
            ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics"),
            {"auth_mode": "admin_secret"},
        )


def test_status_requires_namespace_and_workgroup_available():
    handle = ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics")
    driver = RedshiftServerlessDriver(
        config=_config(),
        serverless_client=_client(namespace=_namespace(), workgroup=_workgroup()),
    )
    assert driver.status(handle).state == "available"

    driver = RedshiftServerlessDriver(
        config=_config(),
        serverless_client=_client(
            namespace=_namespace(),
            workgroup=_workgroup(status="MODIFYING"),
        ),
    )
    assert driver.status(handle).state == "updating"


def test_deprovision_refuses_protection_then_deletes_workgroup_before_namespace():
    handle = "warehouse/platform-steadymd-triage-prod-analytics"
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    refused = driver.deprovision(DeprovisionSpec(handle))
    assert not refused.ok
    assert not refused.retryable
    client.delete_workgroup.assert_not_called()

    first = driver.deprovision(DeprovisionSpec(handle), force_destroy=True)
    assert not first.ok
    assert client.delete_workgroup.call_args.kwargs == {
        "workgroupName": "platform-steadymd-triage-prod-analytics",
    }
    _validate("DeleteWorkgroup", client.delete_workgroup.call_args.kwargs)
    client.delete_namespace.assert_not_called()


def test_safe_namespace_delete_retains_final_snapshot_and_destructive_skips_it():
    handle = "warehouse/platform-steadymd-triage-prod-analytics"
    client = _client(namespace=_namespace(), workgroup=None)
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)

    safe = driver.deprovision(
        DeprovisionSpec(
            handle,
            config={"deletion_protection": False, "snapshot_retention_days": 60},
        ),
    )

    assert not safe.ok
    request = client.delete_namespace.call_args.kwargs
    assert request["finalSnapshotRetentionPeriod"] == 60
    assert request["finalSnapshotName"].startswith("platform-steadymd")
    _validate("DeleteNamespace", request)

    client.reset_mock()
    destructive = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
        delete_data=True,
    )
    assert not destructive.ok
    assert client.delete_namespace.call_args.kwargs == {
        "namespaceName": "platform-steadymd-triage-prod-analytics",
    }


def test_snapshot_preserves_tags_and_restore_targets_available_pair():
    client = _client(namespace=_namespace(), workgroup=_workgroup())
    client.create_snapshot.return_value = {
        "snapshot": {
            "snapshotArn": "arn:aws:redshift-serverless:us-west-2:123456789012:snapshot/snap-id",
        },
    }
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)
    handle = ServiceHandle("warehouse/platform-steadymd-triage-prod-analytics")

    snapshot = driver.snapshot(handle)

    request = client.create_snapshot.call_args.kwargs
    assert request["tags"] == [
        {"key": "astrolift.io/managed-by", "value": "platform"},
    ]
    _validate("CreateSnapshot", request)
    restored = driver.restore(
        SnapshotHandle(handle.handle, snapshot.snapshot_id, snapshot.created_at),
        _spec(),
    )
    assert restored.ok
    restore = client.restore_from_snapshot.call_args.kwargs
    assert restore["snapshotArn"] == snapshot.snapshot_id
    assert restore["manageAdminPassword"] is True
    _validate("RestoreFromSnapshot", restore)


def test_restore_new_target_is_retryable_until_namespace_and_workgroup_are_available():
    client = _client()
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=client)
    snapshot = SnapshotHandle(
        "warehouse/source",
        "serverless-snapshot",
        "2026-08-14T00:00:00+00:00",
    )

    result = driver.restore(snapshot, _spec(service_handle_hint="restored"))

    assert not result.ok
    assert result.errors == ["restore_target_not_ready"]
    client.create_namespace.assert_called_once()
    client.create_workgroup.assert_called_once()
    client.restore_from_snapshot.assert_not_called()


def test_schemas_expose_capacity_network_security_and_auth_controls():
    driver = RedshiftServerlessDriver(config=_config(), serverless_client=_client())

    properties = driver.config_schema()["properties"]
    assert {
        "base_capacity",
        "max_capacity",
        "config_parameters",
        "price_performance_target",
        "manage_admin_password",
        "auth_mode",
        "data_api_access",
        "subnet_ids",
        "snapshot_retention_days",
    } <= properties.keys()
    assert properties["manage_admin_password"]["const"] is True
    assert properties["subnet_ids"]["minItems"] == 3
    assert {
        "WAREHOUSE_URL",
        "WAREHOUSE_AUTH_MODE",
        "WAREHOUSE_RESOURCE_ARN",
        "WAREHOUSE_CREDENTIALS_REF",
    } <= driver.binding_schema().env_vars.keys()
