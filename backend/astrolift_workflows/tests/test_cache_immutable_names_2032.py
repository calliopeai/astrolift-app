"""Saved cache identities target real SDK/moto or request-shaped serverless fixtures."""

from __future__ import annotations

import dataclasses
from importlib import import_module
from types import SimpleNamespace

import pytest

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_sql_immutable_names_2032 import new_service

pytestmark = pytest.mark.django_db


@pytest.fixture(
    params=["redis", "memcached", "serverless_redis", "serverless_valkey", "serverless_memcached"]
)
def cloud(request, monkeypatch):
    engine = request.param
    simulator = None
    calls = []
    if engine.startswith("serverless"):
        engine = engine.removeprefix("serverless_")
        fixture = import_module("providers.tests.aws.test_managed_elasticache_serverless")
        subject, api, secrets = fixture.driver(engine=engine)
        cfg, cls = subject._config, type(subject)
        variant = f"elasticache_serverless_{engine}"
        prefix, maximum = "cache_name_prefix", 38

        class Driver(cls):
            def __init__(self, *, config):
                super().__init__(config=config, elasticache_client=api, secrets_client=secrets)

        def create_count():
            return sum(op == "CreateServerlessCache" for op, _ in api.calls)

        def resources():
            return list(api.caches.values())

        def tags(resource):
            return resource["Tags"]

    elif engine == "memcached":
        fixture = import_module("providers.tests.aws.test_managed_memcached_elasticache")
        subject, api = fixture.driver()
        cfg, cls = subject._config, type(subject)
        prefix, maximum, variant = "cluster_name_prefix", 50, "elasticache_memcached"

        class Driver(cls):
            def __init__(self, *, config):
                super().__init__(config=config, elasticache_client=api)

        def resources():
            return list(api.clusters.values())

        def create_count():
            return sum(op == "CreateCacheCluster" for op, _ in api.calls)

        def tags(resource):
            return resource["Tags"]

    else:
        fixture = import_module("providers.tests.aws.test_managed_redis_elasticache")
        simulator = fixture.aws_mock.__wrapped__()
        world = next(simulator)
        api = world["ec"]
        api.meta.events.register("before-call.elasticache", lambda model, **_: calls.append(model.name))
        config, cls = fixture.ElastiCacheConfig, fixture.ElastiCacheRedisDriver
        prefix, maximum, variant = "replication_group_prefix", 40, "elasticache"
        secrets = world["sm"]

        class Driver(cls):
            def __init__(self, *, config):
                super().__init__(config=config, elasticache_client=api, secrets_client=secrets)

        def resources():
            return api.describe_replication_groups()["ReplicationGroups"]

        def create_count():
            return calls.count("CreateReplicationGroup")

        cfg = config(
            region="us-east-1", cache_subnet_group="astrolift-test", security_group_ids=[world["sg_id"]]
        )

        def tags(resource):
            return api.list_tags_for_resource(ResourceName=resource["ARN"])["TagList"]

    kind = "cache" if engine == "memcached" else "redis"
    state = SimpleNamespace(
        family="aws",
        variant=variant,
        kind=kind,
        engine=request.param,
        cfg=cfg,
        cls=Driver,
        api=api,
        prefix=prefix,
        maximum=maximum,
        create_count=create_count,
        resources=resources,
        tags=tags,
    )
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
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    try:
        yield state
    finally:
        if simulator is not None:
            simulator.close()


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_cache_collision_provisions_distinct_owned_resources(cloud, collision):
    if collision == "joined":
        rows = [new_service(cloud, "alpha-beta", "gamma"), new_service(cloud, "alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix: "p" * 300})
        rows = [new_service(cloud, "alpha"), new_service(cloud, "beta")]
    old = [
        "-".join(
            (
                getattr(cloud.cfg, cloud.prefix),
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[: cloud.maximum]
        for row in rows
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] for result in results), results
    handles = [result["handle"] for result in results]
    assert len(set(handles)) == 2 and cloud.create_count() == 2
    assert len(cloud.resources()) == 2
    owners = [
        {tag["Key"]: tag["Value"] for tag in cloud.tags(resource)}["astrolift.io/managed_service_id"]
        for resource in cloud.resources()
    ]
    assert set(owners) == {str(row.guid) for row in rows}
    for row, handle in zip(rows, handles, strict=True):
        assert handle.endswith(row.guid.hex) and len(handle.split("/", 1)[1]) <= cloud.maximum
        assert _provision_sync(row.pk)["handle"] == handle
    assert cloud.create_count() == 2
    if cloud.engine.startswith("serverless") and cloud.kind == "redis":
        assert len(cloud.api.users) == len(cloud.api.groups) == 2
        for row, handle in zip(rows, handles, strict=True):
            name = handle.partition("/")[2]
            user_id, group_id = cloud.cls._user_id(name), cloud.cls._group_id(name)
            assert user_id.endswith(row.guid.hex) and group_id.endswith(row.guid.hex)
            assert len(user_id) <= 40 and len(group_id) <= 40
            assert cloud.api.groups[group_id]["UserIds"] == [user_id]


def test_recorded_cache_handle_survives_prefix_and_app_rename(cloud):
    row = new_service(cloud, "legacy")
    handle = f"{cloud.kind}/old-human-cache"
    spec = dataclasses.replace(
        build_provision_spec(row, cluster=row.app_environment.tenant_cluster), recorded_handle=handle
    )
    seeded = cloud.cls(config=cloud.cfg).provision(spec)
    assert seeded.ok and seeded.handle == handle
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, **{cloud.prefix: "new-prefix"})
    actual = _provision_sync(row.pk)
    assert actual["ok"] and actual["handle"] == handle, actual
    assert cloud.create_count() == 1 and len(cloud.resources()) == 1
    row.refresh_from_db()
    assert row.backend_ref == handle


def test_foreign_recorded_cache_and_config_spoof_cannot_adopt_owner(cloud):
    owner, contender = new_service(cloud, "owner"), new_service(cloud, "contender")
    actual = _provision_sync(owner.pk)
    assert actual["ok"]
    contender.backend_ref = actual["handle"]
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["backend_ref", "config"])
    refused = _provision_sync(contender.pk)
    assert not refused["ok"] and cloud.create_count() == 1
    assert {tag["Key"]: tag["Value"] for tag in cloud.tags(cloud.resources()[0])}[
        "astrolift.io/managed_service_id"
    ] == str(owner.guid)
