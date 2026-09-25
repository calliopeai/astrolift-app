"""Tests for AWS RDS Postgres managed-service driver (#351).

Covers the full ManagedServiceDriver protocol surface:
provision (idempotent, with-tags, secret-stored), update,
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
from aws.managed.postgres_rds import (
    KIND,
    RDSConfig,
    RDSPostgresDriver,
    _db_name_for,
    _generate_master_password,
)


@pytest.fixture
def aws_mock() -> Generator:
    """Single moto context for the whole test — RDS + Secrets Manager
    + the EC2 prerequisites (VPC, subnets, subnet group) all share it
    so RDS create_db_instance can resolve its dependencies."""
    from moto import mock_aws

    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        rds = boto3.client("rds", region_name="us-east-1")
        sm = boto3.client("secretsmanager", region_name="us-east-1")

        # Provision the EC2 + RDS prereqs moto requires for
        # create_db_instance to succeed:
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnets = []
        for cidr, az in (
            ("10.0.1.0/24", "us-east-1a"),
            ("10.0.2.0/24", "us-east-1b"),
        ):
            sub = ec2.create_subnet(
                VpcId=vpc,
                CidrBlock=cidr,
                AvailabilityZone=az,
            )["Subnet"]["SubnetId"]
            subnets.append(sub)
        rds.create_db_subnet_group(
            DBSubnetGroupName="astrolift-test",
            DBSubnetGroupDescription="test subnet group",
            SubnetIds=subnets,
        )
        sg = ec2.create_security_group(
            GroupName="astrolift-test-sg",
            Description="test",
            VpcId=vpc,
        )["GroupId"]

        yield {"rds": rds, "sm": sm, "sg_id": sg}


@pytest.fixture
def rds_client(aws_mock) -> Any:
    return aws_mock["rds"]


@pytest.fixture
def sm_client(aws_mock) -> Any:
    return aws_mock["sm"]


@pytest.fixture
def driver(aws_mock) -> RDSPostgresDriver:
    return RDSPostgresDriver(
        config=RDSConfig(
            region="us-east-1",
            db_subnet_group="astrolift-test",
            security_group_ids=[aws_mock["sg_id"]],
        ),
        rds_client=aws_mock["rds"],
        secrets_client=aws_mock["sm"],
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="pg",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_db_instance(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, instance_id = parse_handle(result.handle)
    assert kind == KIND
    # Actually exists on the moto side
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=instance_id,
    )
    assert len(resp["DBInstances"]) == 1
    inst = resp["DBInstances"][0]
    assert inst["Engine"] == "postgres"
    assert inst["StorageEncrypted"] is True
    assert inst["MultiAZ"] is False
    assert inst["DeletionProtection"] is True
    assert inst["MasterUsername"] == "astrolift"


def test_provision_idempotent(driver: RDSPostgresDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_stores_master_password_in_secrets_manager(
    driver: RDSPostgresDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, instance_id = parse_handle(result.handle)
    secret_name = f"astrolift/rds/{instance_id}/master"
    resp = sm_client.get_secret_value(SecretId=secret_name)
    assert len(resp["SecretString"]) >= 16
    # Password is opaque, but matches our generator's character set
    assert all(c.isascii() and c not in '/@"\\ ' for c in resp["SecretString"])


def test_provision_honours_size_to_instance_class(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, instance_id = parse_handle(result.handle)
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=instance_id,
    )
    assert resp["DBInstances"][0]["DBInstanceClass"] == "db.m6g.large"
    assert resp["DBInstances"][0]["AllocatedStorage"] == 100


def test_provision_honours_spec_config_override(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "instance_class": "db.r6g.xlarge",
                "allocated_storage": 200,
                "multi_az": True,
                "deletion_protection": False,
            },
        ),
    )
    _, instance_id = parse_handle(result.handle)
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=instance_id,
    )
    inst = resp["DBInstances"][0]
    assert inst["DBInstanceClass"] == "db.r6g.xlarge"
    assert inst["AllocatedStorage"] == 200
    assert inst["MultiAZ"] is True
    assert inst["DeletionProtection"] is False


def _arn_for(rds_client, instance_id: str) -> str:
    """Test helper — fetch the DB instance ARN moto generates."""
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=instance_id,
    )
    return resp["DBInstances"][0]["DBInstanceArn"]


def test_provision_tags_db_instance(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    result = driver.provision(_spec())
    _, instance_id = parse_handle(result.handle)
    tag_resp = rds_client.list_tags_for_resource(
        ResourceName=_arn_for(rds_client, instance_id),
    )
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert tag_map.get("astrolift.io/managed-by") == "platform"
    assert tag_map.get("astrolift.io/app") == "api"
    assert tag_map.get("astrolift.io/environment") == "prod"


def test_provision_stamps_binding_and_managed_service_ids(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    """#438: cost collector joins on astrolift.io/binding +
    astrolift.io/managed_service_id. Both must land on the cloud-side
    object when populated on the ProvisionSpec."""
    binding_guid = "11111111-2222-3333-4444-555555555555"
    msvc_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = driver.provision(
        _spec(binding_id=binding_guid, managed_service_id=msvc_guid),
    )
    _, instance_id = parse_handle(result.handle)
    tag_resp = rds_client.list_tags_for_resource(
        ResourceName=_arn_for(rds_client, instance_id),
    )
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert tag_map.get("astrolift.io/binding") == binding_guid
    assert tag_map.get("astrolift.io/managed_service_id") == msvc_guid


# ---- update -----------------------------------------------------


def test_update_resize(driver: RDSPostgresDriver, rds_client) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            size="medium",
        ),
    )
    assert result.ok
    _, instance_id = parse_handle(provisioned.handle)
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=instance_id,
    )
    assert resp["DBInstances"][0]["DBInstanceClass"] == "db.t4g.medium"


def test_update_engine_version(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "engine_version": "16.5",
                "apply_immediately": True,
            },
        ),
    )
    assert result.ok


def test_update_noop_when_nothing_to_change(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_snapshot_respects_protection(
    driver: RDSPostgresDriver,
) -> None:
    # Provision with DeletionProtection on (the default).
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    # Default behaviour refuses when DeletionProtection is on
    assert not result.ok
    assert "DeletionProtection" in result.message


def test_deprovision_delete_data_only_skips_snapshot(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    # Disable DeletionProtection at provision time so this corner
    # of the matrix can complete without force_destroy.
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message


def test_deprovision_default_with_protection_off_takes_snapshot(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "snapshot=taken" in result.message


def test_deprovision_force_destroy_disables_protection_and_keeps_snapshot(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(_spec())  # DeletionProtection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=taken" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: RDSPostgresDriver,
) -> None:
    # No matching instance — driver must short-circuit cleanly.
    result = driver.deprovision(
        DeprovisionSpec(handle="postgres/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_master_password_secret(
    driver: RDSPostgresDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, instance_id = parse_handle(provisioned.handle)
    secret_name = f"astrolift/rds/{instance_id}/master"
    # Secret exists pre-deprovision
    assert sm_client.get_secret_value(SecretId=secret_name)["SecretString"]

    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    # Force-deleted secret raises on subsequent fetch
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: RDSPostgresDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="postgres/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_available_to_protocol(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    provisioned = driver.provision(_spec())
    # moto reports "available" immediately
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    # Canonical postgres envelope (#1003).
    assert "POSTGRES_HOST" in env
    assert "POSTGRES_PORT" in env
    assert "POSTGRES_DB" in env
    assert "POSTGRES_USER" in env
    assert "POSTGRES_PASSWORD" in env
    assert "DATABASE_URL" in env
    # Password + URL come via secret_ref, not literal
    assert env["POSTGRES_PASSWORD"].secret_ref is not None
    assert env["POSTGRES_PASSWORD"].literal is None
    # Host/port/user/db are literal
    assert env["POSTGRES_HOST"].literal is not None
    assert env["POSTGRES_USER"].literal == "astrolift"


def test_binding_iam_grant_scoped_to_secret(
    driver: RDSPostgresDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    grants = binding.iam_grants
    assert len(grants) == 1
    assert "secretsmanager:GetSecretValue" in grants[0].actions


def test_binding_for_missing_raises(driver: RDSPostgresDriver) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="postgres/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_handle(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id.startswith(parse_handle(provisioned.handle)[1])
    # Snapshot actually exists in moto
    resp = rds_client.describe_db_snapshots(
        DBSnapshotIdentifier=snap.snapshot_id,
    )
    assert len(resp["DBSnapshots"]) == 1


def test_snapshot_surfaces_driver_error(
    driver: RDSPostgresDriver,
) -> None:
    """snapshot() against a non-existent instance must raise
    ManagedServiceError so the workflow's error path engages."""
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="postgres/missing"))


def test_restore_from_snapshot_creates_new_instance(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    # Take a snapshot from one instance, restore into a new one with
    # a different service_handle_hint so the target instance id
    # differs from the source.
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, target_id = parse_handle(result.handle)
    resp = rds_client.describe_db_instances(
        DBInstanceIdentifier=target_id,
    )
    assert len(resp["DBInstances"]) == 1


def test_restore_surfaces_error_on_missing_snapshot(
    driver: RDSPostgresDriver,
) -> None:
    bad = SnapshotHandle(
        handle="postgres/anything",
        snapshot_id="does-not-exist",
        created_at="",
    )
    result = driver.restore(bad, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore_db_instance" in result.message


def test_provision_with_kms_and_parameter_group(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    """KMS key + parameter group are optional config; exercising them
    drives the optional ``if cfg.get(...)`` branches in provision()."""
    result = driver.provision(
        _spec(
            config={
                "kms_key_arn": "arn:aws:kms:us-east-1:123:key/abc",
                "parameter_group": "default.postgres16",
            },
        ),
    )
    assert result.ok


def test_provision_secret_idempotency_updates_existing(
    driver: RDSPostgresDriver,
    sm_client,
) -> None:
    """When the password secret already exists (operator retried a
    failed provision), the driver must update it rather than crash."""
    # Pre-seed a secret at the path the driver will compute.
    instance_id = driver._instance_id_for(spec=_spec())  # type: ignore[attr-defined]
    secret_name = f"astrolift/rds/{instance_id}/master"
    sm_client.create_secret(Name=secret_name, SecretString="stale")

    result = driver.provision(_spec())
    assert result.ok
    # Latest secret value rotated
    resp = sm_client.get_secret_value(SecretId=secret_name)
    assert resp["SecretString"] != "stale"


def test_default_client_construction_path() -> None:
    """When rds_client / secrets_client aren't injected the driver
    must still build successfully — boto3 inits lazily, so this only
    needs to confirm no exception during __init__."""
    from moto import mock_aws

    with mock_aws():
        d = RDSPostgresDriver(
            config=RDSConfig(
                region="us-east-1",
                db_subnet_group="x",
            ),
        )
        assert d._rds is not None  # type: ignore[attr-defined]
        assert d._sm is not None  # type: ignore[attr-defined]


def test_secret_name_for_url_distinct_from_master(
    driver: RDSPostgresDriver,
) -> None:
    """The URL secret has a separate path so the platform can rotate
    it without touching the master password secret."""
    instance_id = "astrolift-acme-api-prod-pg"
    assert driver._secret_name_for(instance_id=instance_id) != (  # type: ignore[attr-defined]
        driver._secret_name_for_url(instance_id=instance_id)  # type: ignore[attr-defined]
    )


def test_status_maps_deleting_to_deprovisioning(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    """Cover the DBInstanceStatus -> protocol mapping table for the
    states we expect during teardown."""
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    # Kick off async delete; moto returns the instance in 'deleting'
    rds_client.delete_db_instance(
        DBInstanceIdentifier=parse_handle(provisioned.handle)[1],
        SkipFinalSnapshot=True,
    )
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state in {"deprovisioning", "deprovisioned"}


def test_update_multi_az_and_backup_retention(
    driver: RDSPostgresDriver,
    rds_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "multi_az": True,
                "backup_retention_days": 14,
                "apply_immediately": True,
            },
        ),
    )
    assert result.ok


def test_update_parameter_group_surfaces_error_for_missing(
    driver: RDSPostgresDriver,
) -> None:
    """An invalid parameter_group must surface a clear error rather
    than silently succeeding."""
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"parameter_group": "does-not-exist"},
        ),
    )
    assert not result.ok
    assert "modify_db_instance" in result.message


# ---- module helpers ---------------------------------------------


def test_db_name_for_sanitizes_dashes() -> None:
    spec = _spec(app_slug="my-app", environment_name="dev-1")
    assert _db_name_for(spec) == "my_app_dev_1"


def test_db_name_for_prefixes_when_starts_with_digit() -> None:
    spec = _spec(app_slug="42-app", environment_name="prod")
    out = _db_name_for(spec)
    assert out.startswith("app_") or out[0].isalpha()


def test_generated_password_uses_safe_charset() -> None:
    pw = _generate_master_password(length=64)
    assert len(pw) == 64
    forbidden = set('/@"\\ ')
    assert not (set(pw) & forbidden)


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: RDSPostgresDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "engine_version",
        "instance_class",
        "allocated_storage",
        "multi_az",
        "deletion_protection",
        "backup_retention_days",
        "parameter_group",
        "kms_key_arn",
        "apply_immediately",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: RDSPostgresDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_URL",
    ):
        assert key in schema.env_vars


# ---- adoption ownership (#1961) ------------------------------------


def test_provision_refuses_to_adopt_another_orgs_colliding_instance(driver: RDSPostgresDriver) -> None:
    """org acme app x-api and org acme-x app api compute the same instance id;
    the second must not adopt the first org's database."""
    first = driver.provision(_spec(organization_slug="acme", app_slug="x-api"))
    second = driver.provision(_spec(organization_slug="acme-x", app_slug="api"))

    assert first.ok
    assert parse_handle(first.handle)[1] == driver._instance_id_for(
        spec=_spec(organization_slug="acme-x", app_slug="api")
    )
    assert second.ok is False
    assert "refusing to adopt" in second.message


def test_provision_refuses_an_instance_tagged_for_another_service(driver: RDSPostgresDriver) -> None:
    driver.provision(_spec(managed_service_id="svc-1"))
    other = driver.provision(_spec(managed_service_id="svc-2"))
    assert other.ok is False
    assert "another managed service" in other.message


def test_provision_still_adopts_its_own_instance(driver: RDSPostgresDriver) -> None:
    a = driver.provision(_spec(managed_service_id="svc-1"))
    b = driver.provision(_spec(managed_service_id="svc-1"))
    legacy = driver.provision(_spec(service_handle_hint="legacy"))
    legacy_again = driver.provision(_spec(service_handle_hint="legacy"))
    assert a.ok and b.ok and a.handle == b.handle
    assert legacy.ok and legacy_again.ok
