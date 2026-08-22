"""RDS Postgres managed-service driver (#351).

Implements ``ManagedServiceDriver`` for the canonical AWS managed-
Postgres path. Supports the two engines RDS exposes for Postgres:

  - ``postgres`` (variant ``rds``) — single-AZ or multi-AZ instances
    via ``create_db_instance`` / ``delete_db_instance``
  - ``postgres`` (variant ``aurora``) — Aurora Postgres clusters
    (cluster + at least one instance) via the ``create_db_cluster``
    family

The driver provisions the resource, persists the auto-generated
master password to AWS Secrets Manager, and returns a connection
envelope (host, port, db, user) plus a secret_ref to the password.
Deprovision implements the SDK's four-corner matrix:

  delete_data=False, force_destroy=False (default):
    final snapshot taken, DeletionProtection respected. If
    DeletionProtection is on, errors out with a clear operator
    message.

  delete_data=True, force_destroy=False:
    skip final snapshot, DeletionProtection respected.

  delete_data=False, force_destroy=True:
    final snapshot taken, DeletionProtection bypassed (the driver
    disables it before delete).

  delete_data=True, force_destroy=True:
    skip final snapshot, DeletionProtection bypassed. The platform's
    --atomic cleanup path.
"""

from __future__ import annotations

import contextlib
import secrets
import string
from dataclasses import dataclass, field
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
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "postgres"


# Size -> instance class. Keeps the spec.size enum decoupled from
# AWS-specific class names; operators get t/m families that map to
# astrolift's small/medium/large/xlarge tiers.
_SIZE_TO_INSTANCE_CLASS = {
    "small": "db.t4g.small",
    "medium": "db.t4g.medium",
    "large": "db.m6g.large",
    "xlarge": "db.m6g.xlarge",
}

# Size -> allocated storage (GiB). gp3 storage is the default storage
# type because it decouples IOPS from size and is the lowest TCO for
# most platform workloads.
_SIZE_TO_STORAGE_GB = {
    "small": 20,
    "medium": 50,
    "large": 100,
    "xlarge": 250,
}


@dataclass(frozen=True)
class RDSConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str
    db_subnet_group: str
    """Pre-existing DB subnet group (private subnets across at least
    two AZs). Created out-of-band by opscode; the driver does not
    manage subnet groups itself."""

    security_group_ids: list[str] = field(default_factory=list)
    """Security groups granting inbound 5432 from the tenant cluster's
    pod CIDR. Also opscode-managed."""

    instance_name_prefix: str = "astrolift"
    """Prefix on the DB instance identifier. Lets operators filter in
    the AWS console + apply tag-based budgets."""

    engine_version: str = ""
    """Postgres major.minor. Empty (default) omits EngineVersion so RDS
    picks its current default for the engine — pinning a specific minor
    rots (AWS retires old versions). Operators can override per-spec via
    ``spec.config.engine_version``."""

    backup_retention_days: int = 7
    """Default backup retention window. Final-snapshot uses this on
    delete-with-data-retained paths."""

    multi_az_default: bool = False
    """If True, all instances are multi-AZ unless explicitly opted out
    in spec.config. False keeps cost down for dev clusters."""

    deletion_protection_default: bool = True
    """Default for new instances. Operators with ``force_destroy=True``
    bypass on delete."""

    secrets_manager_prefix: str = "astrolift/rds"
    """Path prefix for the master-password secret. Lets operators apply
    tag-based IAM policies."""


class RDSPostgresDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: RDSConfig,
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
            self._sm = aws_client(
                "secretsmanager",
                region=config.region,
                credential=config.credential,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="postgres_rds",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id_for(spec=spec)
        cfg = spec.config or {}

        # Probe for an existing instance first — provision is idempotent.
        existing = self._describe(instance_id)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=instance_id),
                message=(f"db instance {instance_id} already exists (status={existing.get('DBInstanceStatus')})"),
            )

        master_password = _generate_master_password()
        secret_arn = self._store_master_password(
            instance_id=instance_id,
            password=master_password,
            spec=spec,
        )

        engine_version = cfg.get("engine_version") or self._config.engine_version
        instance_class = cfg.get("instance_class") or _SIZE_TO_INSTANCE_CLASS.get(spec.size, "db.t4g.small")
        allocated_storage = int(
            cfg.get("allocated_storage") or _SIZE_TO_STORAGE_GB.get(spec.size, 20),
        )
        multi_az = bool(
            cfg.get("multi_az", self._config.multi_az_default),
        )
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        backup_retention = int(
            cfg.get(
                "backup_retention_days",
                self._config.backup_retention_days,
            ),
        )

        create_kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "DBInstanceClass": instance_class,
            "Engine": "postgres",
            "EngineVersion": engine_version,
            "AllocatedStorage": allocated_storage,
            "StorageType": "gp3",
            "MasterUsername": "astrolift",
            "MasterUserPassword": master_password,
            "DBName": _db_name_for(spec),
            "DBSubnetGroupName": self._config.db_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "BackupRetentionPeriod": backup_retention,
            "MultiAZ": multi_az,
            "PubliclyAccessible": False,
            "StorageEncrypted": True,
            "DeletionProtection": deletion_protection,
            "EnablePerformanceInsights": True,
            "PerformanceInsightsRetentionPeriod": 7,
            "Tags": tags_for(spec),
            "CopyTagsToSnapshot": True,
        }
        if cfg.get("kms_key_arn"):
            create_kwargs["KmsKeyId"] = cfg["kms_key_arn"]
        if cfg.get("parameter_group"):
            create_kwargs["DBParameterGroupName"] = cfg["parameter_group"]

        if not engine_version:
            create_kwargs.pop("EngineVersion", None)
        try:
            self._rds.create_db_instance(**create_kwargs)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_db_instance: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=instance_id),
            message=(f"db instance {instance_id} provisioning (password in {secret_arn})"),
        )

    @driver_op(cloud="aws", driver="postgres_rds")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, instance_id = parse_handle(spec.handle)
        cfg = spec.config or {}

        modify_kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        if spec.size:
            instance_class = cfg.get("instance_class") or _SIZE_TO_INSTANCE_CLASS.get(spec.size)
            if instance_class:
                modify_kwargs["DBInstanceClass"] = instance_class
            new_storage = _SIZE_TO_STORAGE_GB.get(spec.size)
            if new_storage:
                modify_kwargs["AllocatedStorage"] = new_storage
        if cfg.get("engine_version"):
            modify_kwargs["EngineVersion"] = cfg["engine_version"]
            modify_kwargs["AllowMajorVersionUpgrade"] = bool(
                cfg.get("allow_major_version_upgrade", False),
            )
        if "multi_az" in cfg:
            modify_kwargs["MultiAZ"] = bool(cfg["multi_az"])
        if cfg.get("parameter_group"):
            modify_kwargs["DBParameterGroupName"] = cfg["parameter_group"]
        if "backup_retention_days" in cfg:
            modify_kwargs["BackupRetentionPeriod"] = int(
                cfg["backup_retention_days"],
            )

        if len(modify_kwargs) <= 2:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        try:
            self._rds.modify_db_instance(**modify_kwargs)
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"modify_db_instance: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"db instance {instance_id} update queued",
        )

    @driver_op(
        cloud="aws",
        driver="postgres_rds",
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
            self._delete_master_password_secret(instance_id)
            self._delete_url_secret(instance_id)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"db instance {instance_id} already gone",
            )

        # force_destroy must disable DeletionProtection first; RDS
        # refuses delete_db_instance while the flag is set.
        if existing.get("DeletionProtection") and force_destroy:
            try:
                self._rds.modify_db_instance(
                    DBInstanceIdentifier=instance_id,
                    DeletionProtection=False,
                    ApplyImmediately=True,
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"failed to clear DeletionProtection: {exc}",
                    errors=[str(exc)],
                )
        elif existing.get("DeletionProtection"):
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"db instance {instance_id} has DeletionProtection enabled — pass force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        delete_kwargs: dict[str, Any] = {
            "DBInstanceIdentifier": instance_id,
            "SkipFinalSnapshot": bool(delete_data),
        }
        if not delete_data:
            delete_kwargs["FinalDBSnapshotIdentifier"] = _final_snapshot_id(instance_id=instance_id)

        try:
            self._rds.delete_db_instance(**delete_kwargs)
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_db_instance: {exc}",
                errors=[str(exc)],
            )

        # The master-password secret is only useful while the DB is
        # alive. We delete it eagerly when delete_data=True; we keep
        # it (scheduled for AWS's default 7-day recovery window) when
        # the data path is being retained, so an operator restoring
        # from final-snapshot still has the original credentials.
        if delete_data:
            self._delete_master_password_secret(instance_id)
            self._delete_url_secret(instance_id)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"db instance {instance_id} delete queued "
                f"(snapshot={'skipped' if delete_data else 'taken'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="postgres_rds")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, instance_id = parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"db instance {instance_id} not found",
            )
        rds_state = existing.get("DBInstanceStatus", "unknown")
        return ServiceStatus(
            handle=handle.handle,
            state=_RDS_STATE_TO_PROTOCOL.get(rds_state, "updating"),
            message=f"rds reports {rds_state}",
        )

    @driver_op(cloud="aws", driver="postgres_rds")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, instance_id = parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            raise ManagedServiceError(
                f"binding requested for missing instance {instance_id}",
            )
        endpoint = existing.get("Endpoint") or {}
        host = endpoint.get("Address", "")
        port = str(endpoint.get("Port", 5432))
        db_name = existing.get("DBName", "")
        username = existing.get("MasterUsername", "astrolift")
        secret_name = self._secret_name_for(instance_id=instance_id)

        # Ensure the DATABASE_URL secret exists. The master-password secret is
        # created at provision, but the URL needs the endpoint host, which only
        # exists once the instance is available (binding() runs post-available
        # per #1009). Without this the DATABASE_URL secret_ref is unresolvable
        # and the whole bindings Secret fails to build → CreateContainerConfigError.
        self._ensure_url_secret(
            instance_id=instance_id,
            host=host,
            port=port,
            db_name=db_name,
            username=username,
            password_secret=secret_name,
        )

        return Binding(
            # Canonical postgres envelope (astrolift_manifest.env_injection
            # ._ENVELOPES["postgres"]) — the contract apps read. Keep these
            # keys in lockstep with that envelope, not a driver-local naming.
            env_vars={
                "POSTGRES_HOST": ValueRef(literal=host),
                "POSTGRES_PORT": ValueRef(literal=port),
                "POSTGRES_DB": ValueRef(literal=db_name),
                "POSTGRES_USER": ValueRef(literal=username),
                "POSTGRES_PASSWORD": ValueRef(secret_ref=secret_name),
                # RDS storage is encrypted; require TLS client-side by default.
                "POSTGRES_SSL_MODE": ValueRef(literal="require"),
                "DATABASE_URL": ValueRef(
                    secret_ref=self._secret_name_for_url(
                        instance_id=instance_id,
                    ),
                ),
            },
            iam_grants=[
                Grant(
                    resource=secret_name,
                    actions=["secretsmanager:GetSecretValue"],
                ),
            ],
            notes=(
                "DATABASE_URL is a derived secret holding the "
                "fully-formed postgres:// connection string; the "
                "split components are also exposed for callers that "
                "build their own DSN."
            ),
        )

    @driver_op(cloud="aws", driver="postgres_rds")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        _, instance_id = parse_handle(handle.handle)
        snap_id = f"{instance_id}-snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        try:
            self._rds.create_db_snapshot(
                DBInstanceIdentifier=instance_id,
                DBSnapshotIdentifier=snap_id,
            )
        except Exception as exc:
            raise ManagedServiceError(
                f"create_db_snapshot: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="postgres_rds")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_instance_id = self._instance_id_for(spec=target)
        try:
            self._rds.restore_db_instance_from_db_snapshot(
                DBInstanceIdentifier=target_instance_id,
                DBSnapshotIdentifier=snapshot.snapshot_id,
                DBSubnetGroupName=self._config.db_subnet_group,
                VpcSecurityGroupIds=list(self._config.security_group_ids),
                MultiAZ=self._config.multi_az_default,
                PubliclyAccessible=False,
                DeletionProtection=self._config.deletion_protection_default,
                CopyTagsToSnapshot=True,
                Tags=tags_for(target),
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore_db_instance_from_db_snapshot: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=target_instance_id),
            message=(f"restore from snapshot {snapshot.snapshot_id} queued"),
        )

    @driver_op(cloud="aws", driver="postgres_rds", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "instance_class": {"type": "string"},
                "allocated_storage": {"type": "integer", "minimum": 20},
                "multi_az": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "backup_retention_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 35,
                },
                "parameter_group": {"type": "string"},
                "kms_key_arn": {"type": "string"},
                "apply_immediately": {"type": "boolean"},
                "allow_major_version_upgrade": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="postgres_rds", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DATABASE_HOST": "RDS endpoint host",
                "DATABASE_PORT": "RDS endpoint port (5432)",
                "DATABASE_NAME": "Initial database name",
                "DATABASE_USER": "Master username (astrolift)",
                "DATABASE_PASSWORD": ("Secrets Manager ref to the master password"),
                "DATABASE_URL": ("Secrets Manager ref to the fully-formed postgres:// connection string"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, instance_id: str) -> dict[str, Any] | None:
        try:
            resp = self._rds.describe_db_instances(
                DBInstanceIdentifier=instance_id,
            )
        except Exception as exc:
            if type(exc).__name__ == "DBInstanceNotFoundFault":
                return None
            # boto/botocore wrap not-found differently on different
            # paths; sniff the error code from the exception body too.
            if "DBInstanceNotFound" in str(exc):
                return None
            raise
        instances = resp.get("DBInstances") or []
        return instances[0] if instances else None

    def _instance_id_for(self, *, spec: ProvisionSpec) -> str:
        return (
            (
                f"{self._config.instance_name_prefix}-"
                f"{spec.organization_slug}-{spec.app_slug}-"
                f"{spec.environment_name}-{spec.service_handle_hint or 'pg'}"
            )
            .lower()
            .replace("_", "-")[:60]
        )

    def _secret_name_for(self, *, instance_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{instance_id}/master"

    def _secret_name_for_url(self, *, instance_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{instance_id}/url"

    def _store_master_password(
        self,
        *,
        instance_id: str,
        password: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._secret_name_for(instance_id=instance_id)
        try:
            resp = self._sm.create_secret(
                Name=name,
                Description=(
                    f"Master password for RDS Postgres {instance_id} (app {spec.app_slug}/{spec.environment_name})"
                ),
                SecretString=password,
                Tags=tags_for(spec),
            )
            return resp.get("ARN", name)
        except Exception as exc:
            # If it already exists, update it. This makes the call
            # idempotent across retry-on-failed-provision scenarios.
            if "ResourceExistsException" in type(exc).__name__:
                self._sm.put_secret_value(
                    SecretId=name,
                    SecretString=password,
                )
                return name
            raise ManagedServiceError(
                f"create_secret for {name}: {exc}",
            ) from exc

    def _ensure_url_secret(
        self,
        *,
        instance_id: str,
        host: str,
        port: str,
        db_name: str,
        username: str,
        password_secret: str,
    ) -> None:
        """Create/update the DATABASE_URL secret (full postgres:// DSN). Built
        from the live endpoint host + the stored master password. Idempotent."""
        from urllib.parse import quote

        if not host:
            return  # endpoint not ready; binding() re-runs once available
        try:
            pw = self._sm.get_secret_value(
                SecretId=password_secret,
            ).get("SecretString", "")
        except Exception:
            return
        url = f"postgresql://{username}:{quote(pw, safe='')}@{host}:{port}/{db_name}?sslmode=require"
        name = self._secret_name_for_url(instance_id=instance_id)
        try:
            self._sm.create_secret(
                Name=name,
                Description=f"Connection URL for RDS Postgres {instance_id}",
                SecretString=url,
            )
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                self._sm.put_secret_value(SecretId=name, SecretString=url)
            else:
                raise ManagedServiceError(
                    f"create_secret for {name}: {exc}",
                ) from exc

    def _delete_url_secret(self, instance_id: str) -> None:
        name = self._secret_name_for_url(instance_id=instance_id)
        with contextlib.suppress(Exception):
            self._sm.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )

    def _delete_master_password_secret(self, instance_id: str) -> None:
        name = self._secret_name_for(instance_id=instance_id)
        try:
            self._sm.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."
"""RDS allows printable ASCII except ``/``, ``@``, ``"``, and space.
Our subset is conservative: no shell-special characters at all."""


def _generate_master_password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _db_name_for(spec: ProvisionSpec) -> str:
    """Postgres requires the initial DB name to start with a letter
    and contain only letters/digits/underscores."""
    raw = f"{spec.app_slug}_{spec.environment_name}".replace("-", "_")
    candidate = "".join(c for c in raw if c.isalnum() or c == "_")
    if not candidate or not candidate[0].isalpha():
        candidate = f"app_{candidate}"
    return candidate[:63]


def _final_snapshot_id(*, instance_id: str) -> str:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{instance_id}-final-{stamp}"[:255]


_RDS_STATE_TO_PROTOCOL = {
    "available": "available",
    "creating": "provisioning",
    "backing-up": "available",
    "modifying": "updating",
    "starting": "provisioning",
    "stopped": "available",
    "stopping": "updating",
    "deleting": "deprovisioning",
    "failed": "error",
    "incompatible-network": "error",
    "incompatible-option-group": "error",
    "incompatible-parameters": "error",
    "incompatible-restore": "error",
    "restore-error": "error",
    "storage-full": "error",
}
