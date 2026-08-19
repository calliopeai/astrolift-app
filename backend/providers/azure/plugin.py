"""Azure provider plugin manifest.

Drivers shipped:
- ACRDriver (#46) — ImageRegistryDriver (Azure Container Registry)
- KeyVaultSecretsBackend (#45) — SecretsBackend (Key Vault)
- AzureFederatedIdentityDriver (#45) — WorkloadIdentityDriver
  (AKS Workload Identity + federated credentials)
- AzureDNSDriver (#44) — DnsDriver (Azure DNS)
- AzureAppGatewayTlsDriver (#44) — TlsDriver (App Gateway / Key
  Vault-backed cert + Azure-managed cert)
- AKSClusterDriver (#42) — ClusterDriver
- AzureAppGatewayIngressDriver (#43) — IngressDriver, multi-variant
  (agic / gateway_api)

Managed services:
- BlobStorageDriver — object_store/blob
- ServiceBusDriver — queue/servicebus
- AzureBlobStorageDriver — object_store/azure_blob
- AzureServiceBusDriver — queue/azure_servicebus and topic/service_bus_topic
- AzureEventHubsDriver — stream/event_hubs and event_stream/event_hubs_kafka
- AzureEventGridDriver — event_bus/event_grid
- AzureEventGridNamespaceDriver — event_bus/event_grid_namespace
- AzureFilesDriver — filesystem/azure_files
- AzurePostgresFlexibleDriver — postgres/azure_pg_flex
- AzureMySQLFlexibleDriver — mysql/azure_mysql_flex
- AzureCacheRedisDriver — redis/azure_cache_redis
- AzureManagedRedisDriver — redis/azure_managed_redis
- AzureCosmosDriver — kv_store/cosmos
- AzureCosmosApiDriver — explicit NoSQL, MongoDB, Gremlin, Cassandra, and Table API variants
- AzureFilesClassicDriver — filesystem/azure_files_classic
- AzureAISearchFullTextDriver — search/azure_ai_search_fulltext
- AzureAISearchVectorDriver — vector_index/azure_ai_search_vector
- AzureMonitorPrometheusDriver — time_series/azure_monitor_prometheus
- AzureCommunicationEmailDriver — email/azure_acs
- AzureOpenAIDriver — model_endpoint/azure_openai
- AzureSQLDatabaseDriver — mssql/azure_sql_database,
  mssql/azure_sql_serverless, mssql/azure_sql_hyperscale
- AzureSQLManagedInstanceDriver — mssql/azure_sql_managed_instance
- AzureAPIMDriver — api_gateway/api_management
- AzureFunctionsDriver — faas/azure_functions
- AzureKeyVaultKeyDriver — encryption_key/key_vault_key
"""

from _sdk.base import ProviderPlugin
from azure.cluster_aks import AKSClusterDriver
from azure.dns_azuredns import AzureDNSDriver
from azure.identity_federated import AzureFederatedIdentityDriver
from azure.ingress_appgw import AzureAppGatewayIngressDriver
from azure.managed.api_management import AzureAPIMDriver
from azure.managed.cache_redis import AzureCacheRedisDriver
from azure.managed.cosmos import AzureCosmosDriver
from azure.managed.cosmos_api import AzureCosmosApiDriver
from azure.managed.email_acs import AzureCommunicationEmailDriver
from azure.managed.encryption_key_vault import AzureKeyVaultKeyDriver
from azure.managed.event_grid import AzureEventGridDriver
from azure.managed.event_grid_namespace import AzureEventGridNamespaceDriver
from azure.managed.event_hubs import AzureEventHubsDriver
from azure.managed.faas_functions import AzureFunctionsDriver
from azure.managed.filesystem_files import AzureFilesDriver
from azure.managed.filesystem_files_classic import AzureFilesClassicDriver
from azure.managed.managed_redis import AzureManagedRedisDriver
from azure.managed.model_endpoint_aoai import AzureOpenAIDriver
from azure.managed.mssql_sql import AzureSQLDatabaseDriver, AzureSQLManagedInstanceDriver
from azure.managed.mysql_flexible import AzureMySQLFlexibleDriver
from azure.managed.object_store_blob import (
    AzureBlobStorageDriver,
    BlobStorageDriver,
)
from azure.managed.postgres_flexible import AzurePostgresFlexibleDriver
from azure.managed.private_endpoint import AzurePrivateEndpointDriver
from azure.managed.queue_servicebus import (
    AzureServiceBusDriver,
    ServiceBusDriver,
)
from azure.managed.search_aisearch import AzureAISearchFullTextDriver
from azure.managed.timeseries_monitor import AzureMonitorPrometheusDriver
from azure.managed.vector_search import AzureAISearchVectorDriver
from azure.notification_anh import AzureNotificationHubsDriver
from azure.registry_acr import ACRDriver
from azure.secrets_keyvault import KeyVaultSecretsBackend
from azure.tls_appgw import AzureAppGatewayTlsDriver

_MANAGED_CONFIG_PROPERTIES = {
    "apim_publisher_email": {
        "type": "string",
        "format": "email",
        "description": "Required publisher email for managed API Management services.",
    },
    "apim_publisher_name": {"type": "string", "default": "Astrolift"},
    "apim_service_name_prefix": {
        "type": "string",
        "pattern": "^[A-Za-z](?:[A-Za-z0-9-]{0,47}[A-Za-z0-9])?$",
        "default": "astrolift",
    },
    "apim_allowed_skus": {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {
            "type": "string",
            "enum": [
                "Basic",
                "BasicV2",
                "Consumption",
                "Developer",
                "Premium",
                "PremiumV2",
                "Standard",
                "StandardV2",
            ],
        },
        "default": ["Developer", "Basic", "Standard", "Premium"],
    },
    "apim_max_capacity": {"type": "integer", "minimum": 1, "maximum": 12, "default": 4},
    "apim_allowed_policy_kinds": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string", "enum": ["backend", "cors", "managed_identity"]},
        "default": ["backend", "cors"],
        "description": "Only these typed policy builders may emit APIM XML; arbitrary XML is never accepted.",
    },
    "apim_allowed_backend_host_suffixes": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^\\.[a-z0-9.-]+$"},
        "default": [".azurecontainerapps.io", ".azurewebsites.net"],
    },
    "apim_allowed_backend_identity_resources": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
    },
    "apim_allowed_user_assigned_identity_ids": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
    },
    "apim_allowed_subnet_ids": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
    },
    "apim_allowed_custom_domain_suffixes": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^\\.[a-z0-9.-]+$"},
    },
    "apim_allowed_key_vault_secret_prefixes": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string", "format": "uri"},
    },
    "apim_allow_internal_network": {"type": "boolean", "default": False},
    "apim_allow_custom_domains": {"type": "boolean", "default": False},
    "apim_allow_subscriptions": {"type": "boolean", "default": False},
    "apim_allow_child_pruning": {"type": "boolean", "default": False},
    "apim_deletion_protection_default": {"type": "boolean", "default": True},
    "apim_max_apis": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
    "apim_max_routes_per_api": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
    "apim_max_backends": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
    "apim_max_subscriptions": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 25},
    "apim_api_endpoint": {
        "type": "string",
        "const": "https://management.azure.com",
        "default": "https://management.azure.com",
    },
    "apim_request_timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120, "default": 30},
    "apim_operation_timeout_seconds": {
        "type": "number",
        "minimum": 60,
        "maximum": 7200,
        "default": 3600,
    },
    "apim_poll_interval_seconds": {"type": "number", "minimum": 0.1, "maximum": 30, "default": 5},
    "blob_container_name_prefix": {"type": "string", "default": "astrolift"},
    "blob_versioning_enabled": {"type": "boolean", "default": True},
    "servicebus_queue_name_prefix": {"type": "string", "default": "astrolift"},
    "servicebus_topic_name_prefix": {"type": "string", "default": "astrolift"},
    "servicebus_location": {
        "type": "string",
        "description": "Region of the operator-managed Service Bus namespace.",
    },
    "servicebus_default_message_ttl": {"type": "string", "default": "P14D"},
    "servicebus_max_size_in_megabytes": {"type": "integer", "minimum": 1024, "default": 1024},
    "servicebus_enable_partitioning": {"type": "boolean", "default": False},
    "servicebus_dead_lettering_on_message_expiration": {"type": "boolean", "default": True},
    "servicebus_max_delivery_count": {"type": "integer", "minimum": 1, "default": 10},
    "servicebus_lock_duration": {"type": "string", "default": "PT30S"},
    "eventhubs_namespace_name_prefix": {"type": "string", "default": "astrolift-eh"},
    "eventhubs_event_hub_name_prefix": {"type": "string", "default": "astrolift"},
    "eventhubs_default_sku": {
        "type": "string",
        "enum": ["Basic", "Standard", "Premium"],
        "default": "Standard",
    },
    "eventhubs_default_capacity": {"type": "integer", "minimum": 1, "maximum": 40, "default": 1},
    "eventhubs_default_consumer_group": {"type": "string", "default": "astrolift"},
    "eventhubs_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled", "SecuredByPerimeter"],
        "default": "Enabled",
    },
    "eventgrid_topic_name_prefix": {"type": "string", "default": "astrolift-eg"},
    "eventgrid_default_input_schema": {
        "type": "string",
        "enum": ["CloudEventSchemaV1_0", "EventGridSchema"],
        "default": "CloudEventSchemaV1_0",
    },
    "eventgrid_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled"],
        "default": "Enabled",
    },
    "eventgrid_namespace_name_prefix": {"type": "string", "default": "astrolift-egns"},
    "eventgrid_namespace_topic_name_prefix": {"type": "string", "default": "events"},
    "eventgrid_namespace_secret_name_prefix": {"type": "string", "default": "event-grid-namespace"},
    "eventgrid_namespace_default_capacity": {
        "type": "integer",
        "minimum": 1,
        "maximum": 40,
        "default": 1,
    },
    "files_name_prefix": {"type": "string", "default": "astrolift-files"},
    "files_default_storage_gib": {"type": "integer", "minimum": 32, "maximum": 262144, "default": 32},
    "files_default_redundancy": {
        "type": "string",
        "enum": ["Local", "Zone"],
        "default": "Local",
    },
    "files_default_root_squash": {
        "type": "string",
        "enum": ["NoRootSquash", "RootSquash", "AllSquash"],
        "default": "RootSquash",
    },
    "files_encryption_in_transit_required_default": {"type": "boolean", "default": True},
    "files_allowed_subnet_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    "files_deletion_protection_default": {"type": "boolean", "default": True},
    "files_classic_account_name_prefix": {
        "type": "string",
        "pattern": "^[a-z0-9]{3,14}$",
        "default": "astroliftfs",
    },
    "files_classic_share_name_prefix": {
        "type": "string",
        "pattern": "^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        "maxLength": 50,
        "default": "astrolift-files",
    },
    "files_classic_default_protocol": {
        "type": "string",
        "enum": ["SMB", "NFS"],
        "default": "SMB",
    },
    "files_classic_default_sku": {
        "type": "string",
        "enum": [
            "Standard_LRS",
            "Standard_GRS",
            "Standard_RAGRS",
            "Standard_ZRS",
            "Standard_GZRS",
            "Standard_RAGZRS",
            "Premium_LRS",
            "Premium_ZRS",
        ],
        "default": "Standard_LRS",
    },
    "files_classic_default_quota_gib": {"type": "integer", "minimum": 1, "maximum": 102400, "default": 100},
    "files_classic_default_access_tier": {
        "type": "string",
        "enum": ["TransactionOptimized", "Hot", "Cool", "Premium"],
        "default": "TransactionOptimized",
    },
    "files_classic_soft_delete_retention_days": {
        "type": "integer",
        "minimum": 1,
        "maximum": 365,
        "default": 14,
    },
    "files_classic_allow_public_access_default": {"type": "boolean", "default": False},
    "files_classic_secret_name_prefix": {
        "type": "string",
        "pattern": "^[A-Za-z0-9-]{1,92}$",
        "default": "astrolift-files",
    },
    "faas_function_name_prefix": {"type": "string", "default": "astrolift"},
    "faas_default_plan_resource_id": {"type": "string"},
    "faas_default_identity_resource_id": {"type": "string"},
    "faas_allowed_plan_resource_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
        "description": "Exact operator-approved Microsoft.Web/serverfarms resource IDs.",
    },
    "faas_allowed_identity_resource_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
        "description": "Exact operator-approved user-assigned managed identity resource IDs.",
    },
    "faas_allowed_storage_resource_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
        "description": "Exact operator-approved host/deployment Storage Account resource IDs.",
    },
    "faas_allowed_registry_resource_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
        "description": "Exact operator-approved ACR resource IDs for container functions.",
    },
    "faas_allowed_subnet_resource_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
        "description": "Exact operator-approved Function App VNet integration subnet IDs.",
    },
    "faas_allow_public_network": {"type": "boolean", "default": False},
    "faas_deletion_protection_default": {"type": "boolean", "default": True},
    "faas_storage_blob_endpoint_suffix": {
        "type": "string",
        "default": "blob.core.windows.net",
    },
    "faas_registry_login_server_suffix": {"type": "string", "default": "azurecr.io"},
    "faas_site_api_version": {"type": "string", "default": "2024-11-01"},
    "faas_identity_api_version": {"type": "string", "default": "2023-01-31"},
    "faas_storage_api_version": {"type": "string", "default": "2023-05-01"},
    "faas_registry_api_version": {"type": "string", "default": "2023-07-01"},
    "faas_authorization_api_version": {"type": "string", "default": "2022-04-01"},
    "faas_operation_timeout_seconds": {"type": "number", "minimum": 1, "default": 900},
    "faas_poll_interval_seconds": {"type": "number", "minimum": 0.1, "default": 3},
    "faas_max_instances": {"type": "integer", "minimum": 1, "default": 100},
    "postgres_server_name_prefix": {"type": "string", "default": "astrolift"},
    "postgres_engine_version": {"type": "string", "default": "16"},
    "postgres_backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35, "default": 7},
    "postgres_high_availability_default": {
        "type": "string",
        "enum": ["Disabled", "SameZone", "ZoneRedundant"],
        "default": "Disabled",
    },
    "postgres_deletion_protection_default": {"type": "boolean", "default": True},
    "postgres_secret_name_prefix": {"type": "string", "default": "astrolift-pg"},
    "mysql_server_name_prefix": {"type": "string", "default": "astrolift"},
    "mysql_engine_version": {"type": "string", "default": "8.0.21"},
    "mysql_backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35, "default": 7},
    "mysql_high_availability_default": {
        "type": "string",
        "enum": ["Disabled", "SameZone", "ZoneRedundant"],
        "default": "Disabled",
    },
    "mysql_deletion_protection_default": {"type": "boolean", "default": True},
    "mysql_secret_name_prefix": {"type": "string", "default": "astrolift-mysql"},
    "redis_cache_name_prefix": {"type": "string", "default": "astrolift"},
    "redis_default_sku": {"type": "string", "default": "Standard_C1"},
    "redis_minimum_tls_version_default": {"type": "string", "default": "1.2"},
    "redis_enable_non_ssl_port_default": {"type": "boolean", "default": False},
    "redis_secret_name_prefix": {"type": "string", "default": "astrolift-redis"},
    "redis_backup_container_uri": {
        "type": "string",
        "format": "uri",
        "description": ("Non-secret HTTPS Blob container URI for Premium Redis RDB exports using managed identity."),
    },
    "redis_backup_storage_subscription_id": {"type": "string"},
    "managed_redis_cluster_name_prefix": {"type": "string", "default": "astrolift-amr"},
    "managed_redis_database_name": {"type": "string", "default": "default"},
    "managed_redis_default_sku": {"type": "string", "default": "Balanced_B3"},
    "managed_redis_high_availability_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled"],
        "default": "Enabled",
    },
    "managed_redis_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled"],
        "default": "Enabled",
    },
    "managed_redis_clustering_policy_default": {
        "type": "string",
        "enum": ["EnterpriseCluster", "OSSCluster", "NoCluster"],
        "default": "OSSCluster",
    },
    "managed_redis_eviction_policy_default": {"type": "string", "default": "AllKeysLRU"},
    "managed_redis_secret_name_prefix": {"type": "string", "default": "astrolift-amr"},
    "cosmos_account_name_prefix": {"type": "string", "default": "astrolift"},
    "cosmos_database_name_default": {"type": "string", "default": "astrolift"},
    "cosmos_default_api_kind": {
        "type": "string",
        "enum": ["MongoDB", "GlobalDocumentDB", "Cassandra", "Table", "Gremlin"],
        "default": "MongoDB",
    },
    "cosmos_backup_policy_default": {
        "type": "string",
        "enum": ["Continuous", "Periodic"],
        "default": "Continuous",
    },
    "cosmos_secret_name_prefix": {"type": "string", "default": "astrolift-cosmos"},
    "cosmos_api_account_name_prefix": {"type": "string", "default": "astrolift-cosmos"},
    "cosmos_api_database_name_default": {"type": "string", "default": "astrolift"},
    "cosmos_api_backup_policy_default": {
        "type": "string",
        "enum": ["Continuous", "Periodic"],
        "default": "Continuous",
    },
    "cosmos_api_continuous_backup_tier_default": {
        "type": "string",
        "enum": ["Continuous7Days", "Continuous30Days"],
        "default": "Continuous30Days",
    },
    "cosmos_api_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled", "SecuredByPerimeter"],
        "default": "Enabled",
    },
    "cosmos_api_consistency_level_default": {
        "type": "string",
        "enum": ["Eventual", "Session", "BoundedStaleness", "Strong", "ConsistentPrefix"],
        "default": "Session",
    },
    "cosmos_api_secret_name_prefix": {"type": "string", "default": "astrolift-cosmos-api"},
    "ai_search_service_name_prefix": {"type": "string", "default": "astrolift"},
    "ai_search_default_sku": {"type": "string", "default": "basic"},
    "ai_search_replica_count_default": {"type": "integer", "minimum": 1, "default": 1},
    "ai_search_partition_count_default": {"type": "integer", "minimum": 1, "default": 1},
    "ai_search_public_network_access_default": {
        "type": "string",
        "enum": ["enabled", "disabled"],
        "default": "enabled",
    },
    "ai_search_secret_name_prefix": {"type": "string", "default": "astrolift-search"},
    "ai_search_vector_service_name_prefix": {"type": "string", "default": "astrolift-vec"},
    "ai_search_vector_default_sku": {"type": "string", "default": "basic"},
    "ai_search_embedding_dimension_default": {"type": "integer", "minimum": 1, "default": 1536},
    "ai_search_vector_profile_default": {"type": "string", "default": "default-profile"},
    "ai_search_vector_algorithm_default": {
        "type": "string",
        "enum": ["hnsw", "exhaustiveKnn"],
        "default": "hnsw",
    },
    "ai_search_vector_secret_name_prefix": {"type": "string", "default": "astrolift-aisearch"},
    "monitor_workspace_name_prefix": {"type": "string", "default": "astrolift-tsdb"},
    "monitor_create_linked_log_analytics_default": {"type": "boolean", "default": True},
    "monitor_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled", "Disabled"],
        "default": "Enabled",
    },
    "acs_email_location": {"type": "string", "default": "global"},
    "acs_email_service_name": {"type": "string", "default": "astrolift-email"},
    "acs_communication_resource_id": {"type": "string"},
    "acs_email_domain_management": {
        "type": "string",
        "enum": ["AzureManaged", "CustomerManaged"],
        "default": "AzureManaged",
    },
    "acs_email_secret_name_prefix": {"type": "string", "default": "astrolift-acs-email"},
    "acs_email_delete_data_default": {"type": "boolean", "default": False},
    "azure_openai_account_name": {"type": "string"},
    "azure_openai_deployment_name_prefix": {"type": "string", "default": "astrolift"},
    "azure_openai_api_version": {"type": "string", "default": "2024-02-15-preview"},
    "azure_openai_secret_name_prefix": {"type": "string", "default": "astrolift-aoai"},
    "mssql_server_name_prefix": {"type": "string", "default": "astrolift-sql"},
    "mssql_database_name_prefix": {"type": "string", "default": "astrolift"},
    "mssql_administrator_login": {"type": "string", "default": "astrolift"},
    "mssql_secret_name_prefix": {"type": "string", "default": "astrolift-mssql"},
    "mssql_virtual_network_subnet_id": {"type": "string"},
    "mssql_virtual_network_rule_name": {"type": "string", "default": "astrolift-aks"},
    "mssql_ignore_missing_vnet_service_endpoint": {"type": "boolean", "default": False},
    "mssql_public_network_access_default": {
        "type": "string",
        "enum": ["Enabled"],
        "default": "Enabled",
        "description": (
            "Azure selected-network mode used with the required VNet service-endpoint rule. "
            "Disabled requires the separately planned Private Endpoint driver."
        ),
    },
    "mssql_minimal_tls_version_default": {"type": "string", "default": "1.2"},
    "mssql_backup_retention_days_default": {"type": "integer", "minimum": 1, "maximum": 35, "default": 7},
    "mssql_backup_storage_redundancy_default": {
        "type": "string",
        "enum": ["Local", "Zone", "Geo", "GeoZone"],
        "default": "Geo",
    },
    "mssql_serverless_auto_pause_delay_minutes_default": {"type": "integer", "minimum": -1, "default": 60},
    "mssql_serverless_min_capacity_default": {"type": "number", "minimum": 0.5, "default": 0.5},
    "mssql_managed_instance_subnet_id": {"type": "string"},
    "mssql_managed_instance_name_prefix": {"type": "string", "default": "astrolift-mi"},
    "mssql_managed_instance_secret_name_prefix": {"type": "string", "default": "astrolift-mssql-mi"},
    "mssql_managed_instance_license_type_default": {
        "type": "string",
        "enum": ["LicenseIncluded", "BasePrice"],
        "default": "LicenseIncluded",
    },
    "mssql_managed_instance_public_data_endpoint_enabled_default": {"type": "boolean", "default": False},
    "private_link_name_prefix": {"type": "string", "default": "astrolift-pe"},
    "private_link_default_subnet_id": {"type": "string"},
    "private_link_allowed_subnet_ids": {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string"},
    },
    "private_link_allowed_service_id_prefixes": {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string"},
    },
    "private_link_allowed_private_dns_zone_id_prefixes": {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
        "default": [],
    },
    "private_link_allow_manual_approval": {"type": "boolean", "default": False},
    "private_link_max_group_ids": {"type": "integer", "minimum": 1, "maximum": 64, "default": 8},
    "private_link_max_private_dns_zones": {"type": "integer", "minimum": 0, "maximum": 64, "default": 8},
    "private_link_deletion_protection_default": {"type": "boolean", "default": True},
    "key_vault_key_name_prefix": {
        "type": "string",
        "pattern": "^[0-9a-zA-Z-]{1,127}$",
        "default": "astrolift",
    },
    "key_vault_key_deletion_protection_default": {"type": "boolean", "default": True},
    "key_vault_key_purge_on_delete_default": {"type": "boolean", "default": False},
    "key_vault_key_rotation_period_default": {
        "type": "string",
        "pattern": "^P(?:\\d+Y)?(?:\\d+M)?(?:\\d+D)?$",
        "default": "P90D",
    },
    "key_vault_key_api_version": {"type": "string", "default": "7.4"},
    "key_vault_key_request_timeout_seconds": {
        "type": "number",
        "minimum": 1,
        "maximum": 120,
        "default": 30,
    },
}

PLUGIN = ProviderPlugin(
    id="azure",
    display_name="Microsoft Azure",
    drivers={
        "registry": ACRDriver,
        "secrets": KeyVaultSecretsBackend,
        "identity": AzureFederatedIdentityDriver,
        "dns": AzureDNSDriver,
        "tls": AzureAppGatewayTlsDriver,
        "cluster": AKSClusterDriver,
        "ingress": AzureAppGatewayIngressDriver,
        "notification": AzureNotificationHubsDriver,
    },
    managed_service_drivers={
        ("object_store", "blob"): BlobStorageDriver,
        ("queue", "servicebus"): ServiceBusDriver,
        ("mysql", "azure_mysql_flex"): AzureMySQLFlexibleDriver,
        ("postgres", "azure_pg_flex"): AzurePostgresFlexibleDriver,
        ("redis", "azure_cache_redis"): AzureCacheRedisDriver,
        ("redis", "azure_managed_redis"): AzureManagedRedisDriver,
        ("object_store", "azure_blob"): AzureBlobStorageDriver,
        ("queue", "azure_servicebus"): AzureServiceBusDriver,
        ("topic", "service_bus_topic"): AzureServiceBusDriver,
        ("stream", "event_hubs"): AzureEventHubsDriver,
        ("event_stream", "event_hubs_kafka"): AzureEventHubsDriver,
        ("event_bus", "event_grid"): AzureEventGridDriver,
        ("event_bus", "event_grid_namespace"): AzureEventGridNamespaceDriver,
        ("filesystem", "azure_files"): AzureFilesDriver,
        ("filesystem", "azure_files_classic"): AzureFilesClassicDriver,
        ("kv_store", "cosmos"): AzureCosmosDriver,
        ("document_db", "cosmos_nosql"): AzureCosmosApiDriver,
        ("document_db", "cosmos_mongodb"): AzureCosmosApiDriver,
        ("graph_db", "cosmos_gremlin"): AzureCosmosApiDriver,
        ("wide_column", "cosmos_cassandra"): AzureCosmosApiDriver,
        ("kv_store", "cosmos_table"): AzureCosmosApiDriver,
        ("search", "azure_ai_search_fulltext"): AzureAISearchFullTextDriver,
        ("vector_index", "azure_ai_search_vector"): AzureAISearchVectorDriver,
        ("time_series", "azure_monitor_prometheus"): AzureMonitorPrometheusDriver,
        ("email", "azure_acs"): AzureCommunicationEmailDriver,
        ("model_endpoint", "azure_openai"): AzureOpenAIDriver,
        ("mssql", "azure_sql_database"): AzureSQLDatabaseDriver,
        ("mssql", "azure_sql_serverless"): AzureSQLDatabaseDriver,
        ("mssql", "azure_sql_hyperscale"): AzureSQLDatabaseDriver,
        ("mssql", "azure_sql_managed_instance"): AzureSQLManagedInstanceDriver,
        ("faas", "azure_functions"): AzureFunctionsDriver,
        ("api_gateway", "api_management"): AzureAPIMDriver,
        ("private_endpoint", "private_link"): AzurePrivateEndpointDriver,
        ("encryption_key", "key_vault_key"): AzureKeyVaultKeyDriver,
    },
    config_schema={
        "type": "object",
        "required": ["subscription_id", "tenant_id", "resource_group"],
        "properties": {
            "subscription_id": {
                "type": "string",
                "description": "Azure subscription ID (UUID).",
            },
            "tenant_id": {
                "type": "string",
                "description": "Azure AD tenant ID (UUID).",
            },
            "resource_group": {
                "type": "string",
                "description": ("Default resource group for platform-managed resources."),
            },
            "location": {
                "type": "string",
                "default": "eastus",
                "description": "Default Azure region.",
            },
            "cluster_oidc_issuer": {
                "type": "string",
                "description": ("AKS cluster OIDC issuer URL. Required for Workload Identity federated credentials."),
            },
            "registry_name": {
                "type": "string",
                "description": ("ACR registry name (without .azurecr.io suffix)."),
            },
            "acr_sku": {
                "type": "string",
                "enum": ["Basic", "Standard", "Premium"],
                "default": "Standard",
            },
            "acr_admin_enabled": {"type": "boolean", "default": False},
            "acr_immutable_tags": {"type": "boolean", "default": True},
            "vault_url": {
                "type": "string",
                "description": ("Key Vault URL (https://<name>.vault.azure.net)."),
            },
            "keyvault_secret_name_prefix": {
                "type": "string",
                "pattern": "^[A-Za-z0-9-]{1,100}$",
                "default": "astrolift",
                "description": "Namespace prefix for logical secret-bundle paths.",
            },
            "storage_account": {
                "type": "string",
                "description": ("Storage account name for object_store managed-service binding."),
            },
            "servicebus_namespace": {
                "type": "string",
                "description": ("Service Bus namespace name for queue and topic managed-service bindings."),
            },
            **_MANAGED_CONFIG_PROPERTIES,
            "ingress_variant": {
                "type": "string",
                "enum": ["agic", "gateway_api"],
                "default": "agic",
            },
            "appgw_id": {
                "type": "string",
                "description": ("Application Gateway resource ID — required when ingress_variant=agic."),
            },
            "akv_secret_id_for_tls": {
                "type": "string",
                "description": ("Key Vault secret ID for the TLS cert (PFX). AGIC reads this via SSL profile."),
            },
            "managed_cert_name_prefix": {
                "type": "string",
                "default": "astrolift",
            },
            "notification_hubs_namespace": {"type": "string"},
            "notification_hub_name": {"type": "string"},
            "notification_hubs_api_version": {
                "type": "string",
                "default": "2020-06",
            },
            "notification_hubs_timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 120,
                "default": 10,
            },
        },
    },
)
