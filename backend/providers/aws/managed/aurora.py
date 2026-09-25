"""Amazon Aurora managed-service driver.

One implementation backs the four portable variants:

* ``postgres/aurora_postgres``
* ``postgres/aurora_postgres_serverless_v2``
* ``mysql/aurora_mysql``
* ``mysql/aurora_mysql_serverless_v2``

Aurora clusters and their writer instances are reconciled independently so a
retry after a partial AWS failure finishes the topology instead of creating a
second cluster.  Serverless v2 uses the normal ``provisioned`` engine mode plus
``ServerlessV2ScalingConfiguration`` and a ``db.serverless`` writer, per the
AWS API contract.
"""

from __future__ import annotations

import contextlib
import secrets
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

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
from aws.managed._base import ManagedServiceError, adoption_refusal, handle_for, parse_handle, tags_for
from aws.session import aws_client

_ENGINE_PORT = {"aurora-postgresql": 5432, "aurora-mysql": 3306}
_SIZE_TO_CLASS = {
    "small": "db.t4g.medium",
    "medium": "db.r7g.large",
    "large": "db.r7g.xlarge",
    "xlarge": "db.r7g.2xlarge",
}
_SIZE_TO_ACU = {
    "small": (0.5, 2.0),
    "medium": (1.0, 8.0),
    "large": (2.0, 32.0),
    "xlarge": (4.0, 64.0),
}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "backing-up": "updating",
    "configuring-enhanced-monitoring": "updating",
    "modifying": "updating",
    "rebooting": "updating",
    "renaming": "updating",
    "resetting-master-credentials": "updating",
    "starting": "updating",
    "stopping": "updating",
    "stopped": "error",
    "deleting": "deprovisioning",
    "failed": "error",
    "inaccessible-encryption-credentials": "error",
}
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."


@dataclass(frozen=True)
class AuroraConfig(CredentialedConfig):
    region: str
    db_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    engine: str = "aurora-postgresql"
    serverless_v2: bool = False
    cluster_name_prefix: str = "astrolift"
    engine_version: str = ""
    backup_retention_days: int = 7
    deletion_protection_default: bool = True
    secrets_manager_prefix: str = "astrolift/managed"

    def __post_init__(self) -> None:
        if self.engine not in _ENGINE_PORT:
            raise ValueError(f"unsupported Aurora engine {self.engine!r}")


class AuroraDriver(ManagedServiceDriver):
    _portable_kind = "postgres"

    def __init__(
        self,
        *,
        config: AuroraConfig,
        rds_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if rds_client is not None:
            self._rds = rds_client
        else:
            self._rds = aws_client("rds", region=config.region, credential=config.credential)
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            self._sm = aws_client("secretsmanager", region=config.region, credential=config.credential)

    @driver_op(
        cloud="aws",
        driver="aurora",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_id = self._cluster_id(spec)
        validation_error = self._validate_scaling(spec.config or {}, size=spec.size)
        if validation_error:
            return ProvisionResult(False, "", validation_error, ["invalid_scaling_configuration"])
        existing = self._describe_cluster(cluster_id)
        if existing is None:
            password = _password()
            secret_arn = self._store_password(cluster_id, password, spec)
            cfg = spec.config or {}
            kwargs: dict[str, Any] = {
                "DBClusterIdentifier": cluster_id,
                "Engine": self._config.engine,
                "EngineMode": "provisioned",
                "DatabaseName": _database_name(spec, self._config.engine),
                "MasterUsername": "astrolift",
                "MasterUserPassword": password,
                "DBSubnetGroupName": self._config.db_subnet_group,
                "VpcSecurityGroupIds": list(self._config.security_group_ids),
                "PubliclyAccessible": False,
                "Port": _ENGINE_PORT[self._config.engine],
                "BackupRetentionPeriod": int(
                    cfg.get("backup_retention_days", self._config.backup_retention_days),
                ),
                "StorageEncrypted": True,
                "DeletionProtection": bool(
                    cfg.get("deletion_protection", self._config.deletion_protection_default),
                ),
                "CopyTagsToSnapshot": True,
                "EnableIAMDatabaseAuthentication": bool(cfg.get("iam_database_auth", False)),
                "Tags": tags_for(spec),
            }
            engine_version = str(cfg.get("engine_version") or self._config.engine_version)
            if engine_version:
                kwargs["EngineVersion"] = engine_version
            if cfg.get("kms_key_arn"):
                kwargs["KmsKeyId"] = str(cfg["kms_key_arn"])
            if cfg.get("cluster_parameter_group"):
                kwargs["DBClusterParameterGroupName"] = str(cfg["cluster_parameter_group"])
            if cfg.get("backtrack_window") is not None and self._config.engine == "aurora-mysql":
                kwargs["BacktrackWindow"] = int(cfg["backtrack_window"])
            if self._config.serverless_v2:
                default_min, default_max = _SIZE_TO_ACU.get(spec.size, _SIZE_TO_ACU["small"])
                kwargs["ServerlessV2ScalingConfiguration"] = {
                    "MinCapacity": float(cfg.get("min_acu", default_min)),
                    "MaxCapacity": float(cfg.get("max_acu", default_max)),
                }
                if cfg.get("seconds_until_auto_pause") is not None:
                    kwargs["ServerlessV2ScalingConfiguration"]["SecondsUntilAutoPause"] = int(
                        cfg["seconds_until_auto_pause"],
                    )
            try:
                self._rds.create_db_cluster(**kwargs)
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"create_db_cluster: {exc}",
                    errors=[str(exc)],
                )
            message = f"Aurora cluster {cluster_id} provisioning (password in {secret_arn})"
        else:
            refusal = adoption_refusal(existing.get("TagList"), spec, resource=f"Aurora cluster {cluster_id}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            message = f"Aurora cluster {cluster_id} already exists"

        writer_result = self._ensure_writer(cluster_id, spec)
        if writer_result is not None:
            return writer_result
        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=self._kind, resource_id=cluster_id),
            message=message,
        )

    @driver_op(cloud="aws", driver="aurora")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, cluster_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        validation_error = self._validate_scaling(cfg, size=spec.size)
        if validation_error:
            return UpdateResult(False, spec.handle, validation_error, ["invalid_scaling_configuration"])
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        if "backup_retention_days" in cfg:
            kwargs["BackupRetentionPeriod"] = int(cfg["backup_retention_days"])
        if "deletion_protection" in cfg:
            kwargs["DeletionProtection"] = bool(cfg["deletion_protection"])
        if cfg.get("engine_version"):
            kwargs["EngineVersion"] = str(cfg["engine_version"])
            kwargs["AllowMajorVersionUpgrade"] = bool(cfg.get("allow_major_version_upgrade", False))
        if "iam_database_auth" in cfg:
            kwargs["EnableIAMDatabaseAuthentication"] = bool(cfg["iam_database_auth"])
        if self._config.serverless_v2 and (
            spec.size or "min_acu" in cfg or "max_acu" in cfg or "seconds_until_auto_pause" in cfg
        ):
            current = self._describe_cluster(cluster_id) or {}
            scaling = current.get("ServerlessV2ScalingConfiguration") or {}
            if spec.size:
                default_min, default_max = _SIZE_TO_ACU.get(spec.size, (0.5, 2.0))
            else:
                default_min = float(scaling.get("MinCapacity", 0.5))
                default_max = float(scaling.get("MaxCapacity", 2.0))
            desired: dict[str, Any] = {
                "MinCapacity": float(cfg.get("min_acu", default_min)),
                "MaxCapacity": float(cfg.get("max_acu", default_max)),
            }
            if "seconds_until_auto_pause" in cfg:
                desired["SecondsUntilAutoPause"] = int(cfg["seconds_until_auto_pause"])
            kwargs["ServerlessV2ScalingConfiguration"] = desired
        if len(kwargs) > 2:
            try:
                self._rds.modify_db_cluster(**kwargs)
            except Exception as exc:
                return UpdateResult(False, spec.handle, f"modify_db_cluster: {exc}", [str(exc)])

        if not self._config.serverless_v2 and spec.size:
            instance_class = str(cfg.get("instance_class") or _SIZE_TO_CLASS.get(spec.size, ""))
            if instance_class:
                try:
                    self._rds.modify_db_instance(
                        DBInstanceIdentifier=self._writer_id(cluster_id),
                        DBInstanceClass=instance_class,
                        ApplyImmediately=bool(cfg.get("apply_immediately", False)),
                    )
                except Exception as exc:
                    return UpdateResult(False, spec.handle, f"modify writer: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Aurora cluster {cluster_id} update queued")

    @driver_op(
        cloud="aws",
        driver="aurora",
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
            if delete_data:
                self._delete_secrets(cluster_id)
            return DeprovisionResult(True, spec.handle, f"Aurora cluster {cluster_id} already gone")
        if cluster.get("DeletionProtection"):
            if not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Aurora deletion protection is enabled; pass force_destroy=True to bypass",
                    ["deletion_protection_enabled"],
                )
            try:
                self._rds.modify_db_cluster(
                    DBClusterIdentifier=cluster_id,
                    DeletionProtection=False,
                    ApplyImmediately=True,
                )
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, f"clear deletion protection: {exc}", [str(exc)])

        members = [
            str(member.get("DBInstanceIdentifier", ""))
            for member in cluster.get("DBClusterMembers", [])
            if member.get("DBInstanceIdentifier")
        ]
        remaining = False
        for instance_id in members:
            instance = self._describe_instance(instance_id)
            if instance is None:
                continue
            remaining = True
            if instance.get("DBInstanceStatus") != "deleting":
                try:
                    self._rds.delete_db_instance(
                        DBInstanceIdentifier=instance_id,
                        SkipFinalSnapshot=True,
                    )
                except Exception as exc:
                    return DeprovisionResult(False, spec.handle, f"delete Aurora instance: {exc}", [str(exc)])
        if remaining:
            return DeprovisionResult(
                False,
                spec.handle,
                "Aurora instance deletion is still in progress; retry cluster deletion",
                ["instance_deletion_in_progress"],
                retryable=True,
            )

        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "SkipFinalSnapshot": bool(delete_data),
        }
        if not delete_data:
            kwargs["FinalDBSnapshotIdentifier"] = _snapshot_id(cluster_id, "final")
        try:
            self._rds.delete_db_cluster(**kwargs)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete_db_cluster: {exc}", [str(exc)])
        if delete_data:
            self._delete_secrets(cluster_id)
        return DeprovisionResult(
            True,
            spec.handle,
            f"Aurora cluster {cluster_id} deletion queued (snapshot={'skipped' if delete_data else 'taken'})",
        )

    @driver_op(cloud="aws", driver="aurora")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"Aurora cluster {cluster_id} not found")
        cluster_state = str(cluster.get("Status", "unknown"))
        writer = self._describe_instance(self._writer_id(cluster_id))
        writer_state = str((writer or {}).get("DBInstanceStatus", "missing"))
        if cluster_state == "available" and writer_state == "available":
            state = "available"
        elif cluster_state in {"failed", "inaccessible-encryption-credentials"}:
            state = "error"
        else:
            state = _STATE.get(cluster_state, "provisioning")
        return ServiceStatus(
            handle.handle,
            state,
            f"Aurora cluster={cluster_state}, writer={writer_state}",
        )

    @driver_op(cloud="aws", driver="aurora")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing Aurora cluster {cluster_id}")
        host = str(cluster.get("Endpoint", ""))
        reader = str(cluster.get("ReaderEndpoint", ""))
        port = str(cluster.get("Port") or _ENGINE_PORT[self._config.engine])
        database = str(cluster.get("DatabaseName", ""))
        user = str(cluster.get("MasterUsername", "astrolift"))
        password_secret = self._password_secret(cluster_id)
        url_secret = self._ensure_url_secret(
            cluster_id=cluster_id,
            host=host,
            port=port,
            database=database,
            user=user,
        )
        if self._kind == "postgres":
            env = {
                "POSTGRES_HOST": ValueRef(literal=host),
                "POSTGRES_PORT": ValueRef(literal=port),
                "POSTGRES_DB": ValueRef(literal=database),
                "POSTGRES_USER": ValueRef(literal=user),
                "POSTGRES_PASSWORD": ValueRef(secret_ref=password_secret),
                "POSTGRES_SSL_MODE": ValueRef(literal="require"),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
                "AURORA_READER_HOST": ValueRef(literal=reader),
            }
        else:
            env = {
                "MYSQL_HOST": ValueRef(literal=host),
                "MYSQL_PORT": ValueRef(literal=port),
                "MYSQL_DB": ValueRef(literal=database),
                "MYSQL_USER": ValueRef(literal=user),
                "MYSQL_PASSWORD": ValueRef(secret_ref=password_secret),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
                "AURORA_READER_HOST": ValueRef(literal=reader),
            }
        return Binding(
            env_vars=env,
            iam_grants=[Grant(password_secret, ["secretsmanager:GetSecretValue"])],
            notes="Aurora writer and reader endpoints; TLS is required.",
        )

    @driver_op(cloud="aws", driver="aurora")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, cluster_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_id(cluster_id, "snap")
        try:
            self._rds.create_db_cluster_snapshot(
                DBClusterIdentifier=cluster_id,
                DBClusterSnapshotIdentifier=snapshot_id,
                Tags=[],
            )
        except Exception as exc:
            raise ManagedServiceError(f"create_db_cluster_snapshot: {exc}") from exc
        return SnapshotHandle(handle.handle, snapshot_id, datetime.now(UTC).isoformat())

    @driver_op(cloud="aws", driver="aurora")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cluster_id = self._cluster_id(target)
        if self._describe_cluster(cluster_id) is not None:
            writer_result = self._ensure_writer(cluster_id, target)
            return writer_result or ProvisionResult(
                True,
                handle_for(kind=self._kind, resource_id=cluster_id),
                f"Aurora cluster {cluster_id} restore already exists",
            )
        cfg = target.config or {}
        kwargs: dict[str, Any] = {
            "DBClusterIdentifier": cluster_id,
            "SnapshotIdentifier": snapshot.snapshot_id,
            "Engine": self._config.engine,
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "Tags": tags_for(target),
        }
        if self._config.serverless_v2:
            low, high = _SIZE_TO_ACU.get(target.size, _SIZE_TO_ACU["small"])
            kwargs["ServerlessV2ScalingConfiguration"] = {
                "MinCapacity": float(cfg.get("min_acu", low)),
                "MaxCapacity": float(cfg.get("max_acu", high)),
            }
        try:
            self._rds.restore_db_cluster_from_snapshot(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, "", f"restore_db_cluster_from_snapshot: {exc}", [str(exc)])
        try:
            _, source_cluster_id = parse_handle(snapshot.handle)
            password = str(
                self._sm.get_secret_value(SecretId=self._password_secret(source_cluster_id)).get(
                    "SecretString",
                    "",
                ),
            )
            self._store_password(cluster_id, password, target)
        except Exception as exc:
            return ProvisionResult(
                False,
                handle_for(kind=self._kind, resource_id=cluster_id),
                f"Aurora data restored but credential copy failed: {exc}",
                [str(exc)],
            )
        writer_result = self._ensure_writer(cluster_id, target)
        if writer_result is not None:
            return writer_result
        return ProvisionResult(
            True,
            handle_for(kind=self._kind, resource_id=cluster_id),
            f"Aurora restore from {snapshot.snapshot_id} and writer creation queued",
        )

    @driver_op(cloud="aws", driver="aurora", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "instance_class": {"type": "string"},
                "min_acu": {"type": "number", "minimum": 0, "maximum": 256, "multipleOf": 0.5},
                "max_acu": {"type": "number", "minimum": 0.5, "maximum": 256, "multipleOf": 0.5},
                "seconds_until_auto_pause": {"type": "integer", "minimum": 300, "maximum": 86400},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "deletion_protection": {"type": "boolean"},
                "iam_database_auth": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "cluster_parameter_group": {"type": "string"},
                "backtrack_window": {"type": "integer", "minimum": 0, "maximum": 259200},
                "apply_immediately": {"type": "boolean"},
                "allow_major_version_upgrade": {"type": "boolean"},
            },
            "allOf": [
                {
                    "if": {"required": ["min_acu", "max_acu"]},
                    "then": {
                        "properties": {},
                        "description": (
                            "max_acu must be greater than or equal to min_acu; server validates this cross-field rule."
                        ),
                    },
                },
            ],
        }

    @driver_op(cloud="aws", driver="aurora", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        if self._kind == "mysql":
            keys = {
                "MYSQL_HOST": "Aurora writer endpoint",
                "MYSQL_PORT": "MySQL port",
                "MYSQL_DB": "Initial database",
                "MYSQL_USER": "Master username",
                "MYSQL_PASSWORD": "Secrets Manager password reference",
                "DATABASE_URL": "Secrets Manager MySQL DSN reference",
                "AURORA_READER_HOST": "Aurora reader endpoint",
            }
        else:
            keys = {
                "POSTGRES_HOST": "Aurora writer endpoint",
                "POSTGRES_PORT": "PostgreSQL port",
                "POSTGRES_DB": "Initial database",
                "POSTGRES_USER": "Master username",
                "POSTGRES_PASSWORD": "Secrets Manager password reference",
                "POSTGRES_SSL_MODE": "TLS mode (require)",
                "DATABASE_URL": "Secrets Manager PostgreSQL DSN reference",
                "AURORA_READER_HOST": "Aurora reader endpoint",
            }
        return BindingSchema(env_vars=keys)

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "engine_version",
            "instance_class",
            "min_acu",
            "max_acu",
            "seconds_until_auto_pause",
            "backup_retention_days",
            "deletion_protection",
            "iam_database_auth",
            "cluster_parameter_group",
            "apply_immediately",
            "allow_major_version_upgrade",
        ]

    @property
    def _kind(self) -> str:
        return self._portable_kind

    def _validate_scaling(self, cfg: dict[str, Any], *, size: str | None) -> str:
        if not self._config.serverless_v2:
            return ""
        default_min, default_max = _SIZE_TO_ACU.get(size or "small", _SIZE_TO_ACU["small"])
        minimum = float(cfg.get("min_acu", default_min))
        maximum = float(cfg.get("max_acu", default_max))
        if minimum < 0 or maximum > 256 or minimum > maximum:
            return "Aurora Serverless v2 requires 0 <= min_acu <= max_acu <= 256"
        if minimum * 2 != int(minimum * 2) or maximum * 2 != int(maximum * 2):
            return "Aurora Serverless v2 capacities must use 0.5 ACU increments"
        if cfg.get("seconds_until_auto_pause") is not None and minimum != 0:
            return "Aurora Serverless v2 auto-pause requires min_acu=0"
        return ""

    def _cluster_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.cluster_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "aurora",
            )
            if part
        )
        clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        if not clean or not clean[0].isalpha():
            clean = f"a-{clean}"
        return clean.strip("-")[:63]

    @staticmethod
    def _writer_id(cluster_id: str) -> str:
        return f"{cluster_id[:56].rstrip('-')}-writer"

    def _ensure_writer(self, cluster_id: str, spec: ProvisionSpec) -> ProvisionResult | None:
        writer_id = self._writer_id(cluster_id)
        if self._describe_instance(writer_id) is not None:
            return None
        cfg = spec.config or {}
        instance_class = (
            "db.serverless"
            if self._config.serverless_v2
            else str(cfg.get("instance_class") or _SIZE_TO_CLASS.get(spec.size, "db.t4g.medium"))
        )
        kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": writer_id,
            "DBClusterIdentifier": cluster_id,
            "DBInstanceClass": instance_class,
            "Engine": self._config.engine,
            "PubliclyAccessible": False,
            "AutoMinorVersionUpgrade": bool(cfg.get("auto_minor_version_upgrade", True)),
            "Tags": tags_for(spec),
        }
        if cfg.get("instance_parameter_group"):
            kwargs["DBParameterGroupName"] = str(cfg["instance_parameter_group"])
        try:
            self._rds.create_db_instance(**kwargs)
        except Exception as exc:
            return ProvisionResult(
                False,
                handle_for(kind=self._kind, resource_id=cluster_id),
                f"create Aurora writer: {exc}",
                [str(exc)],
            )
        return None

    def _describe_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rds.describe_db_clusters(DBClusterIdentifier=cluster_id).get("DBClusters", [])
        except Exception as exc:
            if _not_found(exc, "DBClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _describe_instance(self, instance_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rds.describe_db_instances(DBInstanceIdentifier=instance_id).get("DBInstances", [])
        except Exception as exc:
            if _not_found(exc, "DBInstanceNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _password_secret(self, cluster_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{cluster_id}/master"

    def _url_secret(self, cluster_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{cluster_id}/url"

    def _store_password(self, cluster_id: str, password: str, spec: ProvisionSpec) -> str:
        name = self._password_secret(cluster_id)
        try:
            response = self._sm.create_secret(
                Name=name,
                Description=f"Aurora master password for {cluster_id}",
                SecretString=password,
                Tags=tags_for(spec),
            )
            return str(response.get("ARN", name))
        except Exception as exc:
            if _not_found(exc, "ResourceExists") or "ResourceExists" in str(exc):
                self._sm.put_secret_value(SecretId=name, SecretString=password)
                return name
            raise ManagedServiceError(f"create Aurora password secret: {exc}") from exc

    def _ensure_url_secret(self, *, cluster_id: str, host: str, port: str, database: str, user: str) -> str:
        password_name = self._password_secret(cluster_id)
        try:
            password = str(self._sm.get_secret_value(SecretId=password_name).get("SecretString", ""))
        except Exception as exc:
            raise ManagedServiceError(f"read Aurora password secret: {exc}") from exc
        scheme = "postgresql" if self._kind == "postgres" else "mysql"
        dsn = f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}"
        url_name = self._url_secret(cluster_id)
        try:
            self._sm.create_secret(Name=url_name, SecretString=dsn)
        except Exception as exc:
            if "ResourceExists" not in str(exc) and type(exc).__name__ != "ResourceExistsException":
                raise ManagedServiceError(f"write Aurora URL secret: {exc}") from exc
            self._sm.put_secret_value(SecretId=url_name, SecretString=dsn)
        return url_name

    def _delete_secrets(self, cluster_id: str) -> None:
        for name in (self._password_secret(cluster_id), self._url_secret(cluster_id)):
            with contextlib.suppress(Exception):
                self._sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)


def _password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _database_name(spec: ProvisionSpec, engine: str) -> str:
    raw = f"{spec.app_slug}_{spec.environment_name}".replace("-", "_")
    name = "".join(char for char in raw if char.isalnum() or char == "_")
    if not name or not name[0].isalpha():
        name = f"app_{name}"
    return name[:63] if engine == "aurora-postgresql" else name[:64]


def _snapshot_id(cluster_id: str, label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{cluster_id}-{label}-{stamp}"[:255]


def _not_found(exc: Exception, marker: str) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return marker in code or marker in type(exc).__name__ or marker in str(exc)


class AuroraPostgresDriver(AuroraDriver):
    _portable_kind = "postgres"


class AuroraMySQLDriver(AuroraDriver):
    _portable_kind = "mysql"
