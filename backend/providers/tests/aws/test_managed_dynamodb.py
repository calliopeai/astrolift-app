"""Tests for AWS DynamoDB managed-service driver (#371).

Covers the full ManagedServiceDriver protocol surface:
provision (idempotent, with-tags, billing modes), update,
deprovision four-corner matrix (delete_data x force_destroy),
status state-mapping, binding env-var shape, snapshot + restore.

All cloud calls go through moto; no real AWS access is required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.dynamodb import (
    KIND,
    DynamoDBConfig,
    DynamoDBDriver,
    _final_backup_name,
)


@pytest.fixture
def aws_mock() -> Generator:
    """moto context for DynamoDB tests."""
    from moto import mock_aws

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        yield {"ddb": ddb}


@pytest.fixture
def ddb_client(aws_mock) -> Any:
    return aws_mock["ddb"]


@pytest.fixture
def driver(aws_mock) -> DynamoDBDriver:
    return DynamoDBDriver(
        config=DynamoDBConfig(region="us-east-1"),
        ddb_client=aws_mock["ddb"],
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="sessions",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_table(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, table_name = parse_handle(result.handle)
    assert kind == KIND
    resp = ddb_client.describe_table(TableName=table_name)
    table = resp["Table"]
    assert table["TableStatus"] == "ACTIVE"
    assert table["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
    assert table["DeletionProtectionEnabled"] is True


def test_provision_idempotent(driver: DynamoDBDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_default_key_schema_is_pk_hash(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(_spec())
    _, table_name = parse_handle(result.handle)
    table = ddb_client.describe_table(TableName=table_name)["Table"]
    assert table["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
    ]


def test_provision_honours_provisioned_billing_mode(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(
        _spec(
            size="large",
            config={"billing_mode": "PROVISIONED"},
        ),
    )
    _, table_name = parse_handle(result.handle)
    table = ddb_client.describe_table(TableName=table_name)["Table"]
    pt = table["ProvisionedThroughput"]
    assert pt["ReadCapacityUnits"] == 50
    assert pt["WriteCapacityUnits"] == 50


def test_provision_honours_explicit_capacity(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "billing_mode": "PROVISIONED",
                "read_capacity": 123,
                "write_capacity": 456,
            },
        ),
    )
    _, table_name = parse_handle(result.handle)
    pt = ddb_client.describe_table(
        TableName=table_name,
    )["Table"]["ProvisionedThroughput"]
    assert pt["ReadCapacityUnits"] == 123
    assert pt["WriteCapacityUnits"] == 456


def test_provision_honours_composite_key_schema(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "attribute_definitions": [
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                "key_schema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
            },
        ),
    )
    _, table_name = parse_handle(result.handle)
    table = ddb_client.describe_table(TableName=table_name)["Table"]
    assert len(table["KeySchema"]) == 2


def test_provision_tags_table(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    result = driver.provision(_spec())
    _, table_name = parse_handle(result.handle)
    table = ddb_client.describe_table(TableName=table_name)["Table"]
    arn = table["TableArn"]
    tag_resp = ddb_client.list_tags_of_resource(ResourceArn=arn)
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["Tags"]}
    assert tag_map.get("astrolift.io/managed-by") == "platform"
    assert tag_map.get("astrolift.io/app") == "api"
    assert tag_map.get("astrolift.io/environment") == "prod"


def test_provision_surfaces_create_failure() -> None:
    """When the underlying create_table call raises, the driver must
    surface ok=False rather than crash."""

    class Boom:
        def describe_table(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("ResourceNotFoundException")

        def create_table(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic create failure")

    d = DynamoDBDriver(
        config=DynamoDBConfig(region="us-east-1"),
        ddb_client=Boom(),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "create_table" in result.message


# ---- update -----------------------------------------------------


def test_update_provisioned_capacity(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(
        _spec(
            config={
                "billing_mode": "PROVISIONED",
                "read_capacity": 5,
                "write_capacity": 5,
            },
        ),
    )
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "billing_mode": "PROVISIONED",
                "read_capacity": 25,
                "write_capacity": 25,
            },
        ),
    )
    assert result.ok


def test_update_noop_when_nothing_to_change(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


def test_update_billing_mode_to_provisioned(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())  # default PAY_PER_REQUEST
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            size="medium",
            config={"billing_mode": "PROVISIONED"},
        ),
    )
    assert result.ok


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_when_protected(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())  # protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "DeletionProtection" in result.message


def test_deprovision_delete_data_only_skips_backup(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message


def test_deprovision_default_with_protection_off_takes_backup(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, table_name = parse_handle(provisioned.handle)
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "backup=taken" in result.message
    backups = ddb_client.list_backups(TableName=table_name)
    assert len(backups["BackupSummaries"]) >= 1


def test_deprovision_force_destroy_disables_protection(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())  # protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "backup=taken" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "backup=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: DynamoDBDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="kv_store/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: DynamoDBDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="kv_store/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_active_to_available(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "DYNAMODB_TABLE_NAME",
        "DYNAMODB_TABLE_ARN",
        "DYNAMODB_REGION",
        "DYNAMODB_ENDPOINT",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert key in env
    assert env["DYNAMODB_TABLE_NAME"].literal is not None
    assert env["DYNAMODB_REGION"].literal == "us-east-1"
    assert env["AWS_ACCESS_KEY_ID"].secret_ref is not None
    assert env["AWS_ACCESS_KEY_ID"].literal is None


def test_binding_iam_grant_includes_item_ops(
    driver: DynamoDBDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    for required in (
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan",
    ):
        assert required in actions


def test_binding_for_missing_raises(
    driver: DynamoDBDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="kv_store/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_backup(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id
    _, table_name = parse_handle(provisioned.handle)
    backups = ddb_client.list_backups(TableName=table_name)
    assert backups["BackupSummaries"]


def test_snapshot_surfaces_driver_error(
    driver: DynamoDBDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="kv_store/missing"))


def test_restore_from_backup_creates_new_table(
    driver: DynamoDBDriver,
    ddb_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, target = parse_handle(result.handle)
    resp = ddb_client.describe_table(TableName=target)
    assert resp["Table"]["TableStatus"] in {"ACTIVE", "CREATING"}


def test_restore_surfaces_error_on_missing_backup(
    driver: DynamoDBDriver,
) -> None:
    bad = SnapshotHandle(
        handle="kv_store/anything",
        snapshot_id="arn:aws:dynamodb:us-east-1:000:table/x/backup/does-not-exist",
        created_at="",
    )
    result = driver.restore(bad, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore_table_from_backup" in result.message


# ---- naming + helpers ------------------------------------------


def test_table_name_canonicalization(
    driver: DynamoDBDriver,
) -> None:
    name = driver._table_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="users",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c in "-_."
    assert len(name) <= 255


def test_final_backup_name_is_bounded() -> None:
    name = _final_backup_name(table_name="x" * 400)
    assert len(name) <= 255


def test_default_client_construction_path() -> None:
    """Driver must build successfully without an injected client."""
    from moto import mock_aws

    with mock_aws():
        d = DynamoDBDriver(
            config=DynamoDBConfig(region="us-east-1"),
        )
        assert d._ddb is not None  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: DynamoDBDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "billing_mode",
        "read_capacity",
        "write_capacity",
        "deletion_protection",
        "point_in_time_recovery",
        "stream_enabled",
        "ttl_attribute",
        "sse_kms_key_id",
        "key_schema",
        "attribute_definitions",
        "global_secondary_indexes",
        "local_secondary_indexes",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: DynamoDBDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "DYNAMODB_TABLE_NAME",
        "DYNAMODB_TABLE_ARN",
        "DYNAMODB_REGION",
        "DYNAMODB_ENDPOINT",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert key in schema.env_vars
