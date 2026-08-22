"""Amazon DocumentDB provisioned and Serverless v2 managed-service drivers."""

from __future__ import annotations

import secrets
import string
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

KIND = "document_db"
_SIZE_TO_INSTANCE_CLASS = {
    "small": "db.t4g.medium",
    "medium": "db.r6g.large",
    "large": "db.r8g.large",
    "xlarge": "db.r8g.xlarge",
}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "backing-up": "updating",
    "modifying": "updating",
    "deleting": "deprovisioning",
    "failing-over": "updating",
    "inaccessible-encryption-credentials": "error",
}


@dataclass(frozen=True)
class DocumentDBConfig(CredentialedConfig):
    region: str
    db_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    cluster_name_prefix: str = "astrolift"
    engine_version: str = "5.0.0"
    serverless_v2: bool = False
    backup_retention_days: int = 7
    deletion_protection_default: bool = True
    secrets_manager_prefix: str = "astrolift/documentdb"
    master_username: str = "astrolift"


class DocumentDBDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: DocumentDBConfig,
        docdb_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if docdb_client is None:
            docdb_client = aws_client("docdb", region=config.region, credential=config.credential)
        if secrets_client is None:
            secrets_client = aws_client("secretsmanager", region=config.region, credential=config.credential)
        self._docdb = docdb_client
        self._secrets = secrets_client

    @driver_op(
        cloud="aws",
        driver="documentdb",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_documentdb_config"])
        cluster_id = self._cluster_id(spec)
        handle = handle_for(kind=KIND, resource_id=cluster_id)
        cluster = self._describe_cluster(cluster_id)
        try:
            if cluster is None:
                if cfg.get("snapshot_identifier"):
                    self._restore_cluster(cluster_id, spec, cfg)
                else:
                    self._create_cluster(cluster_id, spec, cfg)
            self._ensure_instances(cluster_id, spec, cfg)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision DocumentDB: {exc}", [str(exc)])
        if cluster is not None:
            updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
            return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        return ProvisionResult(True, handle, f"DocumentDB cluster {cluster_id} provisioning")

    @driver_op(cloud="aws", driver="documentdb")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, cluster_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return UpdateResult(False, spec.handle, f"DocumentDB cluster {cluster_id} not found", ["not_found"])
        validation_cfg = dict(cfg)
        if self._config.serverless_v2:
            current_scaling = cluster.get("ServerlessV2ScalingConfiguration") or {}
            validation_cfg.setdefault(
                "min_capacity",
                float(current_scaling.get("MinCapacity", 0.5)),
            )
            validation_cfg.setdefault(
                "max_capacity",
                float(current_scaling.get("MaxCapacity", 16)),
            )
        error = self._validate_config(validation_cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_documentdb_config"])
        cluster_kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        for key, aws_key, cast in (
            ("engine_version", "EngineVersion", str),
            ("backup_retention_days", "BackupRetentionPeriod", int),
            ("deletion_protection", "DeletionProtection", bool),
            ("cluster_parameter_group", "DBClusterParameterGroupName", str),
            ("backup_window", "PreferredBackupWindow", str),
            ("maintenance_window", "PreferredMaintenanceWindow", str),
            ("storage_type", "StorageType", str),
            ("network_type", "NetworkType", str),
        ):
            if key in cfg:
                cluster_kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            cluster_kwargs["VpcSecurityGroupIds"] = list(cfg["security_group_ids"])
        if self._config.serverless_v2 and any(key in cfg for key in ("min_capacity", "max_capacity")):
            cluster_kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(validation_cfg)
        changed = False
        try:
            if len(cluster_kwargs) > 2:
                self._docdb.modify_db_cluster(**cluster_kwargs)
                changed = True
            instance_fields_requested = any(
                key in cfg
                for key in (
                    "auto_minor_version_upgrade",
                    "enable_performance_insights",
                    "performance_insights_kms_key_id",
                )
            )
            class_requested = bool(spec.size or cfg.get("instance_class"))
            if class_requested or instance_fields_requested:
                for instance in self._instances(cluster_id):
                    desired_class = (
                        self._instance_class(spec.size or "", cfg)
                        if class_requested
                        else str(instance.get("DBInstanceClass", ""))
                    )
                    if str(instance.get("DBInstanceClass", "")) == desired_class and not instance_fields_requested:
                        continue
                    instance_kwargs: dict[str, Any] = {
                        "DBInstanceIdentifier": str(instance["DBInstanceIdentifier"]),
                        "DBInstanceClass": desired_class,
                        "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
                    }
                    for key, aws_key, cast in (
                        ("auto_minor_version_upgrade", "AutoMinorVersionUpgrade", bool),
                        ("enable_performance_insights", "EnablePerformanceInsights", bool),
                        ("performance_insights_kms_key_id", "PerformanceInsightsKMSKeyId", str),
                    ):
                        if key in cfg:
                            instance_kwargs[aws_key] = cast(cfg[key])
                    self._docdb.modify_db_instance(**instance_kwargs)
                    changed = True
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify DocumentDB: {exc}", [str(exc)])
        if not changed:
            return UpdateResult(True, spec.handle, "no DocumentDB changes requested")
        return UpdateResult(True, spec.handle, f"DocumentDB cluster {cluster_id} update queued")

    @driver_op(
        cloud="aws",
        driver="documentdb",
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
        if cluster is not None and cluster.get("DeletionProtection"):
            if not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"DocumentDB cluster {cluster_id} has deletion protection enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            try:
                self._docdb.modify_db_cluster(
                    DBClusterIdentifier=cluster_id,
                    DeletionProtection=False,
                    ApplyImmediately=True,
                )
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"disable deletion protection: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(
                False,
                spec.handle,
                f"DocumentDB deletion protection disabled for {cluster_id}; waiting",
                ["cluster_update_in_progress"],
                retryable=True,
            )
        instances = self._instances(cluster_id)
        if instances:
            pending = False
            for instance in instances:
                state = str(instance.get("DBInstanceStatus", ""))
                if state == "deleting":
                    pending = True
                    continue
                try:
                    self._docdb.delete_db_instance(
                        DBInstanceIdentifier=str(instance["DBInstanceIdentifier"]),
                    )
                    pending = True
                except Exception as exc:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"delete_db_instance: {exc}",
                        [str(exc)],
                        retryable=_retryable_cloud_error(exc),
                    )
            if pending:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"DocumentDB instances for {cluster_id} are deleting",
                    ["instance_deletion_in_progress"],
                    retryable=True,
                )
        cluster = self._describe_cluster(cluster_id)
        if cluster is not None:
            if cluster.get("Status") == "deleting":
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"DocumentDB cluster {cluster_id} deletion is still in progress",
                    ["cluster_deletion_in_progress"],
                    retryable=True,
                )
            kwargs: dict[str, Any] = {
                "DBClusterIdentifier": cluster_id,
                "SkipFinalSnapshot": bool(delete_data),
            }
            if not delete_data:
                kwargs["FinalDBSnapshotIdentifier"] = _snapshot_name(cluster_id, "final")
            try:
                self._docdb.delete_db_cluster(**kwargs)
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete_db_cluster: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(
                False,
                spec.handle,
                f"DocumentDB cluster {cluster_id} deletion queued",
                ["cluster_deletion_in_progress"],
                retryable=True,
            )
        try:
            self._delete_secret(self._password_secret_name(cluster_id))
            self._delete_secret(self._uri_secret_name(cluster_id))
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete DocumentDB secrets: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        return DeprovisionResult(True, spec.handle, f"DocumentDB cluster {cluster_id} already gone")

    @driver_op(cloud="aws", driver="documentdb")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"DocumentDB cluster {cluster_id} not found")
        cluster_state = str(cluster.get("Status", "unknown"))
        instances = self._instances(cluster_id)
        if cluster_state == "available" and instances:
            if all(instance.get("DBInstanceStatus") == "available" for instance in instances):
                return ServiceStatus(handle.handle, "available", "DocumentDB cluster and instances are available")
            return ServiceStatus(handle.handle, "provisioning", "DocumentDB instances are not yet available")
        return ServiceStatus(
            handle.handle,
            _STATE.get(cluster_state, "updating"),
            f"DocumentDB reports {cluster_state}",
        )

    @driver_op(cloud="aws", driver="documentdb")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing DocumentDB cluster {cluster_id}")
        cfg = config or {}
        host = str(cluster.get("Endpoint", ""))
        port = str(cluster.get("Port", 27017))
        database = str(cfg.get("database") or "app")
        user = str(cluster.get("MasterUsername") or cfg.get("master_username") or self._config.master_username)
        self._ensure_uri_secret(cluster_id, user, host, port, database)
        password_ref = self._password_secret_name(cluster_id)
        uri_ref = self._uri_secret_name(cluster_id)
        return Binding(
            env_vars={
                "DOCDB_URI": ValueRef(secret_ref=uri_ref),
                "DOCDB_DB": ValueRef(literal=database),
                "DOCDB_USER": ValueRef(literal=user),
                "DOCDB_PASSWORD": ValueRef(secret_ref=password_ref),
                "DOCDB_TLS": ValueRef(literal="1"),
                "DOCDB_RESOURCE_ARN": ValueRef(literal=str(cluster.get("DBClusterArn", ""))),
            },
            iam_grants=[
                Grant(password_ref, ["secretsmanager:GetSecretValue"]),
                Grant(uri_ref, ["secretsmanager:GetSecretValue"]),
            ],
            notes="TLS DocumentDB replica-set URI; client trust store must include the AWS global CA bundle",
        )

    @driver_op(cloud="aws", driver="documentdb")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, cluster_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_name(cluster_id, "snapshot")
        try:
            response = self._docdb.create_db_cluster_snapshot(
                DBClusterIdentifier=cluster_id,
                DBClusterSnapshotIdentifier=snapshot_id,
            )
        except Exception as exc:
            raise ManagedServiceError(f"create_db_cluster_snapshot: {exc}") from exc
        snapshot = response.get("DBClusterSnapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("DBClusterSnapshotArn") or snapshot_id),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="documentdb")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        _, source_cluster_id = parse_handle(snapshot.handle)
        target_cluster_id = self._cluster_id(target)
        try:
            self._copy_password_secret(source_cluster_id, target_cluster_id, target)
        except Exception as exc:
            return ProvisionResult(
                False,
                "",
                f"copy DocumentDB snapshot credentials: {exc}",
                [str(exc)],
            )
        cfg = dict(target.config or {})
        cfg["snapshot_identifier"] = snapshot.snapshot_id
        return self.provision(replace(target, config=cfg))

    @driver_op(cloud="aws", driver="documentdb", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "master_username": {"type": "string"},
                "instance_class": {"type": "string"},
                "num_instances": {"type": "integer", "minimum": 1, "maximum": 16},
                "engine_version": {"type": "string"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "deletion_protection": {"type": "boolean"},
                "storage_encrypted": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "storage_type": {"type": "string", "enum": ["standard", "iopt1"]},
                "cluster_parameter_group": {"type": "string"},
                "backup_window": {"type": "string"},
                "maintenance_window": {"type": "string"},
                "cloudwatch_log_exports": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["audit", "profiler"]},
                    "uniqueItems": True,
                },
                "network_type": {"type": "string", "enum": ["IPV4", "DUAL"]},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "min_capacity": {"type": "number", "minimum": 0.5, "maximum": 256},
                "max_capacity": {"type": "number", "minimum": 1, "maximum": 256},
                "auto_minor_version_upgrade": {"type": "boolean"},
                "enable_performance_insights": {"type": "boolean"},
                "performance_insights_kms_key_id": {"type": "string"},
                "apply_immediately": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="documentdb", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DOCDB_URI": "TLS Mongo-compatible DocumentDB URI",
                "DOCDB_DB": "Default database",
                "DOCDB_USER": "Master username",
                "DOCDB_PASSWORD": "Master password secret",
                "DOCDB_TLS": "Always 1 for the managed connection",
                "DOCDB_RESOURCE_ARN": "DocumentDB cluster ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "instance_class",
            "engine_version",
            "backup_retention_days",
            "deletion_protection",
            "cluster_parameter_group",
            "backup_window",
            "maintenance_window",
            "storage_type",
            "security_group_ids",
            "min_capacity",
            "max_capacity",
            "enable_performance_insights",
            "apply_immediately",
        ]

    def _create_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        password = self._ensure_password_secret(cluster_id, spec)
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "Engine": "docdb",
            "EngineVersion": str(cfg.get("engine_version") or self._config.engine_version),
            "MasterUsername": str(cfg.get("master_username") or self._config.master_username),
            "MasterUserPassword": password,
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "Port": 27017,
            "BackupRetentionPeriod": int(
                cfg.get("backup_retention_days", self._config.backup_retention_days),
            ),
            "StorageEncrypted": bool(cfg.get("storage_encrypted", True)),
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "Tags": tags_for(spec),
        }
        kms_key = str(cfg.get("kms_key_arn") or "")
        if kms_key:
            kwargs["KmsKeyId"] = kms_key
        for key, aws_key in (
            ("cluster_parameter_group", "DBClusterParameterGroupName"),
            ("backup_window", "PreferredBackupWindow"),
            ("maintenance_window", "PreferredMaintenanceWindow"),
            ("storage_type", "StorageType"),
            ("network_type", "NetworkType"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("cloudwatch_log_exports"):
            kwargs["EnableCloudwatchLogsExports"] = list(cfg["cloudwatch_log_exports"])
        if self._config.serverless_v2:
            kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(cfg)
        self._docdb.create_db_cluster(**kwargs)

    def _restore_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        self._password_value(cluster_id)
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "SnapshotIdentifier": str(cfg["snapshot_identifier"]),
            "Engine": "docdb",
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "Tags": tags_for(spec),
        }
        if self._config.serverless_v2:
            kwargs["ServerlessV2ScalingConfiguration"] = self._scaling(cfg)
        self._docdb.restore_db_cluster_from_snapshot(**kwargs)

    def _ensure_instances(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        existing = {str(row["DBInstanceIdentifier"]): row for row in self._instances(cluster_id)}
        desired_count = int(cfg.get("num_instances", 2 if spec.isolation == "dedicated" else 1))
        instance_class = self._instance_class(spec.size, cfg)
        for index in range(desired_count):
            instance_id = _name(cluster_id, f"i{index + 1}")
            if instance_id in existing:
                continue
            kwargs: dict[str, Any] = {
                "DBInstanceIdentifier": instance_id,
                "DBInstanceClass": instance_class,
                "Engine": "docdb",
                "DBClusterIdentifier": cluster_id,
                "PromotionTier": index,
                "AutoMinorVersionUpgrade": bool(cfg.get("auto_minor_version_upgrade", True)),
                "Tags": tags_for(spec),
            }
            if cfg.get("enable_performance_insights"):
                kwargs["EnablePerformanceInsights"] = True
            if cfg.get("performance_insights_kms_key_id"):
                kwargs["PerformanceInsightsKMSKeyId"] = str(cfg["performance_insights_kms_key_id"])
            self._docdb.create_db_instance(**kwargs)

    def _describe_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        try:
            rows = self._docdb.describe_db_clusters(DBClusterIdentifier=cluster_id).get("DBClusters", [])
        except Exception as exc:
            if _not_found(exc, "DBClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _instances(self, cluster_id: str) -> list[dict[str, Any]]:
        try:
            rows = self._docdb.describe_db_instances(
                Filters=[{"Name": "db-cluster-id", "Values": [cluster_id]}],
            ).get("DBInstances", [])
        except Exception as exc:
            if _not_found(exc, "DBInstanceNotFound"):
                return []
            raise
        return [row for row in rows if row.get("DBClusterIdentifier") == cluster_id]

    def _ensure_password_secret(self, cluster_id: str, spec: ProvisionSpec) -> str:
        name = self._password_secret_name(cluster_id)
        try:
            return self._password_value(cluster_id)
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise
        password = _password()
        self._secrets.create_secret(Name=name, SecretString=password, Tags=tags_for(spec))
        return password

    def _copy_password_secret(
        self,
        source_cluster_id: str,
        target_cluster_id: str,
        target: ProvisionSpec,
    ) -> None:
        password = self._password_value(source_cluster_id)
        target_name = self._password_secret_name(target_cluster_id)
        try:
            self._secrets.create_secret(
                Name=target_name,
                SecretString=password,
                Tags=tags_for(target),
            )
        except Exception as exc:
            if not _not_found(exc, "ResourceExists"):
                raise

    def _password_value(self, cluster_id: str) -> str:
        return str(
            self._secrets.get_secret_value(
                SecretId=self._password_secret_name(cluster_id),
            )["SecretString"],
        )

    def _ensure_uri_secret(self, cluster_id: str, user: str, host: str, port: str, database: str) -> None:
        password = str(
            self._secrets.get_secret_value(SecretId=self._password_secret_name(cluster_id))["SecretString"],
        )
        uri = (
            f"mongodb://{user}:{password}@{host}:{port}/{database}"
            "?tls=true&replicaSet=rs0&readPreference=secondaryPreferred&retryWrites=false"
        )
        name = self._uri_secret_name(cluster_id)
        try:
            self._secrets.create_secret(Name=name, SecretString=uri)
        except Exception as exc:
            if not _not_found(exc, "ResourceExists"):
                raise
            self._secrets.put_secret_value(SecretId=name, SecretString=uri)

    def _delete_secret(self, name: str) -> None:
        try:
            self._secrets.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise

    def _cluster_id(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.cluster_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "documents",
        )

    def _password_secret_name(self, cluster_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{cluster_id}/password"

    def _uri_secret_name(self, cluster_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{cluster_id}/uri"

    def _instance_class(self, size: str, cfg: dict[str, Any]) -> str:
        if self._config.serverless_v2:
            return "db.serverless"
        return str(cfg.get("instance_class") or _SIZE_TO_INSTANCE_CLASS.get(size, "db.t4g.medium"))

    @staticmethod
    def _scaling(cfg: dict[str, Any]) -> dict[str, float]:
        return {
            "MinCapacity": float(cfg.get("min_capacity", 0.5)),
            "MaxCapacity": float(cfg.get("max_capacity", 16)),
        }

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        error = self._validate_update_config(cfg)
        if error:
            return error
        if self._config.serverless_v2:
            if cfg.get("instance_class"):
                return "instance_class cannot be set for DocumentDB Serverless v2"
            minimum = float(cfg.get("min_capacity", 0.5))
            maximum = float(cfg.get("max_capacity", 16))
            if minimum > maximum:
                return "min_capacity cannot exceed max_capacity"
            if minimum * 2 != int(minimum * 2) or maximum * 2 != int(maximum * 2):
                return "DocumentDB serverless capacity must use 0.5 DCU increments"
        elif "min_capacity" in cfg or "max_capacity" in cfg:
            return "min_capacity and max_capacity require the documentdb_serverless_v2 variant"
        return ""

    @staticmethod
    def _validate_update_config(cfg: dict[str, Any]) -> str:
        if "num_instances" in cfg:
            value = cfg["num_instances"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
                return "num_instances must be an integer from 1 through 16"
        if "backup_retention_days" in cfg:
            value = cfg["backup_retention_days"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 35:
                return "backup_retention_days must be an integer from 1 through 35"
        for key, minimum, maximum in (
            ("min_capacity", 0.5, 256.0),
            ("max_capacity", 1.0, 256.0),
        ):
            if key in cfg:
                value = cfg[key]
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not minimum <= float(value) <= maximum
                ):
                    return f"{key} must be a number from {minimum:g} through {maximum:g}"
        if "storage_type" in cfg and cfg["storage_type"] not in {"standard", "iopt1"}:
            return "storage_type must be standard or iopt1"
        if "network_type" in cfg and cfg["network_type"] not in {"IPV4", "DUAL"}:
            return "network_type must be IPV4 or DUAL"
        if "cloudwatch_log_exports" in cfg:
            exports = cfg["cloudwatch_log_exports"]
            if not isinstance(exports, list) or any(item not in {"audit", "profiler"} for item in exports):
                return "cloudwatch_log_exports may contain only audit and profiler"
        for key in (
            "deletion_protection",
            "storage_encrypted",
            "auto_minor_version_upgrade",
            "enable_performance_insights",
            "apply_immediately",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""


class DocumentDBProvisionedDriver(DocumentDBDriver):
    pass


class DocumentDBServerlessV2Driver(DocumentDBDriver):
    pass


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean.strip("-")[:63].rstrip("-")


def _password(length: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _snapshot_name(cluster_id: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    tail = f"-{suffix[:8]}-{stamp}"
    return f"{cluster_id[: 63 - len(tail)]}{tail}".rstrip("-")


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
            "InvalidDBClusterStateFault",
            "InvalidDBInstanceState",
            "ServiceUnavailable",
        }
    )
