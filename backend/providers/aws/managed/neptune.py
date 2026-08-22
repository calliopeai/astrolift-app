"""Amazon Neptune provisioned and Serverless managed-service drivers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
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
from aws.session import aws_client

KIND = "graph_db"
_PORT = 8182
_SIZE_TO_INSTANCE_CLASS = {
    "small": "db.t4g.medium",
    "medium": "db.r6g.large",
    "large": "db.r6g.xlarge",
    "xlarge": "db.r6g.2xlarge",
}
_STATE = {
    "available": "available",
    "backing-up": "updating",
    "creating": "provisioning",
    "deleting": "deprovisioning",
    "failing-over": "updating",
    "maintenance": "updating",
    "migrating": "updating",
    "modifying": "updating",
    "rebooting": "updating",
    "resetting-master-credentials": "updating",
    "starting": "provisioning",
    "stopped": "error",
    "stopping": "updating",
}


@dataclass(frozen=True)
class NeptuneConfig(CredentialedConfig):
    region: str
    account_id: str
    db_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    cluster_name_prefix: str = "astrolift"
    engine_version: str = ""
    serverless_v2: bool = False
    backup_retention_days: int = 7
    deletion_protection_default: bool = True
    iam_auth_default: bool = True


class NeptuneDriver(ManagedServiceDriver):
    def __init__(self, *, config: NeptuneConfig, neptune_client: Any | None = None) -> None:
        self._config = config
        if neptune_client is None:
            neptune_client = aws_client("neptune", region=config.region, credential=config.credential)
        self._neptune = neptune_client

    @driver_op(
        cloud="aws",
        driver="neptune",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_neptune_config"])
        cluster_id = self._cluster_id(spec)
        handle = handle_for(kind=KIND, resource_id=cluster_id)
        cluster = self._describe_cluster(cluster_id)
        try:
            if cluster is None:
                if cfg.get("snapshot_identifier"):
                    self._restore_cluster(cluster_id, spec, cfg)
                else:
                    self._create_cluster(cluster_id, spec, cfg)
                tags = tags_for(spec)
                reconcile_cfg = dict(cfg)
                reconcile_cfg.setdefault(
                    "num_instances",
                    2 if spec.isolation == "dedicated" else 1,
                )
                self._reconcile_instances(cluster_id, spec.size, reconcile_cfg, tags)
            else:
                updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
                return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Neptune: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Neptune cluster {cluster_id} provisioning")

    @driver_op(cloud="aws", driver="neptune")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, cluster_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return UpdateResult(False, spec.handle, f"Neptune cluster {cluster_id} not found", ["not_found"])
        validation_cfg = dict(cfg)
        if self._config.serverless_v2:
            current_scaling = cluster.get("ServerlessV2ScalingConfiguration") or {}
            validation_cfg.setdefault("min_capacity", current_scaling.get("MinCapacity", 1.0))
            validation_cfg.setdefault("max_capacity", current_scaling.get("MaxCapacity", 16.0))
        error = self._validate_config(validation_cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_neptune_config"])
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        for key, aws_key, cast in (
            ("engine_version", "EngineVersion", str),
            ("backup_retention_days", "BackupRetentionPeriod", int),
            ("deletion_protection", "DeletionProtection", bool),
            ("iam_database_authentication", "EnableIAMDatabaseAuthentication", bool),
            ("cluster_parameter_group", "DBClusterParameterGroupName", str),
            ("backup_window", "PreferredBackupWindow", str),
            ("maintenance_window", "PreferredMaintenanceWindow", str),
            ("storage_type", "StorageType", str),
            ("network_type", "NetworkType", str),
            ("copy_tags_to_snapshot", "CopyTagsToSnapshot", bool),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            kwargs["VpcSecurityGroupIds"] = list(cfg["security_group_ids"])
        if self._config.serverless_v2 and any(key in cfg for key in ("min_capacity", "max_capacity")):
            current = cluster.get("ServerlessV2ScalingConfiguration") or {}
            kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(validation_cfg, current=current)
        if "cloudwatch_log_exports" in cfg:
            current_exports = set(cluster.get("EnabledCloudwatchLogsExports") or [])
            desired_exports = set(cfg["cloudwatch_log_exports"])
            kwargs["CloudwatchLogsExportConfiguration"] = {
                "EnableLogTypes": sorted(desired_exports - current_exports),
                "DisableLogTypes": sorted(current_exports - desired_exports),
            }
        changed = False
        try:
            if len(kwargs) > 2:
                self._neptune.modify_db_cluster(**kwargs)
                changed = True
            if spec.size or any(
                key in cfg
                for key in (
                    "num_instances",
                    "instance_class",
                    "instance_classes",
                    "instance_parameter_group",
                    "auto_minor_version_upgrade",
                    "publicly_accessible",
                    "monitoring_interval",
                    "monitoring_role_arn",
                    "enable_performance_insights",
                    "performance_insights_kms_key_id",
                )
            ):
                tags = self._resource_tags(str(cluster.get("DBClusterArn") or ""))
                changed = self._reconcile_instances(cluster_id, spec.size or "", cfg, tags) or changed
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify Neptune: {exc}", [str(exc)])
        message = f"Neptune cluster {cluster_id} update queued" if changed else "no Neptune changes requested"
        return UpdateResult(True, spec.handle, message)

    @driver_op(
        cloud="aws",
        driver="neptune",
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
        _, cluster_id = parse_handle(spec.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return DeprovisionResult(True, spec.handle, f"Neptune cluster {cluster_id} already gone")
        if cluster.get("DeletionProtection"):
            if not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Neptune cluster {cluster_id} has deletion protection enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            try:
                self._neptune.modify_db_cluster(
                    DBClusterIdentifier=cluster_id,
                    DeletionProtection=False,
                    ApplyImmediately=True,
                )
            except Exception as exc:
                return _deprovision_error(spec.handle, "disable Neptune deletion protection", exc)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Neptune deletion protection disabled for {cluster_id}; waiting",
                ["cluster_update_in_progress"],
            )
        instances = self._instances(cluster_id)
        if instances:
            for instance in instances:
                if instance.get("DBInstanceStatus") == "deleting":
                    continue
                try:
                    self._neptune.delete_db_instance(
                        DBInstanceIdentifier=str(instance["DBInstanceIdentifier"]),
                        SkipFinalSnapshot=True,
                    )
                except Exception as exc:
                    return _deprovision_error(spec.handle, "delete Neptune DB instance", exc)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Neptune instances for {cluster_id} are deleting",
                ["instance_deletion_in_progress"],
            )
        if cluster.get("Status") == "deleting":
            return DeprovisionResult(
                False,
                spec.handle,
                f"Neptune cluster {cluster_id} deletion is still in progress",
                ["cluster_deletion_in_progress"],
            )
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "SkipFinalSnapshot": bool(delete_data),
        }
        if not delete_data:
            kwargs["FinalDBSnapshotIdentifier"] = _snapshot_name(cluster_id, "final")
        try:
            self._neptune.delete_db_cluster(**kwargs)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Neptune DB cluster", exc)
        return DeprovisionResult(
            False,
            spec.handle,
            f"Neptune cluster {cluster_id} deletion queued",
            ["cluster_deletion_in_progress"],
        )

    @driver_op(cloud="aws", driver="neptune")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"Neptune cluster {cluster_id} not found")
        cluster_state = str(cluster.get("Status") or "unknown")
        instances = self._instances(cluster_id)
        if cluster_state == "available":
            if not instances:
                return ServiceStatus(handle.handle, "provisioning", "Neptune has no database instances yet")
            if all(instance.get("DBInstanceStatus") == "available" for instance in instances):
                return ServiceStatus(handle.handle, "available", "Neptune cluster and instances are available")
            return ServiceStatus(handle.handle, "provisioning", "Neptune instances are not yet available")
        return ServiceStatus(
            handle.handle,
            _STATE.get(cluster_state, "updating"),
            f"Neptune reports {cluster_state}",
        )

    @driver_op(cloud="aws", driver="neptune")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing Neptune cluster {cluster_id}")
        cfg = config or {}
        protocol = str(cfg.get("protocol") or "gremlin").lower()
        if protocol not in {"gremlin", "sparql", "opencypher"}:
            raise ManagedServiceError("Neptune protocol must be gremlin, sparql, or opencypher")
        host = str(cluster.get("Endpoint") or "")
        reader_host = str(cluster.get("ReaderEndpoint") or host)
        port = int(cluster.get("Port") or _PORT)
        url = _protocol_url(protocol, host, port)
        reader_url = _protocol_url(protocol, reader_host, port)
        iam_enabled = bool(cluster.get("IAMDatabaseAuthenticationEnabled"))
        resource_arn = str(cluster.get("DBClusterArn") or "")
        env_vars = {
            "GRAPH_DB_URL": ValueRef(literal=url),
            "GRAPH_DB_READER_URL": ValueRef(literal=reader_url),
            "GRAPH_DB_ENDPOINT": ValueRef(literal=host),
            "GRAPH_DB_PORT": ValueRef(literal=str(port)),
            "GRAPH_DB_PROTOCOL": ValueRef(literal=protocol),
            "GRAPH_DB_TLS": ValueRef(literal="1"),
            "GRAPH_DB_AUTH_MODE": ValueRef(literal="iam_sigv4" if iam_enabled else "network_only"),
            "GRAPH_DB_REGION": ValueRef(literal=self._config.region),
            "GRAPH_DB_RESOURCE_ARN": ValueRef(literal=resource_arn),
        }
        grants: list[Grant] = []
        if iam_enabled:
            resource_id = str(cluster.get("DbClusterResourceId") or "")
            if not resource_id:
                raise ManagedServiceError(f"Neptune cluster {cluster_id} has no DbClusterResourceId")
            grants.append(
                Grant(
                    _database_auth_arn(
                        resource_arn=resource_arn,
                        region=self._config.region,
                        account_id=self._config.account_id,
                        resource_id=resource_id,
                    ),
                    ["neptune-db:connect"],
                ),
            )
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes="TLS Neptune endpoint; IAM SigV4 is used when database authentication is enabled",
        )

    @driver_op(cloud="aws", driver="neptune")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, cluster_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_name(cluster_id, "snapshot")
        try:
            cluster = self._describe_cluster(cluster_id)
            if cluster is None:
                raise ManagedServiceError(f"snapshot requested for missing Neptune cluster {cluster_id}")
            response = self._neptune.create_db_cluster_snapshot(
                DBClusterIdentifier=cluster_id,
                DBClusterSnapshotIdentifier=snapshot_id,
                Tags=self._resource_tags(str(cluster.get("DBClusterArn") or "")),
            )
        except Exception as exc:
            raise ManagedServiceError(f"create Neptune DB cluster snapshot: {exc}") from exc
        snapshot = response.get("DBClusterSnapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("DBClusterSnapshotArn") or snapshot_id),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="neptune")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        cfg["snapshot_identifier"] = snapshot.snapshot_id
        return self.provision(replace(target, config=cfg))

    @driver_op(cloud="aws", driver="neptune", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "protocol": {"type": "string", "enum": ["gremlin", "sparql", "opencypher"]},
                "instance_class": {"type": "string"},
                "instance_classes": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                "num_instances": {"type": "integer", "minimum": 1, "maximum": 16},
                "engine_version": {"type": "string"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "deletion_protection": {"type": "boolean"},
                "storage_encrypted": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "storage_type": {"type": "string", "enum": ["standard", "iopt1"]},
                "iam_database_authentication": {"type": "boolean"},
                "cluster_parameter_group": {"type": "string"},
                "instance_parameter_group": {"type": "string"},
                "backup_window": {"type": "string"},
                "maintenance_window": {"type": "string"},
                "cloudwatch_log_exports": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["audit", "slowquery"]},
                    "uniqueItems": True,
                },
                "network_type": {"type": "string", "enum": ["IPV4", "DUAL"]},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "availability_zones": {"type": "array", "items": {"type": "string"}},
                "min_capacity": {"type": "number", "minimum": 1, "maximum": 128},
                "max_capacity": {"type": "number", "minimum": 2.5, "maximum": 128},
                "auto_minor_version_upgrade": {"type": "boolean"},
                "publicly_accessible": {"type": "boolean"},
                "monitoring_interval": {"type": "integer", "minimum": 0},
                "monitoring_role_arn": {"type": "string"},
                "enable_performance_insights": {"type": "boolean"},
                "performance_insights_kms_key_id": {"type": "string"},
                "promotion_tiers": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 15},
                    "maxItems": 16,
                },
                "copy_tags_to_snapshot": {"type": "boolean"},
                "global_cluster_identifier": {"type": "string"},
                "replication_source_identifier": {"type": "string"},
                "source_region": {"type": "string"},
                "pre_signed_url": {"type": "string"},
                "apply_immediately": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="neptune", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "GRAPH_DB_URL": "Protocol-specific writer URL",
                "GRAPH_DB_READER_URL": "Protocol-specific read-replica URL",
                "GRAPH_DB_ENDPOINT": "Writer DNS endpoint",
                "GRAPH_DB_PORT": "TLS database port",
                "GRAPH_DB_PROTOCOL": "gremlin, sparql, or opencypher",
                "GRAPH_DB_TLS": "Always 1 for managed Neptune connections",
                "GRAPH_DB_AUTH_MODE": "iam_sigv4 or network_only",
                "GRAPH_DB_REGION": "AWS region used for SigV4 signing",
                "GRAPH_DB_RESOURCE_ARN": "Neptune cluster ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "protocol",
            "instance_class",
            "instance_classes",
            "num_instances",
            "engine_version",
            "backup_retention_days",
            "deletion_protection",
            "iam_database_authentication",
            "cluster_parameter_group",
            "instance_parameter_group",
            "backup_window",
            "maintenance_window",
            "cloudwatch_log_exports",
            "storage_type",
            "network_type",
            "security_group_ids",
            "min_capacity",
            "max_capacity",
            "auto_minor_version_upgrade",
            "publicly_accessible",
            "monitoring_interval",
            "monitoring_role_arn",
            "enable_performance_insights",
            "performance_insights_kms_key_id",
            "promotion_tiers",
            "copy_tags_to_snapshot",
            "apply_immediately",
        ]

    def _create_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "Engine": "neptune",
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "Port": _PORT,
            "BackupRetentionPeriod": int(
                cfg.get("backup_retention_days", self._config.backup_retention_days),
            ),
            "StorageEncrypted": bool(cfg.get("storage_encrypted", True)),
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "EnableIAMDatabaseAuthentication": bool(
                cfg.get("iam_database_authentication", self._config.iam_auth_default),
            ),
            "CopyTagsToSnapshot": bool(cfg.get("copy_tags_to_snapshot", True)),
            "Tags": tags_for(spec),
        }
        engine_version = str(cfg.get("engine_version") or self._config.engine_version)
        if engine_version:
            kwargs["EngineVersion"] = engine_version
        for key, aws_key in (
            ("engine_version", "EngineVersion"),
            ("kms_key_arn", "KmsKeyId"),
            ("cluster_parameter_group", "DBClusterParameterGroupName"),
            ("backup_window", "PreferredBackupWindow"),
            ("maintenance_window", "PreferredMaintenanceWindow"),
            ("storage_type", "StorageType"),
            ("network_type", "NetworkType"),
            ("global_cluster_identifier", "GlobalClusterIdentifier"),
            ("replication_source_identifier", "ReplicationSourceIdentifier"),
            ("source_region", "SourceRegion"),
            ("pre_signed_url", "PreSignedUrl"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("availability_zones"):
            kwargs["AvailabilityZones"] = list(cfg["availability_zones"])
        if cfg.get("cloudwatch_log_exports"):
            kwargs["EnableCloudwatchLogsExports"] = list(cfg["cloudwatch_log_exports"])
        if self._config.serverless_v2:
            kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(cfg)
        self._neptune.create_db_cluster(**kwargs)

    def _restore_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "SnapshotIdentifier": str(cfg["snapshot_identifier"]),
            "Engine": "neptune",
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "Port": _PORT,
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "EnableIAMDatabaseAuthentication": bool(
                cfg.get("iam_database_authentication", self._config.iam_auth_default),
            ),
            "CopyTagsToSnapshot": bool(cfg.get("copy_tags_to_snapshot", True)),
            "Tags": tags_for(spec),
        }
        for key, aws_key in (
            ("kms_key_arn", "KmsKeyId"),
            ("cluster_parameter_group", "DBClusterParameterGroupName"),
            ("storage_type", "StorageType"),
            ("network_type", "NetworkType"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("availability_zones"):
            kwargs["AvailabilityZones"] = list(cfg["availability_zones"])
        if cfg.get("cloudwatch_log_exports"):
            kwargs["EnableCloudwatchLogsExports"] = list(cfg["cloudwatch_log_exports"])
        if self._config.serverless_v2:
            kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(cfg)
        self._neptune.restore_db_cluster_from_snapshot(**kwargs)

    def _reconcile_instances(
        self,
        cluster_id: str,
        size: str,
        cfg: dict[str, Any],
        tags: list[dict[str, str]],
    ) -> bool:
        instances = self._instances(cluster_id)
        existing = {str(row["DBInstanceIdentifier"]): row for row in instances}
        classes = [str(value) for value in cfg.get("instance_classes") or []]
        tiers = list(cfg.get("promotion_tiers") or [])
        desired_count = int(
            cfg.get(
                "num_instances",
                max(len(instances), len(classes), len(tiers), 1),
            ),
        )
        changed = False
        for index in range(desired_count):
            instance_id = _name(cluster_id, f"i{index + 1}")
            desired_class = classes[index] if index < len(classes) else self._instance_class(size, cfg)
            instance = existing.get(instance_id)
            if instance is None:
                kwargs: dict[str, Any] = {
                    "DBInstanceIdentifier": instance_id,
                    "DBInstanceClass": desired_class,
                    "Engine": "neptune",
                    "DBClusterIdentifier": cluster_id,
                    "PromotionTier": int(tiers[index] if index < len(tiers) else index),
                    "AutoMinorVersionUpgrade": bool(cfg.get("auto_minor_version_upgrade", True)),
                    "PubliclyAccessible": bool(cfg.get("publicly_accessible", False)),
                    "Tags": tags,
                }
                zones = list(cfg.get("availability_zones") or [])
                if index < len(zones):
                    kwargs["AvailabilityZone"] = zones[index]
                self._add_instance_options(kwargs, cfg)
                self._neptune.create_db_instance(**kwargs)
                changed = True
                continue
            modify: dict[str, Any] = {
                "DBInstanceIdentifier": instance_id,
                "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
            }
            if desired_class != str(instance.get("DBInstanceClass") or ""):
                modify["DBInstanceClass"] = desired_class
            for key, aws_key, cast in (
                ("instance_parameter_group", "DBParameterGroupName", str),
                ("auto_minor_version_upgrade", "AutoMinorVersionUpgrade", bool),
                ("publicly_accessible", "PubliclyAccessible", bool),
                ("monitoring_interval", "MonitoringInterval", int),
                ("monitoring_role_arn", "MonitoringRoleArn", str),
                ("enable_performance_insights", "EnablePerformanceInsights", bool),
                ("performance_insights_kms_key_id", "PerformanceInsightsKMSKeyId", str),
            ):
                if key in cfg:
                    modify[aws_key] = cast(cfg[key])
            if index < len(tiers):
                modify["PromotionTier"] = int(tiers[index])
            if len(modify) > 2:
                self._neptune.modify_db_instance(**modify)
                changed = True
        desired_ids = {_name(cluster_id, f"i{index + 1}") for index in range(desired_count)}
        for instance_id, instance in sorted(existing.items(), reverse=True):
            if instance_id in desired_ids or instance.get("DBInstanceStatus") == "deleting":
                continue
            self._neptune.delete_db_instance(
                DBInstanceIdentifier=instance_id,
                SkipFinalSnapshot=True,
            )
            changed = True
        return changed

    @staticmethod
    def _add_instance_options(kwargs: dict[str, Any], cfg: dict[str, Any]) -> None:
        for key, aws_key, cast in (
            ("instance_parameter_group", "DBParameterGroupName", str),
            ("monitoring_interval", "MonitoringInterval", int),
            ("monitoring_role_arn", "MonitoringRoleArn", str),
            ("enable_performance_insights", "EnablePerformanceInsights", bool),
            ("performance_insights_kms_key_id", "PerformanceInsightsKMSKeyId", str),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])

    def _describe_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        try:
            rows = self._neptune.describe_db_clusters(DBClusterIdentifier=cluster_id).get("DBClusters", [])
        except Exception as exc:
            if _not_found(exc, "DBClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _instances(self, cluster_id: str) -> list[dict[str, Any]]:
        try:
            rows = self._neptune.describe_db_instances(
                Filters=[{"Name": "db-cluster-id", "Values": [cluster_id]}],
            ).get("DBInstances", [])
        except Exception as exc:
            if _not_found(exc, "DBInstanceNotFound"):
                return []
            raise
        return [row for row in rows if row.get("DBClusterIdentifier") == cluster_id]

    def _resource_tags(self, resource_arn: str) -> list[dict[str, str]]:
        if not resource_arn:
            return []
        return list(self._neptune.list_tags_for_resource(ResourceName=resource_arn).get("TagList", []))

    def _cluster_id(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.cluster_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "graph",
        )

    def _instance_class(self, size: str, cfg: dict[str, Any]) -> str:
        if self._config.serverless_v2:
            return str(cfg.get("instance_class") or "db.serverless")
        return str(cfg.get("instance_class") or _SIZE_TO_INSTANCE_CLASS.get(size, "db.t4g.medium"))

    @staticmethod
    def _scaling(
        cfg: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        existing = current or {}
        return {
            "MinCapacity": float(cfg.get("min_capacity", existing.get("MinCapacity", 1.0))),
            "MaxCapacity": float(cfg.get("max_capacity", existing.get("MaxCapacity", 16.0))),
        }

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if "num_instances" in cfg:
            value = cfg["num_instances"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
                return "num_instances must be an integer from 1 through 16"
        if "instance_classes" in cfg:
            values = cfg["instance_classes"]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 16
                or any(not isinstance(value, str) or not value for value in values)
            ):
                return "instance_classes must contain 1 through 16 non-empty class names"
            if "num_instances" in cfg and len(values) > int(cfg["num_instances"]):
                return "instance_classes cannot contain more entries than num_instances"
        if "promotion_tiers" in cfg:
            values = cfg["promotion_tiers"]
            if (
                not isinstance(values, list)
                or len(values) > 16
                or any(
                    not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 15 for value in values
                )
            ):
                return "promotion_tiers must contain at most 16 integers from 0 through 15"
            if "num_instances" in cfg and len(values) > int(cfg["num_instances"]):
                return "promotion_tiers cannot contain more entries than num_instances"
        if "backup_retention_days" in cfg:
            value = cfg["backup_retention_days"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 35:
                return "backup_retention_days must be an integer from 1 through 35"
        if cfg.get("protocol", "gremlin") not in {"gremlin", "sparql", "opencypher"}:
            return "protocol must be gremlin, sparql, or opencypher"
        if cfg.get("storage_type", "standard") not in {"standard", "iopt1"}:
            return "storage_type must be standard or iopt1"
        if "network_type" in cfg and cfg["network_type"] not in {"IPV4", "DUAL"}:
            return "network_type must be IPV4 or DUAL"
        if "cloudwatch_log_exports" in cfg:
            exports = cfg["cloudwatch_log_exports"]
            if not isinstance(exports, list) or any(item not in {"audit", "slowquery"} for item in exports):
                return "cloudwatch_log_exports may contain only audit and slowquery"
        for key in (
            "deletion_protection",
            "storage_encrypted",
            "iam_database_authentication",
            "auto_minor_version_upgrade",
            "publicly_accessible",
            "enable_performance_insights",
            "copy_tags_to_snapshot",
            "apply_immediately",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        if cfg.get("publicly_accessible") and not cfg.get(
            "iam_database_authentication",
            self._config.iam_auth_default,
        ):
            return "publicly_accessible Neptune instances require IAM database authentication"
        if "monitoring_interval" in cfg and cfg["monitoring_interval"] not in {0, 5, 10, 15, 30, 60}:
            return "monitoring_interval must be one of 0, 5, 10, 15, 30, or 60 seconds"
        if cfg.get("monitoring_interval", 0) and not cfg.get("monitoring_role_arn"):
            return "monitoring_role_arn is required when enhanced monitoring is enabled"
        if self._config.serverless_v2:
            minimum = cfg.get("min_capacity", 1.0)
            maximum = cfg.get("max_capacity", 16.0)
            for key, value, low in (("min_capacity", minimum, 1.0), ("max_capacity", maximum, 2.5)):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= float(value) <= 128.0:
                    return f"{key} must be a number from {low:g} through 128"
                if float(value) * 2 != int(float(value) * 2):
                    return "Neptune Serverless capacity must use 0.5 NCU increments"
            if float(minimum) > float(maximum):
                return "min_capacity cannot exceed max_capacity"
        elif "min_capacity" in cfg or "max_capacity" in cfg:
            return "min_capacity and max_capacity require the neptune_serverless variant"
        return ""


class NeptuneProvisionedDriver(NeptuneDriver):
    pass


class NeptuneServerlessDriver(NeptuneDriver):
    pass


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean.strip("-")[:63].rstrip("-")


def _snapshot_name(cluster_id: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    tail = f"-{suffix[:8]}-{stamp}"
    return f"{cluster_id[: 63 - len(tail)]}{tail}".rstrip("-")


def _protocol_url(protocol: str, host: str, port: int) -> str:
    if protocol == "gremlin":
        return f"wss://{host}:{port}/gremlin"
    path = "openCypher" if protocol == "opencypher" else "sparql"
    return f"https://{host}:{port}/{path}"


def _database_auth_arn(
    *,
    resource_arn: str,
    region: str,
    account_id: str,
    resource_id: str,
) -> str:
    arn_parts = resource_arn.split(":")
    partition = arn_parts[1] if len(arn_parts) > 1 and arn_parts[0] == "arn" else "aws"
    resolved_account = account_id or (arn_parts[4] if len(arn_parts) > 4 else "")
    if not resolved_account:
        raise ManagedServiceError("Neptune IAM binding requires the AWS account id")
    return f"arn:{partition}:neptune-db:{region}:{resolved_account}:{resource_id}/*"


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
        or code in {"InvalidDBClusterStateFault", "InvalidDBInstanceState", "ServiceUnavailable"}
    )


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(
        False,
        handle,
        f"{operation}: {exc}",
        [str(exc)],
        retryable=_retryable_cloud_error(exc),
    )
