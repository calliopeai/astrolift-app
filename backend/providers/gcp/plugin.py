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
- MemorystoreValkeyDriver — redis/memorystore_valkey
- BigtableDriver — kv_store/bigtable
- VertexMatchingEngineDriver — vector_index/vertex_matching_engine
- GCPManagedPrometheusDriver — time_series/gcp_managed_prometheus
- VertexAIEndpointDriver — model_endpoint/vertex_ai
- CloudKMSDriver — encryption_key/cloud_kms
- BigQueryWarehouseDriver — warehouse/bigquery
- FirestoreNativeDriver — document_db/firestore_native
- CloudSQLServerDriver — mssql/cloudsql_sqlserver
- SpannerGraphDriver — graph_db/spanner_graph
- CloudFunctionsDriver — faas/cloud_functions_gen2
- APIGatewayDriver — api_gateway/api_gateway
- ManagedKafkaDriver — event_stream/managed_kafka
- EventarcDriver — event_bus/eventarc

The availability catalogue is authoritative for the remaining planned GCP
resources. Placeholder email/search classes are registered only so callers get
an explicit capability error; the lifecycle config factory refuses them.
"""

from _sdk.base import ProviderPlugin
from gcp.cluster_gke import GKEClusterDriver
from gcp.dns_clouddns import CloudDNSDriver
from gcp.identity_wi import GCPWorkloadIdentityDriver
from gcp.ingress import GCPIngressDriver
from gcp.managed.api_gateway import APIGatewayDriver
from gcp.managed.bigtable import BigtableDriver
from gcp.managed.document_firestore import FirestoreNativeDriver
from gcp.managed.email_thirdparty import GCPEmailStubDriver
from gcp.managed.encryption_cloud_kms import CloudKMSDriver
from gcp.managed.event_bus_eventarc import EventarcDriver
from gcp.managed.event_stream_managed_kafka import ManagedKafkaDriver
from gcp.managed.faas_cloud_functions import CloudFunctionsDriver
from gcp.managed.graph_spanner import SpannerGraphDriver
from gcp.managed.model_endpoint_vertex import VertexAIEndpointDriver
from gcp.managed.mssql_cloudsql import CloudSQLServerDriver
from gcp.managed.mysql_cloudsql import CloudSQLMySQLDriver
from gcp.managed.object_store_gcs import GCSDriver
from gcp.managed.postgres_alloydb import AlloyDBPostgresDriver
from gcp.managed.postgres_cloudsql import CloudSQLPostgresDriver
from gcp.managed.queue_pubsub import PubSubDriver
from gcp.managed.redis_memorystore import MemorystoreRedisDriver
from gcp.managed.redis_memorystore_valkey import MemorystoreValkeyDriver
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
        ("redis", "memorystore_valkey"): MemorystoreValkeyDriver,
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
        ("graph_db", "spanner_graph"): SpannerGraphDriver,
        ("faas", "cloud_functions_gen2"): CloudFunctionsDriver,
        ("api_gateway", "api_gateway"): APIGatewayDriver,
        ("event_stream", "managed_kafka"): ManagedKafkaDriver,
        ("event_bus", "eventarc"): EventarcDriver,
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
            "cloud_functions_region": {
                "type": "string",
                "description": "Cloud Run functions region; falls back to the cluster region.",
            },
            "cloud_functions_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "cloud_functions_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "cloud_functions_api_endpoint": {
                "type": "string",
                "default": "https://cloudfunctions.googleapis.com/v2",
            },
            "cloud_functions_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "cloud_functions_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5,
            },
            "api_gateway_region": {
                "type": "string",
                "description": "API Gateway deployment region; falls back to the cluster region.",
            },
            "api_gateway_api_id_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "api_gateway_gateway_id_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "api_gateway_config_id_prefix": {
                "type": "string",
                "default": "cfg",
            },
            "api_gateway_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "api_gateway_api_endpoint": {
                "type": "string",
                "default": "https://apigateway.googleapis.com/v1",
            },
            "api_gateway_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "api_gateway_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5,
            },
            "managed_kafka_location": {
                "type": "string",
                "description": "Managed Kafka location; falls back to the cluster region.",
            },
            "managed_kafka_cluster_id_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "managed_kafka_subnet_names": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string"},
                "description": "Default PSC subnets for Managed Kafka clusters.",
            },
            "managed_kafka_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "managed_kafka_api_endpoint": {
                "type": "string",
                "default": "https://managedkafka.googleapis.com/v1",
            },
            "managed_kafka_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "managed_kafka_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5,
            },
            "eventarc_location": {
                "type": "string",
                "description": "Eventarc Advanced location; falls back to the cluster region.",
            },
            "eventarc_message_bus_id": {
                "type": "string",
                "default": "astrolift",
                "description": ("Shared Eventarc Advanced bus ID. Google allows one bus per project and region."),
            },
            "eventarc_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "eventarc_api_endpoint": {
                "type": "string",
                "default": "https://eventarc.googleapis.com/v1",
            },
            "eventarc_publishing_endpoint": {
                "type": "string",
                "default": "https://eventarcpublishing.googleapis.com/v1",
            },
            "eventarc_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 900,
            },
            "eventarc_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 5,
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
            "spanner_instance_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "spanner_shared_instance_id": {
                "type": "string",
                "description": "Optional existing or shared Spanner instance for graph databases.",
            },
            "spanner_instance_config": {
                "type": "string",
                "description": "Spanner regional or multi-region instance configuration resource.",
            },
            "spanner_edition": {
                "type": "string",
                "enum": ["ENTERPRISE", "ENTERPRISE_PLUS"],
                "default": "ENTERPRISE",
            },
            "spanner_processing_units": {
                "type": "integer",
                "minimum": 100,
                "multipleOf": 100,
                "default": 100,
            },
            "spanner_automatic_backup_schedule": {
                "type": "boolean",
                "default": True,
            },
            "spanner_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "spanner_backup_retention_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 366,
                "default": 30,
            },
            "spanner_api_endpoint": {
                "type": "string",
                "default": "https://spanner.googleapis.com/v1",
            },
            "spanner_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "spanner_operation_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 2,
            },
            "spanner_adopt_existing_instance": {
                "type": "boolean",
                "default": False,
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
            "memorystore_valkey_network": {
                "type": "string",
                "description": "PSC consumer VPC resource name; defaults to the project's default network.",
            },
            "memorystore_valkey_instance_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "memorystore_valkey_engine_version": {
                "type": "string",
                "enum": ["VALKEY_7_2", "VALKEY_8_0", "VALKEY_9_0", "VALKEY_9_1"],
                "default": "VALKEY_9_0",
            },
            "memorystore_valkey_node_type": {
                "type": "string",
                "enum": [
                    "SHARED_CORE_NANO",
                    "CUSTOM_PICO",
                    "CUSTOM_MICRO",
                    "CUSTOM_MINI",
                    "STANDARD_SMALL",
                    "STANDARD_LARGE",
                    "HIGHCPU_MEDIUM",
                    "HIGHMEM_MEDIUM",
                    "HIGHMEM_XLARGE",
                    "HIGHMEM_2XLARGE",
                ],
                "default": "HIGHMEM_MEDIUM",
            },
            "memorystore_valkey_mode": {
                "type": "string",
                "enum": ["CLUSTER", "CLUSTER_DISABLED"],
                "default": "CLUSTER",
            },
            "memorystore_valkey_shard_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 250,
                "default": 1,
            },
            "memorystore_valkey_replica_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "default": 1,
            },
            "memorystore_valkey_authorization_mode": {
                "type": "string",
                "enum": ["IAM_AUTH", "TOKEN_AUTH", "AUTH_DISABLED"],
                "default": "IAM_AUTH",
            },
            "memorystore_valkey_token_auth_user": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_-]{0,62}$",
                "default": "default",
            },
            "memorystore_valkey_token_auth_rotation_generation": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
            },
            "memorystore_valkey_token_auth_retire_generation": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
            },
            "memorystore_valkey_secret_manager_prefix": {
                "type": "string",
                "default": "astrolift/memorystore-valkey",
                "description": "Logical Secret Manager prefix for Valkey TOKEN_AUTH credentials.",
            },
            "memorystore_valkey_transit_encryption_default": {
                "type": "boolean",
                "default": True,
            },
            "memorystore_valkey_persistence_mode": {
                "type": "string",
                "enum": ["DISABLED", "RDB", "AOF"],
                "default": "RDB",
            },
            "memorystore_valkey_automated_backup_default": {
                "type": "boolean",
                "default": True,
            },
            "memorystore_valkey_backup_retention_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 35,
            },
            "memorystore_valkey_deletion_protection_default": {
                "type": "boolean",
                "default": True,
            },
            "memorystore_valkey_kms_key": {
                "type": "string",
                "description": "Optional Cloud KMS key resource name for data at rest.",
            },
            "memorystore_valkey_server_ca_mode": {
                "type": "string",
                "enum": [
                    "",
                    "GOOGLE_MANAGED_PER_INSTANCE_CA",
                    "GOOGLE_MANAGED_SHARED_CA",
                    "CUSTOMER_MANAGED_CAS_CA",
                ],
                "default": "",
            },
            "memorystore_valkey_server_ca_pool": {
                "type": "string",
                "description": "Optional same-region Certificate Authority Service CA pool.",
            },
            "memorystore_valkey_allow_preview_features": {
                "type": "boolean",
                "default": False,
            },
            "memorystore_valkey_api_endpoint": {
                "type": "string",
                "default": "https://memorystore.googleapis.com/v1",
            },
            "memorystore_valkey_operation_timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1800,
            },
            "memorystore_valkey_poll_interval_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 3,
            },
            "memorystore_valkey_adopt_existing_instance": {
                "type": "boolean",
                "default": False,
            },
        },
    },
)
