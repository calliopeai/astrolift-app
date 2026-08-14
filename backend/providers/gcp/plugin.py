"""GCP provider plugin manifest.

Drivers shipped:
- ArtifactRegistryDriver (#40) — ImageRegistryDriver
- GCPSecretsBackend (#39) — SecretsBackend (Secret Manager)
- GCPWorkloadIdentityDriver (#39) — WorkloadIdentityDriver
- CloudDNSDriver (#38) — DnsDriver
- GCPManagedCertDriver (#38) — TlsDriver
- GKEClusterDriver (#36) — ClusterDriver
- GCPIngressDriver (#37) — IngressDriver, multi-variant
  (gce_ingress / gateway_api)

Executable managed services:
- GCSDriver — object_store/gcs
- PubSubDriver — queue/pubsub
- PubSubTopicDriver — topic/pubsub_topic
- CloudSQLPostgresDriver — postgres/cloudsql
- AlloyDBPostgresDriver — postgres/alloydb
- CloudSQLMySQLDriver — mysql/cloudsql
- MemorystoreRedisDriver — redis/memorystore
- BigtableDriver — kv_store/bigtable
- VertexMatchingEngineDriver — vector_index/vertex_matching_engine
- GCPManagedPrometheusDriver — time_series/gcp_managed_prometheus
- VertexAIEndpointDriver — model_endpoint/vertex_ai
- CloudKMSDriver — encryption_key/cloud_kms
- BigQueryWarehouseDriver — warehouse/bigquery
- FirestoreNativeDriver — document_db/firestore_native
- CloudSQLServerDriver — mssql/cloudsql_sqlserver

The availability catalogue is authoritative for the remaining planned GCP
resources. Placeholder email/search classes are registered only so callers get
an explicit capability error; the lifecycle config factory refuses them.
"""

from _sdk.base import ProviderPlugin
from gcp.cluster_gke import GKEClusterDriver
from gcp.dns_clouddns import CloudDNSDriver
from gcp.identity_wi import GCPWorkloadIdentityDriver
from gcp.ingress import GCPIngressDriver
from gcp.managed.bigtable import BigtableDriver
from gcp.managed.document_firestore import FirestoreNativeDriver
from gcp.managed.email_thirdparty import GCPEmailStubDriver
from gcp.managed.encryption_cloud_kms import CloudKMSDriver
from gcp.managed.model_endpoint_vertex import VertexAIEndpointDriver
from gcp.managed.mssql_cloudsql import CloudSQLServerDriver
from gcp.managed.mysql_cloudsql import CloudSQLMySQLDriver
from gcp.managed.object_store_gcs import GCSDriver
from gcp.managed.postgres_alloydb import AlloyDBPostgresDriver
from gcp.managed.postgres_cloudsql import CloudSQLPostgresDriver
from gcp.managed.queue_pubsub import PubSubDriver
from gcp.managed.redis_memorystore import MemorystoreRedisDriver
from gcp.managed.search_elastic_cloud import GCPElasticCloudStubDriver
from gcp.managed.timeseries_managed_prometheus import GCPManagedPrometheusDriver
from gcp.managed.topic_pubsub import PubSubTopicDriver
from gcp.managed.vector_vertex import VertexMatchingEngineDriver
from gcp.managed.warehouse_bigquery import BigQueryWarehouseDriver
from gcp.notification_fcm import FCMNotificationDriver
from gcp.registry_artifact import ArtifactRegistryDriver
from gcp.secrets import GCPSecretsBackend
from gcp.tls_managed import GCPManagedCertDriver

PLUGIN = ProviderPlugin(
    id="gcp",
    display_name="Google Cloud Platform",
    drivers={
        "registry": ArtifactRegistryDriver,
        "secrets": GCPSecretsBackend,
        "identity": GCPWorkloadIdentityDriver,
        "dns": CloudDNSDriver,
        "tls": GCPManagedCertDriver,
        "cluster": GKEClusterDriver,
        "ingress": GCPIngressDriver,
        "notification": FCMNotificationDriver,
    },
    managed_service_drivers={
        ("object_store", "gcs"): GCSDriver,
        ("queue", "pubsub"): PubSubDriver,
        ("topic", "pubsub_topic"): PubSubTopicDriver,
        ("postgres", "cloudsql"): CloudSQLPostgresDriver,
        ("postgres", "alloydb"): AlloyDBPostgresDriver,
        ("mysql", "cloudsql"): CloudSQLMySQLDriver,
        ("redis", "memorystore"): MemorystoreRedisDriver,
        ("kv_store", "bigtable"): BigtableDriver,
        ("search", "gcp_elastic_cloud"): GCPElasticCloudStubDriver,
        ("vector_index", "vertex_matching_engine"): VertexMatchingEngineDriver,
        ("time_series", "gcp_managed_prometheus"): GCPManagedPrometheusDriver,
        ("email", "gcp_thirdparty"): GCPEmailStubDriver,
        ("model_endpoint", "vertex_ai"): VertexAIEndpointDriver,
        ("encryption_key", "cloud_kms"): CloudKMSDriver,
        ("warehouse", "bigquery"): BigQueryWarehouseDriver,
        ("document_db", "firestore_native"): FirestoreNativeDriver,
        ("mssql", "cloudsql_sqlserver"): CloudSQLServerDriver,
    },
    config_schema={
        "type": "object",
        "required": ["project_id", "region"],
        "properties": {
            "project_id": {
                "type": "string",
                "description": "GCP project ID for this binding.",
            },
            "region": {
                "type": "string",
                "description": "Default GCP region (e.g. us-central1).",
            },
            "zone": {
                "type": "string",
                "description": ("Default GCP zone (e.g. us-central1-a). Required for zonal GKE clusters."),
            },
            "cluster_oidc_issuer": {
                "type": "string",
                "description": ("GKE cluster's workload identity pool URL."),
            },
            "artifact_registry_repo": {
                "type": "string",
                "description": ("Pre-created Artifact Registry repository ID. Driver will auto-create if missing."),
            },
            "ingress_variant": {
                "type": "string",
                "enum": ["gce_ingress", "gateway_api"],
                "default": "gce_ingress",
            },
            "managed_cert_name_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": ("Prefix for GCP-managed SslCertificate resources."),
            },
            "kms_key": {
                "type": "string",
                "description": ("Optional CMEK KMS key resource for Secret Manager + Artifact Registry encryption."),
            },
            "secret_id_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": "Physical Secret Manager id prefix used by operator and managed-service secrets.",
            },
            "secret_manager_kms_key": {
                "type": "string",
                "description": "Optional CMEK resource name for Secret Manager replication.",
            },
            "cloud_kms_location": {
                "type": "string",
                "description": (
                    "Default Cloud KMS location. It should match the locality of services "
                    "using the key; falls back to region."
                ),
            },
            "cloud_kms_key_ring_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "cloud_kms_key_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "cloud_kms_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "cloud_kms_rotation_period_default": {
                "type": "string",
                "default": "7776000s",
                "description": "Google Duration; 7776000s is 90 days.",
            },
            "cloud_kms_destroy_scheduled_duration_default": {
                "type": "string",
                "default": "2592000s",
                "description": "Immutable provider waiting period; 2592000s is 30 days.",
            },
            "bucket_name_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": "Prefix for platform-managed GCS buckets.",
            },
            "pubsub_topic_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": "Prefix for platform-managed Pub/Sub topics.",
            },
            "bigquery_location": {
                "type": "string",
                "description": "BigQuery dataset and capacity location; falls back to region.",
            },
            "bigquery_dataset_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "bigquery_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "bigquery_dataset_api_endpoint": {
                "type": "string",
                "default": "https://bigquery.googleapis.com/bigquery/v2",
            },
            "bigquery_reservation_api_endpoint": {
                "type": "string",
                "default": "https://bigqueryreservation.googleapis.com/v1",
            },
            "firestore_location": {
                "type": "string",
                "description": "Firestore database location; falls back to region.",
            },
            "firestore_database_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "firestore_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "firestore_snapshot_bucket": {
                "type": "string",
                "description": "GCS bucket or gs:// URI used for on-demand exports and safe deletion retention.",
            },
            "firestore_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 900,
            },
            "firestore_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 2,
            },
            "firestore_api_endpoint": {
                "type": "string",
                "default": "https://firestore.googleapis.com/v1",
            },
            "cloudsql_private_network": {
                "type": "string",
                "description": "VPC self-link used for private Cloud SQL connectivity.",
            },
            "cloudsql_instance_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "cloudsql_postgres_engine_version": {
                "type": "string",
                "default": "POSTGRES_16",
            },
            "cloudsql_mysql_engine_version": {
                "type": "string",
                "default": "MYSQL_8_0",
            },
            "cloudsql_sqlserver_engine_version": {
                "type": "string",
                "enum": [
                    "SQLSERVER_2017_EXPRESS",
                    "SQLSERVER_2017_WEB",
                    "SQLSERVER_2017_STANDARD",
                    "SQLSERVER_2017_ENTERPRISE",
                    "SQLSERVER_2019_EXPRESS",
                    "SQLSERVER_2019_WEB",
                    "SQLSERVER_2019_STANDARD",
                    "SQLSERVER_2019_ENTERPRISE",
                    "SQLSERVER_2022_EXPRESS",
                    "SQLSERVER_2022_WEB",
                    "SQLSERVER_2022_STANDARD",
                    "SQLSERVER_2022_ENTERPRISE",
                    "SQLSERVER_2025_EXPRESS",
                    "SQLSERVER_2025_STANDARD",
                    "SQLSERVER_2025_ENTERPRISE",
                ],
                "default": "SQLSERVER_2025_EXPRESS",
            },
            "cloudsql_sqlserver_backup_retention_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 35,
                "default": 7,
            },
            "cloudsql_backup_retention_days": {
                "type": "integer",
                "minimum": 0,
                "maximum": 35,
                "default": 7,
            },
            "cloudsql_high_availability_default": {
                "type": "boolean",
                "default": False,
            },
            "cloudsql_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "cloudsql_secret_manager_prefix": {
                "type": "string",
                "default": "astrolift/cloudsql",
                "description": "Logical path prefix for Cloud SQL credential and connection URL secrets.",
            },
            "cloudsql_api_endpoint": {
                "type": "string",
                "default": "https://sqladmin.googleapis.com/v1",
            },
            "cloudsql_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "cloudsql_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 3,
            },
            "alloydb_network": {
                "type": "string",
                "description": "VPC resource name used for private AlloyDB connectivity.",
            },
            "alloydb_allocated_ip_range": {
                "type": "string",
                "description": "Optional private services access range for AlloyDB.",
            },
            "alloydb_cluster_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "alloydb_primary_instance_id": {
                "type": "string",
                "default": "primary",
            },
            "alloydb_database_version": {
                "type": "string",
                "enum": ["POSTGRES_14", "POSTGRES_15", "POSTGRES_16", "POSTGRES_17", "POSTGRES_18"],
                "default": "POSTGRES_16",
            },
            "alloydb_machine_type_default": {
                "type": "string",
                "default": "n2-highmem-2",
            },
            "alloydb_high_availability_default": {
                "type": "boolean",
                "default": True,
            },
            "alloydb_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "alloydb_backup_retention_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 35,
                "default": 14,
            },
            "alloydb_secret_manager_prefix": {
                "type": "string",
                "default": "astrolift/alloydb",
                "description": "Logical path prefix for AlloyDB credentials and connection URLs.",
            },
            "alloydb_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1200,
            },
            "alloydb_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5,
            },
            "alloydb_api_endpoint": {
                "type": "string",
                "default": "https://alloydb.googleapis.com/v1",
            },
            "memorystore_authorized_network": {
                "type": "string",
                "description": "VPC self-link authorized for Memorystore connectivity.",
            },
            "memorystore_instance_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "memorystore_redis_version": {
                "type": "string",
                "default": "REDIS_7_2",
            },
            "memorystore_tier_default": {
                "type": "string",
                "enum": ["BASIC", "STANDARD_HA"],
                "default": "BASIC",
            },
            "memorystore_transit_encryption_default": {
                "type": "boolean",
                "default": True,
            },
            "memorystore_auth_enabled_default": {
                "type": "boolean",
                "default": True,
            },
            "memorystore_secret_manager_prefix": {
                "type": "string",
                "default": "astrolift/memorystore",
                "description": "Logical path prefix for Memorystore authentication and URL secrets.",
            },
        },
    },
)
