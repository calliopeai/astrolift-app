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

from dataclasses import dataclass, field
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
        return any(
            d.plugin_id == plugin_id and d.role == role
            for d in self.drivers
        )

    def has_managed(
        self, *, plugin_id: str, kind: str, variant: str,
    ) -> bool:
        return any(
            m.plugin_id == plugin_id and m.kind == kind
            and m.variant == variant
            for m in self.managed_services
        )


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
        # GCP plugin
        DriverEntry(role="cluster", plugin_id="gcp"),
        DriverEntry(role="ingress", plugin_id="gcp", variant="gce_ingress"),
        DriverEntry(role="ingress", plugin_id="gcp", variant="gateway_api"),
        DriverEntry(role="dns", plugin_id="gcp", variant="cloud_dns"),
        DriverEntry(role="tls", plugin_id="gcp", variant="gcp_managed_cert"),
        DriverEntry(role="secrets", plugin_id="gcp", variant="secret_manager"),
        DriverEntry(role="identity", plugin_id="gcp", variant="workload_identity"),
        DriverEntry(role="registry", plugin_id="gcp", variant="artifact_registry"),
        # Azure plugin
        DriverEntry(role="cluster", plugin_id="azure"),
        DriverEntry(role="ingress", plugin_id="azure", variant="agic"),
        DriverEntry(role="ingress", plugin_id="azure", variant="gateway_api"),
        DriverEntry(role="dns", plugin_id="azure", variant="azure_dns"),
        DriverEntry(role="tls", plugin_id="azure", variant="azure_managed_cert"),
        DriverEntry(role="tls", plugin_id="azure", variant="akv_referenced"),
        DriverEntry(role="secrets", plugin_id="azure", variant="key_vault"),
        DriverEntry(
            role="identity", plugin_id="azure",
            variant="federated_credentials",
        ),
        DriverEntry(role="registry", plugin_id="azure", variant="acr"),
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
            role="identity", plugin_id="k8s_native",
            variant="projected_sa_token",
        ),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="generic_oci"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="quay"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="dockerhub"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="ghcr"),
        DriverEntry(role="registry", plugin_id="k8s_native", variant="harbor"),
    ),
    managed_services=(
        # AWS — MVP set; extended catalog tracked in #79
        ManagedServiceEntry(
            kind="object_store", variant="s3", plugin_id="aws",
            description="Amazon S3 with versioning + public-access block",
            binding_envs=("S3_BUCKET_NAME", "S3_REGION"),
        ),
        ManagedServiceEntry(
            kind="queue", variant="sqs", plugin_id="aws",
            description="Amazon SQS standard or FIFO queue",
            binding_envs=("SQS_QUEUE_URL", "SQS_QUEUE_NAME", "AWS_REGION"),
        ),
        # GCP
        ManagedServiceEntry(
            kind="object_store", variant="gcs", plugin_id="gcp",
            description="Google Cloud Storage bucket",
            binding_envs=("GCS_BUCKET_NAME", "GCS_BUCKET_URI", "GCP_PROJECT_ID"),
        ),
        ManagedServiceEntry(
            kind="queue", variant="pubsub", plugin_id="gcp",
            description="Pub/Sub topic + subscription pair",
            binding_envs=(
                "PUBSUB_TOPIC", "PUBSUB_SUBSCRIPTION", "GCP_PROJECT_ID",
            ),
        ),
        # Azure
        ManagedServiceEntry(
            kind="object_store", variant="blob", plugin_id="azure",
            description="Azure Blob Storage container",
            binding_envs=(
                "AZURE_STORAGE_ACCOUNT", "AZURE_BLOB_CONTAINER",
                "AZURE_BLOB_ENDPOINT",
            ),
        ),
        ManagedServiceEntry(
            kind="queue", variant="servicebus", plugin_id="azure",
            description="Service Bus queue with dead-lettering",
            binding_envs=(
                "SERVICEBUS_NAMESPACE", "SERVICEBUS_QUEUE",
                "SERVICEBUS_ENDPOINT",
            ),
        ),
        # k8s_native (operator-backed)
        ManagedServiceEntry(
            kind="postgres", variant="cnpg", plugin_id="k8s_native",
            description="CloudNativePG-backed Postgres cluster",
            binding_envs=(
                "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                "POSTGRES_USER", "POSTGRES_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="redis", variant="operator", plugin_id="k8s_native",
            description="Bitnami Redis operator with primary + replicas",
            binding_envs=("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD"),
        ),
        ManagedServiceEntry(
            kind="mysql", variant="operator", plugin_id="k8s_native",
            description=(
                "Percona XtraDB Cluster (default) / MariaDB / Oracle "
                "MySQL operator-backed cluster"
            ),
            binding_envs=(
                "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DB",
                "MYSQL_USER", "MYSQL_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="document_db", variant="mongodb_operator",
            plugin_id="k8s_native",
            description="Percona Server for MongoDB operator",
            binding_envs=(
                "DOCDB_URI", "DOCDB_DB", "DOCDB_USER", "DOCDB_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="event_stream", variant="kafka_strimzi",
            plugin_id="k8s_native",
            description="Strimzi-managed Kafka cluster (KRaft)",
            binding_envs=(
                "EVENT_STREAM_BROKERS", "EVENT_STREAM_USERNAME",
                "EVENT_STREAM_PASSWORD", "EVENT_STREAM_TLS",
            ),
        ),
        ManagedServiceEntry(
            kind="event_stream", variant="nats",
            plugin_id="k8s_native",
            description="NATS StatefulSet (with optional JetStream)",
            binding_envs=("EVENT_STREAM_BROKERS", "EVENT_STREAM_TLS"),
        ),
        ManagedServiceEntry(
            kind="queue", variant="rabbitmq_operator",
            plugin_id="k8s_native",
            description="RabbitMQ Cluster Operator-backed cluster",
            binding_envs=(
                "RABBITMQ_HOST", "RABBITMQ_PORT",
                "RABBITMQ_USER", "RABBITMQ_PASSWORD",
            ),
        ),
        ManagedServiceEntry(
            kind="filesystem", variant="nfs_csi",
            plugin_id="k8s_native",
            description="NFS CSI driver-backed RWX PVC",
            binding_envs=(
                "FILESYSTEM_HANDLE", "FILESYSTEM_MOUNT_PATH",
                "FILESYSTEM_TLS",
            ),
        ),
    ),
)


__all__ = [
    "AvailabilityMatrix",
    "DriverEntry",
    "MATRIX",
    "ManagedServiceEntry",
    "OPTIONAL_ROLES",
    "REQUIRED_ROLES",
    "Status",
]
