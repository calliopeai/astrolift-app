"""Cloud SQL for SQL Server instance, database, backup, and binding lifecycle."""

from __future__ import annotations

import contextlib
import re
import secrets
import string
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from _sdk._telemetry import driver_op
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._secret_store import ManagedSecretStore, ManagedSecretStoreError

KIND = "mssql"
_API_ROOT = "https://sqladmin.googleapis.com/v1"
_ENGINE_VERSIONS = (
    "SQLSERVER_2017_EXPRESS",
    "SQLSERVER_2017_WEB",
    "SQLSERVER_2017_STANDARD",
    "SQLSERVER_2017_ENTERPRISE",
    "SQLSERVER_2019_EXPRESS",
    "SQLSERVER_2019_WEB",
    "SQLSERVER_2019_STANDARD",
    "SQLSERVER_2019_ENTERPRISE",
    "SQLSERVER_2022_EXPRESS",
    "SQLSERVER_2022_WEB",
    "SQLSERVER_2022_STANDARD",
    "SQLSERVER_2022_ENTERPRISE",
    "SQLSERVER_2025_EXPRESS",
    "SQLSERVER_2025_STANDARD",
    "SQLSERVER_2025_ENTERPRISE",
)
_SIZE_TO_TIER = {
    "small": "db-custom-1-3840",
    "medium": "db-custom-2-8192",
    "large": "db-custom-4-16384",
    "xlarge": "db-custom-8-32768",
}
_ENTERPRISE_SIZE_TO_TIER = {
    "small": "db-custom-2-8192",
    "medium": "db-custom-4-16384",
    "large": "db-custom-8-32768",
    "xlarge": "db-custom-16-65536",
}
_ENTERPRISE_PLUS_SIZE_TO_TIER = {
    "small": "db-perf-optimized-N-2",
    "medium": "db-perf-optimized-N-4",
    "large": "db-perf-optimized-N-8",
    "xlarge": "db-perf-optimized-N-16",
}
_SIZE_TO_STORAGE_GB = {"small": 20, "medium": 50, "large": 100, "xlarge": 250}
_STATE = {
    "RUNNABLE": "available",
    "PENDING_CREATE": "provisioning",
    "MAINTENANCE": "updating",
    "FAILED": "error",
    "UNKNOWN_STATE": "updating",
    "SUSPENDED": "error",
    "PENDING_DELETE": "deprovisioning",
}


class CloudSQLServerError(RuntimeError):
    pass


class CloudSQLServerNotFound(CloudSQLServerError):
    pass


@dataclass(frozen=True)
class CloudSQLServerConfig:
    project_id: str
    region: str
    private_network: str | None = None
    instance_name_prefix: str = "astrolift"
    engine_version: str = "SQLSERVER_2025_EXPRESS"
    backup_retention_days: int = 7
    high_availability_default: bool = False
    deletion_protection_default: bool = True
    secret_manager_prefix: str = "astrolift/cloudsql"
    secret_id_prefix: str = "astrolift"
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 1800.0
    poll_interval_seconds: float = 3.0


class CloudSQLRestClient:
    """Authenticated request-shaped adapter for Cloud SQL Admin API v1."""

    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=("https://www.googleapis.com/auth/cloud-platform",))
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_instance(self, project: str, instance: str) -> dict[str, Any]:
        return self._request("GET", f"projects/{project}/instances/{instance}")

    def create_instance(self, project: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"projects/{project}/instances", json=body)

    def patch_instance(
        self,
        project: str,
        instance: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        # Cloud SQL instances.patch is partial by definition and, unlike many
        # Google APIs, does not accept an updateMask query parameter.  Keep the
        # mask in the adapter contract for reconciliation/audit tests but send
        # only the sparse body to the live endpoint.
        del update_mask
        return self._request(
            "PATCH",
            f"projects/{project}/instances/{instance}",
            json=body,
        )

    def delete_instance(self, project: str, instance: str) -> dict[str, Any]:
        return self._request("DELETE", f"projects/{project}/instances/{instance}")

    def get_database(self, project: str, instance: str, database: str) -> dict[str, Any]:
        return self._request("GET", f"projects/{project}/instances/{instance}/databases/{database}")

    def create_database(self, project: str, instance: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"projects/{project}/instances/{instance}/databases", json=body)

    def patch_database(
        self,
        project: str,
        instance: str,
        database: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"projects/{project}/instances/{instance}/databases/{database}",
            json=body,
        )

    def create_backup(self, project: str, instance: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"projects/{project}/instances/{instance}/backupRuns", json=body)

    def get_disk_shrink_config(self, project: str, instance: str) -> dict[str, Any]:
        return self._request("GET", f"projects/{project}/instances/{instance}/getDiskShrinkConfig")

    def perform_disk_shrink(
        self,
        project: str,
        instance: str,
        target_size_gb: int,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"projects/{project}/instances/{instance}/performDiskShrink",
            json={"targetSizeGb": str(target_size_gb)},
        )

    def restore_backup(
        self,
        project: str,
        instance: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"projects/{project}/instances/{instance}/restoreBackup",
            json=body,
        )

    def get_operation(self, project: str, operation: str) -> dict[str, Any]:
        return self._request("GET", f"projects/{project}/operations/{operation}")

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise CloudSQLServerNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise CloudSQLServerError(f"Cloud SQL HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class CloudSQLServerDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: CloudSQLServerConfig,
        client: Any | None = None,
        secrets_client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._sql = client or CloudSQLRestClient(endpoint=config.api_endpoint)
        if secrets_client is None:
            from google.cloud import secretmanager

            secrets_client = secretmanager.SecretManagerServiceClient()
        self._sm = secrets_client
        self._secret_store = ManagedSecretStore(
            project_id=config.project_id,
            secret_id_prefix=config.secret_id_prefix,
            client=secrets_client,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="mssql_cloudsql",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloudsql_sqlserver_config"])
        instance_id = self._instance_id(spec, cfg)
        handle = _handle(instance_id)
        database = self._database_name(spec, cfg)
        secret_name = self._master_secret(instance_id)
        try:
            current = self._get_instance(instance_id)
            if current is None:
                password = _generate_master_password()
                self._secret_store.upsert(secret_name, password, labels=_labels(spec))
                try:
                    operation = self._sql.create_instance(
                        self._config.project_id,
                        self._instance_body(spec, cfg, instance_id, password),
                    )
                    self._wait_operation(operation)
                except Exception:
                    self._delete_connection_secrets(instance_id)
                    raise
                current = self._sql.get_instance(self._config.project_id, instance_id)
            else:
                if self._secret_store.get(secret_name) is None:
                    raise CloudSQLServerError(
                        f"Cloud SQL instance {instance_id} exists but its Astrolift master secret is missing",
                    )
                self._assert_owned(current, cfg)
                recorded_database = str(
                    self._secret_store.get(self._database_secret(instance_id))
                    or self._database_from_instance(current)
                    or "",
                )
                if recorded_database and database != recorded_database:
                    raise CloudSQLServerError(
                        f"database_name is immutable ({recorded_database!r} != {database!r}); replace the service",
                    )
                self._reconcile_instance(instance_id, current, cfg)
            self._ensure_database(instance_id, database, cfg)
            self._secret_store.upsert(self._database_secret(instance_id), database, labels=_labels(spec))
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Cloud SQL for SQL Server: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Cloud SQL for SQL Server instance {instance_id} and database {database} reconciled",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="mssql_cloudsql")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_cloudsql_sqlserver_config"])
        try:
            current = self._sql.get_instance(self._config.project_id, instance_id)
            self._assert_owned(current, cfg)
            self._reconcile_instance(instance_id, current, cfg, size=spec.size)
            recorded_database = str(
                self._secret_store.get(self._database_secret(instance_id))
                or self._database_from_instance(current)
                or "",
            )
            requested_database = str(cfg.get("database_name") or recorded_database)
            if recorded_database and requested_database != recorded_database:
                raise CloudSQLServerError(
                    "database_name is immutable "
                    f"({recorded_database!r} != {requested_database!r}); replace the service",
                )
            database = requested_database
            if database:
                self._ensure_database(instance_id, database, cfg)
        except CloudSQLServerNotFound:
            return UpdateResult(False, spec.handle, f"Cloud SQL instance {instance_id} not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Cloud SQL for SQL Server: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Cloud SQL for SQL Server instance {instance_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="mssql_cloudsql",
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
        try:
            instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        try:
            current = self._get_instance(instance_id)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Cloud SQL instance", exc)
        if current is None:
            if delete_data:
                self._delete_connection_secrets(instance_id)
            return DeprovisionResult(True, spec.handle, f"Cloud SQL instance {instance_id} already gone")
        try:
            self._assert_owned(current, cfg, deleting=True)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [str(exc)], retryable=False)
        settings = current.get("settings") or {}
        protected = bool(settings.get("deletionProtectionEnabled"))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Cloud SQL instance {instance_id} has deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        retained = ""
        if not delete_data:
            try:
                if not settings.get("retainBackupsOnDelete"):
                    operation = self._sql.patch_instance(
                        self._config.project_id,
                        instance_id,
                        {"settings": {"retainBackupsOnDelete": True}},
                        update_mask=["settings.retainBackupsOnDelete"],
                    )
                    self._wait_operation(operation)
                retained = self.snapshot(ServiceHandle(spec.handle)).snapshot_id
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"retain Cloud SQL backup before delete: {exc}",
                    [str(exc)],
                    retryable=False,
                )
        try:
            if protected:
                operation = self._sql.patch_instance(
                    self._config.project_id,
                    instance_id,
                    {"settings": {"deletionProtectionEnabled": False}},
                    update_mask=["settings.deletionProtectionEnabled"],
                )
                self._wait_operation(operation)
            self._wait_operation(self._sql.delete_instance(self._config.project_id, instance_id))
        except CloudSQLServerNotFound:
            pass
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Cloud SQL instance", exc)
        if delete_data:
            self._delete_connection_secrets(instance_id)
        message = f"Cloud SQL for SQL Server instance {instance_id} deleted"
        if retained:
            message += f"; retained backup {retained}"
        return DeprovisionResult(True, spec.handle, message)

    @driver_op(cloud="gcp", driver="mssql_cloudsql")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            instance_id = _parse_handle(handle.handle)
            current = self._sql.get_instance(self._config.project_id, instance_id)
        except CloudSQLServerNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Cloud SQL instance does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Cloud SQL instance: {exc}")
        state = str(current.get("state") or "UNKNOWN_STATE")
        return ServiceStatus(
            handle.handle,
            _STATE.get(state, "updating"),
            f"Cloud SQL for SQL Server reports {state}",
        )

    @driver_op(cloud="gcp", driver="mssql_cloudsql")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        instance_id = _parse_handle(handle.handle)
        cfg = dict(config or {})
        current = self._sql.get_instance(self._config.project_id, instance_id)
        database = str(
            cfg.get("database_name")
            or self._secret_store.get(self._database_secret(instance_id))
            or self._database_from_instance(current)
            or "master",
        )
        host = _preferred_ip(current)
        if not host:
            raise CloudSQLServerError(f"Cloud SQL instance {instance_id} has no reachable IP endpoint")
        secret_name = self._master_secret(instance_id)
        password = self._secret_store.get(secret_name)
        if password is None:
            raise CloudSQLServerError(f"master secret {secret_name!r} is missing")
        url_secret = self._url_secret(instance_id)
        dsn = (
            f"mssql://sqlserver:{quote(password, safe='')}@{host}:1433/{quote(database, safe='')}"
            "?encrypt=true&trustServerCertificate=false"
        )
        try:
            self._secret_store.upsert(url_secret, dsn)
        except ManagedSecretStoreError as exc:
            raise CloudSQLServerError(str(exc)) from exc
        return Binding(
            env_vars={
                "MSSQL_HOST": ValueRef(literal=host),
                "MSSQL_PORT": ValueRef(literal="1433"),
                "MSSQL_DB": ValueRef(literal=database),
                "MSSQL_USER": ValueRef(literal="sqlserver"),
                "MSSQL_PASSWORD": ValueRef(secret_ref=secret_name),
                "MSSQL_ENCRYPT": ValueRef(literal="true"),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
                "GCP_CLOUDSQL_INSTANCE": ValueRef(literal=instance_id),
                "GCP_CLOUDSQL_CONNECTION_NAME": ValueRef(literal=str(current.get("connectionName") or "")),
            },
            iam_grants=[Grant(f"projects/{self._config.project_id}", ["roles/cloudsql.client"])],
            notes=(
                "Built-in SQL Server authentication with encrypted transport. "
                "Use GCP_CLOUDSQL_CONNECTION_NAME with the Auth Proxy/connector when direct IP routing is unavailable."
            ),
        )

    @driver_op(cloud="gcp", driver="mssql_cloudsql")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        instance_id = _parse_handle(handle.handle)
        self._sql.get_instance(self._config.project_id, instance_id)
        created = datetime.now(UTC)
        backup_id = self._create_backup(instance_id, created=created)
        return SnapshotHandle(
            handle.handle,
            f"{self._config.project_id}/{instance_id}/{backup_id}",
            created.isoformat(),
        )

    @driver_op(cloud="gcp", driver="mssql_cloudsql")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            source_project, source_instance, backup_id = _parse_snapshot_id(snapshot.snapshot_id)
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_snapshot"])
        cfg = dict(target.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloudsql_sqlserver_config"])
        target_id = self._instance_id(target, cfg)
        handle = _handle(target_id)
        if self._get_instance(target_id) is not None:
            return ProvisionResult(False, handle, f"restore target {target_id} already exists", ["already_exists"])
        source_password = self._secret_store.get(self._master_secret(source_instance))
        if source_password is None:
            return ProvisionResult(
                False,
                handle,
                f"source instance {source_instance} master secret is missing",
                ["missing_master_password_secret"],
            )
        source_database = self._secret_store.get(self._database_secret(source_instance))
        if source_database and "database_name" not in cfg:
            cfg["database_name"] = source_database
        try:
            self._secret_store.upsert(self._master_secret(target_id), source_password, labels=_labels(target))
            body = {
                "restoreBackupContext": {
                    "backupRunId": backup_id,
                    "instanceId": source_instance,
                    "project": source_project,
                },
                "restoreInstanceSettings": self._instance_body(
                    target,
                    cfg,
                    target_id,
                    source_password,
                ),
            }
            self._wait_operation(self._sql.restore_backup(self._config.project_id, target_id, body))
            database = self._database_name(target, cfg)
            self._ensure_database(target_id, database, cfg)
            self._secret_store.upsert(self._database_secret(target_id), database, labels=_labels(target))
        except Exception as exc:
            self._delete_connection_secrets(target_id)
            return ProvisionResult(False, handle, f"restore Cloud SQL backup: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Cloud SQL for SQL Server instance {target_id} restored from backup {backup_id}",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="mssql_cloudsql", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,97}$"},
                "adopt_existing": {"type": "boolean"},
                "delete_adopted": {"type": "boolean"},
                "engine_version": {"type": "string", "enum": list(_ENGINE_VERSIONS)},
                "cloudsql_edition": {"type": "string", "enum": ["ENTERPRISE", "ENTERPRISE_PLUS"]},
                "tier": {"type": "string"},
                "storage_gb": {"type": "integer", "minimum": 10, "maximum": 65536},
                "storage_type": {"type": "string", "enum": ["PD_SSD", "PD_HDD", "HYPERDISK_BALANCED"]},
                "storage_auto_resize": {"type": "boolean"},
                "storage_auto_resize_limit_gb": {"type": "integer", "minimum": 0, "maximum": 65536},
                "public_ipv4_enabled": {"type": "boolean"},
                "ssl_mode": {
                    "type": "string",
                    "enum": ["ENCRYPTED_ONLY", "ALLOW_UNENCRYPTED_AND_ENCRYPTED"],
                },
                "data_cache_enabled": {"type": "boolean"},
                "high_availability": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "point_in_time_recovery": {"type": "boolean"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "retained_backups_count": {"type": "integer", "minimum": 1, "maximum": 365},
                "retain_backups_on_delete": {"type": "boolean"},
                "final_backup_enabled": {"type": "boolean"},
                "final_backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "backup_start_time": {"type": "string", "pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"},
                "backup_location": {"type": "string"},
                "kms_key_name": {"type": "string"},
                "zone": {"type": "string"},
                "secondary_zone": {"type": "string"},
                "database_name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$"},
                "database_compatibility_level": {
                    "type": "integer",
                    "enum": [140, 150, 160, 170],
                },
                "database_recovery_model": {
                    "type": "string",
                    "enum": ["FULL", "SIMPLE", "BULK_LOGGED"],
                },
                "authorized_networks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "value"],
                        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                "database_flags": {"type": "object", "additionalProperties": {"type": "string"}},
                "maintenance_day": {"type": "integer", "minimum": 1, "maximum": 7},
                "maintenance_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "maintenance_update_track": {"type": "string", "enum": ["canary", "stable", "week5"]},
                "activation_policy": {"type": "string", "enum": ["ALWAYS", "NEVER"]},
                "query_insights": {"type": "boolean"},
                "query_string_length": {"type": "integer", "minimum": 256, "maximum": 1048576},
                "record_application_tags": {"type": "boolean"},
                "record_client_address": {"type": "boolean"},
                "allow_storage_shrink": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="mssql_cloudsql", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MSSQL_HOST": "Cloud SQL private/public IP",
                "MSSQL_PORT": "1433",
                "MSSQL_DB": "Provisioned SQL Server database",
                "MSSQL_USER": "Built-in administrator (sqlserver)",
                "MSSQL_PASSWORD": "Secret Manager reference to the administrator password",
                "MSSQL_ENCRYPT": "true",
                "DATABASE_URL": "Secret Manager reference to an encrypted mssql:// URL",
                "GCP_CLOUDSQL_INSTANCE": "Cloud SQL instance ID",
                "GCP_CLOUDSQL_CONNECTION_NAME": "Cloud SQL Auth Proxy/connector connection name",
            },
        )

    def _instance_body(
        self,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
        instance_id: str,
        password: str,
    ) -> dict[str, Any]:
        engine = str(cfg.get("engine_version") or self._config.engine_version)
        edition = str(cfg.get("cloudsql_edition") or "ENTERPRISE")
        tier = str(cfg.get("tier") or _tier_for(spec.size, engine=engine, edition=edition))
        retention = int(cfg.get("backup_retention_days", self._config.backup_retention_days))
        ssl_mode = str(cfg.get("ssl_mode") or "ENCRYPTED_ONLY")
        ip_config: dict[str, Any] = {
            "ipv4Enabled": bool(cfg.get("public_ipv4_enabled", self._config.private_network is None)),
            "sslMode": ssl_mode,
            "requireSsl": ssl_mode == "ENCRYPTED_ONLY",
        }
        if self._config.private_network:
            ip_config["privateNetwork"] = self._config.private_network
        if "authorized_networks" in cfg:
            ip_config["authorizedNetworks"] = list(cfg["authorized_networks"])
        labels = _labels(spec)
        labels["astrolift-database"] = _label_value(self._database_name(spec, cfg))
        settings: dict[str, Any] = {
            "tier": tier,
            "edition": edition,
            "dataDiskSizeGb": int(cfg.get("storage_gb") or _SIZE_TO_STORAGE_GB.get(spec.size, 20)),
            "dataDiskType": str(cfg.get("storage_type") or "PD_SSD"),
            "storageAutoResize": bool(cfg.get("storage_auto_resize", True)),
            "availabilityType": (
                "REGIONAL" if bool(cfg.get("high_availability", self._config.high_availability_default)) else "ZONAL"
            ),
            "deletionProtectionEnabled": bool(
                cfg.get("deletion_protection", self._config.deletion_protection_default),
            ),
            "backupConfiguration": {
                "enabled": True,
                "pointInTimeRecoveryEnabled": bool(cfg.get("point_in_time_recovery", True)),
                "transactionLogRetentionDays": retention,
                "backupRetentionSettings": {
                    "retainedBackups": int(cfg.get("retained_backups_count", 7)),
                    "retentionUnit": "COUNT",
                },
            },
            "retainBackupsOnDelete": bool(cfg.get("retain_backups_on_delete", True)),
            "finalBackupConfig": {
                "enabled": bool(cfg.get("final_backup_enabled", True)),
                "retentionDays": int(cfg.get("final_backup_retention_days", 30)),
            },
            "ipConfiguration": ip_config,
            "userLabels": labels,
        }
        if "data_cache_enabled" in cfg:
            settings["dataCacheConfig"] = {"dataCacheEnabled": bool(cfg["data_cache_enabled"])}
        if cfg.get("storage_auto_resize_limit_gb") is not None:
            settings["storageAutoResizeLimit"] = int(cfg["storage_auto_resize_limit_gb"])
        if cfg.get("backup_start_time"):
            settings["backupConfiguration"]["startTime"] = cfg["backup_start_time"]
        if cfg.get("backup_location"):
            settings["backupConfiguration"]["location"] = cfg["backup_location"]
        if cfg.get("database_flags") is not None:
            settings["databaseFlags"] = [
                {"name": str(name), "value": str(value)} for name, value in sorted(dict(cfg["database_flags"]).items())
            ]
        if cfg.get("zone") or cfg.get("secondary_zone"):
            settings["locationPreference"] = {
                key: value
                for key, value in {
                    "zone": cfg.get("zone"),
                    "secondaryZone": cfg.get("secondary_zone"),
                }.items()
                if value
            }
        if cfg.get("maintenance_day") is not None or cfg.get("maintenance_hour") is not None:
            settings["maintenanceWindow"] = {
                "day": int(cfg.get("maintenance_day", 1)),
                "hour": int(cfg.get("maintenance_hour", 0)),
                "updateTrack": str(cfg.get("maintenance_update_track") or "stable"),
            }
        if cfg.get("activation_policy"):
            settings["activationPolicy"] = cfg["activation_policy"]
        if "query_insights" in cfg:
            settings["insightsConfig"] = {
                "queryInsightsEnabled": bool(cfg["query_insights"]),
                "queryStringLength": int(cfg.get("query_string_length", 4500)),
                "recordApplicationTags": bool(cfg.get("record_application_tags", False)),
                "recordClientAddress": bool(cfg.get("record_client_address", False)),
            }
        body: dict[str, Any] = {
            "name": instance_id,
            "region": self._config.region,
            "databaseVersion": engine,
            "rootPassword": password,
            "settings": settings,
        }
        if cfg.get("kms_key_name"):
            body["diskEncryptionConfiguration"] = {"kmsKeyName": cfg["kms_key_name"]}
        return body

    def _reconcile_instance(
        self,
        instance_id: str,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        size: str | None = None,
    ) -> None:
        immutable = {
            "engine_version": ("databaseVersion", current.get("databaseVersion")),
            "cloudsql_edition": ("settings.edition", (current.get("settings") or {}).get("edition")),
            "kms_key_name": (
                "diskEncryptionConfiguration.kmsKeyName",
                (current.get("diskEncryptionConfiguration") or {}).get("kmsKeyName"),
            ),
        }
        for key, (field, actual) in immutable.items():
            if key in cfg and cfg[key] != actual:
                raise CloudSQLServerError(f"{field} is immutable ({actual!r} != {cfg[key]!r}); replace the instance")
        desired_settings: dict[str, Any] = {}
        desired_mask: list[str] = []
        settings = current.get("settings") or {}
        mutable = {
            "high_availability": ("availabilityType", "REGIONAL" if cfg.get("high_availability") else "ZONAL"),
            "deletion_protection": ("deletionProtectionEnabled", bool(cfg.get("deletion_protection"))),
            "storage_auto_resize": ("storageAutoResize", bool(cfg.get("storage_auto_resize"))),
            "storage_auto_resize_limit_gb": ("storageAutoResizeLimit", int(cfg.get("storage_auto_resize_limit_gb", 0))),
            "activation_policy": ("activationPolicy", cfg.get("activation_policy")),
            "storage_type": ("dataDiskType", cfg.get("storage_type")),
            "retain_backups_on_delete": ("retainBackupsOnDelete", bool(cfg.get("retain_backups_on_delete"))),
        }
        for key, (api_key, desired) in mutable.items():
            if key in cfg and settings.get(api_key) != desired:
                desired_settings[api_key] = desired
                desired_mask.append(f"settings.{api_key}")
        requested_size = size or ""
        current_engine = str(current.get("databaseVersion") or self._config.engine_version)
        current_edition = str(settings.get("edition") or "ENTERPRISE")
        desired_tier = cfg.get("tier") or (
            _tier_for(requested_size, engine=current_engine, edition=current_edition) if requested_size else None
        )
        if desired_tier and settings.get("tier") != desired_tier:
            desired_settings["tier"] = desired_tier
            desired_mask.append("settings.tier")
        if "storage_gb" in cfg:
            actual_storage = int(settings.get("dataDiskSizeGb") or 0)
            desired_storage = int(cfg["storage_gb"])
            if desired_storage < actual_storage:
                self._shrink_storage(
                    instance_id,
                    actual_storage=actual_storage,
                    desired_storage=desired_storage,
                    acknowledged=bool(cfg.get("allow_storage_shrink")),
                )
            elif desired_storage > actual_storage:
                desired_settings["dataDiskSizeGb"] = desired_storage
                desired_mask.append("settings.dataDiskSizeGb")
        backup = dict(settings.get("backupConfiguration") or {})
        desired_backup: dict[str, Any] = {}
        if "point_in_time_recovery" in cfg:
            desired_backup["pointInTimeRecoveryEnabled"] = bool(cfg["point_in_time_recovery"])
        if "backup_retention_days" in cfg:
            desired_backup["transactionLogRetentionDays"] = int(cfg["backup_retention_days"])
        if "retained_backups_count" in cfg:
            desired_backup["backupRetentionSettings"] = {
                "retainedBackups": int(cfg["retained_backups_count"]),
                "retentionUnit": "COUNT",
            }
        if "backup_start_time" in cfg:
            desired_backup["startTime"] = cfg["backup_start_time"]
        if "backup_location" in cfg:
            desired_backup["location"] = cfg["backup_location"]
        changed_backup = {key: value for key, value in desired_backup.items() if backup.get(key) != value}
        if changed_backup:
            desired_settings["backupConfiguration"] = {**backup, **changed_backup}
            desired_mask.extend(f"settings.backupConfiguration.{key}" for key in changed_backup)
        if "final_backup_enabled" in cfg or "final_backup_retention_days" in cfg:
            actual_final = dict(settings.get("finalBackupConfig") or {})
            desired_final = {
                "enabled": bool(cfg.get("final_backup_enabled", actual_final.get("enabled", True))),
                "retentionDays": int(
                    str(cfg.get("final_backup_retention_days", actual_final.get("retentionDays") or 30)),
                ),
            }
            if actual_final != desired_final:
                desired_settings["finalBackupConfig"] = desired_final
                desired_mask.append("settings.finalBackupConfig")
        if "database_flags" in cfg:
            desired_flags = [
                {"name": str(name), "value": str(value)} for name, value in sorted(dict(cfg["database_flags"]).items())
            ]
            actual_flags = sorted(
                [
                    {"name": str(row.get("name") or ""), "value": str(row.get("value") or "")}
                    for row in settings.get("databaseFlags") or []
                ],
                key=lambda row: row["name"],
            )
            if actual_flags != desired_flags:
                desired_settings["databaseFlags"] = desired_flags
                desired_mask.append("settings.databaseFlags")
        if any(key in cfg for key in ("authorized_networks", "public_ipv4_enabled", "ssl_mode")):
            actual_ip = dict(settings.get("ipConfiguration") or {})
            desired_ip = dict(actual_ip)
            if "authorized_networks" in cfg:
                desired_ip["authorizedNetworks"] = list(cfg["authorized_networks"])
            if "public_ipv4_enabled" in cfg:
                desired_ip["ipv4Enabled"] = bool(cfg["public_ipv4_enabled"])
            if "ssl_mode" in cfg:
                desired_ip["sslMode"] = cfg["ssl_mode"]
                desired_ip["requireSsl"] = cfg["ssl_mode"] == "ENCRYPTED_ONLY"
            if actual_ip != desired_ip:
                desired_settings["ipConfiguration"] = desired_ip
                desired_mask.append("settings.ipConfiguration")
        if "data_cache_enabled" in cfg:
            desired_cache = {"dataCacheEnabled": bool(cfg["data_cache_enabled"])}
            if settings.get("dataCacheConfig") != desired_cache:
                desired_settings["dataCacheConfig"] = desired_cache
                desired_mask.append("settings.dataCacheConfig")
        if any(key in cfg for key in ("maintenance_day", "maintenance_hour", "maintenance_update_track")):
            desired_maintenance = {
                "day": int(cfg.get("maintenance_day", 1)),
                "hour": int(cfg.get("maintenance_hour", 0)),
                "updateTrack": str(cfg.get("maintenance_update_track") or "stable"),
            }
            if settings.get("maintenanceWindow") != desired_maintenance:
                desired_settings["maintenanceWindow"] = desired_maintenance
                desired_mask.append("settings.maintenanceWindow")
        if any(
            key in cfg
            for key in (
                "query_insights",
                "query_string_length",
                "record_application_tags",
                "record_client_address",
            )
        ):
            actual_insights = dict(settings.get("insightsConfig") or {})
            desired_insights = {
                "queryInsightsEnabled": bool(
                    cfg.get("query_insights", actual_insights.get("queryInsightsEnabled", False)),
                ),
                "queryStringLength": int(
                    str(cfg.get("query_string_length", actual_insights.get("queryStringLength") or 4500)),
                ),
                "recordApplicationTags": bool(
                    cfg.get("record_application_tags", actual_insights.get("recordApplicationTags", False)),
                ),
                "recordClientAddress": bool(
                    cfg.get("record_client_address", actual_insights.get("recordClientAddress", False)),
                ),
            }
            if actual_insights != desired_insights:
                desired_settings["insightsConfig"] = desired_insights
                desired_mask.append("settings.insightsConfig")
        if not desired_mask:
            return
        operation = self._sql.patch_instance(
            self._config.project_id,
            instance_id,
            {"settings": desired_settings},
            update_mask=desired_mask,
        )
        self._wait_operation(operation)

    def _shrink_storage(
        self,
        instance_id: str,
        *,
        actual_storage: int,
        desired_storage: int,
        acknowledged: bool,
    ) -> None:
        if not acknowledged:
            raise CloudSQLServerError(
                f"storage shrink {actual_storage} -> {desired_storage} GiB requires allow_storage_shrink=true",
            )
        shrink_config = self._sql.get_disk_shrink_config(self._config.project_id, instance_id)
        minimum_raw = (
            shrink_config.get("min_target_size_gb")
            or shrink_config.get("minimalTargetSizeGb")
            or shrink_config.get("minTargetSizeGb")
        )
        if minimum_raw is None:
            raise CloudSQLServerError("Cloud SQL storage-shrink preflight did not return a minimum target size")
        minimum = int(minimum_raw)
        if desired_storage < minimum:
            raise CloudSQLServerError(
                f"storage shrink target {desired_storage} GiB is below Cloud SQL's safe minimum of {minimum} GiB",
            )
        self._create_backup(instance_id)
        self._wait_operation(
            self._sql.perform_disk_shrink(
                self._config.project_id,
                instance_id,
                desired_storage,
            ),
        )

    def _create_backup(self, instance_id: str, *, created: datetime | None = None) -> str:
        timestamp = created or datetime.now(UTC)
        operation = self._wait_operation(
            self._sql.create_backup(
                self._config.project_id,
                instance_id,
                {"description": f"astrolift-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}"},
            ),
        )
        backup_id = str((operation.get("backupContext") or {}).get("backupId") or "")
        if not backup_id:
            raise CloudSQLServerError("Cloud SQL backup operation completed without backupContext.backupId")
        return backup_id

    def _ensure_database(self, instance_id: str, database: str, cfg: dict[str, Any]) -> None:
        try:
            current = self._sql.get_database(self._config.project_id, instance_id, database)
        except CloudSQLServerNotFound:
            body: dict[str, Any] = {"name": database, "instance": instance_id, "project": self._config.project_id}
            details = {
                key: value
                for key, value in {
                    "compatibilityLevel": cfg.get("database_compatibility_level"),
                    "recoveryModel": cfg.get("database_recovery_model"),
                }.items()
                if value is not None
            }
            if details:
                body["sqlserverDatabaseDetails"] = details
            self._wait_operation(self._sql.create_database(self._config.project_id, instance_id, body))
            return
        actual_details = current.get("sqlserverDatabaseDetails") or {}
        desired_details = dict(actual_details)
        if "database_compatibility_level" in cfg:
            desired_details["compatibilityLevel"] = int(cfg["database_compatibility_level"])
        if "database_recovery_model" in cfg:
            desired_details["recoveryModel"] = str(cfg["database_recovery_model"])
        if desired_details != actual_details:
            body = {
                "name": database,
                "instance": instance_id,
                "project": self._config.project_id,
                "sqlserverDatabaseDetails": desired_details,
            }
            self._wait_operation(
                self._sql.patch_database(
                    self._config.project_id,
                    instance_id,
                    database,
                    body,
                ),
            )

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        name = str(operation.get("name") or "")
        current = operation
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while str(current.get("status") or "") != "DONE":
            if not name:
                raise CloudSQLServerError("Cloud SQL operation response has no name")
            if self._monotonic() >= deadline:
                raise CloudSQLServerError(f"Cloud SQL operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._sql.get_operation(self._config.project_id, name)
        errors = list((current.get("error") or {}).get("errors") or [])
        if errors:
            detail = "; ".join(str(row.get("message") or row.get("code") or row) for row in errors)
            raise CloudSQLServerError(f"Cloud SQL operation {name} failed: {detail}")
        return current

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unknown Cloud SQL for SQL Server config keys: {', '.join(unknown)}"
        engine = str(cfg.get("engine_version") or self._config.engine_version)
        if engine not in _ENGINE_VERSIONS:
            return f"unsupported Cloud SQL SQL Server engine_version {engine!r}"
        edition = str(cfg.get("cloudsql_edition") or "ENTERPRISE")
        if edition not in {"ENTERPRISE", "ENTERPRISE_PLUS"}:
            return f"unsupported Cloud SQL edition {edition!r}"
        enterprise_plus_engines = {
            "SQLSERVER_2019_ENTERPRISE",
            "SQLSERVER_2022_ENTERPRISE",
            "SQLSERVER_2025_ENTERPRISE",
        }
        if edition == "ENTERPRISE_PLUS" and engine not in enterprise_plus_engines:
            return "Cloud SQL ENTERPRISE_PLUS requires SQL Server 2019, 2022, or 2025 Enterprise"
        if cfg.get("data_cache_enabled") and edition != "ENTERPRISE_PLUS":
            return "data_cache_enabled requires Cloud SQL ENTERPRISE_PLUS"
        public_ipv4 = bool(cfg.get("public_ipv4_enabled", self._config.private_network is None))
        if cfg.get("authorized_networks") and not public_ipv4:
            return "authorized_networks require public_ipv4_enabled=true"
        if "database_compatibility_level" in cfg:
            engine_year = int(engine.split("_")[1])
            maximum_compatibility = {2017: 140, 2019: 150, 2022: 160, 2025: 170}[engine_year]
            try:
                compatibility = int(cfg["database_compatibility_level"])
            except (TypeError, ValueError):
                return "database_compatibility_level must be an integer"
            if compatibility not in {140, 150, 160, 170} or compatibility > maximum_compatibility:
                return (
                    f"database_compatibility_level {compatibility} is not supported by {engine}; "
                    f"maximum is {maximum_compatibility}"
                )
        recovery_model = str(cfg.get("database_recovery_model") or "FULL")
        if recovery_model not in {"FULL", "SIMPLE", "BULK_LOGGED"}:
            return f"unsupported SQL Server database_recovery_model {recovery_model!r}"
        if recovery_model != "FULL" and cfg.get("point_in_time_recovery", True) is not False:
            return (
                "database_recovery_model must be FULL while point-in-time recovery is enabled; "
                "set point_in_time_recovery=false explicitly before selecting SIMPLE or BULK_LOGGED"
            )
        try:
            retention = int(cfg.get("backup_retention_days", self._config.backup_retention_days))
        except (TypeError, ValueError):
            return "backup_retention_days must be an integer"
        maximum = 35 if edition == "ENTERPRISE_PLUS" else 7
        if not 1 <= retention <= maximum:
            return f"backup_retention_days must be between 1 and {maximum} for {edition}"
        for key, default in (("retained_backups_count", 7), ("final_backup_retention_days", 30)):
            try:
                value = int(cfg.get(key, default))
            except (TypeError, ValueError):
                return f"{key} must be an integer"
            if not 1 <= value <= 365:
                return f"{key} must be between 1 and 365"
        if "query_string_length" in cfg:
            try:
                query_length = int(cfg["query_string_length"])
            except (TypeError, ValueError):
                return "query_string_length must be an integer"
            query_maximum = 1048576 if edition == "ENTERPRISE_PLUS" else 4500
            if not 256 <= query_length <= query_maximum:
                return f"query_string_length must be between 256 and {query_maximum} for {edition}"
        if cfg.get("instance_id") and not re.fullmatch(r"[a-z][a-z0-9-]{0,97}", str(cfg["instance_id"])):
            return (
                "instance_id must start with a lowercase letter and contain only lowercase letters, digits, and hyphens"
            )
        return ""

    def _get_instance(self, instance_id: str) -> dict[str, Any] | None:
        try:
            return self._sql.get_instance(self._config.project_id, instance_id)
        except CloudSQLServerNotFound:
            return None

    def _assert_owned(self, current: dict[str, Any], cfg: dict[str, Any], *, deleting: bool = False) -> None:
        labels = (current.get("settings") or {}).get("userLabels") or {}
        owned = labels.get("astrolift-managed-by") == "platform"
        if not owned and not cfg.get("adopt_existing"):
            raise CloudSQLServerError("existing Cloud SQL instance is not Astrolift-managed; set adopt_existing=true")
        if deleting and not owned and not cfg.get("delete_adopted"):
            raise CloudSQLServerError("adopted Cloud SQL instances require delete_adopted=true before deletion")

    def _instance_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("instance_id") or "")
        if explicit:
            return explicit
        raw = (
            (
                f"{self._config.instance_name_prefix}-{spec.organization_slug}-{spec.app_slug}-"
                f"{spec.environment_name}-{spec.service_handle_hint or 'sqlserver'}"
            )
            .lower()
            .replace("_", "-")
        )
        value = "".join(char for char in raw if char.isalnum() or char == "-")[:98].rstrip("-")
        if not value or not value[0].isalpha():
            value = f"a-{value}"[:98]
        return value

    def _database_name(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        if cfg.get("database_name"):
            return str(cfg["database_name"])
        raw = f"{spec.app_slug}_{spec.environment_name}_{spec.service_handle_hint or 'app'}"
        value = "".join(char if char.isalnum() or char == "_" else "_" for char in raw)[:128]
        return value if value and value[0].isalpha() else f"app_{value}"[:128]

    def _database_from_instance(self, current: dict[str, Any]) -> str:
        labels = (current.get("settings") or {}).get("userLabels") or {}
        return str(labels.get("astrolift-database") or "")

    def _master_secret(self, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/master"

    def _url_secret(self, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/url"

    def _database_secret(self, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/database"

    def _delete_connection_secrets(self, instance_id: str) -> None:
        for path in (
            self._master_secret(instance_id),
            self._url_secret(instance_id),
            self._database_secret(instance_id),
        ):
            with contextlib.suppress(ManagedSecretStoreError):
                self._secret_store.delete(path)


def _handle(instance_id: str) -> str:
    return f"{KIND}/{instance_id}"


def _parse_handle(handle: str) -> str:
    kind, separator, instance = handle.partition("/")
    if separator != "/" or kind != KIND or not instance:
        raise ValueError(f"handle {handle!r} must be 'mssql/<instance>'")
    return instance


def _parse_snapshot_id(snapshot_id: str) -> tuple[str, str, str]:
    parts = snapshot_id.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError("Cloud SQL snapshot id must be '<project>/<instance>/<backup-run-id>'")
    if not parts[2].isdigit():
        raise ValueError("Cloud SQL backup-run id must be numeric")
    return parts[0], parts[1], parts[2]


def _generate_master_password(length: int = 32) -> str:
    if length < 12:
        raise ValueError("SQL Server passwords must be at least 12 characters")
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("-_.!#$%"),
    ]
    alphabet = string.ascii_letters + string.digits + "-_.!#$%"
    required.extend(secrets.choice(alphabet) for _ in range(length - len(required)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def _preferred_ip(instance: dict[str, Any]) -> str:
    addresses = list(instance.get("ipAddresses") or [])
    for address in addresses:
        if address.get("type") == "PRIVATE":
            return str(address.get("ipAddress") or "")
    for address in addresses:
        if address.get("type") in {"PRIMARY", "OUTGOING"}:
            return str(address.get("ipAddress") or "")
    return ""


def _tier_for(size: str, *, engine: str, edition: str) -> str:
    if edition == "ENTERPRISE_PLUS":
        return _ENTERPRISE_PLUS_SIZE_TO_TIER.get(size, _ENTERPRISE_PLUS_SIZE_TO_TIER["small"])
    if engine.endswith("_ENTERPRISE"):
        return _ENTERPRISE_SIZE_TO_TIER.get(size, _ENTERPRISE_SIZE_TO_TIER["small"])
    return _SIZE_TO_TIER.get(size, _SIZE_TO_TIER["small"])


def _labels(spec: ProvisionSpec) -> dict[str, str]:
    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _label_value(spec.organization_slug),
        "astrolift-app": _label_value(spec.app_slug),
        "astrolift-environment": _label_value(spec.environment_name),
        "astrolift-cluster": _label_value(spec.tenant_cluster_id),
        "astrolift-isolation": _label_value(spec.isolation),
        "astrolift-database": _label_value(
            f"{spec.app_slug}_{spec.environment_name}_{spec.service_handle_hint or 'app'}",
        ),
    }
    if spec.binding_id:
        labels["astrolift-binding"] = _label_value(spec.binding_id)
    if spec.managed_service_id:
        labels["astrolift-managed-service-id"] = _label_value(spec.managed_service_id)
        labels[MANAGED_SERVICE_ID_LABEL] = _label_value(spec.managed_service_id)
    for key, value in (spec.tags or {}).items():
        labels[f"astrolift-extra-{_label_value(key)}"[:63]] = _label_value(value)
    return labels


def _label_value(value: Any) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in str(value).lower())[:63]


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{action}: {exc}", [str(exc)], retryable=True)
