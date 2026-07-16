"""Service Availability Matrix (#21).

The availability matrix is a static catalog that describes:
- which driver roles are required for any cluster binding
  (cluster, ingress, dns, tls, secrets, identity, registry)
- which managed-service kinds + variants exist + which providers
  ship them
- whether each entry is generally-available, preview, or deprecated

The matrix lives next to the plugin entry-point registry. It is the
source of truth for:
- capability negotiation (#18) — at cluster bind time, the control
  plane checks the desired plugin against the matrix to confirm
  every required role + every requested variant is provided
- CI verification (#26) — a green-the-build check confirms that
  every PLUGIN.drivers / PLUGIN.managed_service_drivers entry has
  a corresponding matrix entry with the same status

The matrix is intentionally code-defined (not YAML / JSON). That
keeps cross-driver invariants enforceable at import time + lets
type-checkers verify variant strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["ga", "preview", "deprecated", "experimental"]


@dataclass(frozen=True)
class DriverEntry:
    """A driver role provided by a plugin."""

    role: str
    plugin_id: str
    variant: str | None = None
    status: Status = "ga"
    notes: str = ""


@dataclass(frozen=True)
class ManagedServiceEntry:
    """A managed-service (kind, variant) entry shipped by a plugin."""

    kind: str
    variant: str
    plugin_id: str
    status: Status = "ga"
    description: str = ""
    binding_envs: tuple[str, ...] = ()
    """Stable env-var names the binding emits (for docs + tests)."""


# Driver roles every cluster MUST resolve at bind time. If the
# bound plugin doesn't ship one of these (and no delegation
# composition is registered), cluster registration fails.
REQUIRED_ROLES: tuple[str, ...] = (
    "cluster",
    "ingress",
    "dns",
    "tls",
    "secrets",
    "identity",
    "registry",
)


# Optional roles — drivers that the control plane can use when
# present but doesn't require for a working tenant cluster.
OPTIONAL_ROLES: tuple[str, ...] = (
    "log_stream",
    "metrics",
    "trace",
    "build",
    "object_store",
    "event",
)


@dataclass(frozen=True)
class AvailabilityMatrix:
    drivers: tuple[DriverEntry, ...] = ()
    managed_services: tuple[ManagedServiceEntry, ...] = ()

    def drivers_for_role(self, role: str) -> list[DriverEntry]:
        return [d for d in self.drivers if d.role == role]

    def plugins_for_kind(self, kind: str) -> list[ManagedServiceEntry]:
        return [m for m in self.managed_services if m.kind == kind]

    def variants_for_kind(self, kind: str) -> list[str]:
        return sorted({m.variant for m in self.managed_services if m.kind == kind})

    def has_role(self, *, plugin_id: str, role: str) -> bool:
        return any(d.plugin_id == plugin_id and d.role == role for d in self.drivers)

    def has_managed(
        self,
        *,
        plugin_id: str,
        kind: str,
        variant: str,
    ) -> bool:
        return any(m.plugin_id == plugin_id and m.kind == kind and m.variant == variant for m in self.managed_services)


# The canonical matrix. New driver / managed-service entries land
# here at the same time as their PLUGIN registration. CI (#26)
# enforces consistency.
MATRIX = AvailabilityMatrix(
    drivers=(
        # AWS plugin
        DriverEntry(role="cluster", plugin_id="aws"),
        DriverEntry(role="ingress", plugin_id="aws", variant="alb"),
        DriverEntry(role="dns", plugin_id="aws", variant="route53"),
        DriverEntry(role="tls", plugin_id="aws", variant="acm"),
        DriverEntry(role="secrets", plugin_id="aws", variant="secrets_manager"),
        DriverEntry(role="identity", plugin_id="aws", variant="irsa"),
        DriverEntry(role="registry", plugin_id="aws", variant="ecr"),
        DriverEntry(role="notification", plugin_id="aws", variant="sns"),
        # GCP plugin
        DriverEntry(role="cluster", plugin_id="gcp"),
        DriverEntry(role="ingress", plugin_id="gcp", variant="gce_ingress"),
        DriverEntry(role="ingress", plugin_id="gcp", variant="gateway_api"),
        DriverEntry(role="dns", plugin_id="gcp", variant="cloud_dns"),
        DriverEntry(role="tls", plugin_id="gcp", variant="gcp_managed_cert"),
        DriverEntry(role="secrets", plugin_id="gcp", variant="secret_manager"),
        DriverEntry(role="identity", plugin_id="gcp", variant="workload_identity"),
        DriverEntry(role="registry", plugin_id="gcp", variant="artifact_registry"),
        DriverEntry(role="notification", plugin_id="gcp", variant="fcm"),
        # Azure plugin
        DriverEntry(role="cluster", plugin_id="azure"),
        DriverEntry(role="ingress", plugin_id="azure", variant="agic"),
        DriverEntry(role="ingress", plugin_id="azure", variant="gateway_api"),
        DriverEntry(role="dns", plugin_id="azure", variant="azure_dns"),
        DriverEntry(role="tls", plugin_id="azure", variant="azure_managed_cert"),
        DriverEntry(role="tls", plugin_id="azure", variant="akv_referenced"),
        DriverEntry(role="secrets", plugin_id="azure", variant="key_vault"),
        DriverEntry(
            role="identity",
            plugin_id="azure",
            variant="federated_credentials",
        ),
        DriverEntry(role="registry", plugin_id="azure", variant="acr"),
        DriverEntry(role="notification", plugin_id="azure", variant="notification_hubs"),
        # k8s_native plugin
        DriverEntry(role="cluster", plugin_id="k8s_native"),
        DriverEntry(role="ingress", plugin_id="k8s_native", variant="nginx_ingress"),
        DriverEntry(role="ingress", plugin_id="k8s_native", variant="gateway_api"),
        DriverEntry(role="ingress", plugin_id="k8s_native", variant="traefik"),
        DriverEntry(role="ingress", plugin_id="k8s_native", variant="kong"),
        DriverEntry(role="ingress", plugin_id="k8s_native", variant="istio_gateway"),
        DriverEntry(role="dns", plugin_id="k8s_native", variant="external_dns"),
        DriverEntry(role="tls", plugin_id="k8s_native", variant="cert_manager"),
        DriverEntry(role="secrets", plugin_id="k8s_native", variant="vault"),
        DriverEntry(
            role="identity",
            plugin_id="k8s_native",
            variant="projected_sa_token",
        ),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="generic_oci"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="quay"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="dockerhub"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="ghcr"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="harbor"),
        DriverEntry(role="notification", plugin_id="k8s_native", variant="webhook_smtp"),
    ),
    managed_services=(
        # AWS — MVP set; extended catalog tracked in #79
        ManagedServiceEntry(
            kind="object_store",
            variant="s3",
            plugin_id="aws",
            description="Amazon S3 with versioning + public-access block",
            binding_envs=("S3_BUCKET_NAME", "S3_REGION"),
        ),
        ManagedServiceEntry(
            kind="queue",
            variant="sqs",
            plugin_id="aws",
            description="Amazon SQS standard or FIFO queue",
            binding_envs=("SQS_QUEUE_URL", "SQS_QUEUE_NAME", "AWS_REGION"),
        ),
        # AWS — extended catalog (#79): entries mirror aws/plugin.py
        ManagedServiceEntry(
            kind="postgres",
            variant="rds",
            plugin_id="aws",
            description="Amazon RDS for PostgreSQL",
        ),
        ManagedServiceEntry(
            kind="mysql",
            variant="rds_mysql",
            plugin_id="aws",
            description="Amazon RDS for MySQL",
        ),
        ManagedServiceEntry(
            kind="redis",
            variant="elasticache",
            plugin_id="aws",
            description="Amazon ElastiCache for Redis",
        ),
        ManagedServiceEntry(
            kind="kv_store",
            variant="dynamodb",
            plugin_id="aws",
            description="Amazon DynamoDB table",
        ),
        ManagedServiceEntry(
            kind="cdn",
            variant="cloudfront",
            plugin_id="aws",
            description="Amazon CloudFront distribution (static-site topology)",
        ),
        ManagedServiceEntry(
            kind="faas",
            variant="lambda",
            plugin_id="aws",
            description="AWS Lambda function",
        ),
        ManagedServiceEntry(
            kind="api_gateway",
            variant="http_api",
            plugin_id="aws",
            description="Amazon API Gateway HTTP API",
        ),
        ManagedServiceEntry(
            kind="search",
            variant="opensearch",
            plugin_id="aws",
            description="Amazon OpenSearch full-text domain",
        ),
        ManagedServiceEntry(
            kind="vector_index",
            variant="opensearch_vector",
            plugin_id="aws",
            description="Amazon OpenSearch k-NN vector index",
        ),
        ManagedServiceEntry(
            kind="time_series",
            variant="timestream",
            plugin_id="aws",
            description="Amazon Timestream database",
        ),
        ManagedServiceEntry(
            kind="email",
            variant="ses",
            plugin_id="aws",
            description="Amazon SES identity + configuration set",
        ),
        ManagedServiceEntry(
            kind="model_endpoint",
            variant="bedrock",
            plugin_id="aws",
            description="Amazon Bedrock model endpoint",
        ),
        # GCP
        ManagedServiceEntry(
            kind="object_store",
            variant="gcs",
            plugin_id="gcp",
            description="Google Cloud Storage bucket",
            binding_envs=("GCS_BUCKET_NAME", "GCS_BUCKET_URI", "GCP_PROJECT_ID"),
        ),
        ManagedServiceEntry(
            kind="queue",
            variant="pubsub",
            plugin_id="gcp",
            description="Pub/Sub topic + subscription pair",
            binding_envs=(
                "PUBSUB_TOPIC",
                "PUBSUB_SUBSCRIPTION",
                "GCP_PROJECT_ID",
            ),
        ),
        ManagedServiceEntry(
            kind="postgres",
            variant="cloudsql",
            plugin_id="gcp",
            description="Cloud SQL for PostgreSQL",
        ),
        ManagedServiceEntry(
            kind="mysql",
            variant="cloudsql",
            plugin_id="gcp",
            description="Cloud SQL for MySQL",
        ),
        ManagedServiceEntry(
            kind="redis",
            variant="memorystore",
            plugin_id="gcp",
            description="Memorystore for Redis",
        ),
        ManagedServiceEntry(
            kind="kv_store",
            variant="bigtable",
            plugin_id="gcp",
            description="Cloud Bigtable instance",
        ),
        ManagedServiceEntry(
            kind="search",
            variant="gcp_elastic_cloud",
            plugin_id="gcp",
            status="planned",
            description="Elastic Cloud on GCP (stub driver)",
        ),
        ManagedServiceEntry(
            kind="vector_index",
            variant="vertex_matching_engine",
            plugin_id="gcp",
            description="Vertex AI Matching Engine index",
        ),
        ManagedServiceEntry(
            kind="time_series",
            variant="gcp_managed_prometheus",
            plugin_id="gcp",
            description="Google Cloud Managed Service for Prometheus",
        ),
        ManagedServiceEntry(
            kind="email",
            variant="gcp_thirdparty",
            plugin_id="gcp",
            status="planned",
            description="Third-party email on GCP (stub driver)",
        ),
        ManagedServiceEntry(
            kind="model_endpoint",
            variant="vertex_ai",
            plugin_id="gcp",
            description="Vertex AI model endpoint",
        ),
        # Azure
        ManagedServiceEntry(
            kind="object_store",
            variant="blob",
            plugin_id="azure",
            description="Azure Blob Storage container",
            binding_envs=(
                "AZURE_STORAGE_ACCOUNT",
                "AZURE_BLOB_CONTAINER",
                "AZURE_BLOB_ENDPOINT",
            ),
        ),
        ManagedServiceEntry(
            kind="queue",
            variant="servicebus",
            plugin_id="azure",
            description="Service Bus queue with dead-lettering",
            binding_envs=(
                "SERVICEBUS_NAMESPACE",
                "SERVICEBUS_QUEUE",
                "SERVICEBUS_ENDPOINT",
            ),
        ),
        ManagedServiceEntry(
            kind="postgres",
            variant="azure_pg_flex",
            plugin_id="azure",
            description="Azure Database for PostgreSQL Flexible Server",
        ),
        ManagedServiceEntry(
            kind="mysql",
            variant="azure_mysql_flex",
            plugin_id="azure",
            description="Azure Database for MySQL Flexible Server",
        ),
        ManagedServiceEntry(
            kind="redis",
            variant="azure_cache_redis",
            plugin_id="azure",
            description="Azure Cache for Redis",
        ),
        ManagedServiceEntry(
            kind="object_store",
            variant="azure_blob",
            plugin_id="azure",
            description="Azure Blob Storage container (managed-service catalog id)",
        ),
        ManagedServiceEntry(
            kind="queue",
            variant="azure_servicebus",
            plugin_id="azure",
            description="Azure Service Bus queue (managed-service catalog id)",
        ),
        ManagedServiceEntry(
            kind="kv_store",
            variant="cosmos",
            plugin_id="azure",
            description="Azure Cosmos DB (NoSQL) container",
        ),
        ManagedServiceEntry(
            kind="search",
            variant="azure_ai_search_fulltext",
            plugin_id="azure",
            description="Azure AI Search full-text index",
        ),
        ManagedServiceEntry(
            kind="vector_index",
            variant="azure_ai_search_vector",
            plugin_id="azure",
            description="Azure AI Search vector index",
        ),
        ManagedServiceEntry(
            kind="time_series",
            variant="azure_monitor_prometheus",
            plugin_id="azure",
            description="Azure Monitor managed Prometheus",
        ),
        ManagedServiceEntry(
            kind="email",
            variant="azure_acs",
            plugin_id="azure",
            description="Azure Communication Services email",
        ),
        ManagedServiceEntry(
            kind="model_endpoint",
            variant="azure_openai",
            plugin_id="azure",
            description="Azure OpenAI model deployment",
        ),
        # k8s_native (operator-backed)
        ManagedServiceEntry(
            kind="postgres",
            variant="cnpg",
            plugin_id="k8s_native",
            description="CloudNativePG-backed Postgres cluster",
            binding_envs=(
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "POSTGRES_DB",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="redis",
            variant="operator",
            plugin_id="k8s_native",
            description="Bitnami Redis operator with primary + replicas",
            binding_envs=("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD"),
        ),
        ManagedServiceEntry(
            kind="mysql",
            variant="operator",
            plugin_id="k8s_native",
            description=("Percona XtraDB Cluster (default) / MariaDB / Oracle MySQL operator-backed cluster"),
            binding_envs=(
                "MYSQL_HOST",
                "MYSQL_PORT",
                "MYSQL_DB",
                "MYSQL_USER",
                "MYSQL_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="document_db",
            variant="mongodb_operator",
            plugin_id="k8s_native",
            description="Percona Server for MongoDB operator",
            binding_envs=(
                "DOCDB_URI",
                "DOCDB_DB",
                "DOCDB_USER",
                "DOCDB_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="event_stream",
            variant="kafka_strimzi",
            plugin_id="k8s_native",
            description="Strimzi-managed Kafka cluster (KRaft)",
            binding_envs=(
                "EVENT_STREAM_BROKERS",
                "EVENT_STREAM_USERNAME",
                "EVENT_STREAM_PASSWORD",
                "EVENT_STREAM_TLS",
            ),
        ),
        ManagedServiceEntry(
            kind="event_stream",
            variant="nats",
            plugin_id="k8s_native",
            description="NATS StatefulSet (with optional JetStream)",
            binding_envs=("EVENT_STREAM_BROKERS", "EVENT_STREAM_TLS"),
        ),
        ManagedServiceEntry(
            kind="queue",
            variant="rabbitmq_operator",
            plugin_id="k8s_native",
            description="RabbitMQ Cluster Operator-backed cluster",
            binding_envs=(
                "RABBITMQ_HOST",
                "RABBITMQ_PORT",
                "RABBITMQ_USER",
                "RABBITMQ_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="filesystem",
            variant="nfs_csi",
            plugin_id="k8s_native",
            description="NFS CSI driver-backed RWX PVC",
            binding_envs=(
                "FILESYSTEM_HANDLE",
                "FILESYSTEM_MOUNT_PATH",
                "FILESYSTEM_TLS",
            ),
        ),
    ),
)


__all__ = [
    "MATRIX",
    "OPTIONAL_ROLES",
    "REQUIRED_ROLES",
    "AvailabilityMatrix",
    "DriverEntry",
    "ManagedServiceEntry",
    "Status",
]
