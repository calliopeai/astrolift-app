"""Portable managed-service kinds shared by every provider.

``kind`` is the cloud-neutral contract exposed through GraphQL, MCP, the CLI,
and ``astrolift.toml``.  ``variant`` is the provider-specific implementation
(``postgres/rds``, ``postgres/cloudsql``, and so on).  New variants may be
added without changing clients; a genuinely new resource shape must land in
this catalogue before it can appear in the availability matrix.

This file deliberately keeps the kind definitions in code (typed,
immutable) rather than data; that lets the matrix check (#26)
verify shape and the binding validator (#18) call out missing
envs at validate time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedServiceKind:
    name: str
    """The kind identifier (e.g. 'postgres', 'queue', 'vector_db')."""

    description: str
    binding_envs_required: tuple[str, ...] = ()
    """Env-var keys every variant of this kind MUST set. Example:
    every postgres variant must produce POSTGRES_HOST + POSTGRES_PORT
    + POSTGRES_DB + POSTGRES_USER + POSTGRES_PASSWORD."""

    binding_envs_optional: tuple[str, ...] = ()
    iam_action_pattern: str = ""
    """Free-text description of expected IAM grant. Used in docs +
    least-privilege audit."""

    snapshot_supported: bool = False
    cross_region_replicate_supported: bool = False


@dataclass(frozen=True)
class KindCatalog:
    kinds: tuple[ManagedServiceKind, ...] = ()

    def get(self, name: str) -> ManagedServiceKind | None:
        for kind in self.kinds:
            if kind.name == name:
                return kind
        return None


# Canonical kind catalog. Keep names in lockstep with
# ``astrolift_services.ManagedService.Kind``. Binding variables describe the
# portable envelope; drivers may additionally emit provider-native variables.
KINDS = KindCatalog(
    kinds=(
        ManagedServiceKind(
            name="postgres",
            description="Relational database (Postgres-compatible)",
            binding_envs_required=(
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "POSTGRES_DB",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            ),
            binding_envs_optional=("POSTGRES_SSLMODE",),
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="mysql",
            description="Relational database (MySQL/MariaDB-compatible)",
            binding_envs_required=(
                "MYSQL_HOST",
                "MYSQL_PORT",
                "MYSQL_DB",
                "MYSQL_USER",
                "MYSQL_PASSWORD",
            ),
            binding_envs_optional=("MYSQL_SSL_CA",),
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="mssql",
            description="Microsoft SQL Server-compatible relational database",
            binding_envs_required=(
                "MSSQL_HOST",
                "MSSQL_PORT",
                "MSSQL_DB",
                "MSSQL_USER",
                "MSSQL_PASSWORD",
            ),
            binding_envs_optional=("MSSQL_ENCRYPT", "DATABASE_URL"),
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="redis",
            description="Key-value store (Redis-compatible)",
            binding_envs_required=("REDIS_HOST", "REDIS_PORT"),
            binding_envs_optional=(
                "REDIS_USER",
                "REDIS_PASSWORD",
                "REDIS_TLS",
                "REDIS_URL",
                "REDIS_AUTH_MODE",
                "REDIS_RESOURCE_ARN",
            ),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="cache",
            description="Protocol-neutral ephemeral application cache",
            binding_envs_required=("CACHE_HOST", "CACHE_PORT", "CACHE_PROTOCOL"),
            binding_envs_optional=(
                "CACHE_NODES",
                "CACHE_TLS",
                "CACHE_RESOURCE_ARN",
            ),
        ),
        ManagedServiceKind(
            name="object_store",
            description="Blob / object storage (bucket-shaped)",
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="queue",
            description="Message queue (FIFO or best-effort)",
            binding_envs_required=("QUEUE_URL",),
            binding_envs_optional=("QUEUE_NAME", "QUEUE_ARN", "QUEUE_REGION"),
        ),
        ManagedServiceKind(
            name="topic",
            description="Publish/subscribe topic with fan-out subscriptions",
            binding_envs_required=("TOPIC_ARN_OR_ID", "TOPIC_NAME"),
            binding_envs_optional=("TOPIC_REGION",),
        ),
        ManagedServiceKind(
            name="event_stream",
            description=("Append-only partitioned log (Kafka-style)"),
            binding_envs_required=("EVENT_STREAM_BROKERS",),
            binding_envs_optional=(
                "EVENT_STREAM_USERNAME",
                "EVENT_STREAM_PASSWORD",
                "EVENT_STREAM_TLS",
            ),
            snapshot_supported=False,
        ),
        ManagedServiceKind(
            name="document_db",
            description=("Document database (MongoDB / DocumentDB / Cosmos Mongo API)"),
            binding_envs_required=(
                "DOCDB_URI",
                "DOCDB_DB",
                "DOCDB_USER",
                "DOCDB_PASSWORD",
            ),
            binding_envs_optional=("DOCDB_TLS", "DOCDB_RESOURCE_ARN"),
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="kv_store",
            description=("Key-value store with predictable single-key latency (DynamoDB / Cosmos Table / Bigtable)"),
            binding_envs_required=("KV_TABLE_NAME", "KV_REGION"),
            binding_envs_optional=("KV_ENDPOINT_OVERRIDE",),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="vector_index",
            description=("Vector similarity search store (pgvector, Weaviate, Qdrant, Pinecone-compatible)"),
            binding_envs_required=(
                "VECTOR_ENDPOINT",
                "VECTOR_INDEX_NAME",
            ),
            binding_envs_optional=("VECTOR_API_KEY", "VECTOR_NAMESPACE"),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="search",
            description=("Full-text search index (Elasticsearch / OpenSearch / Typesense / Meilisearch)"),
            binding_envs_required=("SEARCH_ENDPOINT",),
            binding_envs_optional=("SEARCH_USER", "SEARCH_PASSWORD", "SEARCH_INDEX_PREFIX"),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="time_series",
            description=("Time-series database (Prometheus, Thanos, Mimir, InfluxDB, TimescaleDB, Managed Prometheus)"),
            binding_envs_required=("TS_ENDPOINT",),
            binding_envs_optional=(
                "TS_DB",
                "TS_USER",
                "TS_PASSWORD",
                "TS_TOKEN",
                "TS_ORG",
            ),
            snapshot_supported=False,
        ),
        ManagedServiceKind(
            name="filesystem",
            description=("Network-attached shared filesystem (NFS / EFS / Filestore / Azure Files)"),
            binding_envs_required=(
                "FILESYSTEM_HANDLE",
                "FILESYSTEM_MOUNT_PATH",
            ),
            binding_envs_optional=("FILESYSTEM_TLS",),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="email",
            description=("Transactional email sender (SES, SendGrid, Postmark, Mailgun, Resend)"),
            binding_envs_required=("EMAIL_PROVIDER",),
            binding_envs_optional=(
                "EMAIL_API_KEY",
                "EMAIL_FROM_ADDRESS",
                "EMAIL_REGION",
            ),
            snapshot_supported=False,
        ),
        ManagedServiceKind(
            name="model_endpoint",
            description=("Hosted ML model endpoint (Azure OpenAI, Bedrock, Vertex AI, Together, OpenAI)"),
            binding_envs_required=("MODEL_ENDPOINT_URL",),
            binding_envs_optional=(
                "MODEL_API_KEY",
                "MODEL_DEPLOYMENT_NAME",
                "MODEL_REGION",
            ),
            snapshot_supported=False,
        ),
        ManagedServiceKind(
            name="mq",
            description="Managed AMQP/JMS message broker",
            binding_envs_required=("MQ_ENDPOINT",),
            binding_envs_optional=("MQ_USERNAME", "MQ_PASSWORD", "MQ_PROTOCOL"),
            snapshot_supported=False,
        ),
        ManagedServiceKind(
            name="nfs",
            description="Legacy NFS-compatible shared filesystem alias",
            binding_envs_required=("NFS_SERVER", "NFS_PATH"),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="cdn",
            description="Content-delivery network and edge cache",
            binding_envs_required=("CDN_URL",),
            binding_envs_optional=("CDN_DISTRIBUTION_ID", "CDN_DOMAIN"),
        ),
        ManagedServiceKind(
            name="faas",
            description="Cloud function / functions-as-a-service runtime",
            binding_envs_required=("FUNCTION_NAME",),
            binding_envs_optional=("FUNCTION_ARN", "FUNCTION_URL", "FUNCTION_REGION"),
        ),
        ManagedServiceKind(
            name="api_gateway",
            description="Managed HTTP, REST, or WebSocket API gateway",
            binding_envs_required=("API_GATEWAY_URL",),
            binding_envs_optional=("API_GATEWAY_ID", "API_GATEWAY_STAGE"),
        ),
        ManagedServiceKind(
            name="sms",
            description="Transactional SMS delivery capability",
            binding_envs_required=("SMS_PROVIDER",),
            binding_envs_optional=("SMS_SENDER_ID", "SMS_REGION"),
        ),
        ManagedServiceKind(
            name="database_proxy",
            description="Managed database connection pool and proxy",
            binding_envs_required=("DATABASE_PROXY_HOST", "DATABASE_PROXY_PORT"),
            binding_envs_optional=(
                "DATABASE_PROXY_ARN",
                "DATABASE_PROXY_TLS",
                "DATABASE_PROXY_AUTH_MODE",
                "DATABASE_PROXY_CREDENTIALS",
                "DATABASE_PROXY_CREDENTIALS_REF",
                "DATABASE_PROXY_DB_USER",
            ),
        ),
        ManagedServiceKind(
            name="graph_db",
            description="Property-graph or RDF graph database",
            binding_envs_required=("GRAPH_DB_URL",),
            binding_envs_optional=("GRAPH_DB_USER", "GRAPH_DB_PASSWORD", "GRAPH_DB_PROTOCOL"),
            snapshot_supported=True,
            cross_region_replicate_supported=True,
        ),
        ManagedServiceKind(
            name="wide_column",
            description="Wide-column database (Cassandra/Bigtable-compatible)",
            binding_envs_required=("WIDE_COLUMN_ENDPOINT",),
            binding_envs_optional=(
                "WIDE_COLUMN_KEYSPACE",
                "WIDE_COLUMN_TABLE",
                "WIDE_COLUMN_REGION",
                "WIDE_COLUMN_PORT",
                "WIDE_COLUMN_AUTH_MODE",
                "WIDE_COLUMN_RESOURCE_ARN",
            ),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="warehouse",
            description="Analytical data warehouse",
            binding_envs_required=("WAREHOUSE_ENDPOINT",),
            binding_envs_optional=(
                "WAREHOUSE_DATABASE",
                "WAREHOUSE_USER",
                "WAREHOUSE_PASSWORD",
            ),
            snapshot_supported=True,
        ),
        ManagedServiceKind(
            name="event_bus",
            description="Event routing bus with rules, filters, and targets",
            binding_envs_required=("EVENT_BUS_NAME",),
            binding_envs_optional=("EVENT_BUS_ARN", "EVENT_BUS_REGION"),
        ),
        ManagedServiceKind(
            name="stream",
            description="Provider-native real-time data or delivery stream",
            binding_envs_required=("STREAM_NAME",),
            binding_envs_optional=("STREAM_ARN", "STREAM_ENDPOINT", "STREAM_REGION"),
        ),
        ManagedServiceKind(
            name="workflow_engine",
            description="Managed state-machine and workflow orchestration service",
            binding_envs_required=("WORKFLOW_ENGINE_ID",),
            binding_envs_optional=("WORKFLOW_ENGINE_ARN", "WORKFLOW_ENGINE_REGION"),
        ),
        ManagedServiceKind(
            name="encryption_key",
            description="Customer-managed encryption/signing key",
            binding_envs_required=("ENCRYPTION_KEY_ID",),
            binding_envs_optional=("ENCRYPTION_KEY_ARN", "ENCRYPTION_KEY_ALIAS"),
        ),
        ManagedServiceKind(
            name="private_endpoint",
            description="Private network endpoint to a cloud or managed service",
            binding_envs_required=("PRIVATE_ENDPOINT_ID",),
            binding_envs_optional=("PRIVATE_ENDPOINT_DNS", "PRIVATE_ENDPOINT_IPS"),
        ),
        ManagedServiceKind(
            name="observability",
            description="Managed logs, metrics, alarms, and dashboards",
            binding_envs_required=("OBSERVABILITY_PROVIDER",),
            binding_envs_optional=("LOG_GROUP", "METRICS_ENDPOINT", "DASHBOARD_URL"),
        ),
    )
)


def validate_binding_envs(
    *,
    kind: str,
    emitted_envs: list[str],
    catalog: KindCatalog = KINDS,
) -> list[str]:
    """Returns the list of MISSING required envs for this kind.
    Empty list means the binding meets the kind contract."""
    record = catalog.get(kind)
    if record is None:
        return []
    return [env for env in record.binding_envs_required if env not in emitted_envs]
