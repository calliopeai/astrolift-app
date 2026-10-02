"""Saved identities use the registered AlloyDB driver and request-shaped cloud transport."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace

import pytest
from gcp.managed.postgres_alloydb import AlloyDBConfig, AlloyDBPostgresDriver

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.gcp.test_managed_postgres_alloydb import FakeAlloyDBClient, FakeSecretClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    api, secrets = FakeAlloyDBClient(), FakeSecretClient()
    cfg = AlloyDBConfig(
        project_id="shared-test-project",
        region="us-west1",
        network="projects/shared-test-project/global/networks/test",
    )

    class Driver(AlloyDBPostgresDriver):
        def __init__(self, *, config):
            super().__init__(config=config, client=api, secrets_client=secrets, sleep=lambda _: None)

    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="gcp",
            display_name="gcp",
            version="fixture",
            drivers={"managed:postgres:alloydb": Driver},
        )
    )
    state = SimpleNamespace(api=api, secrets=secrets, cfg=cfg, cls=Driver)
    monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    return state


def new_service(org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="gcp", variant="alloydb", backend_ref="")
    svc.kind, svc.name = "postgres", "primary"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


def writes(cloud):
    return [call for call in cloud.api.calls if call[0].startswith(("create", "patch", "delete", "restore"))]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_orgs_with_colliding_previous_names_provision_independently(cloud, collision):
    if collision == "joined":
        first, second = new_service("alpha-beta", "gamma"), new_service("alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="p" * 300)
        first, second = new_service("alpha"), new_service("beta")
    previous = [
        "-".join(
            (
                cloud.cfg.cluster_name_prefix,
                svc.registered_app.organization.slug,
                svc.registered_app.slug,
                svc.app_environment.name,
                svc.name,
            )
        )[:63]
        for svc in (first, second)
    ]
    assert previous[0] == previous[1]
    results = [_provision_sync(svc.pk) for svc in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for svc, result in zip((first, second), results, strict=True):
        name = result["handle"].split("/")[1]
        assert name.endswith(svc.guid.hex) and len(name) <= 63
        cluster = cloud.api.clusters[f"projects/shared-test-project/locations/us-west1/clusters/{name}"]
        assert cluster["labels"]["astrolift_io_managed_service_id"] == str(svc.guid)
        assert _provision_sync(svc.pk)["handle"] == result["handle"]
    assert sum(operation == "create_cluster" for operation, _ in cloud.api.calls) == 2


@pytest.mark.parametrize("legacy_labels", [False, True])
def test_recorded_target_and_secrets_survive_renames_and_config_override(cloud, legacy_labels):
    svc = new_service("legacy")
    locator = "postgres/old-human-cluster"
    spec = dataclasses.replace(
        build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster), recorded_handle=locator
    )
    assert cloud.cls(config=cloud.cfg).provision(spec).ok
    if legacy_labels:
        for resource in [*cloud.api.clusters.values(), *cloud.api.instances.values()]:
            for key in ("astrolift-managed-service-id", "astrolift_io_managed_service_id"):
                resource["labels"].pop(key)
    before = copy.deepcopy((cloud.api.clusters, cloud.api.instances, cloud.secrets.secrets))
    svc.backend_ref, svc.config = locator, {"cluster_id": "replacement-name"}
    svc.save(update_fields=["backend_ref", "config"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="renamed-prefix")
    writes_before = writes(cloud)
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["handle"] == locator
    assert before == (cloud.api.clusters, cloud.api.instances, cloud.secrets.secrets)
    assert writes(cloud) == writes_before
    svc.refresh_from_db()
    assert svc.backend_ref == locator


def test_foreign_recorded_owner_and_config_spoof_cannot_adopt(cloud):
    owner, contender = new_service("owner"), new_service("contender")
    initial = _provision_sync(owner.pk)
    assert initial["ok"]
    owner.backend_ref = initial["handle"]
    owner.save(update_fields=["backend_ref"])
    contender.backend_ref = owner.backend_ref
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    before, writes_before = (
        copy.deepcopy((cloud.api.clusters, cloud.api.instances, cloud.secrets.secrets)),
        writes(cloud),
    )
    result = _provision_sync(contender.pk)
    assert not result["ok"] and result["errors"] == ["resource_not_owned"]
    assert before == (cloud.api.clusters, cloud.api.instances, cloud.secrets.secrets)
    assert writes(cloud) == writes_before


def test_two_live_records_cannot_prove_unmarked_legacy_owner(cloud):
    first, second = new_service("first"), new_service("second")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    first.backend_ref = initial["handle"]
    first.save(update_fields=["backend_ref"])
    second.backend_ref = first.backend_ref
    second.save(update_fields=["backend_ref"])
    for resource in [*cloud.api.clusters.values(), *cloud.api.instances.values()]:
        for key in ("astrolift-managed-service-id", "astrolift_io_managed_service_id"):
            resource["labels"].pop(key)
    before, writes_before = copy.deepcopy((cloud.api.clusters, cloud.api.instances)), writes(cloud)
    assert not _provision_sync(first.pk)["ok"]
    assert not _provision_sync(second.pk)["ok"]
    assert before == (cloud.api.clusters, cloud.api.instances) and writes(cloud) == writes_before


def test_explicit_fresh_cluster_name_preserves_intent_but_never_adopts_foreign_owner(cloud):
    first, second = new_service("first"), new_service("second")
    for row in (first, second):
        row.config = {"cluster_id": "explicit-operator-target"}
        row.save(update_fields=["config"])
    result = _provision_sync(first.pk)
    assert result["ok"] and result["handle"] == "postgres/explicit-operator-target"
    assert not _provision_sync(second.pk)["ok"]
    assert len(cloud.api.clusters) == 1


def test_recorded_wrong_kind_refuses_before_provider_or_secret_calls(cloud):
    row = new_service("wrong-kind")
    row.backend_ref = "other_kind/original"
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"]
    assert cloud.api.calls == [] and cloud.secrets.secrets == {}
