"""Tests for the service availability matrix (#21)."""

from __future__ import annotations

from _sdk.availability import (
    MATRIX,
    OPTIONAL_ROLES,
    REQUIRED_ROLES,
    AvailabilityMatrix,
    DriverEntry,
)


def test_required_roles_complete() -> None:
    """Every cluster bind requires these. Removing one without
    updating consumers would silently disable role checks."""
    assert "cluster" in REQUIRED_ROLES
    assert "ingress" in REQUIRED_ROLES
    assert "secrets" in REQUIRED_ROLES
    assert "identity" in REQUIRED_ROLES


def test_every_plugin_covers_required_roles() -> None:
    plugin_ids = {entry.plugin_id for entry in MATRIX.drivers}
    for plugin_id in plugin_ids:
        roles = {entry.role for entry in MATRIX.drivers if entry.plugin_id == plugin_id}
        missing = set(REQUIRED_ROLES) - roles
        assert not missing, f"plugin {plugin_id!r} matrix coverage missing {missing}"


def test_drivers_for_role_returns_all() -> None:
    cluster_entries = MATRIX.drivers_for_role("cluster")
    ids = {e.plugin_id for e in cluster_entries}
    assert {"aws", "gcp", "azure", "k8s_native"} <= ids


def test_variants_for_kind_dedupes_and_sorts() -> None:
    variants = MATRIX.variants_for_kind("object_store")
    assert variants == sorted(variants)
    assert len(variants) == len(set(variants))


def test_has_role_lookup() -> None:
    assert MATRIX.has_role(plugin_id="aws", role="cluster")
    assert not MATRIX.has_role(plugin_id="aws", role="trace")


def test_has_managed_lookup() -> None:
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="object_store",
        variant="s3",
    )
    assert not MATRIX.has_managed(
        plugin_id="aws",
        kind="object_store",
        variant="gcs",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="postgres",
        variant="aurora_postgres_serverless_v2",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="document_db",
        variant="documentdb",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="document_db",
        variant="documentdb_serverless_v2",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="wide_column",
        variant="keyspaces",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="graph_db",
        variant="neptune",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="graph_db",
        variant="neptune_serverless",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="warehouse",
        variant="redshift",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="warehouse",
        variant="redshift_serverless",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="topic",
        variant="sns_standard",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="topic",
        variant="sns_fifo",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="event_bus",
        variant="eventbridge",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="stream",
        variant="kinesis",
    )
    assert MATRIX.has_managed(
        plugin_id="aws",
        kind="stream",
        variant="firehose",
    )


def test_status_field_defaults_to_ga() -> None:
    entry = DriverEntry(role="cluster", plugin_id="aws")
    assert entry.status == "ga"


def test_managed_service_binding_envs_recorded() -> None:
    s3 = next(m for m in MATRIX.managed_services if m.plugin_id == "aws" and m.variant == "s3")
    assert "S3_BUCKET_NAME" in s3.binding_envs

    service_bus_topic = next(
        entry
        for entry in MATRIX.managed_services
        if entry.plugin_id == "azure" and entry.variant == "service_bus_topic"
    )
    assert service_bus_topic.status == "ga"
    assert {"TOPIC_ARN_OR_ID", "SERVICEBUS_SUBSCRIPTION"} <= set(
        service_bus_topic.binding_envs,
    )

    azure_files = next(m for m in MATRIX.managed_services if m.plugin_id == "azure" and m.variant == "azure_files")
    assert azure_files.status == "preview"
    assert "FILESYSTEM_SOURCE" in azure_files.binding_envs
    assert "FILESYSTEM_READ_ONLY" in azure_files.binding_envs
    assert "AZURE_RESOURCE_GROUP" in azure_files.binding_envs

    classic = next(m for m in MATRIX.managed_services if m.plugin_id == "azure" and m.variant == "azure_files_classic")
    assert classic.status == "planned"


def test_managed_service_keys_are_unique() -> None:
    keys = [(entry.plugin_id, entry.kind, entry.variant) for entry in MATRIX.managed_services]

    assert len(keys) == len(set(keys))


def test_every_planned_service_links_to_its_delivery_issue() -> None:
    planned = [entry for entry in MATRIX.managed_services if entry.status == "planned"]

    assert planned
    assert all(entry.issue_url.startswith("https://github.com/calliopeai/astrolift-app/issues/") for entry in planned)


def test_optional_roles_disjoint_from_required() -> None:
    assert not (set(OPTIONAL_ROLES) & set(REQUIRED_ROLES))


def test_empty_matrix_has_no_entries() -> None:
    empty = AvailabilityMatrix()
    assert empty.drivers == ()
    assert empty.managed_services == ()
    assert empty.drivers_for_role("cluster") == []
