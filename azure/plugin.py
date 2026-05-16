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

Managed services (#47 MVP — symmetry with AWS S3 + SQS,
GCP GCS + Pub/Sub):
- BlobStorageDriver — object_store/blob
- ServiceBusDriver — queue/servicebus

Managed services (#370 — MySQL across clouds):
- AzureMySQLFlexibleDriver — mysql/azure_mysql_flex

Managed services (#364 — cross-cloud parity with the GCP
managed-services completion):
- AzurePostgresFlexibleDriver — postgres/azure_pg_flex
- AzureCacheRedisDriver — redis/azure_cache_redis
- AzureBlobStorageDriver — object_store/azure_blob
- AzureServiceBusDriver — queue/azure_servicebus

Pending (separate tickets, follow-on managed services):
- Cosmos DB (Mongo / Cassandra / SQL APIs)
- Azure Files (filesystem)
- Event Hubs (event-stream)
- Azure OpenAI (model endpoint)
"""

from _sdk.base import ProviderPlugin
from azure.cluster_aks import AKSClusterDriver
from azure.dns_azuredns import AzureDNSDriver
from azure.identity_federated import AzureFederatedIdentityDriver
from azure.ingress_appgw import AzureAppGatewayIngressDriver
from azure.managed.cache_redis import AzureCacheRedisDriver
from azure.managed.cosmos import AzureCosmosDriver
from azure.managed.model_endpoint_aoai import AzureOpenAIDriver
from azure.managed.mysql_flexible import AzureMySQLFlexibleDriver
from azure.managed.object_store_blob import (
    AzureBlobStorageDriver,
    BlobStorageDriver,
)
from azure.managed.postgres_flexible import AzurePostgresFlexibleDriver
from azure.managed.queue_servicebus import (
    AzureServiceBusDriver,
    ServiceBusDriver,
)
from azure.managed.search_aisearch import AzureAISearchFullTextDriver
from azure.managed.vector_search import AzureAISearchVectorDriver
from azure.registry_acr import ACRDriver
from azure.secrets_keyvault import KeyVaultSecretsBackend
from azure.tls_appgw import AzureAppGatewayTlsDriver

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
    },
    managed_service_drivers={
        ("object_store", "blob"): BlobStorageDriver,
        ("queue", "servicebus"): ServiceBusDriver,
        ("mysql", "azure_mysql_flex"): AzureMySQLFlexibleDriver,
        ("postgres", "azure_pg_flex"): AzurePostgresFlexibleDriver,
        ("redis", "azure_cache_redis"): AzureCacheRedisDriver,
        ("object_store", "azure_blob"): AzureBlobStorageDriver,
        ("queue", "azure_servicebus"): AzureServiceBusDriver,
        ("kv_store", "cosmos"): AzureCosmosDriver,
        ("search", "azure_ai_search_fulltext"): AzureAISearchFullTextDriver,
        ("vector_index", "azure_ai_search_vector"): AzureAISearchVectorDriver,
        ("model_endpoint", "azure_openai"): AzureOpenAIDriver,
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
                "description": ("Default resource group for platform-managed " "resources."),
            },
            "location": {
                "type": "string",
                "default": "eastus",
                "description": "Default Azure region.",
            },
            "cluster_oidc_issuer": {
                "type": "string",
                "description": (
                    "AKS cluster OIDC issuer URL. Required for " "Workload Identity federated credentials."
                ),
            },
            "registry_name": {
                "type": "string",
                "description": ("ACR registry name (without .azurecr.io suffix)."),
            },
            "vault_url": {
                "type": "string",
                "description": ("Key Vault URL " "(https://<name>.vault.azure.net)."),
            },
            "storage_account": {
                "type": "string",
                "description": ("Storage account name for object_store " "managed-service binding."),
            },
            "servicebus_namespace": {
                "type": "string",
                "description": ("Service Bus namespace name for queue " "managed-service binding."),
            },
            "ingress_variant": {
                "type": "string",
                "enum": ["agic", "gateway_api"],
                "default": "agic",
            },
            "appgw_id": {
                "type": "string",
                "description": ("Application Gateway resource ID — required " "when ingress_variant=agic."),
            },
            "akv_secret_id_for_tls": {
                "type": "string",
                "description": ("Key Vault secret ID for the TLS cert (PFX). " "AGIC reads this via SSL profile."),
            },
        },
    },
)
