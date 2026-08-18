from __future__ import annotations

import pytest
from _sdk.availability import MATRIX
from _sdk.managed_service import BindingSchema

from astrolift_drivers.registry import PluginManifest, plugins
from astrolift_services.managed_service_catalog import (
    CatalogResolutionError,
    list_catalog,
    resolve_variant,
    validate_config,
)
from astrolift_services.models import ManagedService


class _RdsDriver:
    def __init__(self):
        raise AssertionError("catalogue discovery must not construct cloud drivers")

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "engine_version": {
                    "type": "string",
                    "enum": ["16", "17"],
                },
            },
            "additionalProperties": False,
        }

    def binding_schema(self):
        return BindingSchema(env_vars={"DATABASE_URL": "Connection URI"})


@pytest.fixture(autouse=True)
def _aws_registry(monkeypatch):
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "aws": PluginManifest(
                plugin_id="aws",
                display_name="AWS",
                version="test",
                drivers={"managed:postgres:rds": _RdsDriver},
            )
        },
    )


def test_catalog_joins_executable_drivers_and_planned_roadmap():
    rows = list_catalog("aws")
    by_id = {row.id: row for row in rows}

    rds = by_id["aws:postgres:rds"]
    aurora = by_id["aws:postgres:aurora_postgres_serverless_v2"]
    sql_server = by_id["aws:mssql:rds_sqlserver_express"]

    assert rds.available is True
    assert rds.is_default_for_kind is True
    assert rds.binding_envs == ("DATABASE_URL",)
    assert rds.config_schema["properties"]["size"]["default"] == "small"
    assert aurora.available is False
    # This synthetic registry only installs the legacy RDS driver. Preview
    # metadata remains visible but is correctly unavailable in this process.
    assert aurora.status == "preview"
    assert aurora.issue_url.endswith("/1283")
    assert sql_server.size_options[0] == "small"
    assert sql_server.issue_url.endswith("/1284")


def test_resolve_and_validate_use_the_same_catalog_contract():
    selected = resolve_variant(plugin_slug="aws", kind="postgres", requested_variant=None)

    assert selected is not None
    assert selected.variant == "rds"
    validate_config(selected, {"size": "medium", "engine_version": "17"})
    with pytest.raises(CatalogResolutionError, match="engine_version"):
        validate_config(selected, {"engine_version": "15"})
    with pytest.raises(CatalogResolutionError, match="unavailable"):
        resolve_variant(
            plugin_slug="aws",
            kind="postgres",
            requested_variant="aurora_postgres_serverless_v2",
        )


def test_known_provider_rejects_a_kind_outside_its_catalog():
    with pytest.raises(CatalogResolutionError, match="does not support kind"):
        resolve_variant(plugin_slug="aws", kind="not-a-kind", requested_variant=None)


# ---- in-cluster variants on a cloud cluster (#1484) --------------------------


class _MemcachedDriver:
    def __init__(self):
        raise AssertionError("catalogue discovery must not construct drivers")

    def config_schema(self):
        return {"type": "object", "properties": {}}

    def binding_schema(self):
        return BindingSchema(env_vars={"CACHE_URL": "Memcached endpoint"})


@pytest.fixture
def _azure_plus_in_cluster(monkeypatch):
    """An Azure cluster with the in-cluster plugin also loaded, which is every
    control plane: the plugins are installed together, they were just never
    reachable from one another."""
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "azure": PluginManifest(
                plugin_id="azure",
                display_name="Azure",
                version="test",
                drivers={"managed:postgres:azure_pg_flex": _RdsDriver},
            ),
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name="Kubernetes",
                version="test",
                drivers={
                    "managed:cache:memcached": _MemcachedDriver,
                    "managed:postgres:cnpg": _MemcachedDriver,
                },
            ),
        },
    )


def test_the_catalogue_offers_what_the_runtime_can_resolve(_azure_plus_in_cluster):
    """The gate has to agree with the thing it gates.

    ``createManagedService`` calls ``resolve_variant`` before any activity
    runs, so a catalogue scoped to the cluster's own plugin refuses a variant
    the lifecycle would provision without complaint -- which leaves #1484 fixed
    in the runtime and still broken through the API."""
    resolved = resolve_variant(plugin_slug="azure", kind="cache", requested_variant="memcached")

    assert resolved.available
    assert resolved.id == "azure:cache:memcached"


def test_an_in_cluster_variant_is_the_default_only_where_the_cloud_sells_nothing(
    _azure_plus_in_cluster,
):
    """Azure ships no cache, so Memcached is the answer for that kind. Azure
    does ship postgres, so CNPG is offered but must not displace it -- a
    borrowed variant turning a kind that had one obvious answer into one that
    demands an explicit variant would break every caller that omits it."""
    by_id = {row.id: row for row in list_catalog("azure")}

    assert by_id["azure:cache:memcached"].is_default_for_kind
    assert by_id["azure:postgres:cnpg"].available
    assert not by_id["azure:postgres:cnpg"].is_default_for_kind
    assert (
        resolve_variant(plugin_slug="azure", kind="postgres", requested_variant=None).variant
        == "azure_pg_flex"
    )


def test_the_in_cluster_plugin_borrows_nothing_back(_azure_plus_in_cluster):
    """One-way, exactly as the driver resolver is. A vanilla cluster offering
    Azure Database would be an offer nobody can honour."""
    variants = {row.id for row in list_catalog("k8s_native")}

    assert "k8s_native:postgres:cnpg" in variants
    assert not any(row.endswith("azure_pg_flex") for row in variants)


def test_portable_model_kind_enum_covers_the_full_provider_matrix():
    model_kinds = {value for value, _label in ManagedService.Kind.choices}
    catalog_kinds = {entry.kind for entry in MATRIX.managed_services}

    assert catalog_kinds <= model_kinds


def test_model_and_provider_sdk_share_one_portable_kind_vocabulary():
    from _sdk.managed_service_kinds import KINDS

    model_kinds = {value for value, _label in ManagedService.Kind.choices}
    sdk_kinds = {kind.name for kind in KINDS.kinds}

    assert sdk_kinds == model_kinds
