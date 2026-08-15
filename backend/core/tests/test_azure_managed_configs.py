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
        ("filesystem", "azure_files", "AzureFilesConfig"),
        ("filesystem", "azure_files_classic", "AzureFilesClassicConfig"),
        ("postgres", "azure_pg_flex", "AzurePostgresConfig"),
        ("mysql", "azure_mysql_flex", "AzureMySQLConfig"),
        ("redis", "azure_cache_redis", "AzureCacheRedisConfig"),
        ("kv_store", "cosmos", "AzureCosmosConfig"),
        ("search", "azure_ai_search_fulltext", "AzureAISearchConfig"),
        ("vector_index", "azure_ai_search_vector", "AzureAISearchVectorConfig"),
        ("time_series", "azure_monitor_prometheus", "AzureMonitorPrometheusConfig"),
        ("email", "azure_acs", "AzureCommunicationEmailConfig"),
        ("model_endpoint", "azure_openai", "AzureOpenAIConfig"),
        ("mssql", "azure_sql_database", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_serverless", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_hyperscale", "AzureSQLDatabaseConfig"),
        ("mssql", "azure_sql_managed_instance", "AzureSQLManagedInstanceConfig"),
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


def test_default_variant_selects_the_richer_azure_drivers() -> None:
    blob = managed_config_for("azure", _cluster(), kind="object_store")
    bus = managed_config_for("azure", _cluster(), kind="queue")
    assert type(blob).__name__ == "AzureBlobConfig"
    assert type(bus).__name__ == "AzureServiceBusConfig"
    sql = managed_config_for("azure", _cluster(), kind="mssql")
    assert type(sql).__name__ == "AzureSQLDatabaseConfig"
    assert sql.variant == "azure_sql_database"


@pytest.mark.parametrize(
    ("field", "kind", "variant"),
    [
        ("subscription_id", "postgres", "azure_pg_flex"),
        ("resource_group", "postgres", "azure_pg_flex"),
        ("storage_account", "object_store", "azure_blob"),
        ("servicebus_namespace", "queue", "azure_servicebus"),
        ("azure_openai_account_name", "model_endpoint", "azure_openai"),
        ("acs_communication_resource_id", "email", "azure_acs"),
        ("mssql_managed_instance_subnet_id", "mssql", "azure_sql_managed_instance"),
        ("mssql_virtual_network_subnet_id", "mssql", "azure_sql_database"),
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
