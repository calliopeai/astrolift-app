"""AWS provider plugin manifest.

Registers the AWS-implemented drivers with the Astrolift control
plane. The control plane loads this via the ``astrolift.providers``
entry point at boot.

Drivers shipped:
- ECRDriver (#34) — ImageRegistryDriver
- AWSSecretsBackend (#32) — SecretsBackend (Secrets Manager + SSM)
- IRSADriver (#33) — WorkloadIdentityDriver
- Route53Driver (#31) — DnsDriver
- ACMDriver (#31) — TlsDriver
- EKSClusterDriver (#29) — ClusterDriver
- ALBIngressDriver (#30) — IngressDriver

Pending (separate tickets):
- AWS managed-service drivers (#35) — RDS, Aurora, ElastiCache,
  DynamoDB, SQS, SNS, S3, EFS

The PLUGIN constant is what the control plane consumes; absence
of a driver entry signals that the plugin doesn't cover that
capability and the cluster validator will refuse to bind a
cluster to this plugin until either the missing driver is added
or another plugin contributes the missing role.
"""

from _sdk.base import ProviderPlugin
from aws.cluster_eks import EKSClusterDriver
from aws.dns_route53 import Route53Driver
from aws.identity_irsa import IRSADriver
from aws.ingress_alb import ALBIngressDriver
from aws.managed.object_store_s3 import S3Driver
from aws.managed.postgres_rds import RDSPostgresDriver
from aws.managed.queue_sqs import SQSDriver
from aws.managed.redis_elasticache import ElastiCacheRedisDriver
from aws.registry_ecr import ECRDriver
from aws.secrets import AWSSecretsBackend
from aws.tls_acm import ACMDriver

# Plugin manifest. Drivers map a canonical role name → concrete
# class implementing that role's protocol.
PLUGIN = ProviderPlugin(
    id="aws",
    display_name="Amazon Web Services",
    drivers={
        "registry": ECRDriver,
        "secrets": AWSSecretsBackend,
        "identity": IRSADriver,
        "dns": Route53Driver,
        "tls": ACMDriver,
        "cluster": EKSClusterDriver,
        "ingress": ALBIngressDriver,
    },
    managed_service_drivers={
        # #35 + #351 + #352 — high-traffic kinds (web app + queue +
        # bucket + relational DB + cache). Pending per backlog tickets:
        # postgres/aurora, nosql/dynamodb, pubsub/sns, filesystem/efs.
        ("object_store", "s3"): S3Driver,
        ("queue", "sqs"): SQSDriver,
        ("postgres", "rds"): RDSPostgresDriver,
        ("redis", "elasticache"): ElastiCacheRedisDriver,
    },
    config_schema={
        "type": "object",
        "required": ["region", "account_id"],
        "properties": {
            "region": {
                "type": "string",
                "description": "Default AWS region for this binding.",
            },
            "account_id": {
                "type": "string",
                "pattern": "^[0-9]{12}$",
                "description": "12-digit AWS account ID.",
            },
            "cluster_oidc_issuer": {
                "type": "string",
                "description": (
                    "EKS cluster's OIDC issuer URL (without https://). "
                    "Required for IRSA workload identity."
                ),
            },
            "ecr_image_tag_mutability": {
                "type": "string",
                "enum": ["IMMUTABLE", "MUTABLE"],
                "default": "IMMUTABLE",
                "description": (
                    "ECR tag mutability. IMMUTABLE prevents tag "
                    "overwrites and is recommended for production."
                ),
            },
            "ecr_image_scanning_enabled": {
                "type": "boolean",
                "default": True,
                "description": "Enable ECR's built-in vuln scanning.",
            },
            "kms_key_id": {
                "type": "string",
                "description": (
                    "Optional customer-managed KMS key ARN for "
                    "secrets + ECR encryption. Defaults to AWS-"
                    "managed keys when absent."
                ),
            },
            "secrets_manager_prefix": {
                "type": "string",
                "default": "astrolift",
                "description": (
                    "Prefix for platform-managed Secrets Manager "
                    "names. Lets operators filter via tag-based "
                    "IAM policies."
                ),
            },
            "ssm_prefix": {
                "type": "string",
                "default": "/astrolift",
                "description": "SSM Parameter Store path prefix.",
            },
            "irsa_role_path": {
                "type": "string",
                "default": "/astrolift/",
                "description": (
                    "IAM role path for platform-created roles. "
                    "Helps operators apply tag-based budgets."
                ),
            },
        },
    },
)
