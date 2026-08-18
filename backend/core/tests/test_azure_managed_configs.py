from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cluster_observability import ClusterObservabilityError, managed_config_for


def _cluster(**provider_overrides: object) -> SimpleNamespace:
    provider_config = {
        "subscription_id": "00000000-1111-2222-3333-444444444444",
        "resource_group": "rg-platform-prod",
        "location": "eastus2",
        "vault_url": "https://platform-prod.vault.azure.net",
        "storage_account": "platformprod",
        "servicebus_namespace": "platform-prod-bus",
        "files_allowed_subnet_ids": [
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-network/providers/Microsoft.Network/"
            "virtualNetworks/platform/subnets/aks"
        ],
        "azure_openai_account_name": "platform-prod-ai",
        "mssql_managed_instance_subnet_id": (
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-platform-prod/providers/Microsoft.Network/"
            "virtualNetworks/platform/subnets/sql-mi"
        ),
        "mssql_virtual_network_subnet_id": (
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-platform-prod/providers/Microsoft.Network/"
            "virtualNetworks/platform/subnets/aks"
        ),
        "acs_communication_resource_id": (
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-platform-prod/providers/"
            "Microsoft.Communication/communicationServices/platform-prod"
        ),
        "apim_publisher_email": "platform@example.com",
        "private_link_default_subnet_id": (
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-network/providers/Microsoft.Network/"
            "virtualNetworks/platform/subnets/private-endpoints"
        ),
        "private_link_allowed_subnet_ids": [
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-network/providers/Microsoft.Network/"
            "virtualNetworks/platform/subnets/private-endpoints"
        ],
        "private_link_allowed_service_id_prefixes": [
            "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-data"
        ],
        "private_link_allowed_private_dns_zone_id_prefixes": [
            "/subscriptions/00000000-1111-2222-3333-444444444444/"
            "resourceGroups/rg-network/providers/Microsoft.Network/privateDnsZones"
        ],
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="azure-prod",
        region="westus2",
        provider_config=provider_config,
        auth_config={},
    )


@pytest.mark.parametrize(
    ("kind", "variant", "config_type"),
    [
        ("object_store", "blob", "BlobStorageConfig"),
        ("object_store", "azure_blob", "AzureBlobConfig"),
        ("queue", "servicebus", "ServiceBusConfig"),
        ("queue", "azure_servicebus", "AzureServiceBusConfig"),
        ("topic", "service_bus_topic", "AzureServiceBusConfig"),
        ("stream", "event_hubs", "AzureEventHubsConfig"),
        ("event_stream", "event_hubs_kafka", "AzureEventHubsConfig"),
        ("event_bus", "event_grid", "AzureEventGridConfig"),
        ("event_bus", "event_grid_namespace", "AzureEventGridNamespaceConfig"),
        ("filesystem", "azure_files", "AzureFilesConfig"),
        ("postgres", "azure_pg_flex", "AzurePostgresConfig"),
        ("mysql", "azure_mysql_flex", "AzureMySQLConfig"),
        ("redis", "azure_cache_redis", "AzureCacheRedisConfig"),
        ("redis", "azure_managed_redis", "AzureManagedRedisConfig"),
        ("kv_store", "cosmos", "AzureCosmosConfig"),
        ("document_db", "cosmos_nosql", "AzureCosmosApiConfig"),
        ("document_db", "cosmos_mongodb", "AzureCosmosApiConfig"),
        ("graph_db", "cosmos_gremlin", "AzureCosmosApiConfig"),
        ("wide_column", "cosmos_cassandra", "AzureCosmosApiConfig"),
        ("kv_store", "cosmos_table", "AzureCosmosApiConfig"),
        ("filesystem", "azure_files_classic", "AzureFilesClassicConfig"),
        ("search", "azure_ai_search_fulltext", "AzureAISearchConfig"),
        ("vector_index", "azure_ai_search_vector", "AzureAISearchVectorConfig"),
        ("time_series", "azure_monitor_prometheus", "AzureMonitorPrometheusConfig"),
        ("email", "azure_acs", "AzureCommunicationEmailConfig"),
        ("model_endpoint", "azure_openai", "AzureOpenAIConfig"),
        ("mssql", "azure_sql_database", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_serverless", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_hyperscale", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_managed_instance", "AzureSQLManagedInstanceConfig"),
        ("faas", "azure_functions", "AzureFunctionsConfig"),
        ("api_gateway", "api_management", "AzureAPIMConfig"),
        ("private_endpoint", "private_link", "AzurePrivateEndpointConfig"),
        ("encryption_key", "key_vault_key", "AzureKeyVaultKeyConfig"),
    ],
)
def test_every_registered_azure_managed_service_has_runtime_config(
    kind: str,
    variant: str,
    config_type: str,
) -> None:
    config = managed_config_for("azure", _cluster(), kind=kind, variant=variant)
    assert type(config).__name__ == config_type


def test_registered_driver_matrix_and_runtime_builder_stay_in_sync() -> None:
    from azure.plugin import PLUGIN

    for kind, variant in sorted(PLUGIN.managed_service_drivers):
        config = managed_config_for("azure", _cluster(), kind=kind, variant=variant)
        assert config is not None, (kind, variant)


def test_database_controls_are_preserved() -> None:
    postgres = managed_config_for(
        "azure",
        _cluster(
            postgres_server_name_prefix="smd-pg",
            postgres_engine_version="17",
            postgres_backup_retention_days=31,
            postgres_high_availability_default="ZoneRedundant",
            postgres_deletion_protection_default=False,
            postgres_secret_name_prefix="managed-pg",
        ),
        kind="postgres",
        variant="azure_pg_flex",
    )
    assert postgres.server_name_prefix == "smd-pg"
    assert postgres.engine_version == "17"
    assert postgres.backup_retention_days == 31
    assert postgres.high_availability_default == "ZoneRedundant"
    assert postgres.deletion_protection_default is False
    assert postgres.keyvault_url.endswith(".vault.azure.net")
    assert postgres.secret_name_prefix == "managed-pg"

    cosmos = managed_config_for(
        "azure",
        _cluster(
            cosmos_account_name_prefix="smd-cosmos",
            cosmos_database_name_default="triage",
            cosmos_default_api_kind="Gremlin",
            cosmos_backup_policy_default="Periodic",
        ),
        kind="kv_store",
        variant="cosmos",
    )
    assert cosmos.account_name_prefix == "smd-cosmos"
    assert cosmos.database_name_default == "triage"
    assert cosmos.default_api_kind == "Gremlin"
    assert cosmos.backup_policy_default == "Periodic"

    cosmos_api = managed_config_for(
        "azure",
        _cluster(
            cosmos_api_account_name_prefix="smd-api",
            cosmos_api_database_name_default="agents",
            cosmos_api_backup_policy_default="Continuous",
            cosmos_api_continuous_backup_tier_default="Continuous7Days",
            cosmos_api_public_network_access_default="Enabled",
            cosmos_api_consistency_level_default="Strong",
            cosmos_api_secret_name_prefix="managed-cosmos",
        ),
        kind="graph_db",
        variant="cosmos_gremlin",
    )
    assert cosmos_api.variant == "cosmos_gremlin"
    assert cosmos_api.account_name_prefix == "smd-api"
    assert cosmos_api.database_name_default == "agents"
    assert cosmos_api.continuous_backup_tier_default == "Continuous7Days"
    assert cosmos_api.public_network_access_default == "Enabled"
    assert cosmos_api.consistency_level_default == "Strong"
    assert cosmos_api.secret_name_prefix == "managed-cosmos"
    sql = managed_config_for(
        "azure",
        _cluster(
            mssql_server_name_prefix="smd-sql",
            mssql_database_name_prefix="tenant",
            mssql_backup_retention_days_default=28,
            mssql_backup_storage_redundancy_default="Zone",
            mssql_serverless_auto_pause_delay_minutes_default=720,
            mssql_serverless_min_capacity_default=1.0,
        ),
        kind="mssql",
        variant="azure_sql_serverless",
    )
    assert sql.variant == "azure_sql_serverless"
    assert sql.server_name_prefix == "smd-sql"
    assert sql.database_name_prefix == "tenant"
    assert sql.backup_retention_days_default == 28
    assert sql.backup_storage_redundancy_default == "Zone"
    assert sql.auto_pause_delay_minutes_default == 720
    assert sql.min_capacity_default == 1.0
    assert sql.virtual_network_subnet_id.endswith("/aks")

    managed_instance = managed_config_for(
        "azure",
        _cluster(
            mssql_managed_instance_name_prefix="smd-mi",
            mssql_managed_instance_license_type_default="BasePrice",
        ),
        kind="mssql",
        variant="azure_sql_managed_instance",
    )
    assert managed_instance.instance_name_prefix == "smd-mi"
    assert managed_instance.subnet_id.endswith("/sql-mi")
    assert managed_instance.license_type_default == "BasePrice"


def test_messaging_and_search_controls_are_preserved() -> None:
    bus = managed_config_for(
        "azure",
        _cluster(
            servicebus_topic_name_prefix="events",
            servicebus_default_message_ttl="P2D",
            servicebus_max_size_in_megabytes=5120,
            servicebus_enable_partitioning=True,
            servicebus_dead_lettering_on_message_expiration=False,
            servicebus_max_delivery_count=25,
            servicebus_lock_duration="PT1M",
        ),
        kind="queue",
        variant="azure_servicebus",
    )
    assert bus.namespace_name == "platform-prod-bus"
    assert bus.topic_name_prefix == "events"
    assert bus.default_message_ttl == "P2D"
    assert bus.max_size_in_megabytes == 5120
    assert bus.enable_partitioning is True
    assert bus.dead_lettering_on_message_expiration is False
    assert bus.max_delivery_count == 25
    assert bus.lock_duration == "PT1M"

    topic = managed_config_for(
        "azure",
        _cluster(servicebus_location="westus3"),
        kind="topic",
        variant="service_bus_topic",
    )
    assert topic.handle_kind == "topic"
    assert topic.location == "westus3"
    event_hubs = managed_config_for(
        "azure",
        _cluster(
            eventhubs_namespace_name_prefix="stream",
            eventhubs_event_hub_name_prefix="topic",
            eventhubs_default_sku="Premium",
            eventhubs_default_capacity=2,
            eventhubs_default_consumer_group="workers",
            eventhubs_public_network_access_default="Enabled",
        ),
        kind="event_stream",
        variant="event_hubs_kafka",
    )
    assert event_hubs.variant == "event_hubs_kafka"
    assert event_hubs.namespace_name_prefix == "stream"
    assert event_hubs.event_hub_name_prefix == "topic"
    assert event_hubs.default_sku == "Premium"
    assert event_hubs.default_capacity == 2
    assert event_hubs.default_consumer_group == "workers"
    event_grid = managed_config_for(
        "azure",
        _cluster(
            eventgrid_topic_name_prefix="platform-events",
            eventgrid_default_input_schema="EventGridSchema",
            eventgrid_public_network_access_default="Enabled",
        ),
        kind="event_bus",
        variant="event_grid",
    )
    assert event_grid.topic_name_prefix == "platform-events"
    assert event_grid.default_input_schema == "EventGridSchema"
    assert event_grid.public_network_access_default == "Enabled"

    event_grid_namespace = managed_config_for(
        "azure",
        _cluster(
            eventgrid_namespace_name_prefix="platform-egns",
            eventgrid_namespace_topic_name_prefix="platform-events",
            eventgrid_namespace_secret_name_prefix="egns",
            eventgrid_namespace_default_capacity=4,
        ),
        kind="event_bus",
        variant="event_grid_namespace",
    )
    assert event_grid_namespace.namespace_name_prefix == "platform-egns"
    assert event_grid_namespace.topic_name_prefix == "platform-events"
    assert event_grid_namespace.secret_name_prefix == "egns"
    assert event_grid_namespace.default_capacity == 4

    files = managed_config_for(
        "azure",
        _cluster(
            files_name_prefix="smd-files",
            files_default_storage_gib=256,
            files_default_redundancy="Zone",
            files_default_root_squash="AllSquash",
            files_encryption_in_transit_required_default=False,
            files_deletion_protection_default=False,
        ),
        kind="filesystem",
        variant="azure_files",
    )
    assert files.name_prefix == "smd-files"
    assert files.default_storage_gib == 256
    assert files.default_redundancy == "Zone"
    assert files.default_root_squash == "AllSquash"
    assert files.encryption_in_transit_required_default is False
    assert files.allowed_subnet_ids[0].endswith("/subnets/aks")
    assert files.deletion_protection_default is False

    classic_files = managed_config_for(
        "azure",
        _cluster(
            files_classic_account_name_prefix="smdfiles",
            files_classic_share_name_prefix="shared",
            files_classic_default_protocol="NFS",
            files_classic_default_sku="Premium_ZRS",
            files_classic_default_quota_gib=1024,
            files_classic_default_access_tier="Premium",
            files_classic_soft_delete_retention_days=30,
            files_classic_allow_public_access_default=False,
            files_classic_secret_name_prefix="smd-file-key",
        ),
        kind="filesystem",
        variant="azure_files_classic",
    )
    assert classic_files.account_name_prefix == "smdfiles"
    assert classic_files.share_name_prefix == "shared"
    assert classic_files.default_protocol == "NFS"
    assert classic_files.default_sku == "Premium_ZRS"
    assert classic_files.default_quota_gib == 1024
    assert classic_files.default_access_tier == "Premium"
    assert classic_files.allowed_subnet_ids[0].endswith("/subnets/aks")
    assert classic_files.soft_delete_retention_days == 30
    assert classic_files.keyvault_url == "https://platform-prod.vault.azure.net"
    assert classic_files.secret_name_prefix == "smd-file-key"

    search = managed_config_for(
        "azure",
        _cluster(
            ai_search_service_name_prefix="records",
            ai_search_default_sku="standard3",
            ai_search_replica_count_default=3,
            ai_search_partition_count_default=2,
            ai_search_public_network_access_default="disabled",
        ),
        kind="search",
        variant="azure_ai_search_fulltext",
    )
    assert search.service_name_prefix == "records"
    assert search.default_sku == "standard3"
    assert search.replica_count_default == 3
    assert search.partition_count_default == 2
    assert search.public_network_access_default == "disabled"


def test_redis_backup_controls_are_preserved() -> None:
    redis = managed_config_for(
        "azure",
        _cluster(
            redis_backup_container_uri=("https://backupstore.blob.core.windows.net/redis-backups"),
            redis_backup_storage_subscription_id="backup-subscription",
        ),
        kind="redis",
        variant="azure_cache_redis",
    )
    assert redis.backup_container_uri.endswith("/redis-backups")
    assert redis.backup_storage_subscription_id == "backup-subscription"


def test_managed_redis_controls_are_preserved() -> None:
    redis = managed_config_for(
        "azure",
        _cluster(
            managed_redis_cluster_name_prefix="smd-amr",
            managed_redis_database_name="triage",
            managed_redis_default_sku="MemoryOptimized_M20",
            managed_redis_high_availability_default="Disabled",
            managed_redis_public_network_access_default="Enabled",
            managed_redis_clustering_policy_default="EnterpriseCluster",
            managed_redis_eviction_policy_default="NoEviction",
            managed_redis_secret_name_prefix="managed-amr",
        ),
        kind="redis",
        variant="azure_managed_redis",
    )
    assert redis.cluster_name_prefix == "smd-amr"
    assert redis.database_name == "triage"
    assert redis.default_sku == "MemoryOptimized_M20"
    assert redis.high_availability_default == "Disabled"
    assert redis.public_network_access_default == "Enabled"
    assert redis.clustering_policy_default == "EnterpriseCluster"
    assert redis.eviction_policy_default == "NoEviction"
    assert redis.secret_name_prefix == "managed-amr"


def test_functions_runtime_config_preserves_operator_policy() -> None:
    subscription = "11111111-1111-4111-8111-111111111111"
    base = f"/subscriptions/{subscription}/resourceGroups/rg-functions/providers"
    plan = f"{base}/Microsoft.Web/serverfarms/functions-plan"
    identity = f"{base}/Microsoft.ManagedIdentity/userAssignedIdentities/functions"
    storage = f"{base}/Microsoft.Storage/storageAccounts/functionstorage"
    registry = f"{base}/Microsoft.ContainerRegistry/registries/functionregistry"
    subnet = f"{base}/Microsoft.Network/virtualNetworks/platform/subnets/functions"
    config = managed_config_for(
        "azure",
        _cluster(
            subscription_id=subscription,
            resource_group="rg-functions",
            faas_function_name_prefix="smd-function",
            faas_default_plan_resource_id=plan,
            faas_default_identity_resource_id=identity,
            faas_allowed_plan_resource_ids=[plan],
            faas_allowed_identity_resource_ids=[identity],
            faas_allowed_storage_resource_ids=[storage],
            faas_allowed_registry_resource_ids=[registry],
            faas_allowed_subnet_resource_ids=[subnet],
            faas_allow_public_network=True,
            faas_deletion_protection_default=False,
            faas_storage_api_version="2023-05-01",
            faas_registry_api_version="2023-07-01",
            faas_max_instances=40,
        ),
        kind="faas",
        variant="azure_functions",
    )
    assert config.function_name_prefix == "smd-function"
    assert config.default_plan_resource_id == plan
    assert config.default_identity_resource_id == identity
    assert config.allowed_storage_resource_ids == (storage,)
    assert config.allowed_registry_resource_ids == (registry,)
    assert config.allowed_subnet_resource_ids == (subnet,)
    assert config.allow_public_network is True
    assert config.deletion_protection_default is False
    assert config.max_instances == 40


def test_api_management_controls_are_preserved() -> None:
    config = managed_config_for(
        "azure",
        _cluster(
            apim_publisher_email="apim@example.com",
            apim_publisher_name="Platform API",
            apim_service_name_prefix="smd",
            apim_allowed_skus=["StandardV2", "Premium"],
            apim_max_capacity=6,
            apim_allowed_policy_kinds=["backend", "cors", "managed_identity"],
            apim_allowed_backend_host_suffixes=[".internal.example.com"],
            apim_allowed_backend_identity_resources=["api://backend"],
            apim_allowed_user_assigned_identity_ids=["/subscriptions/sub/resourceGroups/rg/providers/id"],
            apim_allowed_subnet_ids=["/subscriptions/sub/resourceGroups/rg/providers/subnet"],
            apim_allowed_custom_domain_suffixes=[".example.com"],
            apim_allowed_key_vault_secret_prefixes=["https://vault.vault.azure.net/secrets/apim-"],
            apim_allow_internal_network=True,
            apim_allow_custom_domains=True,
            apim_allow_subscriptions=True,
            apim_allow_child_pruning=True,
            apim_allow_adoption=True,
            apim_deletion_protection_default=False,
            apim_max_apis=25,
            apim_max_routes_per_api=40,
            apim_max_backends=15,
            apim_max_subscriptions=5,
        ),
        kind="api_gateway",
        variant="api_management",
    )
    assert config.publisher_email == "apim@example.com"
    assert config.publisher_name == "Platform API"
    assert config.service_name_prefix == "smd"
    assert config.allowed_skus == ("StandardV2", "Premium")
    assert config.max_capacity == 6
    assert config.allowed_policy_kinds == ("backend", "cors", "managed_identity")
    assert config.allowed_backend_host_suffixes == (".internal.example.com",)
    assert config.allowed_backend_identity_resources == ("api://backend",)
    assert config.allow_internal_network is True
    assert config.allow_custom_domains is True
    assert config.allow_subscriptions is True
    assert config.allow_child_pruning is True
    assert config.allow_adoption is True
    assert config.deletion_protection_default is False
    assert (config.max_apis, config.max_routes_per_api, config.max_backends, config.max_subscriptions) == (
        25,
        40,
        15,
        5,
    )


def test_private_link_install_policy_is_preserved() -> None:
    config = managed_config_for(
        "azure",
        _cluster(
            private_link_name_prefix="smd-pe",
            private_link_allow_manual_approval=True,
            private_link_max_group_ids=4,
            private_link_max_private_dns_zones=3,
            private_link_deletion_protection_default=False,
        ),
        kind="private_endpoint",
        variant="private_link",
    )

    assert config.name_prefix == "smd-pe"
    assert config.default_subnet_id.endswith("/private-endpoints")
    assert config.allowed_subnet_ids[0].endswith("/private-endpoints")
    assert config.allowed_service_id_prefixes[0].endswith("/rg-data")
    assert config.allowed_private_dns_zone_id_prefixes[0].endswith("/privateDnsZones")
    assert config.allow_manual_approval is True
    assert config.max_group_ids == 4
    assert config.max_private_dns_zones == 3
    assert config.deletion_protection_default is False


def test_default_variant_selects_the_richer_azure_drivers() -> None:
    blob = managed_config_for("azure", _cluster(), kind="object_store")
    bus = managed_config_for("azure", _cluster(), kind="queue")
    event_bus = managed_config_for("azure", _cluster(), kind="event_bus")
    assert type(blob).__name__ == "AzureBlobConfig"
    assert type(bus).__name__ == "AzureServiceBusConfig"
    assert type(event_bus).__name__ == "AzureEventGridConfig"
    sql = managed_config_for("azure", _cluster(), kind="mssql")
    assert type(sql).__name__ == "AzureSQLDatabaseConfig"
    assert sql.variant == "azure_sql_database"
    faas = managed_config_for("azure", _cluster(), kind="faas")
    assert type(faas).__name__ == "AzureFunctionsConfig"
    gateway = managed_config_for("azure", _cluster(), kind="api_gateway")
    assert type(gateway).__name__ == "AzureAPIMConfig"


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("document_db", "cosmos_nosql"),
        ("graph_db", "cosmos_gremlin"),
        ("wide_column", "cosmos_cassandra"),
    ],
)
def test_default_cosmos_api_variant_matches_portable_kind(kind: str, variant: str) -> None:
    config = managed_config_for("azure", _cluster(), kind=kind)
    assert config.variant == variant


def test_cosmos_api_requires_key_vault_at_runtime_resolution() -> None:
    with pytest.raises(ClusterObservabilityError, match="vault_url"):
        managed_config_for(
            "azure",
            _cluster(vault_url=""),
            kind="document_db",
            variant="cosmos_nosql",
        )


@pytest.mark.parametrize(
    ("field", "kind", "variant"),
    [
        ("subscription_id", "postgres", "azure_pg_flex"),
        ("resource_group", "postgres", "azure_pg_flex"),
        ("storage_account", "object_store", "azure_blob"),
        ("servicebus_namespace", "queue", "azure_servicebus"),
        ("servicebus_namespace", "topic", "service_bus_topic"),
        ("azure_openai_account_name", "model_endpoint", "azure_openai"),
        ("acs_communication_resource_id", "email", "azure_acs"),
        ("mssql_managed_instance_subnet_id", "mssql", "azure_sql_managed_instance"),
        ("mssql_virtual_network_subnet_id", "mssql", "azure_sql_database"),
        ("apim_publisher_email", "api_gateway", "api_management"),
        ("private_link_allowed_subnet_ids", "private_endpoint", "private_link"),
        ("private_link_allowed_service_id_prefixes", "private_endpoint", "private_link"),
        ("vault_url", "encryption_key", "key_vault_key"),
    ],
)
def test_required_install_controls_fail_before_driver_construction(
    field: str,
    kind: str,
    variant: str,
) -> None:
    with pytest.raises(ClusterObservabilityError, match=field):
        managed_config_for(
            "azure",
            _cluster(**{field: ""}),
            kind=kind,
            variant=variant,
        )


def test_unknown_azure_variant_fails_closed() -> None:
    with pytest.raises(ClusterObservabilityError, match="no Azure managed-service config builder"):
        managed_config_for(
            "azure",
            _cluster(),
            kind="filesystem",
            variant="not-shipped",
        )


def test_sql_database_disabled_public_endpoint_requires_private_endpoint_driver() -> None:
    with pytest.raises(ClusterObservabilityError, match="requires a Private Endpoint driver"):
        managed_config_for(
            "azure",
            _cluster(mssql_public_network_access_default="Disabled"),
            kind="mssql",
            variant="azure_sql_database",
        )
