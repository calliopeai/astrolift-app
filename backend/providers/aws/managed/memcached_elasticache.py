"""Amazon ElastiCache node-based Memcached managed-service driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for

KIND = "cache"
_SIZE_TO_NODE_TYPE = {
    "small": "cache.t4g.micro",
    "medium": "cache.t4g.small",
    "large": "cache.m7g.large",
    "xlarge": "cache.m7g.xlarge",
}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "modifying": "updating",
    "deleting": "deprovisioning",
    "create-failed": "error",
    "incompatible-network": "error",
    "restore-failed": "error",
}


@dataclass(frozen=True)
class ElastiCacheMemcachedConfig:
    region: str
    cache_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    cluster_name_prefix: str = "astrolift"
    engine_version: str = ""
    transit_encryption_default: bool = True


class ElastiCacheMemcachedDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: ElastiCacheMemcachedConfig,
        elasticache_client: Any | None = None,
    ) -> None:
        self._config = config
        if elasticache_client is None:
            import boto3

            elasticache_client = boto3.client("elasticache", region_name=config.region)
        self._ec = elasticache_client

    @driver_op(
        cloud="aws",
        driver="elasticache_memcached",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_memcached_config"])
        cluster_id = self._cluster_id(spec)
        handle = handle_for(kind=KIND, resource_id=cluster_id)
        if self._describe(cluster_id) is not None:
            updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
            return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        nodes = int(cfg.get("num_cache_nodes", 2 if spec.isolation == "dedicated" else 1))
        kwargs: dict[str, Any] = {
            "CacheClusterId": cluster_id,
            "Engine": "memcached",
            "CacheNodeType": str(cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size, "cache.t4g.micro")),
            "NumCacheNodes": nodes,
            "AZMode": str(cfg.get("az_mode", "cross-az" if nodes > 1 else "single-az")),
            "CacheSubnetGroupName": self._config.cache_subnet_group,
            "SecurityGroupIds": list(self._config.security_group_ids),
            "TransitEncryptionEnabled": bool(
                cfg.get("transit_encryption", self._config.transit_encryption_default),
            ),
            "Tags": tags_for(spec),
        }
        engine_version = str(cfg.get("engine_version") or self._config.engine_version)
        if engine_version:
            kwargs["EngineVersion"] = engine_version
        for key, aws_key in (
            ("parameter_group", "CacheParameterGroupName"),
            ("maintenance_window", "PreferredMaintenanceWindow"),
            ("notification_topic_arn", "NotificationTopicArn"),
            ("network_type", "NetworkType"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("preferred_availability_zones"):
            kwargs["PreferredAvailabilityZones"] = list(cfg["preferred_availability_zones"])
        try:
            self._ec.create_cache_cluster(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create_cache_cluster: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Memcached cluster {cluster_id} provisioning")

    @driver_op(cloud="aws", driver="elasticache_memcached")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, cluster_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_memcached_config"])
        kwargs: dict[str, Any] = {
            "CacheClusterId": cluster_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        if spec.size:
            node_type = str(cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size, ""))
            if node_type:
                kwargs["CacheNodeType"] = node_type
        for key, aws_key, cast in (
            ("num_cache_nodes", "NumCacheNodes", int),
            ("engine_version", "EngineVersion", str),
            ("parameter_group", "CacheParameterGroupName", str),
            ("maintenance_window", "PreferredMaintenanceWindow", str),
            ("notification_topic_arn", "NotificationTopicArn", str),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            kwargs["SecurityGroupIds"] = list(cfg["security_group_ids"])
        if cfg.get("preferred_availability_zones"):
            kwargs["NewAvailabilityZones"] = list(cfg["preferred_availability_zones"])
        if len(kwargs) == 2:
            return UpdateResult(True, spec.handle, "no Memcached changes requested")
        try:
            self._ec.modify_cache_cluster(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify_cache_cluster: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Memcached cluster {cluster_id} update queued")

    @driver_op(
        cloud="aws",
        driver="elasticache_memcached",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del delete_data, force_destroy
        _, cluster_id = parse_handle(spec.handle)
        cluster = self._describe(cluster_id)
        if cluster is None:
            return DeprovisionResult(True, spec.handle, f"Memcached cluster {cluster_id} already gone")
        if cluster.get("CacheClusterStatus") == "deleting":
            return DeprovisionResult(
                False,
                spec.handle,
                f"Memcached cluster {cluster_id} deletion is still in progress",
                ["cache_deletion_in_progress"],
                retryable=True,
            )
        try:
            self._ec.delete_cache_cluster(CacheClusterId=cluster_id)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete_cache_cluster: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        return DeprovisionResult(
            False,
            spec.handle,
            f"Memcached cluster {cluster_id} deletion queued",
            ["cache_deletion_in_progress"],
            retryable=True,
        )

    @driver_op(cloud="aws", driver="elasticache_memcached")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe(cluster_id)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"Memcached cluster {cluster_id} not found")
        state = str(cluster.get("CacheClusterStatus", "unknown"))
        return ServiceStatus(handle.handle, _STATE.get(state, "updating"), f"ElastiCache reports {state}")

    @driver_op(cloud="aws", driver="elasticache_memcached")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe(cluster_id, show_nodes=True)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing Memcached cluster {cluster_id}")
        endpoint = cluster.get("ConfigurationEndpoint") or {}
        nodes = [node.get("Endpoint") or {} for node in cluster.get("CacheNodes") or [] if node.get("Endpoint")]
        if not endpoint and nodes:
            endpoint = nodes[0]
        host = str(endpoint.get("Address", ""))
        port = str(endpoint.get("Port", 11211))
        node_list = (
            ",".join(f"{node.get('Address')}:{node.get('Port', 11211)}" for node in nodes if node.get("Address"))
            or f"{host}:{port}"
        )
        return Binding(
            env_vars={
                "CACHE_HOST": ValueRef(literal=host),
                "CACHE_PORT": ValueRef(literal=port),
                "CACHE_PROTOCOL": ValueRef(literal="memcached"),
                "CACHE_NODES": ValueRef(literal=node_list),
                "CACHE_TLS": ValueRef(literal="1" if cluster.get("TransitEncryptionEnabled") else "0"),
                "CACHE_RESOURCE_ARN": ValueRef(literal=str(cluster.get("ARN", ""))),
            },
            notes="ElastiCache Memcached auto-discovery and node endpoints",
        )

    @driver_op(cloud="aws", driver="elasticache_memcached")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise ManagedServiceError("ElastiCache Memcached does not support snapshots")

    @driver_op(cloud="aws", driver="elasticache_memcached")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise ManagedServiceError("ElastiCache Memcached does not support snapshots")

    @driver_op(cloud="aws", driver="elasticache_memcached", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node_type": {"type": "string"},
                "num_cache_nodes": {"type": "integer", "minimum": 1, "maximum": 40},
                "engine_version": {"type": "string"},
                "parameter_group": {"type": "string"},
                "transit_encryption": {"type": "boolean"},
                "az_mode": {"type": "string", "enum": ["single-az", "cross-az"]},
                "preferred_availability_zones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "maintenance_window": {"type": "string"},
                "notification_topic_arn": {"type": "string"},
                "network_type": {"type": "string", "enum": ["ipv4", "ipv6", "dual_stack"]},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "apply_immediately": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="elasticache_memcached", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "CACHE_HOST": "Memcached configuration endpoint host",
                "CACHE_PORT": "Memcached endpoint port",
                "CACHE_PROTOCOL": "memcached",
                "CACHE_NODES": "Comma-separated Memcached node endpoints",
                "CACHE_TLS": "1 when in-transit encryption is enabled",
                "CACHE_RESOURCE_ARN": "ElastiCache cluster ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "node_type",
            "num_cache_nodes",
            "engine_version",
            "parameter_group",
            "preferred_availability_zones",
            "maintenance_window",
            "notification_topic_arn",
            "security_group_ids",
            "apply_immediately",
        ]

    def _describe(self, cluster_id: str, *, show_nodes: bool = False) -> dict[str, Any] | None:
        try:
            rows = self._ec.describe_cache_clusters(
                CacheClusterId=cluster_id,
                ShowCacheNodeInfo=show_nodes,
            ).get("CacheClusters", [])
        except Exception as exc:
            if _not_found(exc, "CacheClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _cluster_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.cluster_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "cache",
            )
            if part
        ).lower()
        clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        if not clean or not clean[0].isalpha():
            clean = f"a-{clean}"
        return clean.strip("-")[:50].rstrip("-")

    @staticmethod
    def _validate_config(cfg: dict[str, Any], *, partial: bool = False) -> str:
        del partial
        if "num_cache_nodes" in cfg:
            value = cfg["num_cache_nodes"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 40:
                return "num_cache_nodes must be an integer from 1 through 40"
        if cfg.get("az_mode") == "cross-az" and int(cfg.get("num_cache_nodes", 2)) < 2:
            return "az_mode=cross-az requires at least two cache nodes"
        return ""


def _not_found(exc: Exception, marker: str) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return marker in code or marker in type(exc).__name__ or marker in str(exc)


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return (
        status >= 500
        or code.startswith("Throttl")
        or code
        in {
            "InternalFailure",
            "ServiceUnavailable",
            "RequestTimeout",
            "InvalidCacheClusterState",
        }
    )
