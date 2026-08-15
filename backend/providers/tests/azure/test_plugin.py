"""Tests for the Azure plugin manifest."""

from __future__ import annotations

from azure.plugin import PLUGIN


def test_plugin_id_and_display() -> None:
    assert PLUGIN.id == "azure"
    assert PLUGIN.display_name == "Microsoft Azure"


def test_required_driver_roles_present() -> None:
    expected = {
        "registry",
        "secrets",
        "identity",
        "dns",
        "tls",
        "cluster",
        "ingress",
    }
    assert expected.issubset(PLUGIN.drivers.keys())


def test_managed_services_have_object_store_and_queue() -> None:
    keys = set(PLUGIN.managed_service_drivers.keys())
    assert ("object_store", "blob") in keys
    assert ("queue", "servicebus") in keys


def test_managed_services_have_full_364_set() -> None:
    """Cross-cloud parity: Azure ships Postgres-Flex, Cache-Redis, the
    richer Blob driver, and the Pub/Sub-style Service Bus driver."""
    keys = set(PLUGIN.managed_service_drivers.keys())
    assert ("postgres", "azure_pg_flex") in keys
    assert ("redis", "azure_cache_redis") in keys
    assert ("redis", "azure_managed_redis") in keys
    assert ("object_store", "azure_blob") in keys
    assert ("queue", "azure_servicebus") in keys
    assert ("event_bus", "event_grid") in keys
    assert ("event_bus", "event_grid_namespace") in keys
    assert ("filesystem", "azure_files") in keys


def test_config_schema_requires_core_fields() -> None:
    required = PLUGIN.config_schema.get("required", [])
    assert "subscription_id" in required
    assert "tenant_id" in required
    assert "resource_group" in required


def test_ingress_variant_options() -> None:
    variant = PLUGIN.config_schema["properties"]["ingress_variant"]
    assert set(variant["enum"]) == {"agic", "gateway_api"}


def test_managed_runtime_controls_are_exposed_in_provider_schema() -> None:
    properties = PLUGIN.config_schema["properties"]
    expected = {
        "storage_account",
        "servicebus_namespace",
        "blob_versioning_enabled",
        "servicebus_dead_lettering_on_message_expiration",
        "eventgrid_default_input_schema",
        "eventgrid_public_network_access_default",
        "eventgrid_namespace_default_capacity",
        "files_allowed_subnet_ids",
        "postgres_backup_retention_days",
        "mysql_backup_retention_days",
        "redis_minimum_tls_version_default",
        "cosmos_default_api_kind",
        "ai_search_public_network_access_default",
        "ai_search_vector_algorithm_default",
        "monitor_public_network_access_default",
        "acs_email_domain_management",
        "azure_openai_account_name",
    }
    assert expected <= set(properties)


def test_astrolift_azure_package_extends_official_sdk_namespace() -> None:
    """The local ``azure`` package must not shadow Microsoft's SDKs."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.cosmosdb import CosmosDBManagementClient
    from azure.mgmt.eventgrid import EventGridManagementClient
    from azure.mgmt.fileshares import FileSharesMgmtClient
    from azure.mgmt.mysqlflexibleservers import MySQLManagementClient
    from azure.mgmt.postgresqlflexibleservers import PostgreSQLManagementClient
    from azure.mgmt.resource.locks import ManagementLockClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.storage.blob import BlobServiceClient

    assert all(
        value is not None
        for value in (
            DefaultAzureCredential,
            CosmosDBManagementClient,
            EventGridManagementClient,
            FileSharesMgmtClient,
            MySQLManagementClient,
            PostgreSQLManagementClient,
            ManagementLockClient,
            SearchIndexClient,
            BlobServiceClient,
        )
    )


def test_registered_managed_drivers_target_current_sdk_operation_groups() -> None:
    """Fake clients must not hide renamed or removed Azure SDK operations."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
    from azure.mgmt.communication import CommunicationServiceManagementClient
    from azure.mgmt.cosmosdb import CosmosDBManagementClient
    from azure.mgmt.eventgrid import EventGridManagementClient
    from azure.mgmt.fileshares import FileSharesMgmtClient
    from azure.mgmt.loganalytics import LogAnalyticsManagementClient
    from azure.mgmt.monitor import MonitorManagementClient
    from azure.mgmt.mysqlflexibleservers import MySQLManagementClient
    from azure.mgmt.postgresqlflexibleservers import PostgreSQLManagementClient
    from azure.mgmt.redis import RedisManagementClient
    from azure.mgmt.resource.locks import ManagementLockClient
    from azure.mgmt.search import SearchManagementClient
    from azure.mgmt.servicebus import ServiceBusManagementClient

    subscription_id = "00000000-1111-2222-3333-444444444444"
    credential = DefaultAzureCredential()
    expected = [
        (
            FileSharesMgmtClient(credential, subscription_id),
            {
                "file_shares": {"begin_create_or_update", "begin_delete", "begin_update", "get"},
                "file_share_snapshots": {
                    "begin_create_or_update_file_share_snapshot",
                    "begin_delete_file_share_snapshot",
                    "list_by_file_share",
                },
                "private_endpoint_connections": {"begin_delete", "list_by_file_share"},
            },
        ),
        (
            PostgreSQLManagementClient(credential, subscription_id),
            {
                "servers": {"begin_create_or_update", "begin_delete", "begin_update", "get"},
                "backups_automatic_and_on_demand": {"begin_create"},
            },
        ),
        (
            MySQLManagementClient(credential, subscription_id),
            {
                "servers": {"begin_create", "begin_delete", "begin_update", "get"},
                "backups": {"put"},
            },
        ),
        (
            RedisManagementClient(credential, subscription_id),
            {
                "redis": {
                    "begin_create",
                    "begin_delete",
                    "begin_export_data",
                    "begin_import_data",
                    "begin_update",
                    "get",
                    "list_keys",
                },
            },
        ),
        (
            CosmosDBManagementClient(credential, subscription_id),
            {
                "database_accounts": {
                    "begin_create_or_update",
                    "begin_delete",
                    "begin_update",
                    "get",
                    "list_connection_strings",
                },
                "cassandra_resources": {"begin_create_update_cassandra_keyspace"},
                "gremlin_resources": {"begin_create_update_gremlin_database"},
                "mongo_db_resources": {
                    "begin_create_update_mongo_db_database",
                    "begin_update_mongo_db_database_throughput",
                },
                "sql_resources": {"begin_create_update_sql_database"},
                "table_resources": {"begin_create_update_table"},
            },
        ),
        (
            CommunicationServiceManagementClient(credential, subscription_id),
            {
                "communication_services": {"list_keys"},
                "domains": {"begin_create_or_update", "begin_delete", "begin_update", "get"},
            },
        ),
        (
            CognitiveServicesManagementClient(credential, subscription_id),
            {
                "accounts": {"list_keys"},
                "deployments": {"begin_create_or_update", "begin_delete", "begin_update", "get"},
            },
        ),
        (
            SearchManagementClient(credential, subscription_id),
            {
                "admin_keys": {"get"},
                "services": {"begin_create_or_update", "delete", "get", "update"},
            },
        ),
        (
            MonitorManagementClient(credential, subscription_id),
            {
                "azure_monitor_workspaces": {"begin_delete", "create", "get", "update"},
                "data_collection_endpoints": {"create", "delete", "get"},
                "data_collection_rules": {"create", "delete", "get"},
            },
        ),
        (
            LogAnalyticsManagementClient(credential, subscription_id),
            {"workspaces": {"begin_create_or_update", "begin_delete", "update"}},
        ),
        (
            ServiceBusManagementClient(credential, subscription_id),
            {
                "queues": {"create_or_update", "delete", "get"},
                "topics": {"create_or_update", "delete", "get"},
                "subscriptions": {"create_or_update", "delete"},
            },
        ),
        (
            EventGridManagementClient(credential, subscription_id),
            {
                "topics": {"begin_create_or_update", "begin_delete", "begin_update", "get"},
                "topic_event_subscriptions": {
                    "begin_create_or_update",
                    "begin_delete",
                    "get",
                    "list",
                },
                "namespaces": {
                    "begin_create_or_update",
                    "begin_delete",
                    "begin_update",
                    "get",
                    "list_shared_access_keys",
                },
                "namespace_topics": {
                    "begin_create_or_update",
                    "begin_delete",
                    "begin_update",
                    "get",
                    "list_by_namespace",
                },
                "namespace_topic_event_subscriptions": {
                    "begin_create_or_update",
                    "begin_delete",
                    "get",
                    "list_by_namespace_topic",
                },
            },
        ),
        (
            ManagementLockClient(credential, subscription_id),
            {"management_locks": {"delete_at_resource_level", "list_at_resource_level"}},
        ),
    ]

    for client, groups in expected:
        for group_name, method_names in groups.items():
            operation_group = getattr(client, group_name)
            assert method_names <= set(dir(operation_group)), (
                type(client).__name__,
                group_name,
            )
