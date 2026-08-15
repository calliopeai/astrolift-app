"""Tests for the GCP plugin manifest."""

from __future__ import annotations

from gcp.plugin import PLUGIN


def test_plugin_id_and_display() -> None:
    assert PLUGIN.id == "gcp"
    assert PLUGIN.display_name == "Google Cloud Platform"


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
    assert ("object_store", "gcs") in keys
    assert ("queue", "pubsub") in keys
    assert ("topic", "pubsub_topic") in keys
    assert ("warehouse", "bigquery") in keys
    assert ("document_db", "firestore_native") in keys
    assert ("mssql", "cloudsql_sqlserver") in keys
    assert ("graph_db", "spanner_graph") in keys
    assert ("workflow_engine", "workflows") in keys
    assert ("cdn", "cloud_cdn") in keys
    assert ("private_endpoint", "private_service_connect") in keys
    assert ("faas", "cloud_functions_gen2") in keys
    assert ("filesystem", "filestore") in keys
    assert ("api_gateway", "api_gateway") in keys
    assert ("event_stream", "managed_kafka") in keys
    assert ("event_bus", "eventarc") in keys
    assert ("redis", "memorystore_valkey") in keys


def test_managed_services_have_full_gcp_coverage() -> None:
    """#363 + #370: GCP slice ships postgres + mysql + redis on
    top of object_store + queue."""
    keys = set(PLUGIN.managed_service_drivers.keys())
    assert ("postgres", "cloudsql") in keys
    assert ("postgres", "alloydb") in keys
    assert ("mysql", "cloudsql") in keys
    assert ("mssql", "cloudsql_sqlserver") in keys
    assert ("redis", "memorystore") in keys


def test_config_schema_requires_core_fields() -> None:
    required = PLUGIN.config_schema.get("required", [])
    assert "project_id" in required
    assert "region" in required


def test_ingress_variant_options() -> None:
    variant = PLUGIN.config_schema["properties"]["ingress_variant"]
    assert set(variant["enum"]) == {"gce_ingress", "gateway_api"}


def test_alloydb_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "alloydb_network",
        "alloydb_allocated_ip_range",
        "alloydb_database_version",
        "alloydb_machine_type_default",
        "alloydb_deletion_protection_default",
        "alloydb_secret_manager_prefix",
    ):
        assert field in properties


def test_bigquery_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "bigquery_location",
        "bigquery_dataset_prefix",
        "bigquery_deletion_protection_default",
        "bigquery_dataset_api_endpoint",
        "bigquery_reservation_api_endpoint",
    ):
        assert field in properties


def test_firestore_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "firestore_location",
        "firestore_database_name_prefix",
        "firestore_deletion_protection_default",
        "firestore_snapshot_bucket",
        "firestore_operation_timeout_seconds",
        "firestore_operation_poll_interval_seconds",
        "firestore_api_endpoint",
    ):
        assert field in properties


def test_sqlserver_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "cloudsql_sqlserver_engine_version",
        "cloudsql_sqlserver_backup_retention_days",
        "cloudsql_api_endpoint",
        "cloudsql_operation_timeout_seconds",
        "cloudsql_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_spanner_graph_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "spanner_instance_name_prefix",
        "spanner_shared_instance_id",
        "spanner_instance_config",
        "spanner_edition",
        "spanner_processing_units",
        "spanner_automatic_backup_schedule",
        "spanner_deletion_protection_default",
        "spanner_backup_retention_days",
        "spanner_api_endpoint",
        "spanner_operation_timeout_seconds",
        "spanner_operation_poll_interval_seconds",
        "spanner_adopt_existing_instance",
    ):
        assert field in properties


def test_cloud_cdn_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "cloud_cdn_name_prefix",
        "cloud_cdn_deletion_protection_default",
        "cloud_cdn_cache_mode_default",
        "cloud_cdn_default_ttl_seconds",
        "cloud_cdn_max_ttl_seconds",
        "cloud_cdn_client_ttl_seconds",
        "cloud_cdn_serve_while_stale_seconds",
        "cloud_cdn_invalidation_role",
        "cloud_cdn_api_endpoint",
        "cloud_cdn_operation_timeout_seconds",
        "cloud_cdn_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_workflows_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "workflows_name_prefix",
        "workflows_deletion_protection_default",
        "workflows_call_log_level_default",
        "workflows_execution_history_level_default",
        "workflows_api_endpoint",
        "workflow_executions_api_endpoint",
        "workflows_operation_timeout_seconds",
        "workflows_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_private_service_connect_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "private_service_connect_network",
        "private_service_connect_subnetwork",
        "private_service_connect_name_prefix",
        "private_service_connect_labels",
        "private_service_connect_deletion_protection_default",
        "private_service_connect_api_endpoint",
        "private_service_connect_operation_timeout_seconds",
        "private_service_connect_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_cloud_functions_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "cloud_functions_region",
        "cloud_functions_name_prefix",
        "cloud_functions_deletion_protection_default",
        "cloud_functions_api_endpoint",
        "cloud_functions_operation_timeout_seconds",
        "cloud_functions_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_filestore_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "filestore_location",
        "filestore_network",
        "filestore_instance_name_prefix",
        "filestore_share_name_default",
        "filestore_tier_default",
        "filestore_protocol_default",
        "filestore_connect_mode_default",
        "filestore_reserved_ip_range",
        "filestore_psc_endpoint_project",
        "filestore_kms_key_name",
        "filestore_deletion_protection_default",
        "filestore_backup_location",
        "filestore_backup_kms_key",
        "filestore_api_endpoint",
        "filestore_operation_timeout_seconds",
        "filestore_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_api_gateway_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "api_gateway_region",
        "api_gateway_api_id_prefix",
        "api_gateway_gateway_id_prefix",
        "api_gateway_config_id_prefix",
        "api_gateway_deletion_protection_default",
        "api_gateway_api_endpoint",
        "api_gateway_operation_timeout_seconds",
        "api_gateway_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_managed_kafka_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "managed_kafka_location",
        "managed_kafka_cluster_id_prefix",
        "managed_kafka_subnet_names",
        "managed_kafka_deletion_protection_default",
        "managed_kafka_api_endpoint",
        "managed_kafka_operation_timeout_seconds",
        "managed_kafka_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_eventarc_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "eventarc_location",
        "eventarc_message_bus_id",
        "eventarc_deletion_protection_default",
        "eventarc_api_endpoint",
        "eventarc_publishing_endpoint",
        "eventarc_operation_timeout_seconds",
        "eventarc_operation_poll_interval_seconds",
    ):
        assert field in properties


def test_memorystore_valkey_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "memorystore_valkey_network",
        "memorystore_valkey_instance_name_prefix",
        "memorystore_valkey_engine_version",
        "memorystore_valkey_node_type",
        "memorystore_valkey_mode",
        "memorystore_valkey_shard_count",
        "memorystore_valkey_replica_count",
        "memorystore_valkey_authorization_mode",
        "memorystore_valkey_token_auth_user",
        "memorystore_valkey_token_auth_rotation_generation",
        "memorystore_valkey_token_auth_retire_generation",
        "memorystore_valkey_secret_manager_prefix",
        "memorystore_valkey_transit_encryption_default",
        "memorystore_valkey_persistence_mode",
        "memorystore_valkey_automated_backup_default",
        "memorystore_valkey_backup_retention_days",
        "memorystore_valkey_server_ca_mode",
        "memorystore_valkey_server_ca_pool",
        "memorystore_valkey_deletion_protection_default",
        "memorystore_valkey_kms_key",
        "memorystore_valkey_allow_preview_features",
        "memorystore_valkey_api_endpoint",
        "memorystore_valkey_operation_timeout_seconds",
        "memorystore_valkey_poll_interval_seconds",
        "memorystore_valkey_adopt_existing_instance",
    ):
        assert field in properties
    assert properties["memorystore_valkey_token_auth_user"]["default"] == "default"
    assert properties["managed_cert_name_prefix"]["default"] == "astrolift"
