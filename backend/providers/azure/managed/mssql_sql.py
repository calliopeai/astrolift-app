"""Azure SQL managed-service drivers for the portable ``mssql`` kind.

Azure SQL Database and Azure SQL Managed Instance are deliberately separate
drivers. A SQL Database binding owns a logical server and one database. A
Managed Instance binding owns one managed instance and one database. SQL Server
Express is not an Azure SQL Database edition and is therefore not implemented
by this module; it belongs to a VM/container execution profile.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from _sdk import UnsupportedOperationError
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
from azure.managed.tags import arm_tags_for as tags_for

KIND = "mssql"

SQL_DATABASE_VARIANTS = {
    "azure_sql_database",
    "azure_sql_serverless",
    "azure_sql_hyperscale",
}

_DATABASE_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "azure_sql_database": {
        "small": {"name": "GP_Gen5_2", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 2},
        "medium": {"name": "GP_Gen5_4", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 4},
        "large": {"name": "BC_Gen5_4", "tier": "BusinessCritical", "family": "Gen5", "capacity": 4},
        "xlarge": {"name": "BC_Gen5_8", "tier": "BusinessCritical", "family": "Gen5", "capacity": 8},
    },
    "azure_sql_serverless": {
        "small": {"name": "GP_S_Gen5_1", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 1},
        "medium": {"name": "GP_S_Gen5_2", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 2},
        "large": {"name": "GP_S_Gen5_4", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 4},
        "xlarge": {"name": "GP_S_Gen5_8", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 8},
    },
    "azure_sql_hyperscale": {
        "small": {"name": "HS_Gen5_2", "tier": "Hyperscale", "family": "Gen5", "capacity": 2},
        "medium": {"name": "HS_Gen5_4", "tier": "Hyperscale", "family": "Gen5", "capacity": 4},
        "large": {"name": "HS_Gen5_8", "tier": "Hyperscale", "family": "Gen5", "capacity": 8},
        "xlarge": {"name": "HS_Gen5_16", "tier": "Hyperscale", "family": "Gen5", "capacity": 16},
    },
}

_MAX_SIZE_GB = {"small": 32, "medium": 128, "large": 512, "xlarge": 1024}
_MI_PROFILES: dict[str, dict[str, Any]] = {
    "small": {"name": "GP_Gen5", "tier": "GeneralPurpose", "family": "Gen5", "v_cores": 8, "storage_gb": 256},
    "medium": {"name": "GP_Gen5", "tier": "GeneralPurpose", "family": "Gen5", "v_cores": 16, "storage_gb": 512},
    "large": {"name": "BC_Gen5", "tier": "BusinessCritical", "family": "Gen5", "v_cores": 16, "storage_gb": 1024},
    "xlarge": {"name": "BC_Gen5", "tier": "BusinessCritical", "family": "Gen5", "v_cores": 32, "storage_gb": 2048},
}

_DATABASE_STATES = {
    "Online": "available",
    "Paused": "available",
    "Creating": "provisioning",
    "Copying": "provisioning",
    "Restoring": "provisioning",
    "Recovering": "provisioning",
    "Scaling": "updating",
    "Resuming": "updating",
    "Dropping": "deprovisioning",
    "Inaccessible": "error",
    "Offline": "error",
    "Suspect": "error",
    "EmergencyMode": "error",
}

_MI_STATES = {
    "Ready": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
}

_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."


class AzureSQLError(Exception):
    """Azure SQL lifecycle failure with operator-actionable context."""


@dataclass(frozen=True)
class AzureSQLDatabaseConfig:
    subscription_id: str
    resource_group: str
    variant: str = "azure_sql_database"
    location: str = "eastus"
    server_name_prefix: str = "astrolift-sql"
    database_name_prefix: str = "astrolift"
    administrator_login: str = "astrolift"
    keyvault_url: str = ""
    secret_name_prefix: str = "astrolift-mssql"
    virtual_network_subnet_id: str = ""
    virtual_network_rule_name: str = "astrolift-aks"
    ignore_missing_vnet_service_endpoint: bool = False
    public_network_access_default: str = "Enabled"
    minimal_tls_version_default: str = "1.2"
    backup_retention_days_default: int = 7
    backup_storage_redundancy_default: str = "Geo"
    auto_pause_delay_minutes_default: int = 60
    min_capacity_default: float = 0.5
    mgmt_client: Any | None = None
    secret_client: Any | None = None

    def __post_init__(self) -> None:
        if self.variant not in SQL_DATABASE_VARIANTS:
            raise ValueError(f"unsupported Azure SQL Database variant {self.variant!r}")
        if self.public_network_access_default != "Enabled":
            raise ValueError(
                "Azure SQL Database public_network_access must remain Enabled for the selected-network "
                "service-endpoint driver; Disabled requires a Private Endpoint driver",
            )


@dataclass(frozen=True)
class AzureSQLManagedInstanceConfig:
    subscription_id: str
    resource_group: str
    subnet_id: str
    location: str = "eastus"
    instance_name_prefix: str = "astrolift-mi"
    database_name_prefix: str = "astrolift"
    administrator_login: str = "astrolift"
    keyvault_url: str = ""
    secret_name_prefix: str = "astrolift-mssql-mi"
    license_type_default: str = "LicenseIncluded"
    minimal_tls_version_default: str = "1.2"
    public_data_endpoint_enabled_default: bool = False
    backup_retention_days_default: int = 7
    mgmt_client: Any | None = None
    secret_client: Any | None = None


class _AzureSQLBase:
    def _init_clients(self, *, subscription_id: str, keyvault_url: str, mgmt_client: Any, secret_client: Any) -> None:
        if mgmt_client is not None:
            self._mgmt = mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.sql import SqlManagementClient

            self._mgmt = SqlManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=subscription_id,
            )
        if secret_client is not None:
            self._secrets = secret_client
        elif keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None

    def _secret_value(self, name: str) -> str:
        if self._secrets is None:
            raise AzureSQLError("Azure SQL driver requires a Key Vault")
        value = self._secrets.get_secret(name)
        raw = getattr(value, "value", value)
        if not raw:
            raise AzureSQLError(f"Key Vault secret {name!r} is empty")
        return str(raw)

    def _delete_secret(self, name: str) -> None:
        if self._secrets is None:
            return
        try:
            self._secrets.begin_delete_secret(name)
        except Exception as exc:
            if _not_found(exc):
                return
            raise AzureSQLError(f"delete Key Vault secret {name!r}: {exc}") from exc


class AzureSQLDatabaseDriver(_AzureSQLBase, ManagedServiceDriver):
    def __init__(self, *, config: AzureSQLDatabaseConfig) -> None:
        self._config = config
        self._init_clients(
            subscription_id=config.subscription_id,
            keyvault_url=config.keyvault_url,
            mgmt_client=config.mgmt_client,
            secret_client=config.secret_client,
        )

    @driver_op(cloud="azure", driver="mssql_sql_database", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(False, "", "Azure SQL Database requires a Key Vault", ["no_secret_backend"])
        if not self._config.virtual_network_subnet_id:
            return ProvisionResult(
                False,
                "",
                "Azure SQL Database requires a VNet service-endpoint subnet",
                ["missing_virtual_network_subnet_id"],
            )
        server_name = self._server_name_for(spec)
        database_name = self._database_name_for(spec)
        handle = self._handle_for(server_name, database_name)
        if self._describe_database(server_name, database_name) is not None:
            try:
                self._ensure_vnet_rule(server_name)
                self._apply_retention(server_name, database_name, spec.config or {})
                self._store_url_secret(server_name, database_name)
            except Exception as exc:
                return ProvisionResult(
                    False,
                    handle,
                    f"reconcile existing Azure SQL database: {exc}",
                    [str(exc)],
                )
            return ProvisionResult(
                True,
                handle,
                f"Azure SQL database {server_name}/{database_name} already exists and is reconciled",
                ready=True,
            )

        server = self._describe_server(server_name)
        if server is None:
            from azure.mgmt.sql.models import Server, ServerProperties

            password = _password()
            try:
                self._secrets.set_secret(self._password_secret(server_name), password)
                self._mgmt.servers.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    server_name=server_name,
                    parameters=Server(
                        location=self._config.location,
                        tags=tags_for(spec),
                        properties=ServerProperties(
                            administrator_login=self._config.administrator_login,
                            administrator_login_password=password,
                            version="12.0",
                            minimal_tls_version=self._config.minimal_tls_version_default,
                            public_network_access=self._config.public_network_access_default,
                        ),
                    ),
                ).result()
            except Exception as exc:
                return ProvisionResult(False, handle, f"create Azure SQL logical server: {exc}", [str(exc)])

        try:
            self._ensure_vnet_rule(server_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"configure Azure SQL VNet rule: {exc}", [str(exc)])

        parameters = self._database_parameters(spec)
        try:
            self._mgmt.databases.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                database_name=database_name,
                parameters=parameters,
            ).result()
            self._apply_retention(server_name, database_name, spec.config or {})
            self._store_url_secret(server_name, database_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create Azure SQL database: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Azure SQL database {server_name}/{database_name} is ready", ready=True)

    @driver_op(cloud="azure", driver="mssql_sql_database")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        server_name, database_name = self._parse_handle(spec.handle)
        if self._describe_database(server_name, database_name) is None:
            return UpdateResult(False, spec.handle, "Azure SQL database does not exist", ["not_found"])
        cfg = spec.config or {}
        sku = None
        property_values: dict[str, Any] = {}
        if spec.size or any(key in cfg for key in ("sku_name", "sku_tier", "capacity", "family")):
            sku = self._sku_model(spec.size or "small", cfg)
        if spec.size or "max_size_gb" in cfg:
            property_values["max_size_bytes"] = (
                int(cfg.get("max_size_gb") or _MAX_SIZE_GB.get(spec.size or "small", 32)) * 1024**3
            )
        for key in (
            "zone_redundant",
            "read_scale",
            "high_availability_replica_count",
            "auto_pause_delay",
            "min_capacity",
            "requested_backup_storage_redundancy",
        ):
            if key in cfg:
                property_values[key] = cfg[key]
        if sku is None and not property_values and "backup_retention_days" not in cfg:
            return UpdateResult(True, spec.handle, "no Azure SQL database changes requested")
        try:
            if sku is not None or property_values:
                from azure.mgmt.sql.models import DatabaseUpdate, DatabaseUpdateProperties

                self._mgmt.databases.begin_update(
                    resource_group_name=self._config.resource_group,
                    server_name=server_name,
                    database_name=database_name,
                    parameters=DatabaseUpdate(
                        sku=sku,
                        properties=DatabaseUpdateProperties(**property_values),
                    ),
                ).result()
            if "backup_retention_days" in cfg:
                self._apply_retention(server_name, database_name, cfg)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure SQL database: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure SQL database {server_name}/{database_name} updated")

    @driver_op(cloud="azure", driver="mssql_sql_database", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        server_name, database_name = self._parse_handle(spec.handle)
        if self._describe_database(server_name, database_name) is None:
            try:
                server_deleted = self._cleanup_after_database_delete(
                    server_name,
                    database_name,
                    delete_server_if_empty=delete_data,
                )
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Azure SQL database is gone but credential/server cleanup failed: {exc}",
                    [str(exc)],
                )
            return DeprovisionResult(
                True,
                spec.handle,
                f"Azure SQL database {server_name}/{database_name} already gone (server_deleted={server_deleted})",
            )
        snapshot_id = ""
        if not delete_data:
            try:
                snapshot_id = self._copy_database(server_name, database_name, prefix="final")
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"final Azure SQL database copy failed: {exc}",
                    [str(exc)],
                )
        try:
            self._mgmt.databases.begin_delete(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                database_name=database_name,
            ).result()
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Azure SQL database: {exc}", [str(exc)])
        try:
            server_deleted = self._cleanup_after_database_delete(
                server_name,
                database_name,
                delete_server_if_empty=delete_data,
            )
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"database deleted but credential/logical-server cleanup failed: {exc}",
                [str(exc)],
            )
        retained = f", retained_copy={snapshot_id}" if snapshot_id else ""
        return DeprovisionResult(
            True,
            spec.handle,
            f"Azure SQL database deleted (server_deleted={server_deleted}, force_destroy={force_destroy}{retained})",
        )

    @driver_op(cloud="azure", driver="mssql_sql_database")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        server_name, database_name = self._parse_handle(handle.handle)
        database = self._describe_database(server_name, database_name)
        if database is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Azure SQL database not found")
        state = _field(database, "status", "state", default="Unknown")
        return ServiceStatus(handle.handle, _DATABASE_STATES.get(state, "updating"), f"Azure SQL reports {state}")

    @driver_op(cloud="azure", driver="mssql_sql_database")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        server_name, database_name = self._parse_handle(handle.handle)
        if self._describe_database(server_name, database_name) is None:
            raise AzureSQLError(f"binding requested for missing Azure SQL database {server_name}/{database_name}")
        password_secret = self._password_secret(server_name)
        url_secret = self._store_url_secret(server_name, database_name)
        host = f"{server_name}.database.windows.net"
        return Binding(
            env_vars={
                "MSSQL_HOST": ValueRef(literal=host),
                "MSSQL_PORT": ValueRef(literal="1433"),
                "MSSQL_DB": ValueRef(literal=database_name),
                "MSSQL_USER": ValueRef(literal=self._config.administrator_login),
                "MSSQL_PASSWORD": ValueRef(secret_ref=password_secret),
                "MSSQL_ENCRYPT": ValueRef(literal="true"),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
            },
            iam_grants=[
                Grant(self._database_resource_id(server_name, database_name), ["Microsoft.Sql/servers/databases/read"]),
                Grant(password_secret, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
            ],
            notes=(
                "Azure SQL Database endpoint restricted to the configured VNet service-endpoint subnet; "
                "transport is encrypted and credentials are Key Vault references."
            ),
        )

    @driver_op(cloud="azure", driver="mssql_sql_database")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        server_name, database_name = self._parse_handle(handle.handle)
        if self._describe_database(server_name, database_name) is None:
            raise AzureSQLError(f"snapshot requested for missing Azure SQL database {server_name}/{database_name}")
        created = datetime.now(UTC)
        snapshot_id = self._copy_database(server_name, database_name, prefix="snap", created=created)
        return SnapshotHandle(handle.handle, snapshot_id, created.isoformat())

    @driver_op(cloud="azure", driver="mssql_sql_database")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(False, "", "Azure SQL Database requires a Key Vault", ["no_secret_backend"])
        if not self._config.virtual_network_subnet_id:
            return ProvisionResult(
                False,
                "",
                "Azure SQL Database requires a VNet service-endpoint subnet",
                ["missing_virtual_network_subnet_id"],
            )
        server_name = self._server_name_for(target)
        database_name = self._database_name_for(target)
        handle = self._handle_for(server_name, database_name)
        if self._describe_database(server_name, database_name) is not None:
            return ProvisionResult(
                True, handle, f"Azure SQL restore target {server_name}/{database_name} already exists"
            )
        server = self._describe_server(server_name)
        if server is None:
            from azure.mgmt.sql.models import Server, ServerProperties

            password = _password()
            try:
                self._secrets.set_secret(self._password_secret(server_name), password)
                self._mgmt.servers.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    server_name=server_name,
                    parameters=Server(
                        location=self._config.location,
                        tags=tags_for(target),
                        properties=ServerProperties(
                            administrator_login=self._config.administrator_login,
                            administrator_login_password=password,
                            version="12.0",
                            minimal_tls_version=self._config.minimal_tls_version_default,
                            public_network_access=self._config.public_network_access_default,
                        ),
                    ),
                ).result()
            except Exception as exc:
                return ProvisionResult(False, handle, f"create restore logical server: {exc}", [str(exc)])
        try:
            self._ensure_vnet_rule(server_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"configure restore VNet rule: {exc}", [str(exc)])
        parameters = self._database_parameters(
            target,
            create_mode="Copy",
            source_database_id=snapshot.snapshot_id,
        )
        try:
            self._mgmt.databases.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                database_name=database_name,
                parameters=parameters,
            ).result()
            self._apply_retention(server_name, database_name, target.config or {})
            self._store_url_secret(server_name, database_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"restore Azure SQL database copy: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"restored Azure SQL database from {snapshot.snapshot_id}", ready=True)

    @driver_op(cloud="azure", driver="mssql_sql_database", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "database_name": {"type": "string"},
            "sku_name": {"type": "string"},
            "sku_tier": {"type": "string"},
            "family": {"type": "string"},
            "capacity": {"type": "integer", "minimum": 1},
            "max_size_gb": {"type": "integer", "minimum": 1},
            "zone_redundant": {"type": "boolean"},
            "read_scale": {"type": "string", "enum": ["Enabled", "Disabled"]},
            "high_availability_replica_count": {"type": "integer", "minimum": 0},
            "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
            "diff_backup_interval_in_hours": {"type": "integer", "enum": [12, 24]},
            "requested_backup_storage_redundancy": {
                "type": "string",
                "enum": ["Local", "Zone", "Geo", "GeoZone"],
            },
        }
        if self._config.variant == "azure_sql_serverless":
            properties.update(
                {
                    "auto_pause_delay": {"type": "integer", "minimum": -1},
                    "min_capacity": {"type": "number", "minimum": 0.5},
                },
            )
        return {"type": "object", "properties": properties}

    @driver_op(cloud="azure", driver="mssql_sql_database", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return _binding_schema("Azure SQL Database")

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "sku_name",
            "sku_tier",
            "family",
            "capacity",
            "max_size_gb",
            "zone_redundant",
            "read_scale",
            "high_availability_replica_count",
            "auto_pause_delay",
            "min_capacity",
            "backup_retention_days",
            "diff_backup_interval_in_hours",
            "requested_backup_storage_redundancy",
        ]

    def _database_parameters(
        self,
        spec: ProvisionSpec,
        *,
        create_mode: str | None = None,
        source_database_id: str | None = None,
    ) -> Any:
        from azure.mgmt.sql.models import Database, DatabaseProperties

        cfg = spec.config or {}
        property_values: dict[str, Any] = {
            "max_size_bytes": int(cfg.get("max_size_gb") or _MAX_SIZE_GB.get(spec.size, 32)) * 1024**3,
            "requested_backup_storage_redundancy": str(
                cfg.get("requested_backup_storage_redundancy") or self._config.backup_storage_redundancy_default,
            ),
        }
        for key in ("zone_redundant", "read_scale", "high_availability_replica_count"):
            if key in cfg:
                property_values[key] = cfg[key]
        if self._config.variant == "azure_sql_serverless":
            property_values["auto_pause_delay"] = int(
                cfg.get("auto_pause_delay", self._config.auto_pause_delay_minutes_default),
            )
            property_values["min_capacity"] = float(cfg.get("min_capacity", self._config.min_capacity_default))
        if create_mode:
            property_values["create_mode"] = create_mode
        if source_database_id:
            property_values["source_database_id"] = source_database_id
        return Database(
            location=self._config.location,
            tags=tags_for(spec),
            sku=self._sku_model(spec.size, cfg),
            properties=DatabaseProperties(**property_values),
        )

    def _sku_for(self, size: str, cfg: dict[str, Any]) -> dict[str, Any]:
        profile = dict(
            _DATABASE_PROFILES[self._config.variant].get(size, _DATABASE_PROFILES[self._config.variant]["small"])
        )
        profile["name"] = str(cfg.get("sku_name") or profile["name"])
        profile["tier"] = str(cfg.get("sku_tier") or profile["tier"])
        profile["family"] = str(cfg.get("family") or profile["family"])
        profile["capacity"] = int(cfg.get("capacity") or profile["capacity"])
        return profile

    def _sku_model(self, size: str, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.sql.models import Sku

        return Sku(**self._sku_for(size, cfg))

    def _apply_retention(self, server_name: str, database_name: str, cfg: dict[str, Any]) -> None:
        from azure.mgmt.sql.models import (
            BackupShortTermRetentionPolicy,
            BackupShortTermRetentionPolicyProperties,
        )

        retention_days = int(cfg.get("backup_retention_days", self._config.backup_retention_days_default))
        interval = int(cfg.get("diff_backup_interval_in_hours", 12))
        self._mgmt.backup_short_term_retention_policies.begin_create_or_update(
            resource_group_name=self._config.resource_group,
            server_name=server_name,
            database_name=database_name,
            policy_name="default",
            parameters=BackupShortTermRetentionPolicy(
                properties=BackupShortTermRetentionPolicyProperties(
                    retention_days=retention_days,
                    diff_backup_interval_in_hours=interval,
                ),
            ),
        ).result()

    def _ensure_vnet_rule(self, server_name: str) -> None:
        from azure.mgmt.sql.models import VirtualNetworkRule, VirtualNetworkRuleProperties

        self._mgmt.virtual_network_rules.begin_create_or_update(
            resource_group_name=self._config.resource_group,
            server_name=server_name,
            virtual_network_rule_name=self._config.virtual_network_rule_name,
            parameters=VirtualNetworkRule(
                properties=VirtualNetworkRuleProperties(
                    virtual_network_subnet_id=self._config.virtual_network_subnet_id,
                    ignore_missing_vnet_service_endpoint=self._config.ignore_missing_vnet_service_endpoint,
                ),
            ),
        ).result()

    def _copy_database(
        self,
        server_name: str,
        database_name: str,
        *,
        prefix: str,
        created: datetime | None = None,
    ) -> str:
        created = created or datetime.now(UTC)
        copy_name = _safe_name(f"{database_name}-{prefix}-{created.strftime('%Y%m%d-%H%M%S')}", 128)
        source_id = self._database_resource_id(server_name, database_name)
        source = self._describe_database(server_name, database_name)
        from azure.mgmt.sql.models import Database, DatabaseProperties

        parameters = Database(
            location=self._config.location,
            tags=dict(_field(source, "tags", default={}) or {}),
            properties=DatabaseProperties(
                create_mode="Copy",
                source_database_id=source_id,
            ),
        )
        self._mgmt.databases.begin_create_or_update(
            resource_group_name=self._config.resource_group,
            server_name=server_name,
            database_name=copy_name,
            parameters=parameters,
        ).result()
        return self._database_resource_id(server_name, copy_name)

    def _describe_server(self, server_name: str) -> Any | None:
        try:
            return self._mgmt.servers.get(resource_group_name=self._config.resource_group, server_name=server_name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_database(self, server_name: str, database_name: str) -> Any | None:
        try:
            return self._mgmt.databases.get(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                database_name=database_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _user_databases(self, server_name: str) -> list[Any]:
        try:
            rows = self._mgmt.databases.list_by_server(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return []
            raise
        return [row for row in rows if str(_field(row, "name", default="")).lower() not in {"master", ""}]

    def _cleanup_after_database_delete(
        self,
        server_name: str,
        database_name: str,
        *,
        delete_server_if_empty: bool,
    ) -> bool:
        # Complete cleanup after a partially successful prior attempt. The
        # per-database DSN must never outlive its database.
        self._delete_secret(self._url_secret(server_name, database_name))
        if not delete_server_if_empty:
            return False
        server = self._describe_server(server_name)
        if server is not None and self._user_databases(server_name):
            return False
        if server is not None:
            self._mgmt.servers.begin_delete(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
            ).result()
        self._delete_secret(self._password_secret(server_name))
        return server is not None

    def _server_name_for(self, spec: ProvisionSpec) -> str:
        base = "-".join(
            part
            for part in (
                self._config.server_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "sql",
            )
            if part
        )
        return _unique_name(
            base,
            63,
            entropy=spec.managed_service_id or spec.app_id or spec.environment_id or base,
        )

    def _database_name_for(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("database_name") or "")
        if explicit:
            return _safe_name(explicit, 128)
        return _safe_name(f"{self._config.database_name_prefix}-{spec.service_handle_hint or spec.app_slug}", 128)

    def _handle_for(self, server_name: str, database_name: str) -> str:
        return f"{KIND}/{server_name}/{database_name}"

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != KIND or not parts[1] or not parts[2]:
            raise AzureSQLError(f"invalid Azure SQL Database handle {handle!r}")
        return parts[1], parts[2]

    def _database_resource_id(self, server_name: str, database_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Sql/servers/{server_name}/databases/{database_name}"
        )

    def _password_secret(self, server_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{server_name}",
            suffix="password",
        )

    def _url_secret(self, server_name: str, database_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{server_name}-{database_name}",
            suffix="url",
        )

    def _store_url_secret(self, server_name: str, database_name: str) -> str:
        password = self._secret_value(self._password_secret(server_name))
        name = self._url_secret(server_name, database_name)
        dsn = (
            f"mssql://{quote(self._config.administrator_login, safe='')}:{quote(password, safe='')}"
            f"@{server_name}.database.windows.net:1433/{quote(database_name, safe='')}"
            "?encrypt=true&trustServerCertificate=false"
        )
        self._secrets.set_secret(name, dsn)
        return name


class AzureSQLManagedInstanceDriver(_AzureSQLBase, ManagedServiceDriver):
    def __init__(self, *, config: AzureSQLManagedInstanceConfig) -> None:
        self._config = config
        self._init_clients(
            subscription_id=config.subscription_id,
            keyvault_url=config.keyvault_url,
            mgmt_client=config.mgmt_client,
            secret_client=config.secret_client,
        )

    @driver_op(
        cloud="azure",
        driver="mssql_managed_instance",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if not self._config.subnet_id:
            return ProvisionResult(
                False,
                "",
                "Azure SQL Managed Instance requires a delegated subnet ID",
                ["missing_subnet_id"],
            )
        if self._secrets is None:
            return ProvisionResult(False, "", "Azure SQL Managed Instance requires a Key Vault", ["no_secret_backend"])
        instance_name = self._instance_name_for(spec)
        database_name = self._database_name_for(spec)
        handle = self._handle_for(instance_name, database_name)
        instance = self._describe_instance(instance_name)
        if instance is None:
            from azure.mgmt.sql.models import ManagedInstance, ManagedInstanceProperties, Sku

            profile = self._profile(spec.size, spec.config or {})
            password = _password()
            try:
                self._secrets.set_secret(self._password_secret(instance_name), password)
                instance = self._mgmt.managed_instances.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    managed_instance_name=instance_name,
                    parameters=ManagedInstance(
                        location=self._config.location,
                        tags=tags_for(spec),
                        sku=Sku(
                            name=profile["name"],
                            tier=profile["tier"],
                            family=profile["family"],
                            capacity=profile["v_cores"],
                        ),
                        properties=ManagedInstanceProperties(
                            administrator_login=self._config.administrator_login,
                            administrator_login_password=password,
                            subnet_id=self._config.subnet_id,
                            license_type=str(
                                (spec.config or {}).get("license_type") or self._config.license_type_default,
                            ),
                            v_cores=profile["v_cores"],
                            storage_size_in_gb=profile["storage_gb"],
                            public_data_endpoint_enabled=bool(
                                (spec.config or {}).get(
                                    "public_data_endpoint_enabled",
                                    self._config.public_data_endpoint_enabled_default,
                                ),
                            ),
                            minimal_tls_version=self._config.minimal_tls_version_default,
                            zone_redundant=bool((spec.config or {}).get("zone_redundant", False)),
                            timezone_id=str((spec.config or {}).get("timezone_id", "UTC")),
                        ),
                    ),
                ).result()
            except Exception as exc:
                return ProvisionResult(False, handle, f"create Azure SQL Managed Instance: {exc}", [str(exc)])
        if self._describe_managed_database(instance_name, database_name) is None:
            from azure.mgmt.sql.models import ManagedDatabase

            try:
                self._mgmt.managed_databases.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    managed_instance_name=instance_name,
                    database_name=database_name,
                    parameters=ManagedDatabase(location=self._config.location),
                ).result()
            except Exception as exc:
                return ProvisionResult(False, handle, f"create Managed Instance database: {exc}", [str(exc)])
        try:
            self._apply_retention(instance_name, database_name, spec.config or {})
            if instance is None:
                instance = self._describe_instance(instance_name)
            host = str(_field(instance, "fully_qualified_domain_name", default=""))
            if not host:
                raise AzureSQLError(f"Azure SQL Managed Instance {instance_name} has no FQDN")
            self._store_url_secret(instance_name, database_name, host=host)
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile Managed Instance binding: {exc}", [str(exc)])
        return ProvisionResult(
            True, handle, f"Azure SQL Managed Instance {instance_name}/{database_name} is ready", ready=True
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        instance_name, _ = self._parse_handle(spec.handle)
        if self._describe_instance(instance_name) is None:
            return UpdateResult(False, spec.handle, "Azure SQL Managed Instance does not exist", ["not_found"])
        cfg = spec.config or {}
        sku = None
        property_values: dict[str, Any] = {}
        if spec.size or any(key in cfg for key in ("sku_name", "sku_tier", "family", "v_cores", "storage_gb")):
            from azure.mgmt.sql.models import Sku

            profile = self._profile(spec.size or "small", cfg)
            sku = Sku(
                name=profile["name"],
                tier=profile["tier"],
                family=profile["family"],
                capacity=profile["v_cores"],
            )
            property_values["v_cores"] = profile["v_cores"]
            property_values["storage_size_in_gb"] = profile["storage_gb"]
        for key in ("license_type", "zone_redundant"):
            if key in cfg:
                property_values[key] = cfg[key]
        if sku is None and not property_values:
            return UpdateResult(True, spec.handle, "no Azure SQL Managed Instance changes requested")
        try:
            from azure.mgmt.sql.models import ManagedInstanceProperties, ManagedInstanceUpdate

            self._mgmt.managed_instances.begin_update(
                resource_group_name=self._config.resource_group,
                managed_instance_name=instance_name,
                parameters=ManagedInstanceUpdate(
                    sku=sku,
                    properties=ManagedInstanceProperties(**property_values),
                ),
            ).result()
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure SQL Managed Instance: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure SQL Managed Instance {instance_name} updated")

    @driver_op(
        cloud="azure",
        driver="mssql_managed_instance",
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
        instance_name, database_name = self._parse_handle(spec.handle)
        if self._describe_instance(instance_name) is None:
            try:
                self._delete_secret(self._password_secret(instance_name))
                self._delete_secret(self._url_secret(instance_name, database_name))
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Managed Instance is gone but credential cleanup failed: {exc}",
                    [str(exc)],
                )
            return DeprovisionResult(True, spec.handle, f"Azure SQL Managed Instance {instance_name} already gone")
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                (
                    "Azure SQL Managed Instance has no immediate service snapshot primitive; "
                    "refusing non-destructive teardown. Retain the instance or configure and verify "
                    "long-term retention before using delete_data=True"
                ),
                ["data_preserving_teardown_unsupported"],
                retryable=False,
            )
        try:
            if self._describe_managed_database(instance_name, database_name) is not None:
                self._mgmt.managed_databases.begin_delete(
                    resource_group_name=self._config.resource_group,
                    managed_instance_name=instance_name,
                    database_name=database_name,
                ).result()
            self._mgmt.managed_instances.begin_delete(
                resource_group_name=self._config.resource_group,
                managed_instance_name=instance_name,
            ).result()
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Azure SQL Managed Instance: {exc}", [str(exc)])
        try:
            self._delete_secret(self._password_secret(instance_name))
            self._delete_secret(self._url_secret(instance_name, database_name))
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Managed Instance deleted but credential cleanup failed: {exc}",
                [str(exc)],
            )
        return DeprovisionResult(
            True,
            spec.handle,
            f"Azure SQL Managed Instance deleted (delete_data=True, force_destroy={force_destroy})",
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        instance_name, database_name = self._parse_handle(handle.handle)
        instance = self._describe_instance(instance_name)
        if instance is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Azure SQL Managed Instance not found")
        if self._describe_managed_database(instance_name, database_name) is None:
            return ServiceStatus(
                handle.handle,
                "error",
                f"Azure SQL Managed Instance exists but database {database_name} is missing",
            )
        state = _field(instance, "state", default="Unknown")
        return ServiceStatus(
            handle.handle, _MI_STATES.get(state, "updating"), f"Azure SQL Managed Instance reports {state}"
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        instance_name, database_name = self._parse_handle(handle.handle)
        instance = self._describe_instance(instance_name)
        if instance is None or self._describe_managed_database(instance_name, database_name) is None:
            raise AzureSQLError(
                f"binding requested for missing Managed Instance database {instance_name}/{database_name}"
            )
        host = str(_field(instance, "fully_qualified_domain_name", default=""))
        if not host:
            raise AzureSQLError(f"Azure SQL Managed Instance {instance_name} has no FQDN")
        password_secret = self._password_secret(instance_name)
        url_secret = self._store_url_secret(instance_name, database_name, host=host)
        return Binding(
            env_vars={
                "MSSQL_HOST": ValueRef(literal=host),
                "MSSQL_PORT": ValueRef(literal="1433"),
                "MSSQL_DB": ValueRef(literal=database_name),
                "MSSQL_USER": ValueRef(literal=self._config.administrator_login),
                "MSSQL_PASSWORD": ValueRef(secret_ref=password_secret),
                "MSSQL_ENCRYPT": ValueRef(literal="true"),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
            },
            iam_grants=[
                Grant(self._instance_resource_id(instance_name), ["Microsoft.Sql/managedInstances/read"]),
                Grant(password_secret, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
            ],
            notes="Azure SQL Managed Instance private endpoint with encrypted transport and Key Vault credentials.",
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise UnsupportedOperationError(
            "Azure SQL Managed Instance has automated PITR/LTR backups but no immediate service snapshot API; "
            "configure retention or export a native backup before destructive teardown",
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise UnsupportedOperationError(
            "Azure SQL Managed Instance restore requires a provider backup resource ID and is not represented "
            "by the portable on-demand snapshot contract",
        )

    @driver_op(cloud="azure", driver="mssql_managed_instance", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database_name": {"type": "string"},
                "sku_name": {"type": "string"},
                "sku_tier": {"type": "string"},
                "family": {"type": "string"},
                "v_cores": {"type": "integer", "enum": [8, 16, 24, 32, 40, 64, 80]},
                "storage_gb": {"type": "integer", "minimum": 32, "multipleOf": 32},
                "license_type": {"type": "string", "enum": ["LicenseIncluded", "BasePrice"]},
                "zone_redundant": {"type": "boolean"},
                "timezone_id": {"type": "string"},
                "public_data_endpoint_enabled": {"type": "boolean"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
            },
        }

    @driver_op(cloud="azure", driver="mssql_managed_instance", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return _binding_schema("Azure SQL Managed Instance")

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "sku_name",
            "sku_tier",
            "family",
            "v_cores",
            "storage_gb",
            "license_type",
            "zone_redundant",
        ]

    def _profile(self, size: str, cfg: dict[str, Any]) -> dict[str, Any]:
        profile = dict(_MI_PROFILES.get(size, _MI_PROFILES["small"]))
        profile["name"] = str(cfg.get("sku_name") or profile["name"])
        profile["tier"] = str(cfg.get("sku_tier") or profile["tier"])
        profile["family"] = str(cfg.get("family") or profile["family"])
        profile["v_cores"] = int(cfg.get("v_cores") or profile["v_cores"])
        profile["storage_gb"] = int(cfg.get("storage_gb") or profile["storage_gb"])
        if profile["storage_gb"] % 32:
            raise AzureSQLError("Azure SQL Managed Instance storage_gb must be a multiple of 32")
        return profile

    def _apply_retention(self, instance_name: str, database_name: str, cfg: dict[str, Any]) -> None:
        from azure.mgmt.sql.models import (
            ManagedBackupShortTermRetentionPolicy,
            ManagedBackupShortTermRetentionPolicyProperties,
        )

        self._mgmt.managed_backup_short_term_retention_policies.begin_create_or_update(
            resource_group_name=self._config.resource_group,
            managed_instance_name=instance_name,
            database_name=database_name,
            policy_name="default",
            parameters=ManagedBackupShortTermRetentionPolicy(
                properties=ManagedBackupShortTermRetentionPolicyProperties(
                    retention_days=int(
                        cfg.get("backup_retention_days", self._config.backup_retention_days_default),
                    ),
                ),
            ),
        ).result()

    def _describe_instance(self, instance_name: str) -> Any | None:
        try:
            return self._mgmt.managed_instances.get(
                resource_group_name=self._config.resource_group,
                managed_instance_name=instance_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_managed_database(self, instance_name: str, database_name: str) -> Any | None:
        try:
            return self._mgmt.managed_databases.get(
                resource_group_name=self._config.resource_group,
                managed_instance_name=instance_name,
                database_name=database_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _instance_name_for(self, spec: ProvisionSpec) -> str:
        base = "-".join(
            part
            for part in (
                self._config.instance_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "sql",
            )
            if part
        )
        return _unique_name(
            base,
            63,
            entropy=spec.managed_service_id or spec.app_id or spec.environment_id or base,
        )

    def _database_name_for(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("database_name") or "")
        return _safe_name(
            explicit or f"{self._config.database_name_prefix}-{spec.service_handle_hint or spec.app_slug}", 128
        )

    def _handle_for(self, instance_name: str, database_name: str) -> str:
        return f"{KIND}/{instance_name}/{database_name}"

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != KIND or not parts[1] or not parts[2]:
            raise AzureSQLError(f"invalid Azure SQL Managed Instance handle {handle!r}")
        return parts[1], parts[2]

    def _instance_resource_id(self, instance_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Sql/managedInstances/{instance_name}"
        )

    def _password_secret(self, instance_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{instance_name}",
            suffix="password",
        )

    def _url_secret(self, instance_name: str, database_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{instance_name}-{database_name}",
            suffix="url",
        )

    def _store_url_secret(self, instance_name: str, database_name: str, *, host: str | None = None) -> str:
        password = self._secret_value(self._password_secret(instance_name))
        host = host or f"{instance_name}.database.windows.net"
        name = self._url_secret(instance_name, database_name)
        dsn = (
            f"mssql://{quote(self._config.administrator_login, safe='')}:{quote(password, safe='')}"
            f"@{host}:1433/{quote(database_name, safe='')}?encrypt=true&trustServerCertificate=false"
        )
        self._secrets.set_secret(name, dsn)
        return name


def _binding_schema(product: str) -> BindingSchema:
    return BindingSchema(
        env_vars={
            "MSSQL_HOST": f"{product} endpoint",
            "MSSQL_PORT": "SQL Server port (1433)",
            "MSSQL_DB": "Provisioned database name",
            "MSSQL_USER": "SQL administrator login",
            "MSSQL_PASSWORD": "Key Vault password reference",
            "MSSQL_ENCRYPT": "Require encrypted client transport",
            "DATABASE_URL": "Key Vault SQL Server DSN reference",
        },
    )


def _password(length: int = 40) -> str:
    while True:
        value = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if any(c.isupper() for c in value) and any(c.islower() for c in value) and any(c.isdigit() for c in value):
            return value


def _safe_name(value: str, limit: int) -> str:
    clean = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-")[:limit].rstrip("-")


def _unique_name(value: str, limit: int, *, entropy: str) -> str:
    """Keep a stable uniqueness suffix when truncating global Azure names."""
    suffix = hashlib.sha256(entropy.encode()).hexdigest()[:10]
    clean = _safe_name(value, limit)
    stem = clean[: limit - len(suffix) - 1].rstrip("-") or "astrolift"
    return f"{stem}-{suffix}"


def _keyvault_secret_name(value: str, *, suffix: str) -> str:
    """Return an Azure Key Vault-safe name without truncation collisions."""
    clean = _safe_name(f"{value}-{suffix}", 10_000) or f"astrolift-{suffix}"
    if len(clean) <= 127:
        return clean
    digest = hashlib.sha256(clean.encode()).hexdigest()[:10]
    tail = f"-{digest}-{suffix}"
    stem = clean[: 127 - len(tail)].rstrip("-") or "astrolift"
    return f"{stem}{tail}"


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        found = getattr(value, name, None)
        if found is not None:
            return found
    properties = getattr(value, "properties", None)
    if properties is not None:
        for name in names:
            found = getattr(properties, name, None)
            if found is not None:
                return found
    if isinstance(value, dict) and isinstance(value.get("properties"), dict):
        for name in names:
            if name in value["properties"]:
                return value["properties"][name]
    return default


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ == "ResourceNotFoundError" or "ResourceNotFound" in str(exc) or "NotFound" in str(exc)
