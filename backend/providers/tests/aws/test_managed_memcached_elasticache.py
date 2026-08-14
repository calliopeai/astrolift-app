from __future__ import annotations

from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.memcached_elasticache import (
    ElastiCacheMemcachedConfig,
    ElastiCacheMemcachedDriver,
)


class NotFound(Exception):
    pass


class FakeElastiCache:
    def __init__(self) -> None:
        self.clusters: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_cache_clusters(self, **kwargs):
        name = kwargs["CacheClusterId"]
        if name not in self.clusters:
            raise NotFound("CacheClusterNotFoundFault")
        return {"CacheClusters": [self.clusters[name]]}

    def create_cache_cluster(self, **kwargs):
        self.calls.append(("CreateCacheCluster", kwargs))
        name = kwargs["CacheClusterId"]
        self.clusters[name] = {
            **kwargs,
            "CacheClusterStatus": "creating",
            "ConfigurationEndpoint": {"Address": f"{name}.cfg.cache", "Port": 11211},
            "CacheNodes": [
                {"Endpoint": {"Address": f"{name}-{index}.cache", "Port": 11211}}
                for index in range(kwargs["NumCacheNodes"])
            ],
            "ARN": f"arn:aws:elasticache:us-west-2:123456789012:cluster:{name}",
        }

    def modify_cache_cluster(self, **kwargs):
        self.calls.append(("ModifyCacheCluster", kwargs))
        self.clusters[kwargs["CacheClusterId"]].update(kwargs)

    def delete_cache_cluster(self, **kwargs):
        self.calls.append(("DeleteCacheCluster", kwargs))
        self.clusters[kwargs["CacheClusterId"]]["CacheClusterStatus"] = "deleting"


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="sessions",
        size="medium",
        config=config,
        isolation="dedicated",
    )


def driver():
    ec = FakeElastiCache()
    subject = ElastiCacheMemcachedDriver(
        config=ElastiCacheMemcachedConfig(
            region="us-west-2",
            cache_subnet_group="private-cache",
            security_group_ids=["sg-cache"],
        ),
        elasticache_client=ec,
    )
    return subject, ec


def test_provision_binding_update_and_idempotence():
    subject, ec = driver()
    result = subject.provision(spec(num_cache_nodes=3))
    again = subject.provision(spec(num_cache_nodes=3))
    binding = subject.binding(ServiceHandle(result.handle))
    updated = subject.update(UpdateSpec(result.handle, size="large", config={"num_cache_nodes": 4}))

    assert result.ok and again.ok and updated.ok
    assert result.handle.startswith("cache/")
    assert len([call for call in ec.calls if call[0] == "CreateCacheCluster"]) == 1
    create = next(payload for operation, payload in ec.calls if operation == "CreateCacheCluster")
    assert create["Engine"] == "memcached"
    assert create["CacheNodeType"] == "cache.t4g.small"
    assert create["NumCacheNodes"] == 3
    assert create["AZMode"] == "cross-az"
    assert create["TransitEncryptionEnabled"] is True
    assert binding.env_vars["CACHE_PROTOCOL"].literal == "memcached"
    assert binding.env_vars["CACHE_NODES"].literal.count(",") == 2


def test_invalid_node_count_and_snapshot_refusal():
    subject, _ = driver()
    invalid = subject.provision(spec(num_cache_nodes="many"))
    assert invalid.ok is False
    assert "integer" in invalid.message
    with pytest.raises(ManagedServiceError, match="does not support snapshots"):
        subject.snapshot(ServiceHandle("cache/example"))


def test_async_delete_converges():
    subject, ec = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    first = subject.deprovision(DeprovisionSpec(result.handle))
    assert first.ok is False and first.retryable is True
    ec.clusters.pop(name)
    final = subject.deprovision(DeprovisionSpec(result.handle))
    assert final.ok is True


def test_current_botocore_accepts_memcached_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, ec = driver()
    result = subject.provision(spec(num_cache_nodes=2))
    subject.update(UpdateSpec(result.handle, size="large", config={"num_cache_nodes": 3}))
    subject.deprovision(DeprovisionSpec(result.handle))

    service = Session().get_service_model("elasticache")
    for operation, request in ec.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)
