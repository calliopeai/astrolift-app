"""Tests for the GCP search stub driver (#373).

GCP doesn't ship a first-party managed Elasticsearch; the stub
driver exists so the (kind, variant) catalog is complete. Every
lifecycle entry raises NotImplementedError; read-only schemas
are populated so docs render.
"""

from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.search_elastic_cloud import (
    KIND,
    GCPElasticCloudStubConfig,
    GCPElasticCloudStubDriver,
)


@pytest.fixture
def driver() -> GCPElasticCloudStubDriver:
    return GCPElasticCloudStubDriver(
        config=GCPElasticCloudStubConfig(project_id="acme-prod"),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="search",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


def test_kind_is_search() -> None:
    assert KIND == "search"


def test_driver_constructs_without_config() -> None:
    """The dataclass defaults must allow the no-arg path so a future
    real driver doesn't break the entry point."""
    d = GCPElasticCloudStubDriver()
    assert d is not None


def test_provision_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError) as exc_info:
        driver.provision(_spec())
    assert "GCP" in str(exc_info.value)
    assert "managed Elasticsearch" in str(exc_info.value)


def test_update_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.update(UpdateSpec(handle="search/x"))


def test_deprovision_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.deprovision(DeprovisionSpec(handle="search/x"))


def test_deprovision_with_flags_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    """Stub must remain not-implemented across the four-corner matrix
    -- no axis silently swallows the call."""
    for delete_data, force_destroy in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        with pytest.raises(NotImplementedError):
            driver.deprovision(
                DeprovisionSpec(handle="search/x"),
                delete_data=delete_data,
                force_destroy=force_destroy,
            )


def test_status_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.status(ServiceHandle(handle="search/x"))


def test_binding_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.binding(ServiceHandle(handle="search/x"))


def test_snapshot_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.snapshot(ServiceHandle(handle="search/x"))


def test_restore_raises_not_implemented(
    driver: GCPElasticCloudStubDriver,
) -> None:
    snap = SnapshotHandle(
        handle="search/x", snapshot_id="snap-1", created_at="",
    )
    with pytest.raises(NotImplementedError):
        driver.restore(snap, _spec())


def test_config_schema_documents_stub_status(
    driver: GCPElasticCloudStubDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    assert "Stub" in schema["description"]


def test_binding_schema_lists_contract_envs(
    driver: GCPElasticCloudStubDriver,
) -> None:
    schema = driver.binding_schema()
    for key in ("SEARCH_URL", "SEARCH_API_KEY", "SEARCH_INDEX_PREFIX"):
        assert key in schema.env_vars
