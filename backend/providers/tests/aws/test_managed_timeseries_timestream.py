"""Tests for AWS Timestream managed-service driver (#374).

Covers the full ManagedServiceDriver protocol surface:
provision (idempotent, with-tags, retention defaults), update,
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
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.timeseries_timestream import (
    KIND,
    TimestreamConfig,
    TimestreamDriver,
)


@pytest.fixture
def aws_mock() -> Generator:
    """moto context for Timestream tests."""
    from moto import mock_aws

    with mock_aws():
        tsw = boto3.client("timestream-write", region_name="us-east-1")
        tsq = boto3.client("timestream-query", region_name="us-east-1")
        yield {"tsw": tsw, "tsq": tsq}


@pytest.fixture
def tsw_client(aws_mock) -> Any:
    return aws_mock["tsw"]


@pytest.fixture
def driver(aws_mock) -> TimestreamDriver:
    return TimestreamDriver(
        config=TimestreamConfig(region="us-east-1"),
        ts_write_client=aws_mock["tsw"],
        ts_query_client=aws_mock["tsq"],
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
        service_handle_hint="metrics",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_database_and_table(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, resource_id = parse_handle(result.handle)
    assert kind == KIND
    database_name, _, table_name = resource_id.partition("/")
    resp = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )
    assert resp["Table"]["TableStatus"] == "ACTIVE"


def test_provision_idempotent(driver: TimestreamDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_applies_size_to_retention(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, resource_id = parse_handle(result.handle)
    database_name, _, table_name = resource_id.partition("/")
    table = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )["Table"]
    rp = table["RetentionProperties"]
    assert rp["MemoryStoreRetentionPeriodInHours"] == 72
    assert rp["MagneticStoreRetentionPeriodInDays"] == 730


def test_provision_honours_explicit_retention(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "memory_store_retention_hours": 48,
                "magnetic_store_retention_days": 30,
            },
        ),
    )
    _, resource_id = parse_handle(result.handle)
    database_name, _, table_name = resource_id.partition("/")
    rp = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )["Table"]["RetentionProperties"]
    assert rp["MemoryStoreRetentionPeriodInHours"] == 48
    assert rp["MagneticStoreRetentionPeriodInDays"] == 30


def test_provision_tags_database(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    result = driver.provision(_spec())
    _, resource_id = parse_handle(result.handle)
    database_name, _, _ = resource_id.partition("/")
    db_arn = tsw_client.describe_database(
        DatabaseName=database_name,
    )["Database"]["Arn"]
    tag_resp = tsw_client.list_tags_for_resource(ResourceARN=db_arn)
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["Tags"]}
    assert tag_map.get("astrolift.io/managed-by") == "platform"
    assert tag_map.get("astrolift.io/app") == "api"
    # Deletion-protection marker tag is written by the driver
    assert tag_map.get("astrolift.io/deletion-protection") == "1"


def test_provision_surfaces_create_failure() -> None:
    """When the underlying create_table call raises, the driver must
    surface ok=False rather than crash."""

    class Boom:
        def describe_database(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("ResourceNotFoundException")

        def describe_table(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("ResourceNotFoundException")

        def create_database(self, **_):  # type: ignore[no-untyped-def]
            return None

        def update_database(self, **_):  # type: ignore[no-untyped-def]
            return None

        def create_table(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic create failure")

        def tag_resource(self, **_):  # type: ignore[no-untyped-def]
            return None

        def describe_endpoints(self, **_):  # type: ignore[no-untyped-def]
            return {"Endpoints": []}

    d = TimestreamDriver(
        config=TimestreamConfig(region="us-east-1"),
        ts_write_client=Boom(),
        ts_query_client=Boom(),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "create_table" in result.message


# ---- update -----------------------------------------------------


def test_update_retention(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "memory_store_retention_hours": 12,
                "magnetic_store_retention_days": 60,
            },
        ),
    )
    assert result.ok
    _, resource_id = parse_handle(provisioned.handle)
    database_name, _, table_name = resource_id.partition("/")
    rp = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )["Table"]["RetentionProperties"]
    assert rp["MemoryStoreRetentionPeriodInHours"] == 12
    assert rp["MagneticStoreRetentionPeriodInDays"] == 60


def test_update_noop_when_nothing_to_change(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


def test_update_resize(driver: TimestreamDriver, tsw_client) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="xlarge"),
    )
    assert result.ok
    _, resource_id = parse_handle(provisioned.handle)
    database_name, _, table_name = resource_id.partition("/")
    rp = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )["Table"]["RetentionProperties"]
    assert rp["MemoryStoreRetentionPeriodInHours"] == 168
    assert rp["MagneticStoreRetentionPeriodInDays"] == 730


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_when_protected(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())  # default deletion_protection=True
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "DeletionProtection" in result.message


def test_deprovision_delete_data_only_skips_flush_drops_database(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "flush=skipped" in result.message
    assert "database=deleted" in result.message
    _, resource_id = parse_handle(provisioned.handle)
    database_name, _, _ = resource_id.partition("/")
    # database actually gone
    with pytest.raises(Exception):  # noqa: B017
        tsw_client.describe_database(DatabaseName=database_name)


def test_deprovision_default_with_protection_off_flushes(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "flush=taken" in result.message
    assert "database=retained" in result.message
    _, resource_id = parse_handle(provisioned.handle)
    database_name, _, _ = resource_id.partition("/")
    # database still alive (retained-data path)
    assert tsw_client.describe_database(DatabaseName=database_name)


def test_deprovision_force_destroy_bypasses_protection(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())  # protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "flush=taken" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "flush=skipped" in result.message
    assert "database=deleted" in result.message
    assert "force_destroy=True" in result.message
    _, resource_id = parse_handle(provisioned.handle)
    database_name, _, _ = resource_id.partition("/")
    with pytest.raises(Exception):  # noqa: B017
        tsw_client.describe_database(DatabaseName=database_name)


def test_deprovision_idempotent_when_already_gone(
    driver: TimestreamDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="time_series/missing/never"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: TimestreamDriver,
) -> None:
    state = driver.status(
        ServiceHandle(handle="time_series/missing/never"),
    )
    assert state.state == "deprovisioned"


def test_status_maps_active_to_available(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "TIMESTREAM_DATABASE_NAME",
        "TIMESTREAM_TABLE_NAME",
        "TIMESTREAM_REGION",
        "TIMESTREAM_WRITE_ENDPOINT",
        "TIMESTREAM_QUERY_ENDPOINT",
        "AWS_REGION",
    ):
        assert key in env
    assert env["TIME_SERIES_URL"].literal is not None
    assert env["TIMESTREAM_REGION"].literal == "us-east-1"
    assert env["TIMESTREAM_WRITE_ENDPOINT"].literal.startswith("https://")


def test_binding_iam_grants_include_write_and_query(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    for required in (
        "timestream:WriteRecords",
        "timestream:Select",
        "timestream:DescribeTable",
        "timestream:DescribeDatabase",
    ):
        assert required in actions


def test_binding_for_missing_raises(
    driver: TimestreamDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(
            ServiceHandle(handle="time_series/missing/never"),
        )


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_pit_handle(
    driver: TimestreamDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id
    assert "-pit-" in snap.snapshot_id


def test_snapshot_surfaces_driver_error(
    driver: TimestreamDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(
            ServiceHandle(handle="time_series/missing/never"),
        )


def test_restore_provisions_target(
    driver: TimestreamDriver,
    tsw_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="metrics-restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, resource_id = parse_handle(result.handle)
    database_name, _, table_name = resource_id.partition("/")
    resp = tsw_client.describe_table(
        DatabaseName=database_name,
        TableName=table_name,
    )
    assert resp["Table"]["TableStatus"] == "ACTIVE"


# ---- naming + helpers ------------------------------------------


def test_database_name_canonicalization(
    driver: TimestreamDriver,
) -> None:
    name = driver._database_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c in "-_."
    assert len(name) <= 64


def test_handle_rejects_malformed(driver: TimestreamDriver) -> None:
    with pytest.raises(ManagedServiceError):
        driver._split_handle("time_series/no-table-piece")  # type: ignore[attr-defined]


def test_default_client_construction_path() -> None:
    """Driver must build successfully without injected clients."""
    from moto import mock_aws

    with mock_aws():
        d = TimestreamDriver(
            config=TimestreamConfig(region="us-east-1"),
        )
        assert d._tsw is not None  # type: ignore[attr-defined]
        assert d._tsq is not None  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: TimestreamDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "memory_store_retention_hours",
        "magnetic_store_retention_days",
        "deletion_protection",
        "enable_magnetic_store_writes",
        "rejected_data_s3_bucket",
        "kms_key_id",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: TimestreamDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "TIMESTREAM_DATABASE_NAME",
        "TIMESTREAM_TABLE_NAME",
        "TIMESTREAM_REGION",
        "TIMESTREAM_WRITE_ENDPOINT",
        "TIMESTREAM_QUERY_ENDPOINT",
        "AWS_REGION",
    ):
        assert key in schema.env_vars


def test_provision_does_not_adopt_another_services_table(driver: TimestreamDriver) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    first = driver.provision(_spec(managed_service_id="svc-a"))
    second = driver.provision(_spec(managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "refusing to adopt" in second.message
