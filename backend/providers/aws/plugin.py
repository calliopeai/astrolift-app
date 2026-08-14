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
from aws.managed.api_gateway_http import ApiGatewayHttpDriver
from aws.managed.api_gateway_rest import ApiGatewayRestDriver
from aws.managed.api_gateway_websocket import ApiGatewayWebSocketDriver
from aws.managed.aurora import AuroraMySQLDriver, AuroraPostgresDriver
from aws.managed.cdn_cloudfront import CloudFrontDriver
from aws.managed.documentdb import (
    DocumentDBProvisionedDriver,
    DocumentDBServerlessV2Driver,
)
from aws.managed.dynamodb import DynamoDBDriver
from aws.managed.elasticache_serverless import (
    ElastiCacheServerlessMemcachedDriver,
    ElastiCacheServerlessRedisDriver,
)
from aws.managed.email_ses import AmazonSESDriver
from aws.managed.encryption_kms import KMSDriver
from aws.managed.event_bus_eventbridge import EventBridgeDriver
from aws.managed.event_stream_msk import MSKProvisionedDriver, MSKServerlessDriver
from aws.managed.faas_lambda import LambdaDriver
from aws.managed.filesystem_efs import EFSDriver
from aws.managed.filesystem_fsx import FSxLustreDriver, FSxOpenZFSDriver, FSxWindowsDriver
from aws.managed.keyspaces import KeyspacesDriver
from aws.managed.memcached_elasticache import ElastiCacheMemcachedDriver
from aws.managed.memorydb import MemoryDBDriver
from aws.managed.model_endpoint_bedrock import AmazonBedrockDriver
from aws.managed.mq_amazon import AmazonMQActiveMQDriver, AmazonMQRabbitMQDriver
from aws.managed.mssql_rds import RDSSqlServerDriver
from aws.managed.mysql_rds import RDSMySQLDriver
from aws.managed.neptune import NeptuneProvisionedDriver, NeptuneServerlessDriver
from aws.managed.object_store_s3 import S3Driver
from aws.managed.observability_cloudwatch import CloudWatchDriver
from aws.managed.opensearch_serverless import (
    OpenSearchServerlessSearchDriver,
    OpenSearchServerlessVectorDriver,
)
from aws.managed.postgres_rds import RDSPostgresDriver
from aws.managed.private_endpoint_vpc import VpcEndpointDriver
from aws.managed.queue_sqs import SQSDriver
from aws.managed.rds_proxy import RDSProxyDriver
from aws.managed.redis_elasticache import ElastiCacheRedisDriver
from aws.managed.redshift import RedshiftProvisionedDriver
from aws.managed.redshift_serverless import RedshiftServerlessDriver
from aws.managed.search_opensearch import OpenSearchSearchDriver
from aws.managed.stream_firehose import FirehoseDriver
from aws.managed.stream_kinesis import KinesisDriver
from aws.managed.timeseries_timestream import TimestreamDriver
from aws.managed.topic_sns import SNSFifoTopicDriver, SNSStandardTopicDriver
from aws.managed.vector_opensearch import OpenSearchVectorDriver
from aws.managed.workflow_step_functions import (
    StepFunctionsExpressDriver,
    StepFunctionsStandardDriver,
)
from aws.notification_sns import SNSNotificationDriver
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
        "notification": SNSNotificationDriver,
    },
    managed_service_drivers={
        # Cloud-neutral kinds map to provider-specific executable variants.
        ("object_store", "s3"): S3Driver,
        ("kv_store", "dynamodb"): DynamoDBDriver,
        ("cdn", "cloudfront"): CloudFrontDriver,
        ("faas", "lambda"): LambdaDriver,
        ("api_gateway", "http_api"): ApiGatewayHttpDriver,
        ("api_gateway", "rest_api"): ApiGatewayRestDriver,
        ("api_gateway", "websocket_api"): ApiGatewayWebSocketDriver,
        ("queue", "sqs"): SQSDriver,
        ("topic", "sns_standard"): SNSStandardTopicDriver,
        ("topic", "sns_fifo"): SNSFifoTopicDriver,
        ("event_bus", "eventbridge"): EventBridgeDriver,
        ("stream", "kinesis"): KinesisDriver,
        ("stream", "firehose"): FirehoseDriver,
        ("event_stream", "msk"): MSKProvisionedDriver,
        ("event_stream", "msk_serverless"): MSKServerlessDriver,
        ("mq", "amazon_mq_rabbitmq"): AmazonMQRabbitMQDriver,
        ("mq", "amazon_mq_activemq"): AmazonMQActiveMQDriver,
        ("filesystem", "efs"): EFSDriver,
        ("filesystem", "fsx_lustre"): FSxLustreDriver,
        ("filesystem", "fsx_openzfs"): FSxOpenZFSDriver,
        ("filesystem", "fsx_windows"): FSxWindowsDriver,
        ("postgres", "rds"): RDSPostgresDriver,
        ("postgres", "aurora_postgres"): AuroraPostgresDriver,
        ("postgres", "aurora_postgres_serverless_v2"): AuroraPostgresDriver,
        ("mysql", "rds_mysql"): RDSMySQLDriver,
        ("mysql", "aurora_mysql"): AuroraMySQLDriver,
        ("mysql", "aurora_mysql_serverless_v2"): AuroraMySQLDriver,
        ("mssql", "rds_sqlserver_express"): RDSSqlServerDriver,
        ("mssql", "rds_sqlserver_web"): RDSSqlServerDriver,
        ("mssql", "rds_sqlserver_standard"): RDSSqlServerDriver,
        ("mssql", "rds_sqlserver_enterprise"): RDSSqlServerDriver,
        ("database_proxy", "rds_proxy"): RDSProxyDriver,
        ("document_db", "documentdb"): DocumentDBProvisionedDriver,
        ("document_db", "documentdb_serverless_v2"): DocumentDBServerlessV2Driver,
        ("wide_column", "keyspaces"): KeyspacesDriver,
        ("graph_db", "neptune"): NeptuneProvisionedDriver,
        ("graph_db", "neptune_serverless"): NeptuneServerlessDriver,
        ("warehouse", "redshift"): RedshiftProvisionedDriver,
        ("warehouse", "redshift_serverless"): RedshiftServerlessDriver,
        ("redis", "elasticache"): ElastiCacheRedisDriver,
        ("redis", "elasticache_valkey"): ElastiCacheRedisDriver,
        ("redis", "elasticache_serverless_valkey"): ElastiCacheServerlessRedisDriver,
        ("redis", "elasticache_serverless_redis"): ElastiCacheServerlessRedisDriver,
        ("redis", "memorydb"): MemoryDBDriver,
        ("cache", "elasticache_serverless_memcached"): ElastiCacheServerlessMemcachedDriver,
        ("cache", "elasticache_memcached"): ElastiCacheMemcachedDriver,
        ("vector_index", "opensearch_vector"): OpenSearchVectorDriver,
        ("vector_index", "opensearch_serverless_vector"): OpenSearchServerlessVectorDriver,
        ("search", "opensearch"): OpenSearchSearchDriver,
        ("search", "opensearch_serverless"): OpenSearchServerlessSearchDriver,
        ("time_series", "timestream"): TimestreamDriver,
        ("email", "ses"): AmazonSESDriver,
        ("model_endpoint", "bedrock"): AmazonBedrockDriver,
        ("encryption_key", "kms"): KMSDriver,
        ("observability", "cloudwatch"): CloudWatchDriver,
        ("workflow_engine", "step_functions_standard"): StepFunctionsStandardDriver,
        ("workflow_engine", "step_functions_express"): StepFunctionsExpressDriver,
        ("private_endpoint", "vpc_endpoint"): VpcEndpointDriver,
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
                    "EKS cluster's OIDC issuer URL (without https://). Required for IRSA workload identity."
                ),
            },
            "ecr_image_tag_mutability": {
                "type": "string",
                "enum": ["IMMUTABLE", "MUTABLE"],
                "default": "IMMUTABLE",
                "description": (
                    "ECR tag mutability. IMMUTABLE prevents tag overwrites and is recommended for production."
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
                "description": ("IAM role path for platform-created roles. Helps operators apply tag-based budgets."),
            },
        },
    },
)
