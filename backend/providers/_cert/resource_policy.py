"""Explicit resource authorization for uncertified cloud variants (#2464).

These reviewed selections preserve the existing certification boundary without
prices or monetary acknowledgements. Unknown variants fail closed. Capacity
resources require operator authorization; unsupported lifecycles remain refused.
The flag proves neither observed capacity nor provider permissions.
"""

from __future__ import annotations

from dataclasses import dataclass


class UnreviewedVariant(LookupError):
    """No resource lifecycle review exists for this exact provider/kind/variant."""


class CapacityReviewRequired(RuntimeError):
    """The operator has not authorized this capacity resource."""


class NotCertifiable(RuntimeError):
    """This lifecycle remains unsupported for certification."""


@dataclass(frozen=True)
class ResourcePolicy:
    capacity_review_required: bool
    supported: bool


REVIEWED_VARIANTS: dict[str, frozenset[tuple[str, str]]] = {
    "aws": frozenset(
        {
            ("api_gateway", "rest_api"),
            ("api_gateway", "websocket_api"),
            ("cache", "elasticache_memcached"),
            ("cache", "elasticache_serverless_memcached"),
            ("database_proxy", "rds_proxy"),
            ("document_db", "documentdb"),
            ("document_db", "documentdb_serverless_v2"),
            ("encryption_key", "kms"),
            ("event_bus", "eventbridge"),
            ("event_stream", "msk"),
            ("event_stream", "msk_serverless"),
            ("filesystem", "efs"),
            ("filesystem", "fsx_lustre"),
            ("filesystem", "fsx_openzfs"),
            ("filesystem", "fsx_windows"),
            ("graph_db", "neptune"),
            ("graph_db", "neptune_serverless"),
            ("mq", "amazon_mq_activemq"),
            ("mq", "amazon_mq_rabbitmq"),
            ("mssql", "rds_sqlserver_enterprise"),
            ("mssql", "rds_sqlserver_express"),
            ("mssql", "rds_sqlserver_standard"),
            ("mssql", "rds_sqlserver_web"),
            ("mysql", "aurora_mysql"),
            ("mysql", "aurora_mysql_serverless_v2"),
            ("observability", "cloudwatch"),
            ("postgres", "aurora_postgres"),
            ("postgres", "aurora_postgres_serverless_v2"),
            ("private_endpoint", "vpc_endpoint"),
            ("redis", "elasticache_serverless_redis"),
            ("redis", "elasticache_serverless_valkey"),
            ("redis", "elasticache_valkey"),
            ("redis", "memorydb"),
            ("search", "opensearch_serverless"),
            ("sms", "sns_sms"),
            ("stream", "firehose"),
            ("stream", "kinesis"),
            ("topic", "sns_fifo"),
            ("topic", "sns_standard"),
            ("vector_index", "opensearch_serverless_vector"),
            ("warehouse", "redshift"),
            ("warehouse", "redshift_serverless"),
            ("wide_column", "keyspaces"),
            ("workflow_engine", "step_functions_express"),
            ("workflow_engine", "step_functions_standard"),
        }
    ),
    "gcp": frozenset(
        {
            ("cdn", "cloud_cdn"),
            ("email", "smtp"),
            ("encryption_key", "cloud_kms"),
            ("observability", "cloud_operations"),
            ("private_endpoint", "private_service_connect"),
            ("workflow_engine", "workflows"),
        }
    ),
    "azure": frozenset(
        {
            ("api_gateway", "api_management"),
            ("document_db", "cosmos_mongodb"),
            ("document_db", "cosmos_nosql"),
            ("encryption_key", "key_vault_key"),
            ("event_bus", "event_grid"),
            ("event_bus", "event_grid_namespace"),
            ("event_stream", "event_hubs_kafka"),
            ("faas", "azure_functions"),
            ("filesystem", "azure_files"),
            ("filesystem", "azure_files_classic"),
            ("graph_db", "cosmos_gremlin"),
            ("kv_store", "cosmos_table"),
            ("model_endpoint", "azure_foundry"),
            ("mssql", "azure_sql_database"),
            ("mssql", "azure_sql_hyperscale"),
            ("mssql", "azure_sql_managed_instance"),
            ("mssql", "azure_sql_serverless"),
            ("private_endpoint", "private_link"),
            ("redis", "azure_managed_redis"),
            ("stream", "event_hubs"),
            ("wide_column", "cosmos_cassandra"),
        }
    ),
}

CAPACITY_VARIANTS: dict[str, frozenset[tuple[str, str]]] = {
    "aws": frozenset(
        {
            ("cache", "elasticache_memcached"),
            ("cache", "elasticache_serverless_memcached"),
            ("database_proxy", "rds_proxy"),
            ("document_db", "documentdb"),
            ("document_db", "documentdb_serverless_v2"),
            ("event_stream", "msk"),
            ("event_stream", "msk_serverless"),
            ("filesystem", "fsx_lustre"),
            ("filesystem", "fsx_openzfs"),
            ("filesystem", "fsx_windows"),
            ("graph_db", "neptune"),
            ("graph_db", "neptune_serverless"),
            ("mq", "amazon_mq_activemq"),
            ("mq", "amazon_mq_rabbitmq"),
            ("mssql", "rds_sqlserver_express"),
            ("mssql", "rds_sqlserver_standard"),
            ("mssql", "rds_sqlserver_web"),
            ("mysql", "aurora_mysql"),
            ("mysql", "aurora_mysql_serverless_v2"),
            ("postgres", "aurora_postgres"),
            ("postgres", "aurora_postgres_serverless_v2"),
            ("redis", "elasticache_serverless_redis"),
            ("redis", "elasticache_serverless_valkey"),
            ("redis", "elasticache_valkey"),
            ("redis", "memorydb"),
            ("search", "opensearch_serverless"),
            ("vector_index", "opensearch_serverless_vector"),
            ("warehouse", "redshift"),
            ("warehouse", "redshift_serverless"),
        }
    ),
    "gcp": frozenset({}),
    "azure": frozenset(
        {
            ("document_db", "cosmos_mongodb"),
            ("document_db", "cosmos_nosql"),
            ("event_bus", "event_grid_namespace"),
            ("event_stream", "event_hubs_kafka"),
            ("filesystem", "azure_files"),
            ("filesystem", "azure_files_classic"),
            ("graph_db", "cosmos_gremlin"),
            ("kv_store", "cosmos_table"),
            ("mssql", "azure_sql_database"),
            ("mssql", "azure_sql_hyperscale"),
            ("mssql", "azure_sql_serverless"),
            ("redis", "azure_managed_redis"),
            ("stream", "event_hubs"),
            ("wide_column", "cosmos_cassandra"),
        }
    ),
}

UNSUPPORTED_VARIANTS: dict[str, frozenset[tuple[str, str]]] = {
    "aws": frozenset(
        {
            ("mssql", "rds_sqlserver_enterprise"),
        }
    ),
    "gcp": frozenset({}),
    "azure": frozenset(
        {
            ("api_gateway", "api_management"),
            ("mssql", "azure_sql_managed_instance"),
        }
    ),
}


def classify(provider: str, kind: str, variant: str) -> ResourcePolicy:
    if provider not in REVIEWED_VARIANTS:
        raise UnreviewedVariant(f"no resource review for provider {provider!r}")
    key = (kind, variant)
    if key not in REVIEWED_VARIANTS[provider]:
        raise UnreviewedVariant(f"{provider} {kind}:{variant} has no resource review; not assumed safe")
    return ResourcePolicy(key in CAPACITY_VARIANTS[provider], key not in UNSUPPORTED_VARIANTS[provider])


def check_may_run(
    provider: str, kind: str, variant: str, *, capacity_resources_authorized: bool = False
) -> ResourcePolicy:
    policy = classify(provider, kind, variant)
    if not policy.supported:
        raise NotCertifiable(f"{provider} {kind}:{variant} has an unsupported certification lifecycle")
    if policy.capacity_review_required and not capacity_resources_authorized:
        raise CapacityReviewRequired(
            f"{provider} {kind}:{variant} requires explicit operator resource authorization "
            "with --allow-capacity-resources; this does not prove capacity or provider permissions"
        )
    return policy
