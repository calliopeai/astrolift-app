from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.elasticache_serverless import (
    ElastiCacheServerlessConfig,
    ElastiCacheServerlessMemcachedDriver,
    ElastiCacheServerlessRedisDriver,
)


class NotFound(Exception):
    pass


class FakeElastiCache:
    def __init__(self) -> None:
        self.caches: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_serverless_caches(self, **kwargs):
        name = kwargs["ServerlessCacheName"]
        if name not in self.caches:
            raise NotFound("ServerlessCacheNotFoundFault")
        return {"ServerlessCaches": [self.caches[name]]}

    def list_tags_for_resource(self, **kwargs):
        for cache in [*self.caches.values(), *self.users.values(), *self.groups.values()]:
            if cache["ARN"] == kwargs["ResourceName"]:
                return {"TagList": cache.get("Tags", [])}
        return {"TagList": []}

    def create_serverless_cache(self, **kwargs):
        self.calls.append(("CreateServerlessCache", kwargs))
        name = kwargs["ServerlessCacheName"]
        port = 11211 if kwargs["Engine"] == "memcached" else 6379
        self.caches[name] = {
            **kwargs,
            "Status": "creating",
            "Endpoint": {"Address": f"{name}.serverless.cache", "Port": port},
            "ARN": f"arn:aws:elasticache:us-west-2:123456789012:serverlesscache:{name}",
        }

    def modify_serverless_cache(self, **kwargs):
        self.calls.append(("ModifyServerlessCache", kwargs))
        self.caches[kwargs["ServerlessCacheName"]].update(kwargs)

    def delete_serverless_cache(self, **kwargs):
        self.calls.append(("DeleteServerlessCache", kwargs))
        self.caches[kwargs["ServerlessCacheName"]]["Status"] = "deleting"

    def describe_users(self, **kwargs):
        user_id = kwargs["UserId"]
        if user_id not in self.users:
            raise NotFound("UserNotFound")
        return {"Users": [self.users[user_id]]}

    def create_user(self, **kwargs):
        self.calls.append(("CreateUser", kwargs))
        self.users[kwargs["UserId"]] = {
            **kwargs,
            "Status": "active",
            "ARN": f"arn:aws:elasticache:us-west-2:123456789012:user:{kwargs['UserId']}",
        }

    def delete_user(self, **kwargs):
        self.calls.append(("DeleteUser", kwargs))
        self.users[kwargs["UserId"]]["Status"] = "deleting"

    def describe_user_groups(self, **kwargs):
        group_id = kwargs["UserGroupId"]
        if group_id not in self.groups:
            raise NotFound("UserGroupNotFound")
        return {"UserGroups": [self.groups[group_id]]}

    def create_user_group(self, **kwargs):
        self.calls.append(("CreateUserGroup", kwargs))
        self.groups[kwargs["UserGroupId"]] = {
            **kwargs,
            "Status": "active",
            "ARN": f"arn:aws:elasticache:us-west-2:123456789012:usergroup:{kwargs['UserGroupId']}",
        }

    def delete_user_group(self, **kwargs):
        self.calls.append(("DeleteUserGroup", kwargs))
        self.groups[kwargs["UserGroupId"]]["Status"] = "deleting"

    def create_serverless_cache_snapshot(self, **kwargs):
        self.calls.append(("CreateServerlessCacheSnapshot", kwargs))
        snapshot_name = kwargs["ServerlessCacheSnapshotName"]
        return {
            "ServerlessCacheSnapshot": {
                "ARN": (f"arn:aws:elasticache:us-west-2:123456789012:serverlesscachesnapshot:{snapshot_name}")
            }
        }


class ResourceNotFound(Exception):
    pass


class ResourceExists(Exception):
    pass


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_secret_value(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise ResourceNotFound("ResourceNotFoundException")
        return {"SecretString": self.values[name]}

    def create_secret(self, **kwargs):
        name = kwargs["Name"]
        if name in self.values:
            raise ResourceExists("ResourceExistsException")
        self.values[name] = kwargs["SecretString"]

    def put_secret_value(self, **kwargs):
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]

    def delete_secret(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise ResourceNotFound("ResourceNotFoundException")
        del self.values[name]


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456789",
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="cache",
        size="small",
        config=config,
        isolation="dedicated",
    )


def driver(engine: str = "valkey"):
    ec = FakeElastiCache()
    sm = FakeSecrets()
    cls = ElastiCacheServerlessMemcachedDriver if engine == "memcached" else ElastiCacheServerlessRedisDriver
    subject = cls(
        config=ElastiCacheServerlessConfig(
            region="us-west-2",
            subnet_ids=["subnet-a", "subnet-b"],
            security_group_ids=["sg-cache"],
            engine=engine,
        ),
        elasticache_client=ec,
        secrets_client=sm,
    )
    return subject, ec, sm


def test_valkey_password_provision_binding_update_and_idempotence():
    subject, ec, sm = driver()

    result = subject.provision(spec(auth_mode="password"))
    again = subject.provision(spec(auth_mode="password"))
    name = result.handle.split("/", 1)[1]
    ec.caches[name]["Status"] = "available"
    binding = subject.binding(ServiceHandle(result.handle), {"auth_mode": "password"})
    updated = subject.update(
        UpdateSpec(
            result.handle,
            config={"data_storage_min_gb": 2, "data_storage_max_gb": 20},
        )
    )

    assert result.ok and again.ok and updated.ok
    assert result.handle.startswith("redis/")
    assert len([call for call in ec.calls if call[0] == "CreateServerlessCache"]) == 1
    create = next(payload for operation, payload in ec.calls if operation == "CreateServerlessCache")
    assert create["Engine"] == "valkey"
    assert create["UserGroupId"].startswith("g-")
    assert len(ec.users) == len(ec.groups) == 1
    assert set(ec.users).isdisjoint(ec.groups)
    assert binding.env_vars["REDIS_TLS"].literal == "1"
    assert binding.env_vars["REDIS_PASSWORD"].secret_ref in sm.values
    assert binding.env_vars["REDIS_URL"].secret_ref in sm.values


def test_redis_iam_auth_emits_connect_grants_without_password_secret():
    subject, ec, sm = driver("redis")
    result = subject.provision(spec(auth_mode="iam", major_engine_version="7"))
    name = result.handle.split("/", 1)[1]
    ec.caches[name]["Status"] = "available"

    binding = subject.binding(
        ServiceHandle(result.handle),
        {"auth_mode": "iam", "major_engine_version": "7"},
    )

    create_user = next(payload for operation, payload in ec.calls if operation == "CreateUser")
    assert create_user["AuthenticationMode"] == {"Type": "iam"}
    assert create_user["UserName"] == "default"
    assert not sm.values
    assert {grant.actions[0] for grant in binding.iam_grants} == {"elasticache:Connect"}
    assert {grant.resource for grant in binding.iam_grants} == {
        ec.caches[name]["ARN"],
        next(iter(ec.users.values()))["ARN"],
    }


def test_memcached_has_distinct_portable_envelope_and_no_user_group():
    subject, ec, sm = driver("memcached")
    result = subject.provision(spec())
    binding = subject.binding(ServiceHandle(result.handle))
    create = next(payload for operation, payload in ec.calls if operation == "CreateServerlessCache")

    assert result.handle.startswith("cache/")
    assert create["Engine"] == "memcached"
    assert "UserGroupId" not in create
    assert not ec.users and not ec.groups and not sm.values
    assert binding.env_vars["CACHE_PROTOCOL"].literal == "memcached"
    assert binding.env_vars["CACHE_PORT"].literal == "11211"
    # CACHE_NODES is a comma-separated endpoint list for every cache driver
    # (#1403); serverless just happens to have exactly one endpoint.
    assert binding.env_vars["CACHE_NODES"].literal.split(",") == [
        f"{binding.env_vars['CACHE_HOST'].literal}:11211",
    ]
    with pytest.raises(ManagedServiceError, match="does not support snapshots"):
        subject.snapshot(ServiceHandle(result.handle))


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"data_storage_min_gb": "bad"}, "must be an integer"),
        ({"data_storage_min_gb": 20, "data_storage_max_gb": 10}, "cannot exceed"),
        ({"auth_mode": "iam", "major_engine_version": "six"}, "numeric version"),
    ],
)
def test_invalid_config_returns_controlled_failure(config, message):
    subject, _, _ = driver()
    result = subject.provision(spec(**config))
    assert result.ok is False
    assert message in result.message


def test_async_delete_then_access_cleanup_converges():
    subject, ec, sm = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]

    first = subject.deprovision(DeprovisionSpec(result.handle))
    assert first.ok is False and first.retryable is True
    ec.caches.pop(name)
    second = subject.deprovision(DeprovisionSpec(result.handle))
    assert second.ok is False
    ec.groups.clear()
    third = subject.deprovision(DeprovisionSpec(result.handle))
    assert third.ok is False
    ec.users.clear()
    final = subject.deprovision(DeprovisionSpec(result.handle))
    assert final.ok is True
    assert not sm.values


def test_current_botocore_accepts_serverless_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, ec, _ = driver()
    result = subject.provision(spec(auth_mode="iam", major_engine_version="7"))
    subject.update(UpdateSpec(result.handle, size="medium", config={"description": "updated"}))
    subject.snapshot(ServiceHandle(result.handle))
    subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)

    service = Session().get_service_model("elasticache")
    for operation, request in ec.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)


def test_provision_does_not_adopt_another_services_cache():
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    subject, ec, _ = driver()
    first = subject.provision(spec())
    second = subject.provision(
        dataclasses.replace(
            spec(), managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456780", recorded_handle=first.handle
        )
    )

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "refusing to adopt" in second.message
    assert [name for name, _ in ec.calls].count("ModifyServerlessCache") == 0


def test_config_cannot_name_a_snapshot_to_copy_data_from():
    # restore() takes only the snapshot the platform retained for this app; a
    # config naming one itself skipped that check (#2087).
    subject, ec, _ = driver()
    foreign = {"snapshot_arns_to_restore": ["arn:aws:elasticache:us-west-2:123456789012:serverlesscachesnapshot:x"]}

    provisioned = subject.provision(spec(**foreign))
    widened = subject.restore(
        SnapshotHandle("redis/retained", "arn:aws:elasticache:us-west-2:123456789012:serverlesscachesnapshot:r", ""),
        spec(**foreign),
    )

    assert not provisioned.ok and "cannot name a snapshot to copy data from" in provisioned.message
    assert not widened.ok and "cannot name a snapshot to copy data from" in widened.message
    assert not [operation for operation, _ in ec.calls if operation == "CreateServerlessCache"]


def test_restore_seeds_the_cache_from_the_retained_snapshot_only():
    subject, ec, _ = driver()
    retained = "arn:aws:elasticache:us-west-2:123456789012:serverlesscachesnapshot:retained"

    restored = subject.restore(SnapshotHandle("redis/retained", retained, ""), spec())

    assert restored.ok, restored.message
    create = next(payload for operation, payload in ec.calls if operation == "CreateServerlessCache")
    assert create["SnapshotArnsToRestore"] == [retained]


@pytest.mark.parametrize("target", ["user", "group", "membership", "untagged"])
def test_existing_access_resources_require_exact_owner_and_membership(target):
    import dataclasses

    subject, ec, _ = driver()
    owner = spec()
    name = subject._cache_name(owner)
    assert subject.provision(owner).ok
    ec.caches.clear()
    user_id, group_id = subject._user_id(name), subject._group_id(name)
    foreign = "8b7e2c6b-0b93-4126-a121-abc123456780"
    resource = ec.users[user_id] if target in {"user", "untagged"} else ec.groups[group_id]
    if target == "membership":
        resource["UserIds"] = ["another-user"]
    elif target == "untagged":
        resource["Tags"] = []
    else:
        resource["Tags"] = [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/managed_service_id", "Value": foreign},
        ]
    before = dataclasses.replace(owner, recorded_handle=f"redis/{name}")
    creates = len([op for op, _ in ec.calls if op == "CreateServerlessCache"])
    result = subject.provision(before)
    assert not result.ok
    assert len([op for op, _ in ec.calls if op == "CreateServerlessCache"]) == creates
    assert ec.users[user_id] is resource or ec.groups[group_id] is resource
    if target == "membership":
        assert ec.groups[group_id]["UserIds"] == ["another-user"]
    elif target == "untagged":
        assert resource["Tags"] == []
    else:
        assert resource["Tags"][-1]["Value"] == foreign


def test_long_prefix_near_identical_service_ids_keep_distinct_cache_and_access_names():
    import dataclasses

    subject, ec, _ = driver()
    subject._config = dataclasses.replace(subject._config, cache_name_prefix="p" * 300)
    rows = [spec(), dataclasses.replace(spec(), managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456788")]
    results = [subject.provision(row) for row in rows]
    assert all(result.ok for result in results)
    assert len(ec.caches) == len(ec.users) == len(ec.groups) == 2
    for row, result in zip(rows, results, strict=True):
        name = result.handle.partition("/")[2]
        assert name.endswith(row.managed_service_id.replace("-", "")) and len(name) <= 38
        for identifier in [subject._user_id(name), subject._group_id(name)]:
            assert identifier.endswith(row.managed_service_id.replace("-", "")) and len(identifier) <= 40
