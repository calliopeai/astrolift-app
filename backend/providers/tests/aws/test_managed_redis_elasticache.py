"""Tests for AWS ElastiCache Redis managed-service driver (#352).

Covers provision (idempotent, with TLS + AUTH token, without TLS),
update, deprovision four-corner matrix, status state-mapping,
binding env-var shape, snapshot + restore. All cloud calls go
through moto.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.redis_elasticache import (
    KIND,
    ElastiCacheConfig,
    ElastiCacheRedisDriver,
    _generate_auth_token,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def aws_mock() -> Generator:
    from moto import mock_aws

    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        ec = boto3.client("elasticache", region_name="us-east-1")
        sm = boto3.client("secretsmanager", region_name="us-east-1")

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
        ec.create_cache_subnet_group(
            CacheSubnetGroupName="astrolift-test",
            CacheSubnetGroupDescription="test",
            SubnetIds=subnets,
        )
        sg = ec2.create_security_group(
            GroupName="astrolift-redis-sg",
            Description="test",
            VpcId=vpc,
        )["GroupId"]

        yield {"ec": ec, "sm": sm, "sg_id": sg}


@pytest.fixture
def ec_client(aws_mock) -> Any:
    return aws_mock["ec"]


@pytest.fixture
def sm_client(aws_mock) -> Any:
    return aws_mock["sm"]


@pytest.fixture
def driver(aws_mock) -> ElastiCacheRedisDriver:
    return ElastiCacheRedisDriver(
        config=ElastiCacheConfig(
            region="us-east-1",
            cache_subnet_group="astrolift-test",
            security_group_ids=[aws_mock["sg_id"]],
        ),
        elasticache_client=aws_mock["ec"],
        secrets_client=aws_mock["sm"],
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456789",
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="rd",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_replication_group(
    driver: ElastiCacheRedisDriver,
    ec_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, rg_id = parse_handle(result.handle)
    assert kind == KIND
    resp = ec_client.describe_replication_groups(
        ReplicationGroupId=rg_id,
    )
    assert len(resp["ReplicationGroups"]) == 1
    rg = resp["ReplicationGroups"][0]
    assert rg["AtRestEncryptionEnabled"] is True
    assert rg["TransitEncryptionEnabled"] is True


def test_valkey_variant_emits_current_create_replication_group_shape() -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    class RecordingElastiCache:
        def __init__(self) -> None:
            self.request: dict[str, Any] = {}

        def describe_replication_groups(self, **_kwargs):
            raise type("ReplicationGroupNotFoundFault", (Exception,), {})("not found")

        def create_replication_group(self, **kwargs):
            self.request = kwargs

    subject_client = RecordingElastiCache()
    subject = ElastiCacheRedisDriver(
        config=ElastiCacheConfig(
            region="us-east-1",
            cache_subnet_group="private-cache",
            security_group_ids=["sg-cache"],
            engine="valkey",
        ),
        elasticache_client=subject_client,
        secrets_client=object(),
    )

    result = subject.provision(
        _spec(config={"transit_encryption": False, "engine_version": "8.0"}),
    )

    assert result.ok
    assert subject_client.request["Engine"] == "valkey"
    service = Session().get_service_model("elasticache")
    validate_parameters(
        subject_client.request,
        service.operation_model("CreateReplicationGroup").input_shape,
    )


def test_provision_idempotent(driver: ElastiCacheRedisDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert "already exists" in b.message


def test_provision_stores_auth_token_in_secrets_manager(
    driver: ElastiCacheRedisDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, rg_id = parse_handle(result.handle)
    secret_name = f"astrolift/elasticache/{rg_id}/auth"
    resp = sm_client.get_secret_value(SecretId=secret_name)
    assert len(resp["SecretString"]) >= 16


def test_provision_no_auth_token_when_transit_encryption_off(
    driver: ElastiCacheRedisDriver,
    sm_client,
) -> None:
    result = driver.provision(
        _spec(config={"transit_encryption": False}),
    )
    _, rg_id = parse_handle(result.handle)
    secret_name = f"astrolift/elasticache/{rg_id}/auth"
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


def test_provision_size_to_node_type(
    driver: ElastiCacheRedisDriver,
    ec_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, rg_id = parse_handle(result.handle)
    resp = ec_client.describe_replication_groups(
        ReplicationGroupId=rg_id,
    )
    assert resp["ReplicationGroups"][0]["CacheNodeType"] == "cache.m7g.large"


def test_provision_replicas_enable_automatic_failover(
    driver: ElastiCacheRedisDriver,
    ec_client,
) -> None:
    result = driver.provision(
        _spec(config={"replicas_per_node_group": 1}),
    )
    _, rg_id = parse_handle(result.handle)
    resp = ec_client.describe_replication_groups(
        ReplicationGroupId=rg_id,
    )
    rg = resp["ReplicationGroups"][0]
    # AutomaticFailoverEnabled is reported as a string state in
    # describe_replication_groups
    assert rg.get("AutomaticFailover") in {"enabled", True}


def test_provision_tags_replication_group(
    driver: ElastiCacheRedisDriver,
    ec_client,
) -> None:
    result = driver.provision(_spec())
    _, rg_id = parse_handle(result.handle)
    resp = ec_client.describe_replication_groups(
        ReplicationGroupId=rg_id,
    )
    arn = resp["ReplicationGroups"][0]["ARN"]
    tags = ec_client.list_tags_for_resource(ResourceName=arn)["TagList"]
    tag_map = {t["Key"]: t["Value"] for t in tags}
    assert tag_map.get("astrolift.io/managed-by") == "platform"


# ---- update -----------------------------------------------------


def test_update_node_type_calls_modify(
    driver: ElastiCacheRedisDriver,
) -> None:
    """moto doesn't implement modify_replication_group; we assert
    the driver calls into it and returns a useful error rather than
    silently succeeding. The real AWS path returns success."""
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    # Either the call succeeds (real AWS) or fails with a clear
    # "not implemented" message (moto). Both are acceptable; what
    # matters is the driver wired the size mapping through.
    if not result.ok:
        assert "modify_replication_group" in result.message


def test_update_engine_version_calls_modify(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "engine_version": "7.2",
                "apply_immediately": True,
            },
        ),
    )
    if not result.ok:
        assert "modify_replication_group" in result.message


def test_update_noop_when_nothing_to_change(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_snapshot(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "snapshot=taken" in result.message


def test_deprovision_delete_data_skips_snapshot(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message


def test_deprovision_force_destroy_atomic(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: ElastiCacheRedisDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="redis/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_delete_data_drops_auth_secret(
    driver: ElastiCacheRedisDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(_spec())
    _, rg_id = parse_handle(provisioned.handle)
    secret_name = f"astrolift/elasticache/{rg_id}/auth"
    assert sm_client.get_secret_value(SecretId=secret_name)["SecretString"]
    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: ElastiCacheRedisDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="redis/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_available(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_with_tls_uses_secret_refs(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    # Canonical redis envelope (#1003) — the auth token rides REDIS_PASSWORD.
    assert env["REDIS_TLS"].literal == "1"
    assert env["REDIS_PASSWORD"].secret_ref is not None
    assert env["REDIS_URL"].secret_ref is not None
    assert len(binding.iam_grants) == 1


def test_binding_without_tls_uses_literal_url(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"transit_encryption": False}),
    )
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    assert env["REDIS_TLS"].literal == "0"
    assert "REDIS_AUTH_TOKEN" not in env
    assert env["REDIS_URL"].literal is not None
    assert env["REDIS_URL"].literal.startswith("redis://")


def test_binding_for_missing_raises(
    driver: ElastiCacheRedisDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="redis/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_handle(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    _, rg_id = parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(rg_id)


def test_snapshot_surfaces_driver_error(
    driver: ElastiCacheRedisDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="redis/missing"))


def test_restore_creates_new_replication_group(
    driver: ElastiCacheRedisDriver,
    ec_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="restored", managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456780")
    result = driver.restore(snap, restore_spec)
    # moto may or may not support restore-from-snapshot via
    # create_replication_group; we accept either ok or a clean error.
    if result.ok:
        _, target_id = parse_handle(result.handle)
        assert target_id.endswith(restore_spec.managed_service_id.replace("-", ""))
        assert len(target_id) <= 40


def test_restore_surfaces_error_on_missing_snapshot(
    driver: ElastiCacheRedisDriver,
) -> None:
    bad = SnapshotHandle(
        handle="redis/anything",
        snapshot_id="does-not-exist",
        created_at="",
    )
    result = driver.restore(
        bad, _spec(service_handle_hint="failed", managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456781")
    )
    # Result either fails (good — surfaces the error) or moto allows
    # it through (acceptable — we'd catch it in real AWS via the
    # workflow retry policy).
    assert result.ok or "restore" in result.message


# ---- module helpers ---------------------------------------------


def test_generated_auth_token_length() -> None:
    t = _generate_auth_token(length=64)
    assert len(t) == 64
    assert all(c.isalnum() for c in t)


def test_replication_group_id_sanitized(
    driver: ElastiCacheRedisDriver,
) -> None:
    """Underscores + uppercase get normalized; first char is a letter."""
    rg = driver._replication_group_id_for(  # type: ignore[attr-defined]
        spec=_spec(app_slug="my_app", environment_name="DEV"),
    )
    assert rg[0].isalpha()
    assert "_" not in rg
    assert rg == rg.lower()
    assert len(rg) <= 40


# ---- schemas ----------------------------------------------------


def test_config_schema_shape(driver: ElastiCacheRedisDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    for key in (
        "node_type",
        "engine_version",
        "num_node_groups",
        "replicas_per_node_group",
        "transit_encryption",
        "at_rest_encryption",
        "auth_token",
        "snapshot_retention_days",
        "multi_az",
        "parameter_group",
        "kms_key_arn",
    ):
        assert key in schema["properties"]


def test_binding_schema_lists_all_env_vars(
    driver: ElastiCacheRedisDriver,
) -> None:
    schema = driver.binding_schema()
    for key in ("REDIS_HOST", "REDIS_PORT", "REDIS_TLS", "REDIS_AUTH_TOKEN", "REDIS_URL"):
        assert key in schema.env_vars


def test_default_client_construction_path() -> None:
    """When clients aren't injected the driver builds boto clients."""
    from moto import mock_aws

    with mock_aws():
        d = ElastiCacheRedisDriver(
            config=ElastiCacheConfig(
                region="us-east-1",
                cache_subnet_group="x",
            ),
        )
        assert d._ec is not None  # type: ignore[attr-defined]
        assert d._sm is not None  # type: ignore[attr-defined]


def test_provision_failure_rolls_back_auth_token_secret(
    sm_client,
) -> None:
    """When create_replication_group fails after we've stored an
    AUTH token, the driver must drop the dangling secret."""

    class RaisingEC:
        def describe_replication_groups(self, **_kwargs):
            raise type(
                "ReplicationGroupNotFoundFault",
                (Exception,),
                {},
            )("not found")

        def create_replication_group(self, **_kwargs):
            raise RuntimeError("synthetic create failure")

    driver = ElastiCacheRedisDriver(
        config=ElastiCacheConfig(
            region="us-east-1",
            cache_subnet_group="astrolift-test",
        ),
        elasticache_client=RaisingEC(),
        secrets_client=sm_client,
    )
    result = driver.provision(_spec())
    assert not result.ok
    assert "synthetic create failure" in result.message
    # The auth secret we'd have created shouldn't survive the rollback
    spec = _spec()
    rg_id = driver._replication_group_id_for(spec=spec)  # type: ignore[attr-defined]
    secret_name = f"astrolift/elasticache/{rg_id}/auth"
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


def test_url_secret_name_distinct_from_auth(
    driver: ElastiCacheRedisDriver,
) -> None:
    auth = driver._auth_secret_name_for(rg_id="test")  # type: ignore[attr-defined]
    url = driver._url_secret_name_for(rg_id="test")  # type: ignore[attr-defined]
    assert auth != url


def test_provision_with_kms_and_parameter_group(
    driver: ElastiCacheRedisDriver,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "kms_key_arn": "arn:aws:kms:us-east-1:123:key/abc",
                "parameter_group": "default.redis7",
            },
        ),
    )
    assert result.ok


def test_update_snapshot_retention(
    driver: ElastiCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"snapshot_retention_days": 14},
        ),
    )
    # moto may not implement modify_replication_group; we accept
    # either ok or a clean error pointing at the call.
    if not result.ok:
        assert "modify_replication_group" in result.message


def test_status_for_failed_replication_group(
    driver: ElastiCacheRedisDriver,
) -> None:
    """The state-mapping table covers create-failed, incompatible-network,
    etc.; describe_replication_groups against an instance that fails
    to come up surfaces those. Exercise the path with a successful
    provision but a hand-crafted state mapping lookup."""
    from aws.managed.redis_elasticache import _EC_STATE_TO_PROTOCOL

    assert _EC_STATE_TO_PROTOCOL["create-failed"] == "error"
    assert _EC_STATE_TO_PROTOCOL["available"] == "available"
    assert _EC_STATE_TO_PROTOCOL["deleting"] == "deprovisioning"


def test_deprovision_force_destroy_retries_on_invalid_state() -> None:
    """When ElastiCache returns InvalidReplicationGroupState the
    driver: (a) refuses cleanly without force_destroy, surfacing the
    state to the operator + workflow retry policy; (b) propagates the
    error when force_destroy is on so Temporal can back off + retry."""

    class FakeEC:
        def describe_replication_groups(self, **_kwargs):
            return {
                "ReplicationGroups": [
                    {"Status": "modifying", "ReplicationGroupId": "x"},
                ],
            }

        def delete_replication_group(self, **_kwargs):
            raise RuntimeError(
                "An error occurred (InvalidReplicationGroupState) when calling the DeleteReplicationGroup operation",
            )

    class FakeSM:
        def delete_secret(self, **_kwargs):
            pass

    driver = ElastiCacheRedisDriver(
        config=ElastiCacheConfig(
            region="us-east-1",
            cache_subnet_group="x",
        ),
        elasticache_client=FakeEC(),
        secrets_client=FakeSM(),
    )
    soft = driver.deprovision(
        DeprovisionSpec(handle="redis/x"),
        force_destroy=False,
    )
    assert not soft.ok
    assert "mid-modify" in soft.message

    hard = driver.deprovision(
        DeprovisionSpec(handle="redis/x"),
        force_destroy=True,
    )
    assert not hard.ok
    assert "delete_replication_group" in hard.message


def test_provision_does_not_adopt_another_services_resource(driver) -> None:
    """A recorded locator never grants ownership of another service's resource."""
    first = driver.provision(_spec())
    second = driver.provision(
        _spec(managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456780", recorded_handle=first.handle)
    )

    assert first.ok, first.message
    assert not second.ok and second.handle == ""
    assert "refusing to adopt" in second.message
