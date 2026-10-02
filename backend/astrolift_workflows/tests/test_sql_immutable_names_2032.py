"""Saved service identities reach real driver code; cloud SDKs use protocol fixtures."""

from __future__ import annotations

import dataclasses
from importlib import import_module
from types import SimpleNamespace

import pytest

from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(
    params=[
        ("gcp", "postgres_cloudsql", "postgres"),
        ("gcp", "mysql_cloudsql", "mysql"),
        ("azure", "postgres_flexible", "postgres"),
        ("azure", "mysql_flexible", "mysql"),
    ]
)
def cloud(request, monkeypatch):
    family, variant, kind = request.param
    fixture = import_module(f"providers.tests.{family}.test_managed_{variant}")
    if family == "gcp":
        cls = fixture.CloudSQLPostgresDriver if kind == "postgres" else fixture.CloudSQLMySQLDriver
        config = fixture.CloudSQLConfig if kind == "postgres" else fixture.CloudSQLMySQLConfig
        api, secrets = fixture.FakeSqlClient(), fixture.FakeSecretClient()
        cfg = config(project_id="shared-test-project", region="us-west1")
        resources, prefix, maximum = api.instances, "instance_name_prefix", 98

        class Driver(cls):
            def __init__(self, *, config):
                super().__init__(config=config, sql_client=api, secrets_client=secrets)
    else:
        cls = fixture.AzurePostgresFlexibleDriver if kind == "postgres" else fixture.AzureMySQLFlexibleDriver
        config = fixture.AzurePostgresConfig if kind == "postgres" else fixture.AzureMySQLConfig
        api, secrets = fixture.FakeMgmtClient(), fixture.FakeSecretClient()
        cfg = config(
            subscription_id="shared-test-subscription",
            resource_group="shared-group",
            location="eastus",
            keyvault_url="https://fixture.invalid",
            mgmt_client=api,
            secret_client=secrets,
        )
        resources, prefix, maximum = api.servers_obj.servers, "server_name_prefix", 63

        class Driver(cls):
            pass

    state = SimpleNamespace(
        family=family,
        variant=variant,
        kind=kind,
        cls=Driver,
        cfg=cfg,
        api=api,
        resources=resources,
        secrets=secrets,
        prefix=prefix,
        maximum=maximum,
    )
    monkeypatch.setattr("astrolift_drivers.registry.plugins.get", lambda *_: Driver)
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    return state


def new_service(cloud, org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug=cloud.family, variant=cloud.variant, backend_ref="")
    svc.kind = cloud.kind
    svc.name = "primary"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


def creates(cloud):
    return (
        [kw for op, kw in cloud.api.calls if op == "insert"]
        if cloud.family == "gcp"
        else cloud.api.servers_obj.create_calls
    )


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_saved_orgs_with_colliding_old_names_both_provision(cloud, collision):
    if collision == "joined":
        first, second = new_service(cloud, "alpha-beta", "gamma"), new_service(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix: "p" * 300})
        first, second = new_service(cloud, "alpha"), new_service(cloud, "beta")
    old_names = [
        "-".join(
            (
                getattr(cloud.cfg, cloud.prefix),
                svc.registered_app.organization.slug,
                svc.registered_app.slug,
                svc.app_environment.name,
                svc.name,
            )
        )[: cloud.maximum]
        for svc in (first, second)
    ]
    assert old_names[0] == old_names[1]
    results = [_provision_sync(svc.pk) for svc in (first, second)]
    assert all(row["ok"] for row in results), results
    names = [row["handle"].partition("/")[2] for row in results]
    assert names[0] != names[1] and len(creates(cloud)) == 2
    for svc, name in zip((first, second), names, strict=True):
        assert name.endswith(svc.guid.hex) and len(name) <= cloud.maximum
        resource = cloud.resources[name]
        labels = resource.settings["userLabels"] if cloud.family == "gcp" else resource.tags
        assert labels["astrolift-managed-service-id"] == str(svc.guid)
    assert all(
        _provision_sync(svc.pk)["handle"] == result["handle"]
        for svc, result in zip((first, second), results, strict=True)
    )
    assert len(creates(cloud)) == 2


def test_recorded_legacy_name_and_resource_survive_changed_prefix_and_slugs(cloud):
    svc = new_service(cloud, "legacy-org")
    legacy = f"{cloud.kind}/old-human-resource"
    spec = dataclasses.replace(
        build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster), recorded_handle=legacy
    )
    seeded = cloud.cls(config=cloud.cfg).provision(spec)
    assert seeded.ok and seeded.handle == legacy
    resource = cloud.resources["old-human-resource"]
    resource.preserved_marker = "existing-provider-data"
    svc.backend_ref = legacy
    svc.save(update_fields=["backend_ref"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix: "changed-prefix"})
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["handle"] == legacy
    assert len(creates(cloud)) == 1 and len(cloud.resources) == 1
    assert cloud.resources["old-human-resource"].preserved_marker == "existing-provider-data"
    svc.refresh_from_db()
    assert svc.backend_ref == legacy


def test_foreign_recorded_target_is_refused_without_creating_or_overwriting(cloud):
    owner, contender = new_service(cloud, "owner"), new_service(cloud, "contender")
    result = _provision_sync(owner.pk)
    assert result["ok"]
    contender.backend_ref = result["handle"]
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    refused = _provision_sync(contender.pk)
    assert not refused["ok"] and len(creates(cloud)) == 1
    name = result["handle"].partition("/")[2]
    resource = cloud.resources[name]
    labels = resource.settings["userLabels"] if cloud.family == "gcp" else resource.tags
    assert labels["astrolift-managed-service-id"] == str(owner.guid)


def test_recorded_handle_for_another_kind_is_refused_before_provider_calls(cloud):
    svc = new_service(cloud, "wrong-kind")
    svc.backend_ref = "other_kind/original-resource"
    svc.save(update_fields=["backend_ref"])
    with pytest.raises(ValueError, match="recorded"):
        _provision_sync(svc.pk)
    assert creates(cloud) == [] and cloud.resources == {}
