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

import pytest
from core.cluster_observability import managed_config_for

from aws.managed._networking import (
    ensure_db_networking,
    ensure_documentdb_networking,
    ensure_keyspaces_networking,
    ensure_memorydb_networking,
    ensure_neptune_networking,
    ensure_opensearch_serverless_networking,
    ensure_redshift_networking,
)


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
    # Backend-tree contract — runs in the backend test job (which has
    # Django); the standalone providers job skips it.
    pytest.importorskip("django")
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


def test_aurora_and_sql_server_variants_resolve_distinct_runtime_configs():
    from aws.managed.aurora import AuroraConfig
    from aws.managed.mssql_rds import RDSSqlServerConfig

    cluster = _cluster(
        {
            "db_subnet_group": "private-db",
            "db_security_group_ids": ["sg-db"],
        },
    )
    aurora_pg = managed_config_for(
        "aws",
        cluster,
        kind="postgres",
        variant="aurora_postgres_serverless_v2",
    )
    aurora_mysql = managed_config_for(
        "aws",
        cluster,
        kind="mysql",
        variant="aurora_mysql",
    )
    sql_express = managed_config_for(
        "aws",
        cluster,
        kind="mssql",
        variant="rds_sqlserver_express",
    )

    assert isinstance(aurora_pg, AuroraConfig)
    assert aurora_pg.engine == "aurora-postgresql"
    assert aurora_pg.serverless_v2 is True
    assert aurora_mysql.engine == "aurora-mysql"
    assert aurora_mysql.serverless_v2 is False
    assert isinstance(sql_express, RDSSqlServerConfig)
    assert sql_express.engine == "sqlserver-ex"


def test_rds_proxy_config_uses_raw_private_network_ids():
    from aws.managed.rds_proxy import RDSProxyConfig

    cfg = managed_config_for(
        "aws",
        _cluster(
            {
                "db_proxy_subnet_ids": ["subnet-a", "subnet-b"],
                "db_proxy_security_group_ids": ["sg-proxy"],
                "db_proxy_role_arn": "arn:aws:iam::123:role/proxy",
            },
        ),
        kind="database_proxy",
        variant="rds_proxy",
    )

    assert isinstance(cfg, RDSProxyConfig)
    assert cfg.vpc_subnet_ids == ["subnet-a", "subnet-b"]
    assert cfg.vpc_security_group_ids == ["sg-proxy"]
    assert cfg.role_arn.endswith("role/proxy")


def test_memorydb_config_uses_pinned_private_networking_and_engine_options():
    from aws.managed.memorydb import MemoryDBConfig

    cfg = managed_config_for(
        "aws",
        _cluster(
            {
                "memorydb_subnet_group": "durable-cache-subnets",
                "memorydb_security_group_ids": ["sg-memorydb"],
                "memorydb_engine": "redis",
                "memorydb_engine_version": "7.0",
                "memorydb_auth_mode": "iam",
            },
        ),
        kind="redis",
        variant="memorydb",
    )

    assert isinstance(cfg, MemoryDBConfig)
    assert cfg.subnet_group == "durable-cache-subnets"
    assert cfg.security_group_ids == ["sg-memorydb"]
    assert cfg.engine == "redis"
    assert cfg.engine_version == "7.0"
    assert cfg.auth_mode_default == "iam"


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


def test_documentdb_config_selects_provisioned_and_serverless_variants():
    from aws.managed.documentdb import DocumentDBConfig

    provider_config = {
        "documentdb_subnet_group": "documentdb-private",
        "documentdb_security_group_ids": ["sg-documentdb"],
        "documentdb_engine_version": "5.0.0",
        "documentdb_backup_retention_days": 14,
    }
    provisioned = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="document_db",
        variant="documentdb",
    )
    serverless = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="document_db",
        variant="documentdb_serverless_v2",
    )

    assert isinstance(provisioned, DocumentDBConfig)
    assert provisioned.serverless_v2 is False
    assert serverless.serverless_v2 is True
    assert serverless.db_subnet_group == "documentdb-private"
    assert serverless.security_group_ids == ["sg-documentdb"]
    assert serverless.backup_retention_days == 14


def test_keyspaces_config_uses_cloud_identity_and_portable_defaults():
    from aws.managed.keyspaces import KeyspacesConfig

    cfg = managed_config_for(
        "aws",
        _cluster(
            {
                "account_id": "123456789012",
                "keyspaces_name_prefix": "platform",
                "keyspaces_throughput_mode_default": "PROVISIONED",
                "keyspaces_vpc_endpoint_id": "vpce-keyspaces",
            },
        ),
        kind="wide_column",
        variant="keyspaces",
    )

    assert isinstance(cfg, KeyspacesConfig)
    assert cfg.region == "us-west-2"
    assert cfg.account_id == "123456789012"
    assert cfg.name_prefix == "platform"
    assert cfg.throughput_mode_default == "PROVISIONED"
    assert cfg.vpc_endpoint_id == "vpce-keyspaces"


def test_neptune_variants_use_private_networking_and_cloud_identity():
    from aws.managed.neptune import NeptuneConfig

    provider_config = {
        "account_id": "123456789012",
        "neptune_subnet_group": "neptune-private",
        "neptune_security_group_ids": ["sg-neptune"],
        "neptune_cluster_name_prefix": "platform",
        "neptune_backup_retention_days": 14,
    }
    provisioned = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="graph_db",
        variant="neptune",
    )
    serverless = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="graph_db",
        variant="neptune_serverless",
    )

    assert isinstance(provisioned, NeptuneConfig)
    assert provisioned.serverless_v2 is False
    assert serverless.serverless_v2 is True
    assert serverless.account_id == "123456789012"
    assert serverless.db_subnet_group == "neptune-private"
    assert serverless.security_group_ids == ["sg-neptune"]
    assert serverless.backup_retention_days == 14


def test_redshift_variants_resolve_distinct_private_runtime_configs():
    from aws.managed.redshift import RedshiftConfig
    from aws.managed.redshift_serverless import RedshiftServerlessConfig

    provider_config = {
        "account_id": "123456789012",
        "redshift_subnet_group": "redshift-private",
        "redshift_subnet_ids": ["subnet-a", "subnet-b", "subnet-c"],
        "redshift_security_group_ids": ["sg-redshift"],
        "redshift_name_prefix": "platform",
        "redshift_snapshot_retention_days": 60,
        "redshift_serverless_base_capacity": 32,
    }
    provisioned = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="warehouse",
        variant="redshift",
    )
    serverless = managed_config_for(
        "aws",
        _cluster(provider_config),
        kind="warehouse",
        variant="redshift_serverless",
    )

    assert isinstance(provisioned, RedshiftConfig)
    assert provisioned.cluster_subnet_group == "redshift-private"
    assert provisioned.manual_snapshot_retention_days == 60
    assert isinstance(serverless, RedshiftServerlessConfig)
    assert serverless.subnet_ids == ["subnet-a", "subnet-b", "subnet-c"]
    assert serverless.security_group_ids == ["sg-redshift"]
    assert serverless.base_capacity_default == 32
    assert serverless.snapshot_retention_days == 60


def test_every_registered_managed_service_driver_has_a_config_builder():
    """Regression guard for #1037 / #982: every (kind, variant) the AWS
    plugin registers a managed-service driver for MUST resolve through
    ``managed_config_for``. The #982 live grid found 6 registered drivers
    with no config branch, so ``provisionManagedService`` died with
    'no managed-service config builder for kind=<X>'. Iterating the manifest
    means a driver added to the plugin without a matching config builder
    fails here instead of failing live."""
    from aws.plugin import PLUGIN

    # Pin DB networking for the VPC-bound kinds (postgres/mysql/redis) so
    # config resolution short-circuits instead of reaching boto3.
    cluster = _cluster(
        {
            "db_subnet_group": "subnets",
            "cache_subnet_group": "subnets",
            "db_security_group_ids": ["sg-1"],
            "db_proxy_subnet_ids": ["subnet-a", "subnet-b"],
            "db_proxy_security_group_ids": ["sg-proxy"],
            "serverless_cache_subnet_ids": ["subnet-a", "subnet-b"],
            "serverless_cache_security_group_ids": ["sg-cache"],
            "memorydb_subnet_group": "memorydb-subnets",
            "memorydb_security_group_ids": ["sg-memorydb"],
            "documentdb_subnet_group": "documentdb-subnets",
            "documentdb_security_group_ids": ["sg-documentdb"],
            "account_id": "123456789012",
            "opensearch_serverless_vpc_endpoint_ids": ["vpce-aoss"],
            "keyspaces_vpc_endpoint_id": "vpce-keyspaces",
            "neptune_subnet_group": "neptune-subnets",
            "neptune_security_group_ids": ["sg-neptune"],
            "redshift_subnet_group": "redshift-subnets",
            "redshift_subnet_ids": ["subnet-a", "subnet-b", "subnet-c"],
            "redshift_security_group_ids": ["sg-redshift"],
        },
    )
    for kind, variant in PLUGIN.managed_service_drivers:
        cfg = managed_config_for("aws", cluster, kind=kind, variant=variant)
        assert cfg is not None, f"no config builder for registered driver kind={kind!r} variant={variant!r}"


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


def test_ensure_memorydb_networking_discovers_and_creates():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    memorydb = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": "subnet-a", "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-b", "AvailabilityZone": "us-west-2b", "MapPublicIpOnLaunch": False},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-memorydb"}

    group, security_groups = ensure_memorydb_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        clients=(ec2, memorydb, eks),
    )

    assert group == "astrolift-aws-prod-memorydb"
    assert security_groups == ["sg-memorydb"]
    subnet_call = memorydb.create_subnet_group.call_args.kwargs
    assert subnet_call["SubnetIds"] == ["subnet-a", "subnet-b"]
    service = Session().get_service_model("memorydb")
    validate_parameters(
        subnet_call,
        service.operation_model("CreateSubnetGroup").input_shape,
    )
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    assert ingress["IpPermissions"][0]["FromPort"] == 6379


def test_ensure_opensearch_serverless_networking_discovers_and_creates():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    aoss = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": "subnet-a", "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
            {"SubnetId": "subnet-b", "AvailabilityZone": "us-west-2b", "MapPublicIpOnLaunch": False},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-aoss"}
    aoss.list_vpc_endpoints.return_value = {"vpcEndpointSummaries": []}
    aoss.create_vpc_endpoint.return_value = {"createVpcEndpointDetail": {"id": "vpce-aoss"}}

    endpoint_ids = ensure_opensearch_serverless_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        clients=(ec2, aoss, eks),
    )

    assert endpoint_ids == ["vpce-aoss"]
    create = aoss.create_vpc_endpoint.call_args.kwargs
    assert create["vpcId"] == "vpc-abc"
    assert create["subnetIds"] == ["subnet-a", "subnet-b"]
    assert create["securityGroupIds"] == ["sg-aoss"]
    service = Session().get_service_model("opensearchserverless")
    validate_parameters(create, service.operation_model("CreateVpcEndpoint").input_shape)


def test_ensure_documentdb_networking_discovers_and_creates():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    docdb = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-a",
                "AvailabilityZone": "us-west-2a",
                "MapPublicIpOnLaunch": False,
            },
            {
                "SubnetId": "subnet-b",
                "AvailabilityZone": "us-west-2b",
                "MapPublicIpOnLaunch": False,
            },
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-documentdb"}

    group, security_groups = ensure_documentdb_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        clients=(ec2, docdb, eks),
    )

    assert group == "astrolift-aws-prod-docdb"
    assert security_groups == ["sg-documentdb"]
    subnet_call = docdb.create_db_subnet_group.call_args.kwargs
    assert subnet_call["SubnetIds"] == ["subnet-a", "subnet-b"]
    service = Session().get_service_model("docdb")
    validate_parameters(
        subnet_call,
        service.operation_model("CreateDBSubnetGroup").input_shape,
    )
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    assert ingress["IpPermissions"][0]["FromPort"] == 27017


def test_ensure_keyspaces_networking_creates_private_interface_endpoint():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-a",
                "AvailabilityZone": "us-west-2a",
                "MapPublicIpOnLaunch": False,
            },
            {
                "SubnetId": "subnet-b",
                "AvailabilityZone": "us-west-2b",
                "MapPublicIpOnLaunch": False,
            },
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-keyspaces"}
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    ec2.create_vpc_endpoint.return_value = {
        "VpcEndpoint": {"VpcEndpointId": "vpce-keyspaces"},
    }

    endpoint_id = ensure_keyspaces_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        clients=(ec2, eks),
    )

    assert endpoint_id == "vpce-keyspaces"
    create = ec2.create_vpc_endpoint.call_args.kwargs
    assert create["VpcEndpointType"] == "Interface"
    assert create["ServiceName"] == "com.amazonaws.us-west-2.cassandra"
    assert create["SubnetIds"] == ["subnet-a", "subnet-b"]
    assert create["SecurityGroupIds"] == ["sg-keyspaces"]
    assert create["PrivateDnsEnabled"] is True
    service = Session().get_service_model("ec2")
    validate_parameters(create, service.operation_model("CreateVpcEndpoint").input_shape)
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    assert ingress["IpPermissions"][0]["FromPort"] == 9142


def test_ensure_neptune_networking_creates_private_subnet_group_and_sg():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    neptune = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-a",
                "AvailabilityZone": "us-west-2a",
                "MapPublicIpOnLaunch": False,
            },
            {
                "SubnetId": "subnet-b",
                "AvailabilityZone": "us-west-2b",
                "MapPublicIpOnLaunch": False,
            },
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-neptune"}

    group, security_groups = ensure_neptune_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        clients=(ec2, neptune, eks),
    )

    assert group == "astrolift-aws-prod-neptune"
    assert security_groups == ["sg-neptune"]
    subnet_call = neptune.create_db_subnet_group.call_args.kwargs
    assert subnet_call["SubnetIds"] == ["subnet-a", "subnet-b"]
    service = Session().get_service_model("neptune")
    validate_parameters(
        subnet_call,
        service.operation_model("CreateDBSubnetGroup").input_shape,
    )
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    assert ingress["IpPermissions"][0]["FromPort"] == 8182


def test_ensure_redshift_networking_creates_provisioned_subnet_group_and_sg():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    ec2 = MagicMock()
    eks = MagicMock()
    redshift = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-abc"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": f"subnet-{suffix}",
                "AvailabilityZone": f"us-west-2{suffix}",
                "MapPublicIpOnLaunch": False,
            }
            for suffix in ("a", "b", "c")
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-redshift"}

    group, subnets, security_groups = ensure_redshift_networking(
        _cluster(slug="aws-prod"),
        region="us-west-2",
        serverless=False,
        clients=(ec2, redshift, eks),
    )

    assert group == "astrolift-aws-prod-redshift"
    assert subnets == ["subnet-a", "subnet-b", "subnet-c"]
    assert security_groups == ["sg-redshift"]
    subnet_call = redshift.create_cluster_subnet_group.call_args.kwargs
    service = Session().get_service_model("redshift")
    validate_parameters(
        subnet_call,
        service.operation_model("CreateClusterSubnetGroup").input_shape,
    )
    ingress = ec2.authorize_security_group_ingress.call_args.kwargs
    assert ingress["IpPermissions"][0]["FromPort"] == 5439


def test_redshift_serverless_networking_requires_three_distinct_subnets():
    with pytest.raises(RuntimeError, match="three distinct subnets"):
        ensure_redshift_networking(
            _cluster(
                {
                    "redshift_subnet_ids": ["subnet-a", "subnet-b"],
                    "redshift_security_group_ids": ["sg-redshift"],
                },
            ),
            region="us-west-2",
            serverless=True,
        )
