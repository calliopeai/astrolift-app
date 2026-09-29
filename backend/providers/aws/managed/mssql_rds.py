"""Amazon RDS for Microsoft SQL Server managed-service driver.

The provider variant selects one of the AWS license-included engines:
``sqlserver-ex``, ``sqlserver-web``, ``sqlserver-se``, or ``sqlserver-ee``.
The portable kind remains ``mssql`` for every edition.
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

KIND = "mssql"
_ENGINES = {"sqlserver-ex", "sqlserver-web", "sqlserver-se", "sqlserver-ee"}
_CLASS_PREFERENCES = {
    "sqlserver-ex": {
        "small": ("db.t3.micro", "db.t3.small", "db.t3.medium"),
        "medium": ("db.t3.medium", "db.m5.large", "db.m6i.large"),
        "large": ("db.m5.xlarge", "db.m6i.xlarge", "db.r6i.xlarge"),
        "xlarge": ("db.m5.2xlarge", "db.m6i.2xlarge", "db.r6i.2xlarge"),
    },
    "sqlserver-web": {
        "small": ("db.t3.small", "db.t3.medium", "db.m5.large"),
        "medium": ("db.m5.large", "db.m6i.large", "db.r6i.large"),
        "large": ("db.m5.xlarge", "db.m6i.xlarge", "db.r6i.xlarge"),
        "xlarge": ("db.m5.2xlarge", "db.m6i.2xlarge", "db.r6i.2xlarge"),
    },
    "sqlserver-se": {
        "small": ("db.t3.small", "db.t3.medium", "db.m5.large"),
        "medium": ("db.m5.large", "db.m6i.large", "db.r6i.large"),
        "large": ("db.m5.xlarge", "db.m6i.xlarge", "db.r6i.xlarge"),
        "xlarge": ("db.m5.2xlarge", "db.m6i.2xlarge", "db.r6i.2xlarge"),
    },
    "sqlserver-ee": {
        "small": ("db.t3.xlarge", "db.m5.xlarge", "db.m6i.xlarge"),
        "medium": ("db.m5.xlarge", "db.m6i.xlarge", "db.r6i.xlarge"),
        "large": ("db.m5.2xlarge", "db.m6i.2xlarge", "db.r6i.2xlarge"),
        "xlarge": ("db.m5.4xlarge", "db.m6i.4xlarge", "db.r6i.4xlarge"),
    },
}
_SIZE_TO_STORAGE = {"small": 20, "medium": 100, "large": 250, "xlarge": 500}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "backing-up": "updating",
    "configuring-enhanced-monitoring": "updating",
    "maintenance": "updating",
    "modifying": "updating",
    "rebooting": "updating",
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
class RDSSqlServerConfig(CredentialedConfig):
    region: str
    db_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    engine: str = "sqlserver-ex"
    instance_name_prefix: str = "astrolift"
    engine_version: str = ""
    backup_retention_days: int = 7
    multi_az_default: bool = False
    deletion_protection_default: bool = True
    secrets_manager_prefix: str = "astrolift/managed"
    # Option groups an instance may join. One can carry the IAM role SQL
    # Server's native backup and restore reads S3 with, or an audit bucket it
    # writes to, so an unlisted group is refused; empty refuses every one
    # (#2087).
    allowed_option_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.engine not in _ENGINES:
            raise ValueError(f"unsupported RDS SQL Server engine {self.engine!r}")


class RDSSqlServerDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: RDSSqlServerConfig,
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
        driver="mssql_rds",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        refusal = self._option_group_refusal(spec.config or {})
        if refusal:
            return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
        instance_id = self._instance_id(spec)
        existing = self._describe(instance_id)
        if existing is not None:
            refusal = adoption_refusal(existing.get("TagList"), spec, resource=f"RDS SQL Server {instance_id}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                True,
                handle_for(kind=KIND, resource_id=instance_id),
                f"RDS SQL Server {instance_id} already exists (status={existing.get('DBInstanceStatus')})",
            )

        cfg = spec.config or {}
        password = _password()
        multi_az = bool(cfg.get("multi_az", self._config.multi_az_default))
        if self._config.engine == "sqlserver-ex" and multi_az:
            return ProvisionResult(
                False,
                "",
                "RDS SQL Server Express does not support Multi-AZ deployments",
                ["multi_az_unsupported"],
            )
        try:
            instance_class = self._instance_class(
                size=spec.size,
                explicit=str(cfg.get("instance_class", "")),
                engine_version=str(cfg.get("engine_version") or self._config.engine_version),
            )
        except ManagedServiceError as exc:
            return ProvisionResult(False, "", str(exc), [str(exc)])
        try:
            secret_arn = self._store_password(instance_id, password, spec)
        except Exception as exc:
            return ProvisionResult(False, "", str(exc), [str(exc)])
        kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "DBInstanceClass": instance_class,
            "Engine": self._config.engine,
            "LicenseModel": "license-included",
            "AllocatedStorage": int(
                cfg.get("allocated_storage") or _SIZE_TO_STORAGE.get(spec.size, 20),
            ),
            "StorageType": str(cfg.get("storage_type", "gp3")),
            "MasterUsername": str(cfg.get("master_username", "astrolift")),
            "MasterUserPassword": password,
            "Port": 1433,
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "BackupRetentionPeriod": int(
                cfg.get("backup_retention_days", self._config.backup_retention_days),
            ),
            "MultiAZ": multi_az,
            "PubliclyAccessible": False,
            "StorageEncrypted": True,
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "AutoMinorVersionUpgrade": bool(cfg.get("auto_minor_version_upgrade", True)),
            "CopyTagsToSnapshot": True,
            "Tags": tags_for(spec),
        }
        engine_version = str(cfg.get("engine_version") or self._config.engine_version)
        if engine_version:
            kwargs["EngineVersion"] = engine_version
        if cfg.get("kms_key_arn"):
            kwargs["KmsKeyId"] = str(cfg["kms_key_arn"])
        if cfg.get("option_group"):
            kwargs["OptionGroupName"] = str(cfg["option_group"])
        if cfg.get("parameter_group"):
            kwargs["DBParameterGroupName"] = str(cfg["parameter_group"])
        if cfg.get("timezone"):
            kwargs["Timezone"] = str(cfg["timezone"])
        if cfg.get("character_set"):
            kwargs["CharacterSetName"] = str(cfg["character_set"])
        try:
            self._rds.create_db_instance(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, "", f"create_db_instance: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=instance_id),
            f"RDS SQL Server {instance_id} provisioning (password in {secret_arn})",
        )

    @driver_op(cloud="aws", driver="mssql_rds")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, instance_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        refusal = self._option_group_refusal(cfg)
        if refusal:
            return UpdateResult(ok=False, handle=spec.handle, message=refusal, errors=[refusal])
        kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        if spec.size or cfg.get("instance_class"):
            try:
                kwargs["DBInstanceClass"] = self._instance_class(
                    size=spec.size,
                    explicit=str(cfg.get("instance_class", "")),
                    engine_version=str(cfg.get("engine_version") or self._config.engine_version),
                )
            except ManagedServiceError as exc:
                return UpdateResult(False, spec.handle, str(exc), [str(exc)])
            if storage := _SIZE_TO_STORAGE.get(spec.size):
                kwargs["AllocatedStorage"] = storage
        for key, aws_key, cast in (
            ("allocated_storage", "AllocatedStorage", int),
            ("backup_retention_days", "BackupRetentionPeriod", int),
            ("multi_az", "MultiAZ", bool),
            ("deletion_protection", "DeletionProtection", bool),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if cfg.get("engine_version"):
            kwargs["EngineVersion"] = str(cfg["engine_version"])
            kwargs["AllowMajorVersionUpgrade"] = bool(cfg.get("allow_major_version_upgrade", False))
        if cfg.get("parameter_group"):
            kwargs["DBParameterGroupName"] = str(cfg["parameter_group"])
        if cfg.get("option_group"):
            kwargs["OptionGroupName"] = str(cfg["option_group"])
        if self._config.engine == "sqlserver-ex" and kwargs.get("MultiAZ"):
            return UpdateResult(
                False,
                spec.handle,
                "RDS SQL Server Express does not support Multi-AZ deployments",
                ["multi_az_unsupported"],
            )
        if len(kwargs) == 2:
            return UpdateResult(True, spec.handle, "no SQL Server changes requested")
        try:
            self._rds.modify_db_instance(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify_db_instance: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"RDS SQL Server {instance_id} update queued")

    @driver_op(
        cloud="aws",
        driver="mssql_rds",
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
        _, instance_id = parse_handle(spec.handle)
        existing = self._describe(instance_id)
        if existing is None:
            if delete_data:
                self._delete_secrets(instance_id)
            return DeprovisionResult(True, spec.handle, f"RDS SQL Server {instance_id} already gone")
        if existing.get("DeletionProtection"):
            if not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "RDS SQL Server deletion protection is enabled; pass force_destroy=True to bypass",
                    ["deletion_protection_enabled"],
                )
            try:
                self._rds.modify_db_instance(
                    DBInstanceIdentifier=instance_id,
                    DeletionProtection=False,
                    ApplyImmediately=True,
                )
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, f"clear deletion protection: {exc}", [str(exc)])
        kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "SkipFinalSnapshot": bool(delete_data),
        }
        if not delete_data:
            kwargs["FinalDBSnapshotIdentifier"] = _snapshot_id(instance_id, "final")
        try:
            self._rds.delete_db_instance(**kwargs)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete_db_instance: {exc}", [str(exc)])
        if delete_data:
            self._delete_secrets(instance_id)
        return DeprovisionResult(
            True,
            spec.handle,
            f"RDS SQL Server {instance_id} deletion queued (snapshot={'skipped' if delete_data else 'taken'})",
        )

    @driver_op(cloud="aws", driver="mssql_rds")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, instance_id = parse_handle(handle.handle)
        instance = self._describe(instance_id)
        if instance is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"RDS SQL Server {instance_id} not found")
        aws_state = str(instance.get("DBInstanceStatus", "unknown"))
        return ServiceStatus(handle.handle, _STATE.get(aws_state, "updating"), f"RDS reports {aws_state}")

    @driver_op(cloud="aws", driver="mssql_rds")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, instance_id = parse_handle(handle.handle)
        instance = self._describe(instance_id)
        if instance is None:
            raise ManagedServiceError(f"binding requested for missing SQL Server {instance_id}")
        endpoint = instance.get("Endpoint") or {}
        host = str(endpoint.get("Address", ""))
        port = str(endpoint.get("Port", 1433))
        database = str(instance.get("DBName") or "master")
        user = str(instance.get("MasterUsername", "astrolift"))
        password_secret = self._password_secret(instance_id)
        url_secret = self._ensure_url_secret(instance_id, host, port, database, user)
        return Binding(
            env_vars={
                "MSSQL_HOST": ValueRef(literal=host),
                "MSSQL_PORT": ValueRef(literal=port),
                "MSSQL_DB": ValueRef(literal=database),
                "MSSQL_USER": ValueRef(literal=user),
                "MSSQL_PASSWORD": ValueRef(secret_ref=password_secret),
                "MSSQL_ENCRYPT": ValueRef(literal="true"),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
            },
            iam_grants=[Grant(password_secret, ["secretsmanager:GetSecretValue"])],
            notes="RDS SQL Server license-included endpoint; encrypted client connections enabled.",
        )

    @driver_op(cloud="aws", driver="mssql_rds")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, instance_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_id(instance_id, "snap")
        try:
            self._rds.create_db_snapshot(
                DBInstanceIdentifier=instance_id,
                DBSnapshotIdentifier=snapshot_id,
                Tags=[],
            )
        except Exception as exc:
            raise ManagedServiceError(f"create_db_snapshot: {exc}") from exc
        return SnapshotHandle(handle.handle, snapshot_id, datetime.now(UTC).isoformat())

    @driver_op(cloud="aws", driver="mssql_rds")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id(target)
        cfg = target.config or {}
        multi_az = bool(cfg.get("multi_az", self._config.multi_az_default))
        if self._config.engine == "sqlserver-ex" and multi_az:
            return ProvisionResult(
                False,
                "",
                "RDS SQL Server Express does not support Multi-AZ deployments",
                ["multi_az_unsupported"],
            )
        try:
            instance_class = self._instance_class(
                size=target.size,
                explicit=str(cfg.get("instance_class", "")),
                engine_version=str(cfg.get("engine_version") or self._config.engine_version),
            )
        except ManagedServiceError as exc:
            return ProvisionResult(False, "", str(exc), [str(exc)])
        try:
            _, source_instance_id = parse_handle(snapshot.handle)
            password = str(
                self._sm.get_secret_value(SecretId=self._password_secret(source_instance_id)).get(
                    "SecretString",
                    "",
                ),
            )
            self._store_password(instance_id, password, target)
        except Exception as exc:
            return ProvisionResult(False, "", f"copy SQL Server restore credentials: {exc}", [str(exc)])
        if self._describe(instance_id) is not None:
            return ProvisionResult(
                True,
                handle_for(kind=KIND, resource_id=instance_id),
                f"RDS SQL Server restore target {instance_id} already exists",
            )
        kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "DBSnapshotIdentifier": snapshot.snapshot_id,
            "DBInstanceClass": instance_class,
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "MultiAZ": multi_az,
            "PubliclyAccessible": False,
            "DeletionProtection": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "CopyTagsToSnapshot": True,
            "Tags": tags_for(target),
        }
        try:
            self._rds.restore_db_instance_from_db_snapshot(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, "", f"restore SQL Server snapshot: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=instance_id),
            f"RDS SQL Server restore from {snapshot.snapshot_id} queued",
        )

    @driver_op(cloud="aws", driver="mssql_rds", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "instance_class": {"type": "string"},
                "allocated_storage": {"type": "integer", "minimum": 20, "maximum": 16384},
                "storage_type": {"type": "string", "enum": ["gp2", "gp3", "io1", "io2"]},
                "master_username": {"type": "string"},
                "multi_az": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "backup_retention_days": {"type": "integer", "minimum": 0, "maximum": 35},
                "auto_minor_version_upgrade": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "option_group": {"type": "string"},
                "parameter_group": {"type": "string"},
                "timezone": {"type": "string"},
                "character_set": {"type": "string"},
                "apply_immediately": {"type": "boolean"},
                "allow_major_version_upgrade": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="mssql_rds", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MSSQL_HOST": "RDS SQL Server endpoint",
                "MSSQL_PORT": "SQL Server port (1433)",
                "MSSQL_DB": "Initial database (master after snapshot restore)",
                "MSSQL_USER": "Master username",
                "MSSQL_PASSWORD": "Secrets Manager password reference",
                "MSSQL_ENCRYPT": "Require encrypted client transport",
                "DATABASE_URL": "Secrets Manager SQL Server DSN reference",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "engine_version",
            "instance_class",
            "allocated_storage",
            "backup_retention_days",
            "multi_az",
            "deletion_protection",
            "option_group",
            "parameter_group",
            "apply_immediately",
            "allow_major_version_upgrade",
        ]

    def _describe(self, instance_id: str) -> dict[str, Any] | None:
        try:
            rows = self._rds.describe_db_instances(DBInstanceIdentifier=instance_id).get("DBInstances", [])
        except Exception as exc:
            if _not_found(exc, "DBInstanceNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _instance_class(self, *, size: str, explicit: str, engine_version: str) -> str:
        if explicit:
            return explicit
        kwargs: dict[str, Any] = {
            "Engine": self._config.engine,
            "LicenseModel": "license-included",
            "Vpc": True,
            "MaxRecords": 100,
        }
        if engine_version:
            kwargs["EngineVersion"] = engine_version
        available: set[str] = set()
        try:
            while True:
                response = self._rds.describe_orderable_db_instance_options(**kwargs)
                available.update(
                    str(row.get("DBInstanceClass", ""))
                    for row in response.get("OrderableDBInstanceOptions", [])
                    if row.get("DBInstanceClass")
                )
                marker = str(response.get("Marker", ""))
                if not marker:
                    break
                kwargs["Marker"] = marker
        except Exception as exc:
            raise ManagedServiceError(
                f"describe orderable SQL Server instance classes: {exc}",
            ) from exc
        preferences = _CLASS_PREFERENCES[self._config.engine].get(
            size,
            _CLASS_PREFERENCES[self._config.engine]["small"],
        )
        selected = next((candidate for candidate in preferences if candidate in available), "")
        if not selected:
            raise ManagedServiceError(
                f"no orderable {self._config.engine} instance class matches Astrolift size {size!r}; "
                "set config.instance_class explicitly from describe-orderable-db-instance-options",
            )
        return selected

    def _option_group_refusal(self, cfg: dict[str, Any]) -> str:
        group = str(cfg.get("option_group") or "").strip()
        if not group or group.casefold() in {
            str(item).strip().casefold() for item in self._config.allowed_option_groups
        }:
            return ""
        return f"option_group {group!r} is not allowed by the cluster install policy mssql_allowed_option_groups"

    def _instance_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.instance_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "mssql",
            )
            if part
        )
        clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        if not clean or not clean[0].isalpha():
            clean = f"m-{clean}"
        return clean.strip("-")[:63]

    def _password_secret(self, instance_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{instance_id}/master"

    def _url_secret(self, instance_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{instance_id}/url"

    def _store_password(self, instance_id: str, password: str, spec: ProvisionSpec) -> str:
        name = self._password_secret(instance_id)
        try:
            response = self._sm.create_secret(
                Name=name,
                Description=f"RDS SQL Server master password for {instance_id}",
                SecretString=password,
                Tags=tags_for(spec),
            )
            return str(response.get("ARN", name))
        except Exception as exc:
            if "ResourceExists" not in str(exc) and type(exc).__name__ != "ResourceExistsException":
                raise ManagedServiceError(f"create SQL Server password secret: {exc}") from exc
            self._sm.put_secret_value(SecretId=name, SecretString=password)
            return name

    def _ensure_url_secret(self, instance_id: str, host: str, port: str, database: str, user: str) -> str:
        try:
            password = str(
                self._sm.get_secret_value(SecretId=self._password_secret(instance_id)).get(
                    "SecretString",
                    "",
                ),
            )
        except Exception as exc:
            raise ManagedServiceError(f"read SQL Server password secret: {exc}") from exc
        dsn = f"sqlserver://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}?encrypt=true"
        name = self._url_secret(instance_id)
        try:
            self._sm.create_secret(Name=name, SecretString=dsn)
        except Exception as exc:
            if "ResourceExists" not in str(exc) and type(exc).__name__ != "ResourceExistsException":
                raise ManagedServiceError(f"write SQL Server URL secret: {exc}") from exc
            self._sm.put_secret_value(SecretId=name, SecretString=dsn)
        return name

    def _delete_secrets(self, instance_id: str) -> None:
        for name in (self._password_secret(instance_id), self._url_secret(instance_id)):
            with contextlib.suppress(Exception):
                self._sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)


def _password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _snapshot_id(instance_id: str, label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{instance_id}-{label}-{stamp}"[:255]


def _not_found(exc: Exception, marker: str) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return marker in code or marker in type(exc).__name__ or marker in str(exc)
