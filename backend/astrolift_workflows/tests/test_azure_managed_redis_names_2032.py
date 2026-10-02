"""The real registered Redis driver preserves both components of a saved ARM locator."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace

import pytest
from azure.managed.managed_redis import AzureManagedRedisDriver, AzureManagedRedisError

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_managed_managed_redis import _driver

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    original, api, secrets = _driver()

    class Driver(AzureManagedRedisDriver):
        pass

    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="azure",
            display_name="azure",
            version="fixture",
            drivers={"managed:redis:azure_managed_redis": Driver},
        )
    )
    state = SimpleNamespace(api=api, secrets=secrets, cfg=original._config, cls=Driver)
    monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    return state


def new_service(org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="azure", variant="azure_managed_redis", backend_ref="")
    svc.kind, svc.name = "redis", "cache"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_ambiguous_slugs_and_truncated_prefixes_do_not_merge_saved_service_names(cloud, collision):
    if collision == "joined":
        first, second = new_service("alpha-beta", "gamma"), new_service("alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="p" * 300)
        first, second = new_service("alpha"), new_service("beta")
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        name = result["handle"].split("/")[1]
        assert name.endswith(row.guid.hex) and len(name) <= 60
        assert cloud.api.clusters[name].tags["astrolift-managed-service-id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert sum(call[0] == "cluster.create" for call in cloud.api.calls) == 2


def test_recorded_cluster_database_and_binding_secrets_survive_renames(cloud):
    row = new_service("legacy")
    locator = "redis/old-human-cluster/old-database"
    spec = dataclasses.replace(
        build_provision_spec(row, cluster=row.app_environment.tenant_cluster), recorded_handle=locator
    )
    assert cloud.cls(config=cloud.cfg).provision(spec).ok
    row.backend_ref, row.config = locator, {"database_name": "replacement-database"}
    row.save(update_fields=["backend_ref", "config"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="renamed-prefix")
    before = copy.deepcopy(cloud.secrets.values)
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == locator
    assert list(cloud.api.clusters) == ["old-human-cluster"]
    assert ("old-human-cluster", "old-database") in cloud.api.databases
    assert cloud.secrets.values == before
    assert sum(call[0] == "cluster.create" for call in cloud.api.calls) == 1
    assert sum(call[0] == "database.create" for call in cloud.api.calls) == 1
    row.refresh_from_db()
    assert row.backend_ref == locator


def test_recorded_foreign_cluster_refuses_config_identity_spoof_before_arm_write(cloud):
    owner, contender = new_service("owner"), new_service("contender")
    first = _provision_sync(owner.pk)
    assert first["ok"]
    contender.backend_ref = first["handle"]
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    before = copy.deepcopy(cloud.secrets.values)
    offset = len(cloud.api.calls)
    refused = _provision_sync(contender.pk)
    assert not refused["ok"] and refused["errors"] == ["external_resource_collision"]
    assert not any(call[0].endswith(("create", "update", "delete")) for call in cloud.api.calls[offset:])
    assert cloud.secrets.values == before


@pytest.mark.parametrize(
    "locator", ["other_kind/cluster/database", "redis/cluster/database/extra", "redis/../database"]
)
def test_malformed_recorded_locator_never_reaches_arm_or_secrets(cloud, locator):
    row = new_service("invalid")
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    with pytest.raises((AzureManagedRedisError, ValueError)):
        _provision_sync(row.pk)
    assert cloud.api.calls == [] and cloud.secrets.values == {}
