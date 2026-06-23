"""Tests for self-sufficient managed-service config resolution (#1002).

``managed_config_for`` builds a managed-service DRIVER config (S3Config /
RDSConfig / ElastiCacheConfig) from the cluster's install settings, and
``ensure_db_networking`` discovers the cluster VPC and creates the DB
subnet group + SG itself (no out-of-band terraform). Both honor
``provider_config`` overrides.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from aws.managed._networking import ensure_db_networking
from core.cluster_observability import managed_config_for


def _cluster(provider_config=None, *, slug="aws-prod", region="us-west-2"):
    return SimpleNamespace(
        slug=slug,
        region=region,
        provider_config=provider_config or {},
        auth_config={},
    )


# ---- managed_config_for: object_store (S3, no VPC) --------------------


def test_s3_config_defaults_and_overrides():
    cfg = managed_config_for(
        "aws", _cluster(), kind="object_store",
    )
    assert cfg.region == "us-west-2"
    assert cfg.bucket_name_prefix == "astrolift"
    assert cfg.public_access_blocked is True

    pinned = managed_config_for(
        "aws",
        _cluster({"bucket_name_prefix": "acme", "public_access_blocked": False}),
        kind="object_store",
    )
    assert pinned.bucket_name_prefix == "acme"
    assert pinned.public_access_blocked is False


# ---- managed_config_for: postgres with pinned networking --------------


def test_postgres_config_uses_pinned_networking_no_discovery():
    """When the install pins the subnet group + SG, no cloud discovery
    runs and the config carries them verbatim."""
    cfg = managed_config_for(
        "aws",
        _cluster(
            {
                "db_subnet_group": "my-db-subnets",
                "db_security_group_ids": ["sg-123"],
                "instance_name_prefix": "acme",
                "postgres_engine_version": "15.5",
                "multi_az_default": True,
            },
        ),
        kind="postgres",
    )
    assert cfg.db_subnet_group == "my-db-subnets"
    assert cfg.security_group_ids == ["sg-123"]
    assert cfg.instance_name_prefix == "acme"
    assert cfg.engine_version == "15.5"
    assert cfg.multi_az_default is True


def test_unknown_kind_raises():
    import pytest

    from core.cluster_observability import ClusterObservabilityError

    with pytest.raises(ClusterObservabilityError):
        managed_config_for("aws", _cluster(), kind="not-a-kind")


# ---- ensure_db_networking: discover + create -------------------------


def test_ensure_db_networking_discovers_and_creates():
    ec2 = MagicMock()
    eks = MagicMock()
    rds = MagicMock()
    elasticache = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": "subnet-a", "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-b", "AvailabilityZone": "us-west-2b", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-pub", "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": True},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-new"}

    grp, sgs = ensure_db_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        port=5432,
        service="rds",
        clients=(ec2, rds, elasticache, eks),
    )

    assert grp == "astrolift-aws-prod-db"
    assert sgs == ["sg-new"]
    # Created the SG and opened 5432 from the VPC CIDR (the pods).
    ec2.create_security_group.assert_called_once()
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    perm = ingress["IpPermissions"][0]
    assert perm["FromPort"] == 5432
    assert perm["IpRanges"][0]["CidrIp"] == "10.0.0.0/16"
    # Created the RDS subnet group across the two private AZ subnets.
    sg_call = rds.create_db_subnet_group.call_args.kwargs
    assert sorted(sg_call["SubnetIds"]) == ["subnet-a", "subnet-b"]


def test_ensure_db_networking_override_short_circuits():
    ec2 = MagicMock()
    grp, sgs = ensure_db_networking(
        _cluster({"db_subnet_group": "pinned", "db_security_group_ids": ["sg-x"]}),
        region="us-west-2",
        port=5432,
        service="rds",
        clients=(ec2, MagicMock(), MagicMock(), MagicMock()),
    )
    assert grp == "pinned"
    assert sgs == ["sg-x"]
    ec2.describe_vpcs.assert_not_called()  # no discovery when pinned
