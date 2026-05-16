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

Managed services (#363 + #370 GCP slice — full symmetry with AWS):
- GCSDriver — object_store/gcs
- PubSubDriver — queue/pubsub
- CloudSQLPostgresDriver — postgres/cloudsql
- CloudSQLMySQLDriver — mysql/cloudsql
- MemorystoreRedisDriver — redis/memorystore

Pending (separate tickets, follow-on managed services):
- Filestore (filesystem)
- Firestore / Bigtable (nosql)
"""

from _sdk.base import ProviderPlugin

from gcp.cluster_gke import GKEClusterDriver
from gcp.dns_clouddns import CloudDNSDriver
from gcp.identity_wi import GCPWorkloadIdentityDriver
from gcp.ingress import GCPIngressDriver
from gcp.managed.mysql_cloudsql import CloudSQLMySQLDriver
from gcp.managed.object_store_gcs import GCSDriver
from gcp.managed.postgres_cloudsql import CloudSQLPostgresDriver
from gcp.managed.queue_pubsub import PubSubDriver
from gcp.managed.redis_memorystore import MemorystoreRedisDriver
from gcp.managed.search_elastic_cloud import GCPElasticCloudStubDriver
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
    },
    managed_service_drivers={
        ("object_store", "gcs"): GCSDriver,
        ("queue", "pubsub"): PubSubDriver,
        ("postgres", "cloudsql"): CloudSQLPostgresDriver,
        ("mysql", "cloudsql"): CloudSQLMySQLDriver,
        ("redis", "memorystore"): MemorystoreRedisDriver,
        ("search", "gcp_elastic_cloud"): GCPElasticCloudStubDriver,
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
                "description": (
                    "Default GCP zone (e.g. us-central1-a). "
                    "Required for zonal GKE clusters."
                ),
            },
            "cluster_oidc_issuer": {
                "type": "string",
                "description": (
                    "GKE cluster's workload identity pool URL."
                ),
            },
            "artifact_registry_repo": {
                "type": "string",
                "description": (
                    "Pre-created Artifact Registry repository ID. "
                    "Driver will auto-create if missing."
                ),
            },
            "ingress_variant": {
                "type": "string",
                "enum": ["gce_ingress", "gateway_api"],
                "default": "gce_ingress",
            },
            "managed_cert_name_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": (
                    "Prefix for GCP-managed SslCertificate resources."
                ),
            },
            "kms_key": {
                "type": "string",
                "description": (
                    "Optional CMEK KMS key resource for Secret Manager "
                    "+ Artifact Registry encryption."
                ),
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
        },
    },
)
