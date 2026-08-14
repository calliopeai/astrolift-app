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
    assert aurora.status == "planned"
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


def test_portable_model_kind_enum_covers_the_full_provider_matrix():
    model_kinds = {value for value, _label in ManagedService.Kind.choices}
    catalog_kinds = {entry.kind for entry in MATRIX.managed_services}

    assert catalog_kinds <= model_kinds


def test_model_and_provider_sdk_share_one_portable_kind_vocabulary():
    from _sdk.managed_service_kinds import KINDS

    model_kinds = {value for value, _label in ManagedService.Kind.choices}
    sdk_kinds = {kind.name for kind in KINDS.kinds}

    assert sdk_kinds == model_kinds
