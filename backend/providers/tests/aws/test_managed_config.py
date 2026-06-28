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

from core.cluster_observability import managed_config_for

from aws.managed._networking import ensure_db_networking


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
        "aws",
        _cluster(),
        kind="object_store",
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


# ---- managed_config_for: cdn (CloudFront, #1010) ----------------------


def test_cdn_config_defaults_and_overrides():
    from aws.managed.cdn_cloudfront import CloudFrontConfig

    cfg = managed_config_for("aws", _cluster(), kind="cdn")
    assert isinstance(cfg, CloudFrontConfig)
    # CloudFront + its ACM certs are always us-east-1, regardless of cluster region.
    assert cfg.region == "us-east-1"
    assert cfg.comment_prefix == "astrolift"
    assert cfg.price_class == "PriceClass_100"

    pinned = managed_config_for(
        "aws",
        _cluster({"cdn_comment_prefix": "acme", "cloudfront_price_class": "PriceClass_All"}),
        kind="cdn",
    )
    assert pinned.comment_prefix == "acme"
    assert pinned.price_class == "PriceClass_All"


def test_managed_service_kind_has_cdn():
    # The cdn ManagedService row shape relies on the enum addition (#1010).
    from astrolift_services.models import ManagedService

    assert ManagedService.Kind.CDN == "cdn"
    assert "cdn" in {choice for choice, _label in ManagedService.Kind.choices}


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


# ---- managed_config_for: the non-VPC kinds wired in #1037 -------------
#
# plugin.py registers managed-service drivers for these (kind, variant)
# pairs but managed_config_for had no branch, so provisioning died with
# "no managed-service config builder for kind=<X>". One falsifiable guard
# per kind: right Config type, region resolved, one override honored.


def test_queue_config_defaults_and_overrides():
    from aws.managed.queue_sqs import SQSConfig

    cfg = managed_config_for("aws", _cluster(), kind="queue")
    assert isinstance(cfg, SQSConfig)
    assert cfg.region == "us-west-2"
    assert cfg.queue_name_prefix == "astrolift"
    assert cfg.fifo_default is False

    pinned = managed_config_for(
        "aws",
        _cluster({"queue_name_prefix": "acme", "sqs_fifo_default": True}),
        kind="queue",
    )
    assert pinned.queue_name_prefix == "acme"
    assert pinned.fifo_default is True


def test_search_config_defaults_and_overrides():
    from aws.managed.search_opensearch import OpenSearchSearchConfig

    cfg = managed_config_for("aws", _cluster(), kind="search")
    assert isinstance(cfg, OpenSearchSearchConfig)
    assert cfg.region == "us-west-2"
    assert cfg.domain_name_prefix == "astrolift"
    assert cfg.engine_version == "OpenSearch_2.11"

    pinned = managed_config_for(
        "aws",
        _cluster({"search_domain_name_prefix": "acme", "opensearch_engine_version": "OpenSearch_2.13"}),
        kind="search",
    )
    assert pinned.domain_name_prefix == "acme"
    assert pinned.engine_version == "OpenSearch_2.13"


def test_vector_index_config_is_distinct_from_search():
    """search and vector_index resolve to SEPARATE Config types from
    SEPARATE modules — not two variants of one class."""
    from aws.managed.search_opensearch import OpenSearchSearchConfig
    from aws.managed.vector_opensearch import OpenSearchVectorConfig

    cfg = managed_config_for("aws", _cluster(), kind="vector_index")
    assert isinstance(cfg, OpenSearchVectorConfig)
    assert not isinstance(cfg, OpenSearchSearchConfig)
    assert cfg.region == "us-west-2"
    assert cfg.domain_name_prefix == "astrolift-vec"
    assert cfg.instance_count_default == 1

    pinned = managed_config_for(
        "aws",
        _cluster({"vector_domain_name_prefix": "acme-vec", "vector_instance_count_default": 3}),
        kind="vector_index",
    )
    assert pinned.domain_name_prefix == "acme-vec"
    assert pinned.instance_count_default == 3


def test_email_config_defaults_and_overrides():
    from aws.managed.email_ses import SESEmailConfig

    cfg = managed_config_for("aws", _cluster(), kind="email")
    assert isinstance(cfg, SESEmailConfig)
    assert cfg.region == "us-west-2"
    assert cfg.identity_prefix == "astrolift"
    assert cfg.base_domain == ""

    pinned = managed_config_for(
        "aws",
        _cluster({"ses_identity_prefix": "acme", "base_domain": "mail.acme.test"}),
        kind="email",
    )
    assert pinned.identity_prefix == "acme"
    assert pinned.base_domain == "mail.acme.test"


def test_model_endpoint_config_defaults_and_overrides():
    from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig

    cfg = managed_config_for("aws", _cluster(), kind="model_endpoint")
    assert isinstance(cfg, AmazonBedrockConfig)
    assert cfg.region == "us-west-2"
    assert cfg.default_model_id == "anthropic.claude-3-haiku-20240307-v1:0"
    assert cfg.invocation_log_retention_days == 30

    pinned = managed_config_for(
        "aws",
        _cluster(
            {
                "bedrock_default_model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                "bedrock_invocation_log_retention_days": 90,
            },
        ),
        kind="model_endpoint",
    )
    assert pinned.default_model_id == "anthropic.claude-3-5-sonnet-20240620-v1:0"
    assert pinned.invocation_log_retention_days == 90


def test_time_series_config_defaults_and_overrides():
    from aws.managed.timeseries_timestream import TimestreamConfig

    cfg = managed_config_for("aws", _cluster(), kind="time_series")
    assert isinstance(cfg, TimestreamConfig)
    assert cfg.region == "us-west-2"
    assert cfg.database_name_prefix == "astrolift"
    assert cfg.table_name_default == "metrics"
    assert cfg.kms_key_id == ""

    pinned = managed_config_for(
        "aws",
        _cluster({"timestream_table_name_default": "events", "kms_key_id": "arn:aws:kms:::key/abc"}),
        kind="time_series",
    )
    assert pinned.table_name_default == "events"
    assert pinned.kms_key_id == "arn:aws:kms:::key/abc"


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
