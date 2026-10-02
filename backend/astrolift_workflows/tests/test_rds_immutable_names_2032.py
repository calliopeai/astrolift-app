"""PostgreSQL lifecycle records drive RDS SDK/moto targets across org collisions."""

from __future__ import annotations

import dataclasses
import hashlib
from importlib import import_module
from types import SimpleNamespace

import pytest

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["postgres", "mysql"])
def cloud(request, monkeypatch):
    kind = request.param
    fixture = import_module(f"providers.tests.aws.test_managed_{kind}_rds")
    simulator = fixture.aws_mock.__wrapped__()
    world = next(simulator)
    cls = fixture.RDSPostgresDriver if kind == "postgres" else fixture.RDSMySQLDriver
    config = fixture.RDSConfig if kind == "postgres" else fixture.RDSMySQLConfig
    calls = []
    world["rds"].meta.events.register("before-call.rds", lambda model, **_: calls.append(model.name))
    cfg = config(region="us-east-1", db_subnet_group="astrolift-test", security_group_ids=[world["sg_id"]])

    class Driver(cls):
        def __init__(self, *, config):
            super().__init__(config=config, rds_client=world["rds"], secrets_client=world["sm"])

    variant = "rds" if kind == "postgres" else "rds_mysql"
    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="aws",
            display_name="AWS fixture",
            version="fixture",
            drivers={f"managed:{kind}:{variant}": Driver},
        )
    )
    monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
    state = SimpleNamespace(
        kind=kind, variant=variant, cls=Driver, cfg=cfg, rds=world["rds"], secrets=world["sm"], calls=calls
    )
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    try:
        yield state
    finally:
        simulator.close()


def service(cloud, org, app="api"):
    row = _service(org_slug=org, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    row.kind, row.name = cloud.kind, "primary"
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name"])
    return row


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_real_saved_two_org_collision_creates_two_actual_rds_resources(cloud, collision):
    if collision == "joined":
        rows = [service(cloud, "alpha-beta", "gamma"), service(cloud, "alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, instance_name_prefix="p" * 300)
        rows = [service(cloud, "alpha"), service(cloud, "beta")]
    old = [
        "-".join(
            (
                cloud.cfg.instance_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:60]
        for row in rows
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] for result in results), results
    names = [result["handle"].partition("/")[2] for result in results]
    assert names[0] != names[1] and cloud.calls.count("CreateDBInstance") == 2
    for row, name in zip(rows, names, strict=True):
        instance = cloud.rds.describe_db_instances(DBInstanceIdentifier=name)["DBInstances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in instance["TagList"]}
        assert tags["astrolift.io/managed_service_id"] == str(row.guid)
        assert name.endswith(row.guid.hex) and len(name) <= 60
        assert _provision_sync(row.pk)["handle"] == f"{cloud.kind}/{name}"
    assert cloud.calls.count("CreateDBInstance") == 2


def test_real_recorded_legacy_rds_handle_and_secret_remain_unchanged(cloud):
    row = service(cloud, "legacy-owner")
    handle = f"{cloud.kind}/old-human-database"
    spec = dataclasses.replace(
        build_provision_spec(row, cluster=row.app_environment.tenant_cluster), recorded_handle=handle
    )
    subject = cloud.cls(config=cloud.cfg)
    seeded = subject.provision(spec)
    assert seeded.ok and seeded.handle == handle
    secret_name = subject._secret_name_for(instance_id="old-human-database")
    before = hashlib.sha256(
        cloud.secrets.get_secret_value(SecretId=secret_name)["SecretString"].encode()
    ).digest()
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, instance_name_prefix="changed-prefix")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == handle
    assert cloud.calls.count("CreateDBInstance") == 1
    assert len(cloud.rds.describe_db_instances()["DBInstances"]) == 1
    after = hashlib.sha256(
        cloud.secrets.get_secret_value(SecretId=secret_name)["SecretString"].encode()
    ).digest()
    assert before == after
    row.refresh_from_db()
    assert row.backend_ref == handle


def test_foreign_recorded_rds_handle_and_config_cannot_adopt_another_owner(cloud):
    owner, contender = service(cloud, "owner"), service(cloud, "contender")
    actual = _provision_sync(owner.pk)
    assert actual["ok"]
    contender.backend_ref = actual["handle"]
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    refused = _provision_sync(contender.pk)
    assert not refused["ok"] and cloud.calls.count("CreateDBInstance") == 1
    instance = cloud.rds.describe_db_instances()["DBInstances"][0]
    assert {tag["Key"]: tag["Value"] for tag in instance["TagList"]}[
        "astrolift.io/managed_service_id"
    ] == str(owner.guid)
