"""Tests for the GCP plugin manifest."""

from __future__ import annotations

from gcp.plugin import PLUGIN


def test_plugin_id_and_display() -> None:
    assert PLUGIN.id == "gcp"
    assert PLUGIN.display_name == "Google Cloud Platform"


def test_required_driver_roles_present() -> None:
    expected = {
        "registry", "secrets", "identity",
        "dns", "tls", "cluster", "ingress",
    }
    assert expected.issubset(PLUGIN.drivers.keys())


def test_managed_services_have_object_store_and_queue() -> None:
    keys = set(PLUGIN.managed_service_drivers.keys())
    assert ("object_store", "gcs") in keys
    assert ("queue", "pubsub") in keys


def test_config_schema_requires_core_fields() -> None:
    required = PLUGIN.config_schema.get("required", [])
    assert "project_id" in required
    assert "region" in required


def test_ingress_variant_options() -> None:
    variant = (
        PLUGIN.config_schema["properties"]["ingress_variant"]
    )
    assert set(variant["enum"]) == {"gce_ingress", "gateway_api"}
