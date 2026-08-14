from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
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
        self.groups[kwargs["UserGroupId"]] = {**kwargs, "Status": "active"}

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
