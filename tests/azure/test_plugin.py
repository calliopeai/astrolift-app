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
    assert ("object_store", "azure_blob") in keys
    assert ("queue", "azure_servicebus") in keys


def test_config_schema_requires_core_fields() -> None:
    required = PLUGIN.config_schema.get("required", [])
    assert "subscription_id" in required
    assert "tenant_id" in required
    assert "resource_group" in required


def test_ingress_variant_options() -> None:
    variant = PLUGIN.config_schema["properties"]["ingress_variant"]
    assert set(variant["enum"]) == {"agic", "gateway_api"}
