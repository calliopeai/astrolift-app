"""Managed-service plugin SDK -- adding new kinds (#16).

The set of kinds shipped today (postgres, redis, object_store,
queue) covers the MVP. New kinds (vector_db, search, kafka, etc.)
land via this SDK — a plugin declares the kind's required binding
envs + runtime expectations, and the matrix-check picks up new
entries on the next CI run.

This file deliberately keeps the kind definitions in code (typed,
immutable) rather than data; that lets the matrix check (#26)
verify shape and the binding validator (#18) call out missing
envs at validate time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


# Canonical kind catalog. New kinds land here at the same time as
# their first variant's plugin registration.
KINDS = KindCatalog(kinds=(
    ManagedServiceKind(
        name="postgres",
        description="Relational database (Postgres-compatible)",
        binding_envs_required=(
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
        ),
        binding_envs_optional=("POSTGRES_SSLMODE",),
        snapshot_supported=True,
        cross_region_replicate_supported=True,
    ),
    ManagedServiceKind(
        name="mysql",
        description="Relational database (MySQL/MariaDB-compatible)",
        binding_envs_required=(
            "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DB",
            "MYSQL_USER", "MYSQL_PASSWORD",
        ),
        binding_envs_optional=("MYSQL_SSL_CA",),
        snapshot_supported=True,
        cross_region_replicate_supported=True,
    ),
    ManagedServiceKind(
        name="redis",
        description="Key-value store (Redis-compatible)",
        binding_envs_required=("REDIS_HOST", "REDIS_PORT"),
        binding_envs_optional=("REDIS_PASSWORD", "REDIS_TLS"),
        snapshot_supported=True,
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
    ),
    ManagedServiceKind(
        name="event_stream",
        description=(
            "Append-only partitioned log (Kafka-style)"
        ),
        binding_envs_required=("EVENT_STREAM_BROKERS",),
        binding_envs_optional=(
            "EVENT_STREAM_USERNAME", "EVENT_STREAM_PASSWORD",
            "EVENT_STREAM_TLS",
        ),
        snapshot_supported=False,
    ),
    ManagedServiceKind(
        name="document_db",
        description=(
            "Document database (MongoDB / DocumentDB / Cosmos "
            "Mongo API)"
        ),
        binding_envs_required=(
            "DOCDB_URI", "DOCDB_DB", "DOCDB_USER", "DOCDB_PASSWORD",
        ),
        snapshot_supported=True,
        cross_region_replicate_supported=True,
    ),
    ManagedServiceKind(
        name="key_value",
        description=(
            "Key-value store with predictable single-key latency "
            "(DynamoDB / Cosmos Table / Bigtable)"
        ),
        binding_envs_required=("KV_TABLE_NAME", "KV_REGION"),
        binding_envs_optional=("KV_ENDPOINT_OVERRIDE",),
        snapshot_supported=True,
    ),
    ManagedServiceKind(
        name="vector_db",
        description=(
            "Vector similarity search store (pgvector, Weaviate, "
            "Qdrant, Pinecone-compatible)"
        ),
        binding_envs_required=(
            "VECTOR_DB_URL", "VECTOR_DB_API_KEY",
        ),
        binding_envs_optional=("VECTOR_DB_INDEX",),
        snapshot_supported=True,
    ),
    ManagedServiceKind(
        name="search",
        description=(
            "Full-text search index (Elasticsearch / OpenSearch / "
            "Typesense / Meilisearch)"
        ),
        binding_envs_required=(
            "SEARCH_URL", "SEARCH_API_KEY",
        ),
        binding_envs_optional=("SEARCH_INDEX_PREFIX",),
        snapshot_supported=True,
    ),
    ManagedServiceKind(
        name="time_series",
        description=(
            "Time-series database (Prometheus, Thanos, Mimir, "
            "InfluxDB, TimescaleDB, Managed Prometheus)"
        ),
        binding_envs_required=(
            "TIME_SERIES_URL",
        ),
        binding_envs_optional=(
            "TIME_SERIES_USERNAME", "TIME_SERIES_PASSWORD",
            "TIME_SERIES_ORG", "TIME_SERIES_BUCKET",
        ),
        snapshot_supported=False,
    ),
    ManagedServiceKind(
        name="filesystem",
        description=(
            "Network-attached shared filesystem (NFS / EFS / "
            "Filestore / Azure Files)"
        ),
        binding_envs_required=(
            "FILESYSTEM_HANDLE", "FILESYSTEM_MOUNT_PATH",
        ),
        binding_envs_optional=("FILESYSTEM_TLS",),
        snapshot_supported=True,
    ),
    ManagedServiceKind(
        name="email",
        description=(
            "Transactional email sender (SES, SendGrid, Postmark, "
            "Mailgun, Resend)"
        ),
        binding_envs_required=(
            "EMAIL_PROVIDER", "EMAIL_API_KEY",
        ),
        binding_envs_optional=(
            "EMAIL_FROM_ADDRESS", "EMAIL_REGION",
        ),
        snapshot_supported=False,
    ),
    ManagedServiceKind(
        name="model_endpoint",
        description=(
            "Hosted ML model endpoint (Azure OpenAI, Bedrock, "
            "Vertex AI, Together, OpenAI)"
        ),
        binding_envs_required=(
            "MODEL_ENDPOINT_URL", "MODEL_API_KEY",
        ),
        binding_envs_optional=(
            "MODEL_DEPLOYMENT_NAME", "MODEL_REGION",
        ),
        snapshot_supported=False,
    ),
))


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
    return [
        env for env in record.binding_envs_required
        if env not in emitted_envs
    ]
