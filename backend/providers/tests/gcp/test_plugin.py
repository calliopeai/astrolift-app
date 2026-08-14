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
    assert ("observability", "cloud_operations") in keys


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


def test_cloud_operations_operator_controls_are_exposed() -> None:
    properties = PLUGIN.config_schema["properties"]
    for field in (
        "cloud_operations_location",
        "cloud_operations_name_prefix",
        "cloud_operations_retention_days_default",
        "cloud_operations_deletion_protection_default",
        "cloud_operations_logging_api_endpoint",
        "cloud_operations_monitoring_api_endpoint",
        "cloud_operations_request_timeout_seconds",
        "cloud_operations_operation_timeout_seconds",
        "cloud_operations_operation_poll_interval_seconds",
    ):
        assert field in properties
